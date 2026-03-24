"""识屏二阶段：上下文收集、多模态请求与主分析流程。

同伴向记忆/共同经历等片段由 ``companion_context`` 模块生成，本模块只负责编排进 ``prompt_parts``，
不在别处重复拼装同类长文（见该模块顶部的分工说明）。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from typing import Any

from astrbot.api import logger
from astrbot.api.message_components import BaseMessageComponent, Image, Plain
from astrbot.api.star import StarTools

from .gemini_multimodal import (
    call_provider_multimodal_direct,
    get_current_chat_provider_id,
    supports_native_gemini_video_audio,
)
from .observations_store import add_observation
from .companion_context import (
    build_companion_response_guide,
    get_relevant_shared_activities,
    shared_activity_category_label,
    should_offer_shared_activity_invite,
    trigger_related_memories,
)
from .learning_preferences import get_scene_preference
from .persona import append_privacy_guard_prompt, get_persona_prompt
from .screen_vision import recognize_screen_material


async def gather_screen_analysis_context(
    host: Any,
    *,
    active_window_title: str,
    debug_mode: bool,
    allow_rest_hint: bool = False,
) -> dict[str, str]:
    scene = "未知"
    scene_prompt = ""
    time_prompt = ""
    holiday_prompt = ""
    system_status_prompt = ""
    weather_prompt = ""

    if active_window_title:
        try:
            scene = host._identify_scene(active_window_title)
            scene_prompt = get_scene_preference(host, scene)
        except Exception as e:
            if debug_mode:
                logger.debug(f"场景识别失败: {e}")

    try:
        time_prompt = host._get_time_prompt(allow_rest_hint=allow_rest_hint)
    except Exception as e:
        if debug_mode:
            logger.debug(f"获取时间提示失败: {e}")

    try:
        holiday_prompt = host._get_holiday_prompt()
    except Exception as e:
        if debug_mode:
            logger.debug(f"获取节日提示失败: {e}")

    try:
        system_status_prompt, _ = host._get_system_status_prompt()
    except Exception as e:
        if debug_mode:
            logger.debug(f"获取系统状态失败: {e}")

    try:
        weather_prompt = await host._get_weather_prompt()
    except Exception as e:
        if debug_mode:
            logger.debug(f"获取天气提示失败: {e}")

    return {
        "scene": scene,
        "scene_prompt": scene_prompt,
        "time_prompt": time_prompt,
        "holiday_prompt": holiday_prompt,
        "system_status_prompt": system_status_prompt,
        "weather_prompt": weather_prompt,
    }


async def collect_recent_conversation_context(
    host: Any,
    session=None,
    *,
    debug_mode: bool,
) -> list[str]:
    contexts: list[str] = []
    try:
        ctx = host._as_context()
        conv_mgr = ctx.conversation_manager
        uid = ""
        try:
            uid = session.unified_msg_origin if session else ""
        except Exception as e:
            if debug_mode:
                logger.debug(f"读取会话 UID 失败: {e}")

        if not uid:
            pt = str(getattr(host, "proactive_target", "") or "").strip()
            if pt:
                uid = host._normalize_target(pt)

        if not uid:
            return contexts

        try:
            curr_cid = await conv_mgr.get_curr_conversation_id(uid)
            if curr_cid:
                conversation = await conv_mgr.get_conversation(uid, curr_cid)
                if conversation and conversation.history:
                    try:
                        history_records = json.loads(conversation.history)
                    except (json.JSONDecodeError, TypeError):
                        history_records = []
                    if isinstance(history_records, list):
                        tail = history_records[
                            -host.recent_chat_context_messages :
                        ]
                        for msg in tail:
                            if not isinstance(msg, dict):
                                continue
                            if msg.get("role") in {"user", "assistant"}:
                                content = str(
                                    msg.get("content", "") or ""
                                ).strip()
                                if content:
                                    role = (
                                        "用户"
                                        if msg.get("role") == "user"
                                        else "助手"
                                    )
                                    contexts.append(f"{role}: {content}")
        except Exception as e:
            if debug_mode:
                logger.debug(f"读取对话上下文失败: {e}")
    except Exception as e:
        if debug_mode:
            logger.debug(f"收集上下文失败: {e}")
    return contexts


async def request_screen_interaction(
    host: Any,
    *,
    provider: Any,
    use_external_vision: bool,
    interaction_prompt: str,
    system_prompt: str,
    media_bytes: bytes,
    media_kind: str,
    mime_type: str,
    umo: str | None,
) -> Any:
    timeout_seconds = host._get_interaction_timeout(
        media_kind,
        use_external_vision,
    )
    if use_external_vision:
        return await asyncio.wait_for(
            provider.text_chat(
                prompt=interaction_prompt,
                system_prompt=system_prompt,
            ),
            timeout=timeout_seconds,
        )

    return await asyncio.wait_for(
        call_provider_multimodal_direct(
            host,
            provider,
            interaction_prompt,
            system_prompt,
            media_bytes,
            media_kind=media_kind,
            mime_type=mime_type,
            provider_id=await get_current_chat_provider_id(host, umo=umo),
        ),
        timeout=timeout_seconds,
    )


async def analyze_screen(
    host: Any,
    capture_context: dict[str, Any],
    session=None,
    active_window_title: str = "",
    custom_prompt: str = "",
    task_id: str = "unknown",
) -> list[BaseMessageComponent]:
    """Analyze the current screenshot or recording context and generate a reply."""
    should_send_rest_reminder, rest_reminder_info = host._should_send_rest_reminder()
    if host._is_in_rest_time_range() and not (should_send_rest_reminder and not custom_prompt):
        logger.info(f"[任务 {task_id}] 当前处于休息时段，跳过识屏。")
        return []

    if not host._is_in_active_time_range():
        logger.info(f"[任务 {task_id}] 当前不在主动互动时段，跳过识屏。")
        return []

    provider = host._as_context().get_using_provider()
    if not provider:
        return [Plain("当前没有可用的 AstrBot 模型提供方。")]

    umo = None
    if session and hasattr(session, "unified_msg_origin"):
        umo = session.unified_msg_origin

    system_prompt = await get_persona_prompt(host, umo)
    debug_mode = host._get_runtime_flag("debug")
    media_kind = str(capture_context.get("media_kind", "image") or "image")
    mime_type = str(capture_context.get("mime_type", "image/jpeg") or "image/jpeg")
    media_bytes = capture_context.get("media_bytes", b"") or b""
    vision_sampling_external = host._vision_prefers_external_sampling()
    effective_use_external_vision = False
    analysis_trace: dict[str, Any] = {
        "task_id": task_id,
        "trigger_reason": str(capture_context.get("trigger_reason", "") or ""),
        "media_kind": media_kind,
        "analysis_material_kind": media_kind,
        "sampling_strategy": "",
        "frame_count": 0,
        "frame_labels": [],
        "active_window_title": active_window_title,
        "scene": "",
        "recognition_summary": "",
        "reply_preview": "",
        "stored_as_observation": False,
        "stored_in_diary": False,
        "used_full_video": media_kind == "video",
        "status": "running",
        "memory_hints": [],
        "rest_reminder_planned": False,
    }
    analysis_trace["latest_window_title"] = str(
        capture_context.get("latest_window_title", "") or ""
    )
    analysis_trace["clip_active_window_title"] = str(
        capture_context.get("clip_active_window_title", "") or ""
    )
    capture_context["_rest_reminder_planned"] = False
    capture_context["_rest_reminder_info"] = {}

    analysis_context = await gather_screen_analysis_context(
        host,
        active_window_title=active_window_title,
        debug_mode=debug_mode,
        allow_rest_hint=should_send_rest_reminder and not custom_prompt,
    )
    scene = analysis_context["scene"]
    scene_prompt = analysis_context["scene_prompt"]
    time_prompt = analysis_context["time_prompt"]
    holiday_prompt = analysis_context["holiday_prompt"]
    system_status_prompt = analysis_context["system_status_prompt"]
    weather_prompt = analysis_context["weather_prompt"]
    analysis_trace["scene"] = scene

    contexts = await collect_recent_conversation_context(
        host,
        session,
        debug_mode=debug_mode,
    )
    reply_interval_guidance, reply_interval_info = host._build_reply_interval_guidance(
        task_id
    )
    analysis_trace["reply_interval_seconds"] = int(
        reply_interval_info.get("elapsed_seconds", 0) or 0
    )
    analysis_trace["reply_interval_bucket"] = str(
        reply_interval_info.get("bucket", "") or ""
    )
    preserve_full_video_for_audio = False
    if media_kind == "video" and not vision_sampling_external:
        preserve_full_video_for_audio = await supports_native_gemini_video_audio(
            host,
            provider=provider,
            umo=umo,
        )
        analysis_trace["native_video_audio_capable"] = preserve_full_video_for_audio

    try:
        if debug_mode:
            logger.info("开始分析当前识屏素材")
            logger.debug(f"System prompt: {system_prompt}")
            logger.debug(f"Media kind: {media_kind}")
            logger.debug(f"Mime type: {mime_type}")
            logger.debug(f"Media size: {len(media_bytes)} bytes")

        effective_media_kind = media_kind
        effective_mime_type = mime_type
        effective_media_bytes = media_bytes
        material_label = "录屏视频" if media_kind == "video" else "截图"
        sampling_profile = host._get_scene_behavior_profile(scene)
        sampled_capture_context = None
        recognition_capture_context = capture_context

        if media_kind == "video":
            sampled_capture_context = await host._build_video_sample_capture_context(
                capture_context,
                scene=scene,
                use_external_vision=vision_sampling_external,
            )
            if sampled_capture_context:
                analysis_trace["sampling_strategy"] = str(
                    sampled_capture_context.get("sampling_strategy", "keyframe_sheet")
                )
                analysis_trace["frame_count"] = int(
                    sampled_capture_context.get("frame_count", 0) or 0
                )
                analysis_trace["frame_labels"] = list(
                    sampled_capture_context.get("frame_labels", []) or []
                )
                analysis_trace["has_live_anchor_frame"] = bool(
                    sampled_capture_context.get("has_live_anchor_frame")
                )
                if host._should_keep_sampled_video_only(
                    scene,
                    use_external_vision=vision_sampling_external,
                    preserve_full_video_for_audio=preserve_full_video_for_audio,
                ):
                    effective_media_kind = str(
                        sampled_capture_context.get("media_kind", "image") or "image"
                    )
                    effective_mime_type = str(
                        sampled_capture_context.get("mime_type", "image/jpeg")
                        or "image/jpeg"
                    )
                    effective_media_bytes = (
                        sampled_capture_context.get("media_bytes", b"") or b""
                    )
                    material_label = "录屏关键帧拼图"
                    analysis_trace["analysis_material_kind"] = effective_media_kind
                    analysis_trace["used_full_video"] = False
                    if vision_sampling_external:
                        recognition_capture_context = sampled_capture_context

        recognition_text = await recognize_screen_material(
            host,
            capture_context=recognition_capture_context,
            scene=scene,
            active_window_title=active_window_title,
            session=session,
        )
        vs_resolved = host._resolve_vision_source()
        if vs_resolved == "仅框架" and not (recognition_text and recognition_text.strip()):
            analysis_trace["status"] = "framework_vision_empty"
            capture_context["_analysis_trace"] = analysis_trace
            return [
                Plain(
                    "无法识别屏幕内容，请检查框架的图片转述模型或当前多模态模型是否可用。"
                )
            ]

        if (
            media_kind == "video"
            and vision_sampling_external
            and sampled_capture_context is not None
            and recognition_capture_context is sampled_capture_context
            and host._looks_uncertain_screen_result(recognition_text)
        ):
            recognition_text = await recognize_screen_material(
                host,
                capture_context=capture_context,
                scene=scene,
                active_window_title=active_window_title,
                session=session,
            )
            analysis_trace["analysis_material_kind"] = "video"
            analysis_trace["used_full_video"] = True
            material_label = "录屏视频"

        effective_use_external_vision = bool(recognition_text and recognition_text.strip())

        if effective_use_external_vision and host._is_screen_error_text(recognition_text):
            logger.warning(
                f"[任务 {task_id}] 外部视觉识别失败，尝试回退到当前 provider 多模态链路: {recognition_text}"
            )
            effective_use_external_vision = False
            recognition_text = ""
            analysis_trace["sampling_strategy"] = (
                f"{analysis_trace['sampling_strategy']}+provider_fallback"
                if analysis_trace["sampling_strategy"]
                else "provider_fallback"
            )
            analysis_trace["analysis_material_kind"] = effective_media_kind

        prompt_parts: list[str] = []
        if effective_use_external_vision:
            prompt_parts.extend(
                [
                    "你是屏幕伴侣，请结合下面的识屏结果与对话上下文，自然地继续陪伴用户。",
                    f"当前场景：{scene}",
                    f"识别结果：{recognition_text or '未获得有效识别结果。'}",
                    "请优先判断用户正在做什么、可能卡在哪一步，以及现在最值得提醒的一条建议。",
                ]
            )
        else:
            prompt_parts.extend(
                [
                    f"你会直接收到一份当前桌面的{material_label}作为多模态输入，请先理解素材内容，再决定如何回复用户。",
                    f"当前场景：{scene}",
                    f"素材类型：{media_kind}",
                    "请只基于当前素材与已有上下文做判断；如果看不清或信息不足，要明确说明不确定。",
                    "请优先关注用户正在做什么、进行到哪一步，以及此刻最值得提醒的一条建议。",
                ]
            )

        if contexts:
            prompt_parts.append("最近对话：\n" + "\n".join(contexts))
            prompt_parts.append(
                "连续性要求：把这条消息视作同一段持续陪伴的延续，优先补充新的变化、判断或下一步；"
                "不要每条都重新用情绪化称呼开场，也不要重复上一条已经说过的提醒。"
            )
        outbound_recent = host._companion_outbound_context_lines(umo)
        if outbound_recent:
            prompt_parts.append(
                "你在本会话中最近几次识屏/陪伴回复摘要（不要复述相同套话与称呼，承接上文任务与语气）：\n"
                + "\n".join(f"- {line}" for line in outbound_recent)
            )
        prompt_parts.append(f"回复节奏：{reply_interval_guidance}")

        related_memories = trigger_related_memories(host, scene, active_window_title)
        analysis_trace["memory_hints"] = related_memories[:4]
        if related_memories:
            memory_lines = "\n".join(f"- {memory}" for memory in related_memories[:3])
            prompt_parts.append("可参考的相关记忆：\n" + memory_lines)

        shared_activities = get_relevant_shared_activities(host, scene, limit=3)
        if shared_activities:
            activity_lines = []
            for activity_name, activity_data in shared_activities:
                category = shared_activity_category_label(
                    activity_data.get("category", "other")
                )
                last_shared = activity_data.get("last_shared", "未知")
                activity_lines.append(f"- {category}: {activity_name}（最近共同提到：{last_shared}）")
            prompt_parts.append("可引用的共同经历：\n" + "\n".join(activity_lines))

        if host.observations:
            observation_lines = []
            for obs in host.observations[-3:][::-1]:
                timestamp = str(obs.get("timestamp", "")).split("T")[-1][:5]
                observation_lines.append(
                    f"- {timestamp} {obs.get('scene', '未知')}: {obs.get('description', '')}"
                )
            if observation_lines:
                prompt_parts.append("最近观察记录：\n" + "\n".join(observation_lines))

        if custom_prompt:
            prompt_parts.append(f"额外要求：{custom_prompt}")
        else:
            if scene_prompt:
                prompt_parts.append(f"场景偏好：{scene_prompt}")
            if time_prompt:
                prompt_parts.append(f"时间提示：{time_prompt}")
            if holiday_prompt:
                prompt_parts.append(f"节日提示：{holiday_prompt}")
            if weather_prompt:
                prompt_parts.append(f"天气提示：{weather_prompt}")
            if system_status_prompt:
                prompt_parts.append(f"系统状态：{system_status_prompt}")
            if not effective_use_external_vision and analysis_trace["trigger_reason"]:
                trigger_reason = analysis_trace["trigger_reason"]
                prompt_parts.append(f"触发背景：{trigger_reason}")
                if "窗口变化" in trigger_reason or "提升" in trigger_reason:
                    prompt_parts.append("场景重点：用户刚切换到新应用或新内容，请先确认当前窗口的实际内容再给出建议。")
                elif "停留较久" in trigger_reason or "低频" in trigger_reason:
                    prompt_parts.append("场景重点：用户可能正处于深度专注状态，建议以轻柔陪伴为主，避免打断。")
                elif "变化不大" in trigger_reason or "降低" in trigger_reason:
                    prompt_parts.append("场景重点：当前画面相对稳定，建议只提供最有价值的1条简短提醒即可。")

        prompt_parts.append(f"语气控制：{sampling_profile['tone_instruction']}")

        if not should_send_rest_reminder:
            prompt_parts.append(
                "如果最近几条消息已经提过休息、熬夜或睡觉，这次不要再重复这些提醒。"
            )

        if should_send_rest_reminder and not custom_prompt:
            prompt_parts.append(
                "用户快到平时休息的时间了。请只在这次回复里顺带轻提醒一次休息，"
                "语气要自然、克制、不要说教，也不要打断当前任务。"
            )
            analysis_trace["rest_reminder_planned"] = True
            capture_context["_rest_reminder_planned"] = True
            capture_context["_rest_reminder_info"] = dict(rest_reminder_info or {})

        prompt_parts.append(
            build_companion_response_guide(
                scene=scene,
                recognition_text=recognition_text,
                custom_prompt=custom_prompt,
                context_count=len(contexts),
            )
        )

        if should_offer_shared_activity_invite(host, scene, custom_prompt):
            prompt_parts.append(
                "如果语气自然，可以轻轻表达你也想和用户一起做点轻松的事，但必须低频、顺势，不能打断正事。"
            )

        if sampling_profile["category"] == "entertainment":
            prompt_parts.append("更偏轻声陪伴和顺势提醒，不要过度推动任务。")
        elif sampling_profile["category"] == "work":
            prompt_parts.append("建议尽量收束成 1 到 2 个具体判断或下一步。")
        else:
            prompt_parts.append("回复尽量简短、具体、贴近当前任务。")

        latest_window_title = host._normalize_window_title(
            capture_context.get("latest_window_title", "")
        )
        clip_window_title = host._normalize_window_title(
            capture_context.get("clip_active_window_title", "")
        )
        if media_kind == "video" and latest_window_title:
            if (
                clip_window_title
                and latest_window_title.casefold() != clip_window_title.casefold()
            ):
                prompt_parts.append(
                    f"时序补充：这段录屏对应的是刚刚过去的一小段画面，"
                    f"更接近当前时刻的活动窗口是《{latest_window_title}》。"
                    "如果录屏尾段和此刻状态略有错位，请优先按更接近当前的线索理解用户现在在做什么。"
                )
            elif analysis_trace.get("has_live_anchor_frame"):
                prompt_parts.append(
                    "时序补充：关键帧拼图最后一张标注为“现在”，是触发分析时刚补抓的当前画面。"
                    "判断用户此刻状态时，请优先参考这张最新画面，再结合前面的录屏变化。"
                )

        if media_kind == "video":
            if effective_media_kind == "video":
                prompt_parts.append(
                    "补充要求：如果视频里有可辨识的系统音频、提示音、语音或音乐，也请结合音频一起判断当前进展。"
                    "如果没有听清、音轨不明显，或模型当前无法可靠利用音频，请直接说明不确定，不要编造音频内容。"
                )
            else:
                prompt_parts.append(
                    "补充要求：当前收到的是录屏关键帧拼图，只能依据画面判断，请不要假设视频中的音频内容。"
                )

        interaction_prompt = "\n\n".join(part for part in prompt_parts if part)
        interaction_prompt = append_privacy_guard_prompt(host, interaction_prompt)

        try:
            interaction_response = await request_screen_interaction(
                host,
                provider=provider,
                use_external_vision=effective_use_external_vision,
                interaction_prompt=interaction_prompt,
                system_prompt=system_prompt,
                media_bytes=effective_media_bytes,
                media_kind=effective_media_kind,
                mime_type=effective_mime_type,
                umo=umo,
            )
        except asyncio.TimeoutError:
            logger.error("LLM 响应超时")
            analysis_trace["status"] = "timeout"
            capture_context["_analysis_trace"] = analysis_trace
            return [Plain("这次识屏响应超时了，请稍后再试。")]

        response_text = "我看过了，但这一轮还没成功生成回复。"
        if (
            interaction_response
            and hasattr(interaction_response, "completion_text")
            and interaction_response.completion_text
        ):
            response_text = interaction_response.completion_text
        elif debug_mode:
            logger.warning("模型返回为空")

        if not effective_use_external_vision:
            recognition_text = host._compress_recognition_text(response_text)

        analysis_trace["recognition_summary"] = host._truncate_preview_text(
            recognition_text or response_text,
            limit=120,
        )
        observation_stored = add_observation(
            host,
            scene,
            recognition_text or response_text,
            active_window_title,
            extra={
                "trigger_reason": analysis_trace["trigger_reason"],
                "material_kind": media_kind,
                "analysis_material_kind": analysis_trace["analysis_material_kind"],
                "sampling_strategy": analysis_trace["sampling_strategy"],
                "frame_count": analysis_trace["frame_count"],
                "frame_labels": analysis_trace["frame_labels"],
                "recognition_summary": analysis_trace["recognition_summary"],
                "used_full_video": analysis_trace["used_full_video"],
            },
        )
        analysis_trace["stored_as_observation"] = observation_stored
        if observation_stored:
            host._update_long_term_memory(
                scene,
                active_window_title,
                1,
                memory_summary=recognition_text or response_text,
                response_preview=response_text,
            )

        host._update_activity(scene, active_window_title)
        response_text = host._polish_response_text(
            response_text,
            scene,
            contexts=contexts,
            allow_rest_hint=bool(analysis_trace.get("rest_reminder_planned")),
            task_id=task_id,
        )
        analysis_trace["reply_preview"] = host._truncate_preview_text(
            response_text,
            limit=140,
        )
        analysis_trace["status"] = "ok"
        capture_context["_analysis_trace"] = analysis_trace
        host._adjust_interaction_frequency(response_text)
        host._record_screen_analysis_result(True)

    except Exception as e:
        logger.error(f"识屏分析失败: {e}")
        error_msg = str(e).lower()
        error_type = "unknown"
        error_text = "这次识屏分析失败了，请稍后再试。"

        if "timeout" in error_msg:
            error_type = "timeout"
            error_text = "这次识屏请求超时了，请稍后再试。"
        elif "api" in error_msg:
            error_type = "api"
            error_text = "外部接口调用失败了，请检查配置或稍后再试。"
        elif "vision" in error_msg or "video" in error_msg:
            error_type = "vision"
            error_text = "当前模型暂时不支持这次多模态识别，请检查视觉配置。"

        analysis_trace["status"] = f"error:{error_type}"
        analysis_trace["reply_preview"] = error_text
        capture_context["_analysis_trace"] = analysis_trace
        host._record_screen_analysis_result(False, error_type=error_type)
        return [Plain(error_text)]

    if media_kind != "image":
        return [Plain(response_text)]

    temp_dir = tempfile.gettempdir()
    temp_file_path = os.path.join(temp_dir, f"screen_shot_{uuid.uuid4()}.jpg")
    with open(temp_file_path, "wb") as f:
        f.write(media_bytes)

    if host.save_local:
        try:
            plugin_name = str(
                getattr(getattr(host, "plugin_config", None), "_plugin_name", "")
                or "astrbot_plugin_screen_companion"
            )
            data_dir = StarTools.get_data_dir(plugin_name=plugin_name)
            data_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = str(data_dir / "screen_shot_latest.jpg")
            shutil.copy2(temp_file_path, screenshot_path)
        except Exception as e:
            logger.error(f"保存最新截图失败: {e}")

    try:
        return [Plain(response_text), Image(file=temp_file_path)]
    finally:
        try:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
        except Exception as e:
            logger.error(f"清理临时截图失败: {e}")
