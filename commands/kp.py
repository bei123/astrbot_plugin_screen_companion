"""`/kp`、`/kpr` 手动识屏命令。"""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import AsyncIterator
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain


async def run_kp(sc: Any, event: AstrMessageEvent) -> AsyncIterator[Any]:
    ok, err_msg = sc._check_screenshot_env()
    if not ok:
        yield event.plain_result(f"无法使用屏幕观察：\n{err_msg}")
        return

    try:
        capture_context = await asyncio.wait_for(
            sc._capture_screenshot_context(), timeout=20.0
        )
        screen_result = await sc._run_screen_assist(
            event,
            task_id="manual",
            custom_prompt="",
            history_user_text="/kp",
            capture_context=capture_context,
        )

        if not screen_result:
            yield event.plain_result("未获取到有效识别结果")
            return

        segments = sc._split_message(screen_result)
        if len(segments) > 1:
            for i in range(len(segments) - 1):
                segment = segments[i]
                if segment.strip():
                    await sc._as_context().send_message(
                        event.unified_msg_origin, MessageChain([Plain(segment)])
                    )
                    await asyncio.sleep(0.5)
            if segments[-1].strip():
                yield event.plain_result(segments[-1])
        else:
            yield event.plain_result(screen_result)

        if sc.debug:
            logger.info("处理完成")
    except asyncio.TimeoutError:
        logger.error("操作超时，请检查网络连接、模型响应速度或系统资源。")
        yield event.plain_result("操作超时，请稍后重试。")
    except Exception as e:
        logger.error(f"发送消息失败: {e}")
        logger.error(traceback.format_exc())
        yield event.plain_result("这次处理失败了，我先缓一口气，你可以再试一次。")


async def run_kpr(sc: Any, event: AstrMessageEvent) -> AsyncIterator[Any]:
    ok, err_msg = sc._check_recording_env()
    if not ok:
        yield event.plain_result(f"无法使用录屏识别：\n{err_msg}")
        return

    try:
        duration = sc._get_recording_duration_seconds()
        capture_timeout = sc._get_capture_context_timeout("video")
        yield event.plain_result(
            f"开始录制最近 {duration} 秒桌面画面了。\n"
            "录制完成后我会继续分析内容，整个过程会比 /kp 慢一些。"
        )
        capture_context = await asyncio.wait_for(
            sc._capture_one_shot_recording_context(duration),
            timeout=capture_timeout,
        )
        yield event.plain_result("录制完成，正在分析画面内容...")

        screen_result = await sc._run_screen_assist(
            event,
            task_id="manual_recording",
            custom_prompt="",
            history_user_text="/kpr",
            capture_context=capture_context,
            analysis_timeout=sc._get_screen_analysis_timeout("video"),
        )

        if not screen_result:
            yield event.plain_result("这次录屏没有拿到有效识别结果，可以稍后再试一次。")
            return

        segments = sc._split_message(screen_result)
        if len(segments) > 1:
            for i in range(len(segments) - 1):
                segment = segments[i]
                if segment.strip():
                    await sc._as_context().send_message(
                        event.unified_msg_origin, MessageChain([Plain(segment)])
                    )
                    await asyncio.sleep(0.5)
            if segments[-1].strip():
                yield event.plain_result(segments[-1])
        else:
            yield event.plain_result(screen_result)

        if sc.debug:
            logger.info("单次录屏指令处理完成")
    except asyncio.TimeoutError:
        logger.error("单次录屏或识别操作超时")
        yield event.plain_result(
            "这次 /kpr 超时了。\n"
            f"当前录屏时长是 {sc._get_recording_duration_seconds()} 秒，"
            "如果这个问题经常出现，建议优先缩短录屏时长或降低帧率后再试。"
        )
    except Exception as e:
        logger.error(f"单次录屏识别失败: {e}")
        logger.error(traceback.format_exc())
        yield event.plain_result("这次录屏识别失败了，你可以稍后再试一次。")
