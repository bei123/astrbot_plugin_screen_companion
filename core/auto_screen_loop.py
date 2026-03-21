"""自动观察主循环：定时等待、变化感知、触发判定与识屏发送。"""

from __future__ import annotations

import asyncio
import traceback
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .proactive_messaging import VirtualProactiveEvent

AutoScreenSessionEvent = AstrMessageEvent | VirtualProactiveEvent


async def run_auto_screen_loop(
    sc: Any,
    event: AutoScreenSessionEvent,
    task_id: str = "default",
    custom_prompt: str = "",
    interval: int | None = None,
) -> None:
    """后台自动截图分析任务（原 ScreenCompanion._auto_screen_task）。"""
    sc._ensure_runtime_state()
    logger.info(f"[任务 {task_id}] 启动自动识屏任务")

    try:
        while sc.is_running and sc.state == "active" and sc._is_current_process_instance():
            if not sc._is_in_active_time_range():
                logger.info(f"[任务 {task_id}] 当前不在活跃时间段，准备停止任务")
                if task_id in sc.auto_tasks:
                    del sc.auto_tasks[task_id]
                if not sc.auto_tasks:
                    sc.is_running = False
                    sc.state = "inactive"
                break

            current_check_interval, current_trigger_probability = sc._get_current_preset_params()

            check_interval = current_check_interval
            probability = current_trigger_probability

            if interval is not None:
                check_interval = interval
                logger.info(f"[任务 {task_id}] 使用自定义检查间隔: {check_interval} 秒")
            else:
                logger.info(f"[任务 {task_id}] 使用当前预设间隔: {check_interval} 秒")

            logger.info(f"[任务 {task_id}] 等待 {check_interval} 秒后进入触发判定")
            elapsed = 0
            window_changed = False
            latest_new_windows: list[str] = []
            while elapsed < check_interval:
                if not sc.is_running or sc.state != "active":
                    logger.info(f"[任务 {task_id}] 任务状态已变化，停止等待")
                    break
                try:
                    if elapsed % 3 == 0:
                        latest_window_changed, new_windows = sc._detect_window_changes()
                        if latest_window_changed:
                            window_changed = True
                            latest_new_windows = list(new_windows or [])
                        if latest_window_changed and new_windows:
                            logger.info(f"[任务 {task_id}] 检测到新打开的窗口: {new_windows}")

                    if elapsed > 0 and elapsed % 10 == 0 and interval is None:
                        new_check_interval, new_probability = sc._get_current_preset_params()
                        if new_check_interval != check_interval:
                            check_interval = new_check_interval
                            logger.info(
                                f"[Task {task_id}] preset interval updated to {check_interval} seconds"
                            )
                            if elapsed >= check_interval:
                                logger.info(
                                    f"[Task {task_id}] new interval is now active; triggering early"
                                )
                                break
                        if new_probability != probability:
                            probability = new_probability
                            logger.info(
                                f"[任务 {task_id}] 预设参数已更新，触发概率变为 {probability}%"
                            )
                    await asyncio.sleep(1)
                    elapsed += 1
                except asyncio.CancelledError:
                    logger.info(f"[任务 {task_id}] 等待期间收到取消信号")
                    raise

            if not sc.is_running or sc.state != "active":
                logger.info(f"[任务 {task_id}] 任务状态已变化，结束本轮")
                break

            if not sc._is_in_active_time_range():
                logger.info(f"[任务 {task_id}] 已离开活跃时间段，停止任务")
                if task_id in sc.auto_tasks:
                    del sc.auto_tasks[task_id]
                if not sc.auto_tasks:
                    sc.is_running = False
                    sc.state = "inactive"
                break

            if not sc.is_running or sc.state != "active":
                logger.info(f"[任务 {task_id}] 任务状态已变化，结束本轮")
                break

            system_high_load = False
            try:
                import psutil

                cpu_percent = psutil.cpu_percent(interval=1)
                memory = psutil.virtual_memory()
                memory_percent = memory.percent

                if cpu_percent > 80 or memory_percent > 80:
                    system_high_load = True
                    logger.info(
                        f"[任务 {task_id}] 系统资源占用较高: CPU={cpu_percent}%, 内存={memory_percent}%"
                    )
            except ImportError:
                logger.debug(f"[任务 {task_id}] 未安装 psutil，跳过系统负载检测")
            except Exception as e:
                logger.debug(f"[任务 {task_id}] 系统状态检测失败: {e}")

            change_snapshot = sc._build_auto_screen_change_snapshot(
                task_id,
                window_changed=window_changed,
                new_windows=latest_new_windows,
            )
            logger.info(
                f"[任务 {task_id}] 变化感知: changed={change_snapshot['changed']}, "
                f"window={change_snapshot['active_window_title'] or '未知'}, "
                f"reason={change_snapshot['reason'] or '无明显变化'}"
            )
            decision = sc._decide_auto_screen_trigger(
                task_id,
                probability=probability,
                check_interval=check_interval,
                system_high_load=system_high_load,
                change_snapshot=change_snapshot,
            )
            trigger = bool(decision["trigger"])
            if decision["random_number"] is None:
                logger.info(f"[任务 {task_id}] {decision['reason']}")
            else:
                logger.info(
                    f"[任务 {task_id}] {decision['reason']}，随机数={decision['random_number']}，"
                    f"生效概率={decision['effective_probability']}%"
                )

            if not sc.is_running or sc.state != "active":
                logger.info(f"[任务 {task_id}] 任务状态已变化，结束本轮")
                break

            if not sc.is_running or sc.state != "active":
                logger.info(f"[任务 {task_id}] 任务状态已变化，结束本轮")
                break

            if trigger:
                logger.info(f"[任务 {task_id}] 满足触发条件，准备执行识屏分析")
                try:
                    should_defer, defer_reason = sc._should_defer_for_recent_user_activity(
                        event,
                        task_id=task_id,
                        change_snapshot=change_snapshot,
                    )
                    if should_defer:
                        logger.info(f"[任务 {task_id}] 主动识屏暂缓: {defer_reason}")
                        continue

                    if not sc.is_running or sc.state != "active":
                        logger.info(f"[任务 {task_id}] 任务停止标志已设置，取消本次屏幕分析")
                        break

                    if not sc._is_in_active_time_range():
                        logger.info(f"[Task {task_id}] outside active time range, stopping task")
                        if task_id in sc.auto_tasks:
                            del sc.auto_tasks[task_id]
                        if not sc.auto_tasks:
                            sc.is_running = False
                        break

                    if not sc.is_running or sc.state != "active":
                        logger.info(f"[Task {task_id}] stop flag detected, cancelling screen analysis")
                        break

                    capture_timeout = sc._get_capture_context_timeout(
                        "video" if sc._use_screen_recording_mode() else "image"
                    )
                    capture_context = await asyncio.wait_for(
                        sc._capture_proactive_recognition_context(),
                        timeout=capture_timeout,
                    )
                    capture_context["trigger_reason"] = decision["reason"]
                    active_window_title = capture_context.get("active_window_title", "")

                    if not sc.is_running or sc.state != "active":
                        logger.info(f"[任务 {task_id}] 任务运行状态被取消，取消屏幕分析")
                        break

                    components = await asyncio.wait_for(
                        sc._analyze_screen(
                            capture_context,
                            session=event,
                            active_window_title=active_window_title,
                            custom_prompt=custom_prompt,
                            task_id=task_id,
                        ),
                        timeout=sc._get_screen_analysis_timeout(
                            capture_context.get("media_kind", "image")
                        ),
                    )

                    if not sc.is_running or sc.state != "active":
                        logger.info(f"[Task {task_id}] stop flag detected, canceling proactive send")
                        break

                    chain = sc._build_message_chain(components)
                    target = sc._resolve_proactive_target(event)
                    text_content = sc._extract_plain_text(components)
                    analysis_trace = capture_context.get("_analysis_trace", {}) or {}
                    current_scene = str(
                        analysis_trace.get("scene") or change_snapshot.get("scene") or ""
                    ).strip()
                    skip_similar, skip_reason = sc._should_skip_similar_auto_reply(
                        task_id,
                        active_window_title=active_window_title,
                        text_content=text_content,
                        check_interval=check_interval,
                    )

                    if skip_similar:
                        logger.info(f"[任务 {task_id}] 主动回复已跳过: {skip_reason}")
                        sc._remember_auto_reply_state(
                            task_id,
                            active_window_title=active_window_title,
                            text_content=text_content,
                            sent=False,
                            scene=current_scene,
                            note=skip_reason,
                        )
                        analysis_trace["status"] = "skipped_similar"
                        analysis_trace["reply_preview"] = sc._truncate_preview_text(
                            text_content,
                            limit=140,
                        )
                        sc._remember_screen_analysis_trace(analysis_trace)
                        continue

                    skip_window_limit, window_limit_reason = sc._should_skip_same_window_followup(
                        task_id,
                        active_window_title=active_window_title,
                        scene=current_scene,
                    )
                    if skip_window_limit:
                        logger.info(f"[任务 {task_id}] 主动回复已降频: {window_limit_reason}")
                        sc._remember_auto_reply_state(
                            task_id,
                            active_window_title=active_window_title,
                            text_content=text_content,
                            sent=False,
                            scene=current_scene,
                            note=window_limit_reason,
                        )
                        analysis_trace["status"] = "skipped_window_cooldown"
                        analysis_trace["reply_preview"] = sc._truncate_preview_text(
                            text_content,
                            limit=140,
                        )
                        sc._remember_screen_analysis_trace(analysis_trace)
                        continue

                    diary_stored = sc._add_diary_entry(text_content, active_window_title)
                    analysis_trace["stored_in_diary"] = bool(diary_stored)

                    if text_content:
                        logger.info(f"准备发送主动消息，目标: {target}, 文本内容: {text_content}")
                        sent = await sc._send_segmented_text(
                            target,
                            text_content,
                            should_continue=lambda: sc.is_running,
                        )
                        sc._remember_auto_reply_state(
                            task_id,
                            active_window_title=active_window_title,
                            text_content=text_content,
                            sent=sent,
                            scene=current_scene,
                        )
                        if sent and capture_context.get("_rest_reminder_planned"):
                            sc._mark_rest_reminder_sent(
                                capture_context.get("_rest_reminder_info", {}) or {}
                            )
                        if sent and text_content:
                            sc._remember_companion_outbound_for_umo(
                                getattr(event, "unified_msg_origin", None),
                                text_content,
                            )
                    else:
                        sent = False
                        if sc.is_running:
                            sent = await sc._send_proactive_message(target, chain)
                        sc._remember_auto_reply_state(
                            task_id,
                            active_window_title=active_window_title,
                            text_content="[非纯文本回复]",
                            sent=sent,
                            scene=current_scene,
                        )
                        if sent and capture_context.get("_rest_reminder_planned"):
                            sc._mark_rest_reminder_sent(
                                capture_context.get("_rest_reminder_info", {}) or {}
                            )
                        if sent:
                            _plain = sc._extract_plain_text(components)
                            if str(_plain or "").strip():
                                sc._remember_companion_outbound_for_umo(
                                    getattr(event, "unified_msg_origin", None),
                                    _plain,
                                )
                    analysis_trace["reply_preview"] = sc._truncate_preview_text(
                        text_content or "[非纯文本回复]",
                        limit=140,
                    )
                    analysis_trace["status"] = "sent" if sent else "not_sent"
                    sc._remember_screen_analysis_trace(analysis_trace)

                    try:
                        from astrbot.core.agent.message import (
                            AssistantMessageSegment,
                            TextPart,
                            UserMessageSegment,
                        )

                        ctx = sc._as_context()
                        conv_mgr = ctx.conversation_manager
                        uid = event.unified_msg_origin
                        curr_cid = await conv_mgr.get_curr_conversation_id(uid)

                        if curr_cid:
                            user_msg = UserMessageSegment(
                                role="user",
                                content=[TextPart(text="[主动识屏触发]")],
                            )
                            assistant_msg = AssistantMessageSegment(
                                role="assistant",
                                content=[TextPart(text=text_content)],
                            )

                            await conv_mgr.add_message_pair(
                                cid=curr_cid,
                                user_message=user_msg,
                                assistant_message=assistant_msg,
                            )
                            logger.info("已写入一条主动消息到会话历史")
                    except Exception as e:
                        logger.debug(f"添加对话历史失败: {e}")
                except asyncio.TimeoutError:
                    logger.error("自动识屏任务超时，请检查系统资源和网络连接")
                except Exception as e:
                    logger.error(f"自动观察任务执行失败: {e}")
                    logger.error(traceback.format_exc())
    except asyncio.CancelledError:
        logger.info(f"任务 {task_id} 已被取消")
    except Exception as e:
        logger.error(f"任务 {task_id} 异常: {e}")
    finally:
        if task_id in sc.auto_tasks:
            del sc.auto_tasks[task_id]
            logger.info(f"[任务 {task_id}] 已从自动任务列表移除")
        if not sc.auto_tasks:
            sc.is_running = False
            logger.info("所有自动观察任务已结束")
        logger.info(f"任务 {task_id} 已结束")
