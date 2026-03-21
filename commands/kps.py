"""`/kps` 切换自动观察启停。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core.persona import get_end_response, get_start_response


async def run_kps(sc: Any, event: AstrMessageEvent) -> AsyncIterator[Any]:
    sc._ensure_runtime_state()
    if sc.state == "active":
        sc.state = "inactive"
        sc.is_running = False
        logger.info("正在停止所有自动观察任务...")

        tasks_to_cancel = list(sc.auto_tasks.items())
        for task_id, task in tasks_to_cancel:
            logger.info(f"取消任务 {task_id}")
            task.cancel()

        for task_id, task in tasks_to_cancel:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"等待任务 {task_id} 停止超时")
            except asyncio.CancelledError:
                logger.info(f"[Task {task_id}] status update")
            except Exception as e:
                logger.error(f"等待任务 {task_id} 停止时出错: {e}")

        sc.auto_tasks.clear()
        logger.info("所有自动观察任务已停止")
        end_response = await get_end_response(sc, event.unified_msg_origin)
        yield event.plain_result(end_response)
    else:
        if not sc.enabled:
            yield event.plain_result(
                "插件当前未启用，请先在配置中开启后再启动自动观察。"
            )
            return

        ok, err_msg = sc._check_env(check_mic=False)
        if not ok:
            yield event.plain_result(f"启动失败：\n{err_msg}")
            return

        if sc.AUTO_TASK_ID in sc.auto_tasks or sc.is_running:
            logger.info("自动观察任务已存在，无需重复启动")
            yield event.plain_result("自动观察任务已在运行中")
            return

        sc.state = "active"
        sc.is_running = True
        logger.info(f"启动任务 {sc.AUTO_TASK_ID}")
        sc.auto_tasks[sc.AUTO_TASK_ID] = asyncio.create_task(
            sc._auto_screen_task(event, task_id=sc.AUTO_TASK_ID)
        )
        start_response = await get_start_response(sc, event.unified_msg_origin)
        yield event.plain_result(start_response)
