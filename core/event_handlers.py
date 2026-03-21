"""全消息事件：共同经历学习、自然语言识屏求助。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain


async def run_on_shared_activity_memory(sc: Any, event: AstrMessageEvent) -> None:
    """从用户明确提到的共同经历里学习。"""
    try:
        message_text = str(getattr(event, "message_str", "") or "").strip()
        if not message_text or message_text.startswith("/"):
            return
        sc._remember_recent_user_activity(event)
        sc._learn_shared_activity_from_message(message_text)
    except Exception as e:
        logger.debug(f"记录共同经历失败: {e}")


async def run_natural_language_screen_assist(
    sc: Any, event: AstrMessageEvent
) -> AsyncIterator[Any]:
    """处理自然语言触发的识屏求助。"""
    if not getattr(sc, "enable_natural_language_screen_assist", False):
        return
    try:
        message_text = str(getattr(event, "message_str", "") or "").strip()
        if not message_text or message_text.startswith("/"):
            return

        request_prompt = sc._extract_screen_assist_prompt(message_text)
        if not request_prompt:
            return

        cooldown_key = str(
            getattr(event, "unified_msg_origin", "")
            or getattr(event, "get_sender_id", lambda: "")()
        )
        now_ts = time.time()
        last_trigger = float(
            (getattr(sc, "_screen_assist_cooldowns", {}) or {}).get(cooldown_key, 0.0)
        )
        if now_ts - last_trigger < 20:
            if sc.debug:
                logger.info("自然语言识屏求助命中过冷却时间，跳过触发")
            return

        ok, err_msg = sc._check_env()
        if not ok:
            if sc.debug:
                logger.warning(f"自然语言识屏求助环境检查失败: {err_msg}")
            return
        custom_prompt = (
            "这是用户主动请求你看看当前屏幕并给建议。"
            "请直接回应眼前任务，不要提自动撤回或系统设定。"
        )
        screen_result = await sc._run_screen_assist(
            event,
            task_id="nl_screen_assist",
            custom_prompt=custom_prompt,
            history_user_text=message_text,
        )
        if not screen_result:
            return

        event.stop_event()
        segments = sc._split_message(screen_result)
        for index, segment in enumerate(segments):
            if not segment.strip():
                continue
            if index == len(segments) - 1:
                yield event.plain_result(segment)
            else:
                await sc._as_context().send_message(
                    event.unified_msg_origin, MessageChain([Plain(segment)])
                )
                await asyncio.sleep(0.4)
    except Exception as e:
        logger.error(f"自然语言识屏助手失败: {e}")
