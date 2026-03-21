"""按配置时刻触发的自定义监控任务调度循环。"""

from __future__ import annotations

import asyncio
import datetime
import time
from typing import Any

from astrbot.api import logger


async def run_custom_tasks_scheduler(sc: Any) -> None:
    sc._ensure_runtime_state()
    while sc.running and sc._is_current_process_instance():
        try:
            now = datetime.datetime.now()
            current_date = now.date()
            current_hour = now.hour
            current_minute = now.minute

            for task in sc.parsed_custom_tasks:
                task_key = f"{task['hour']}:{task['minute']}:{task['prompt']}"
                if sc.last_task_execution.get(task_key) == current_date:
                    continue
                if task["hour"] != current_hour or task["minute"] != current_minute:
                    continue
                if not sc._try_mark_custom_task_dispatch(task_key):
                    logger.info(f"跳过重复的自定义监控任务派发: {task['prompt']}")
                    sc.last_task_execution[task_key] = current_date
                    continue

                if task["hour"] == current_hour and task["minute"] == current_minute:
                    logger.info(f"执行自定义监控任务: {task['prompt']}")
                    sc.last_task_execution[task_key] = current_date
                    ok, err_msg = sc._check_env()
                    if not ok:
                        logger.error(f"自定义任务执行失败: {err_msg}")
                        continue

                    try:
                        current_state = sc.state
                        if current_state == "inactive":
                            sc.state = "temporary"

                        temp_task_id = f"temp_custom_{int(time.time())}"

                        async def temp_custom_task(
                            *,
                            _task=task,
                            _temp_id=temp_task_id,
                            _saved_state=current_state,
                        ):
                            background_job_started = False
                            try:
                                background_job_started, skip_reason = sc._try_begin_background_screen_job()
                                if not background_job_started:
                                    logger.info(f"[{_temp_id}] 跳过自定义监控识屏: {skip_reason}")
                                    return
                                capture_timeout = sc._get_capture_context_timeout(
                                    "video" if sc._use_screen_recording_mode() else "image"
                                )
                                capture_context = await asyncio.wait_for(
                                    sc._capture_proactive_recognition_context(),
                                    timeout=capture_timeout,
                                )
                                capture_context["trigger_reason"] = f"定时提醒：{_task['prompt']}"
                                active_window_title = capture_context.get("active_window_title", "")
                                target = sc._resolve_proactive_target()
                                proactive_event = sc._create_virtual_event(target) if target else None
                                components = await asyncio.wait_for(
                                    sc._analyze_screen(
                                        capture_context,
                                        session=proactive_event,
                                        active_window_title=active_window_title,
                                        custom_prompt=_task["prompt"],
                                        task_id=_temp_id,
                                    ),
                                    timeout=sc._get_screen_analysis_timeout(
                                        capture_context.get("media_kind", "image")
                                    ),
                                )

                                analysis_trace = capture_context.get("_analysis_trace", {}) or {}

                                if target and await sc._send_component_text(
                                    target,
                                    components,
                                    prefix="【定时提醒】",
                                ):
                                    analysis_trace["status"] = "sent"
                                    analysis_trace["reply_preview"] = sc._truncate_preview_text(
                                        sc._extract_plain_text(components),
                                        limit=140,
                                    )
                                    sc._remember_screen_analysis_trace(analysis_trace)
                                    logger.info("自定义任务提醒消息发送成功")
                                    _cust_plain = sc._extract_plain_text(components)
                                    if str(_cust_plain or "").strip():
                                        sc._remember_companion_outbound_for_umo(
                                            getattr(
                                                proactive_event,
                                                "unified_msg_origin",
                                                None,
                                            ),
                                            _cust_plain,
                                        )
                                    if capture_context.get("_rest_reminder_planned"):
                                        sc._mark_rest_reminder_sent(
                                            capture_context.get("_rest_reminder_info", {}) or {}
                                        )
                            finally:
                                if _temp_id in sc.temporary_tasks:
                                    del sc.temporary_tasks[_temp_id]
                                if background_job_started:
                                    sc._finish_background_screen_job()
                                if not sc.auto_tasks and not sc.temporary_tasks:
                                    sc.state = _saved_state

                        sc.temporary_tasks[temp_task_id] = asyncio.create_task(
                            temp_custom_task()
                        )
                        logger.info(f"已创建自定义临时任务: {temp_task_id}")
                    except Exception as e:
                        logger.error(f"创建自定义临时任务时出错: {e}")
                        if not sc.auto_tasks and not sc.temporary_tasks:
                            sc.state = current_state

            for _ in range(60):
                if not sc.running:
                    break
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"自定义任务异常: {e}")
            for _ in range(60):
                if not sc.running:
                    break
                await asyncio.sleep(1)
