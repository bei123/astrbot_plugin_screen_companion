"""插件宿主上的任务/窗口/识屏等运行时字段惰性初始化。"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from .recording import ensure_recording_runtime_state


def ensure_plugin_runtime_state(host: Any) -> None:
    if not hasattr(host, "auto_tasks") or host.auto_tasks is None:
        host.auto_tasks = {}
    if not hasattr(host, "temporary_tasks") or host.temporary_tasks is None:
        host.temporary_tasks = {}
    if not hasattr(host, "background_tasks") or host.background_tasks is None:
        host.background_tasks = []
    if not hasattr(host, "active_tasks") or host.active_tasks is None:
        host.active_tasks = {}
    if not hasattr(host, "last_task_execution") or host.last_task_execution is None:
        host.last_task_execution = {}
    if not hasattr(host, "task_counter"):
        host.task_counter = 0
    if not hasattr(host, "is_running"):
        host.is_running = False
    if not hasattr(host, "running"):
        host.running = True
    if not hasattr(host, "state"):
        host.state = "inactive"
    if not hasattr(host, "web_server"):
        host.web_server = None
    if not hasattr(host, "task_semaphore") or host.task_semaphore is None:
        host.task_semaphore = asyncio.Semaphore(2)
    if not hasattr(host, "task_queue") or host.task_queue is None:
        host.task_queue = asyncio.Queue()
    if not hasattr(host, "_shutdown_lock") or host._shutdown_lock is None:
        host._shutdown_lock = asyncio.Lock()
    if not hasattr(host, "_webui_lock") or host._webui_lock is None:
        host._webui_lock = asyncio.Lock()
    if not hasattr(host, "_is_stopping"):
        host._is_stopping = False
    if not hasattr(host, "_screen_assist_cooldowns") or host._screen_assist_cooldowns is None:
        host._screen_assist_cooldowns = {}
    if not hasattr(host, "last_shared_activity_invite_time"):
        host.last_shared_activity_invite_time = 0.0
    if not hasattr(host, "previous_windows") or host.previous_windows is None:
        host.previous_windows = set()
    if not hasattr(host, "window_change_cooldown"):
        host.window_change_cooldown = 0
    if not hasattr(host, "window_timestamps") or host.window_timestamps is None:
        host.window_timestamps = {}
    if not hasattr(host, "auto_screen_runtime") or host.auto_screen_runtime is None:
        host.auto_screen_runtime = {}
    if not hasattr(host, "recent_user_activity") or host.recent_user_activity is None:
        host.recent_user_activity = {}
    if not hasattr(host, "screen_analysis_traces") or host.screen_analysis_traces is None:
        host.screen_analysis_traces = []
    if not hasattr(host, "_companion_outbound_by_umo") or host._companion_outbound_by_umo is None:
        host._companion_outbound_by_umo = {}
    if not hasattr(host, "recent_chat_context_messages"):
        host.recent_chat_context_messages = 15
    if not hasattr(host, "companion_outbound_memory_max"):
        host.companion_outbound_memory_max = 8
    if not hasattr(host, "companion_outbound_snippet_chars"):
        host.companion_outbound_snippet_chars = 220
    if not hasattr(host, "_instance_token"):
        host._instance_token = ""
    if not hasattr(host, "_screen_analysis_failure_count"):
        host._screen_analysis_failure_count = 0
    if not hasattr(host, "_screen_analysis_backoff_until"):
        host._screen_analysis_backoff_until = 0.0
    if not hasattr(host, "window_companion_active_title"):
        host.window_companion_active_title = ""
    if not hasattr(host, "window_companion_active_target"):
        host.window_companion_active_target = ""
    if not hasattr(host, "window_companion_active_rule") or host.window_companion_active_rule is None:
        host.window_companion_active_rule = {}
    if not hasattr(host, "last_rest_reminder_time"):
        host.last_rest_reminder_time = None
    if not hasattr(host, "last_rest_reminder_day"):
        host.last_rest_reminder_day = ""
    if not hasattr(host, "rest_reminder_state_file"):
        host.rest_reminder_state_file = os.path.join(
            host.learning_storage,
            "rest_reminder_state.json",
        )
    ensure_recording_runtime_state(host)
