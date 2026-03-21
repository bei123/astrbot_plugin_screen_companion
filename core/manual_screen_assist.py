"""手动识屏管线：采集上下文、分析、写入会话与短期记忆。"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Plain


async def run_manual_screen_assist(
    host: Any,
    event: AstrMessageEvent,
    *,
    task_id: str = "manual",
    custom_prompt: str = "",
    history_user_text: str = "/kp",
    capture_context: dict[str, Any] | None = None,
    capture_timeout: float | None = None,
    analysis_timeout: float | None = None,
) -> str | None:
    """截图/录屏素材就绪后的识屏、落库与记忆收尾（供 /kp、/kpr、自然语言求助等复用）。"""
    debug_mode = bool(getattr(host, "debug", False))
    if debug_mode:
        logger.info(f"[Task {task_id}] status update")

    if capture_context is None:
        effective_capture_timeout = (
            float(capture_timeout)
            if capture_timeout is not None
            else host._get_capture_context_timeout()
        )
        capture_context = await asyncio.wait_for(
            host._capture_recognition_context(),
            timeout=effective_capture_timeout,
        )
    if not isinstance(capture_context, dict):
        if debug_mode:
            logger.warning(f"[{task_id}] 未获取到识屏上下文")
        return None
    capture_context.setdefault(
        "trigger_reason",
        "用户手动发起识屏请求"
        if task_id.startswith("manual") or task_id in {"manual", "manual_recording"}
        else f"任务 {task_id} 发起识屏",
    )
    media_bytes = capture_context["media_bytes"]
    media_kind = str(capture_context.get("media_kind", "image") or "image")
    active_window_title = capture_context.get("active_window_title", "")
    if debug_mode:
        logger.info(
            f"[{task_id}] 识屏素材已准备，模式: {media_kind}, 大小: {len(media_bytes)} bytes, 活动窗口: {active_window_title}"
        )

    effective_analysis_timeout = (
        float(analysis_timeout)
        if analysis_timeout is not None
        else host._get_screen_analysis_timeout(media_kind)
    )
    components = await asyncio.wait_for(
        host._analyze_screen(
            capture_context,
            session=event,
            active_window_title=active_window_title,
            custom_prompt=custom_prompt,
            task_id=task_id,
        ),
        timeout=effective_analysis_timeout,
    )
    if debug_mode:
        logger.info(f"[{task_id}] 分析完成，组件数量: {len(components)}")

    if not components or not isinstance(components[0], Plain):
        if debug_mode:
            logger.warning(f"[{task_id}] 未获取到有效识别结果")
        return None

    screen_result = components[0].text
    if debug_mode:
        logger.info(f"[{task_id}] 屏幕识别结果: {screen_result}")

    try:
        from astrbot.core.agent.message import (
            AssistantMessageSegment,
            TextPart,
            UserMessageSegment,
        )

        ctx = host._as_context()
        conv_mgr = ctx.conversation_manager
        uid = event.unified_msg_origin
        curr_cid = await conv_mgr.get_curr_conversation_id(uid)

        if curr_cid:
            user_msg = UserMessageSegment(
                role="user",
                content=[TextPart(text=str(history_user_text or "/kp"))],
            )
            assistant_msg = AssistantMessageSegment(
                role="assistant",
                content=[TextPart(text=screen_result)],
            )
            await conv_mgr.add_message_pair(
                cid=curr_cid,
                user_message=user_msg,
                assistant_message=assistant_msg,
            )
            if debug_mode:
                logger.info(f"[Task {task_id}] status update")
    except Exception as e:
        if debug_mode:
            logger.debug(f"[{task_id}] 添加对话历史失败: {e}")

    host._remember_companion_outbound_for_umo(
        getattr(event, "unified_msg_origin", None), screen_result
    )
    host._remember_screen_analysis_trace(capture_context.get("_analysis_trace"))
    return screen_result
