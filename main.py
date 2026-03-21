import asyncio
from collections import deque
import base64
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .web_server import WebServer

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.event.filter import PermissionType, permission_type
from astrbot.api.message_components import BaseMessageComponent, Image, Plain
from astrbot.api.star import Context, Star, StarTools

from .core.config import InteractionMode, PluginConfig
from .core.capture import capture_screen_bytes
from .core.recording import (
    build_recording_video_args,
    build_video_sample_capture_context,
    cleanup_recording_cache,
    detect_system_audio_device,
    ensure_recording_ready,
    ensure_recording_runtime_state,
    extract_video_sample_sheet_sync,
    get_ffmpeg_path,
    get_ffmpeg_storage_dir,
    get_recording_cache_dir,
    get_recording_duration_seconds,
    get_recording_fps,
    get_recording_video_encoder,
    get_video_sampling_plan,
    record_screen_clip_sync,
    should_keep_sampled_video_only,
    start_screen_recording_sync,
    stop_recording_if_running,
    stop_screen_recording_sync,
)
from .core.scene_from_window import classify_browser_content, identify_scene
from .core.screen_relay import capture_screen_bytes_remote, run_screen_relay_server

from .core.diary import (
    build_diary_document,
    build_diary_reflection_fallback,
    build_diary_reflection_prompt,
    build_diary_structured_summary,
    build_diary_summary_markdown,
    compact_diary_entries,
    ensure_diary_reflection_text,
    extract_actionable_suggestions,
    extract_diary_preview_text,
    get_diary_summary_path,
    load_diary_metadata,
    load_diary_structured_summary,
    parse_clock_to_minutes,
    remember_diary_summary_memories,
    render_diary_message_to_png,
    resolve_diary_target_date,
    save_diary_metadata,
    save_diary_structured_summary,
    sanitize_diary_section_text,
    should_store_diary_entry,
    update_diary_view_status,
)
from .commands.kp import run_kp, run_kpr
from .commands.kps import run_kps
from .commands.kpi import (
    run_kpi_add,
    run_kpi_cd,
    run_kpi_correct,
    run_kpi_d,
    run_kpi_debug,
    run_kpi_ffmpeg,
    run_kpi_list,
    run_kpi_p,
    run_kpi_preference,
    run_kpi_recent,
    run_kpi_start,
    run_kpi_status,
    run_kpi_stop,
    run_kpi_webui,
    run_kpi_y,
    run_kpi_ys,
)
from .core.diary_runtime import (
    add_diary_entry,
    generate_diary,
    run_diary_scheduler,
)
from .core.screen_vision import (
    build_vision_prompt,
    extract_screen_assist_prompt,
    recognize_screen_material,
)
from .core.manual_screen_assist import run_manual_screen_assist
from .core.observations_store import (
    add_observation,
    load_observations,
    save_observations,
    should_store_observation,
)
from .core.activity_tracking import update_activity
from .core.learning_preferences import (
    load_corrections,
    load_learning_data,
    parse_user_preferences,
)
from .core.plugin_config_sync import sync_all_config
from .core.response_polish import polish_response_text, strip_repeated_companion_opening
from .core.runtime_state import ensure_plugin_runtime_state
from .core.shared_activity_heuristics import extract_shared_activity_from_message
from .core.mic_monitor import (
    ensure_mic_monitor_background_task,
    get_microphone_volume,
    mic_monitor_task,
    resolve_microphone_input_device,
    stop_mic_monitor_background_task,
)
from .core.event_handlers import (
    run_natural_language_screen_assist,
    run_on_shared_activity_memory,
)
from .core.webui_lifecycle import (
    ensure_webui_password,
    is_webui_runtime_changed,
    normalize_webui_port,
    restart_webui,
    snapshot_webui_runtime,
    start_webui,
    stop_webui,
)
from .core.screen_analysis import (
    analyze_screen,
    collect_recent_conversation_context,
    gather_screen_analysis_context,
    request_screen_interaction,
)
from .core.auto_screen_loop import AutoScreenSessionEvent, run_auto_screen_loop
from .core.capture_pipeline import (
    capture_latest_screen_anchor,
    capture_one_shot_recording_context,
    capture_proactive_recognition_context,
    capture_recognition_context,
    capture_recording_context,
    capture_screenshot_context,
    check_dependencies,
    check_env,
    check_recording_env,
    check_screenshot_env,
)
from .core.custom_tasks_scheduler import run_custom_tasks_scheduler
from .core.auto_screen_trigger import (
    build_auto_screen_change_snapshot,
    build_reply_interval_guidance,
    contains_rest_cue,
    decide_auto_screen_trigger,
    detect_window_changes,
    ensure_auto_screen_runtime_state,
    format_reply_interval_text,
    get_scene_behavior_profile,
    has_recent_rest_cue,
    is_idle_keepalive_due,
    remember_auto_reply_state,
    should_defer_for_recent_user_activity,
    should_skip_same_window_followup,
    should_skip_similar_auto_reply,
    strip_rest_cue_sentences,
)
from .core.proactive_messaging import (
    VirtualProactiveEvent,
    build_message_chain,
    build_private_target,
    create_virtual_event,
    extract_plain_text,
    get_available_platforms,
    get_default_target,
    get_preferred_platform_id,
    normalize_target,
    resolve_proactive_target,
    send_component_text,
    send_plain_message,
    send_proactive_message,
    send_segmented_text,
)
from .core.window_companion import (
    build_window_companion_prompt,
    is_window_companion_session_active,
    list_open_window_titles,
    match_window_companion_target,
    parse_window_companion_targets,
    run_window_companion_task,
    start_window_companion_session,
    stop_window_companion_session,
)
from .core.long_term_memory import (
    apply_memory_decay,
    build_memory_associations,
    build_memory_priority_value,
    clean_long_term_memory_noise,
    ensure_long_term_memory_defaults,
    extract_memory_focus as ltm_extract_memory_focus,
    is_continuing_memory_context,
    load_long_term_memory,
    remember_episodic_memory,
    remember_focus_pattern,
    save_long_term_memory,
    update_long_term_memory,
    update_memory_priorities,
)
from .core.memory_heuristics import (
    is_low_value_record_text,
    is_screen_error_text,
    is_similar_record,
)
from .core.text_normalization import (
    compress_recognition_text,
    limit_ranked_dict_items,
    normalize_record_text,
    normalize_scene_label,
    normalize_shared_activity_summary,
    normalize_window_title,
    truncate_preview_text,
)

_PROCESS_GUARD_LOCK = threading.Lock()
_PROCESS_GUARDS: dict[str, float] = {}
_ACTIVE_INSTANCE_TOKEN = ""


class ScreenCompanion(Star):
    LEGACY_DEFAULT_CUSTOM_TASK = "02:00 根据用户行为催促其尽快休息"
    DEFAULT_WEBUI_PORT = 6314
    SCREENSHOT_MODE = "screenshot"
    RECORDING_MODE = "recording"
    REST_ACTIVITY_WINDOW_START_HOUR = 20
    REST_REMINDER_CUTOFF_HOUR = 4
    REST_REMINDER_ADVANCE_MINUTES = 20
    REST_REMINDER_LATEST_AFTER_MINUTES = 30
    REST_INFERENCE_LOOKBACK_DAYS = 10
    REST_INFERENCE_MIN_SAMPLES = 1
    RECORDING_FPS = 1.0
    RECORDING_DURATION_SECONDS = 10
    CHANGE_AWARE_IDLE_KEEPALIVE_SECONDS = 15 * 60
    CHANGE_AWARE_SIMILAR_REPLY_COOLDOWN_SECONDS = 8 * 60
    USER_ACTIVITY_GRACE_SECONDS = 45
    USER_ACTIVITY_CHANGE_GRACE_SECONDS = 15
    WORK_WINDOW_MESSAGE_COOLDOWN_SECONDS = 150
    GENERAL_WINDOW_MESSAGE_COOLDOWN_SECONDS = 240
    ENTERTAINMENT_WINDOW_MESSAGE_COOLDOWN_SECONDS = 360
    REST_CUE_REPLY_COOLDOWN_SECONDS = 90 * 60
    CUSTOM_TASK_PROCESS_DEDUP_SECONDS = 90
    BACKGROUND_SCREEN_GUARD_STALE_SECONDS = 5 * 60
    SCREEN_ANALYSIS_FAILURE_BACKOFF_BASE_SECONDS = 30
    SCREEN_ANALYSIS_FAILURE_BACKOFF_MAX_SECONDS = 5 * 60
    SCREEN_TRACE_LIMIT = 40
    SHORT_EMOTION_MEMORY_TTL_SECONDS = 18 * 60
    SHORT_EMOTION_MEMORY_TURNS = 3
    REPLY_VARIATION_TTL_SECONDS = 2 * 60 * 60
    REPLY_VARIATION_HISTORY_LIMIT = 8
    START_END_CONTEXT_LOOKBACK = 2
    SENSITIVE_SKIP_NOTE = "检测到敏感界面，已跳过回复"
    LONG_TERM_MEMORY_RETENTION_DAYS = 45
    LIGHT_MEMORY_RETENTION_DAYS = 90
    EPISODIC_MEMORY_LIMIT = 120
    FOCUS_PATTERN_LIMIT = 80
    ACTIVITY_HISTORY_LIMIT = 1000
    ACTIVITY_MIN_DURATION_SECONDS = 15
    LIVE_ACTIVITY_MIN_DURATION_SECONDS = 5
    GEMINI_API_BASE = "https://generativelanguage.googleapis.com"
    GEMINI_FILE_POLL_TIMEOUT_SECONDS = 120
    GEMINI_FILE_POLL_INTERVAL_SECONDS = 2

    web_server: "WebServer | None"
    # 由 sync_all_config（core/plugin_config_sync）写入；此处声明供静态检查识别
    bot_name: str
    enabled: bool
    interaction_mode: InteractionMode
    check_interval: int
    trigger_probability: int
    active_time_range: str
    diary_storage: str
    learning_storage: str
    observation_storage: str
    enable_learning: bool
    enable_diary: bool
    webui_enabled: bool
    weather_api_key: str
    weather_city: str
    weather_lat: float
    weather_lon: float
    companion_outbound_snippet_chars: int
    companion_outbound_memory_max: int

    def __init__(self, context: Context, config: dict):
        import os

        super().__init__(context)
        
        self.plugin_config = PluginConfig(config, context)
        
        self._sync_all_config()
        self._instance_token = ""
        self._register_process_instance()
        self._cleanup_legacy_default_custom_tasks()
        
        self.auto_tasks = {}
        self.is_running = False
        self.task_counter = 0
        self.running = True
        self.background_tasks = []
        self._latest_remote_image_bytes: bytes | None = None
        self._latest_remote_window_title = ""
        self._latest_remote_mic_level = 0
        self._remote_image_event = asyncio.Event()
        self._remote_relay_server = None
        self._remote_relay_task: asyncio.Task | None = None
        self._screen_recording_lock = asyncio.Lock()
        self._screen_recording_process = None
        self._screen_recording_path = ""
        self._recording_audio_device = None
        self._recording_ffmpeg_path = None
        self._recording_video_encoder = None
        self._recording_video_encoder_source = ""
        self._mic_monitor_background_task = None
        self._mic_input_device_index = None
        self._mic_input_device_name = ""
        self.state = "inactive"  # active, inactive, temporary
        self.temporary_tasks = {}
        # 固定自动观察任务 ID
        self.AUTO_TASK_ID = "task_0"
        self.WINDOW_COMPANION_TASK_ID = "window_companion_auto"

        # 日记功能相关
        self.diary_entries = []
        self.last_diary_date = None

        if not self.diary_storage:
            self.diary_storage = str(self.plugin_config.diary_dir)
        os.makedirs(self.diary_storage, exist_ok=True)

        self.parsed_custom_tasks = []
        self._parse_custom_tasks()

        self.last_mic_trigger = 0  # 上次麦克风触发时间
        self.mic_debounce_time = 60  # 麦克风防抖时间，单位为秒
        self.last_rest_reminder_time = None  # 上次休息提醒时间，用于冷却
        self.last_rest_reminder_day = ""

        self.parsed_preferences = {}
        self.learning_data = {}

        self.custom_presets = self.plugin_config.custom_presets
        self.current_preset_index = self.plugin_config.current_preset_index
        self.parsed_custom_presets = []
        self._parse_custom_presets()
        # 确保预设索引有效
        if self.current_preset_index >= len(self.parsed_custom_presets):
            self.current_preset_index = -1

        self.last_interaction_mode = self.interaction_mode
        self.last_check_interval = self.check_interval
        self.last_trigger_probability = self.trigger_probability
        self.last_active_time_range = self.active_time_range

        if not self.learning_storage:
            self.learning_storage = str(self.plugin_config.learning_dir)
        os.makedirs(self.learning_storage, exist_ok=True)

        # 观察记录相关
        self.observations = []  # 存储观察记录

        if not self.observation_storage:
            self.observation_storage = str(self.plugin_config.observations_dir)
        os.makedirs(self.observation_storage, exist_ok=True)

        # 加载观察记录
        self._load_observations()

        # WebUI 相关
        self.web_server = None
        ensure_webui_password(self)

        # 日记元数据相关（记录日记查看状态）
        self.diary_metadata = {}
        self.diary_metadata_file = os.path.join(self.diary_storage, "diary_metadata.json")
        self._load_diary_metadata()

        # 长期记忆系统
        self.long_term_memory = {}
        self.long_term_memory_file = os.path.join(self.learning_storage, "long_term_memory.json")
        self._load_long_term_memory()

        # 互动频率管理
        self.user_engagement = 5  # 用户参与度，范围 1-10
        self.engagement_history = []  # 记录用户参与度历史

        self.active_tasks = {}
        self.corrections = {}
        self.corrections_file = os.path.join(self.learning_storage, "corrections.json")
        load_corrections(self)
        
        # 窗口变化检测相关
        self.previous_windows = set()
        self.window_change_cooldown = 0
        self.window_timestamps = {}  # 记录窗口首次出现的时间戳
        self.auto_screen_runtime = {}
        self.recent_user_activity = {}
        self.screen_analysis_traces = []
        self._companion_outbound_by_umo: dict[str, deque[str]] = {}
        
        # 情绪记忆短缓存
        self.recent_emotion_memory = {}
        
        # 回复变体（固定句式复用）
        self.recent_reply_variations = {}
        
        # 时间跟踪相关
        self.current_activity = None  # 当前活动
        self.activity_start_time = None  # 活动开始时间
        self.activity_history = []  # 活动历史记录
        self.activity_history_file = os.path.join(self.learning_storage, "activity_history.json")
        self._load_activity_history()
        self.rest_reminder_state_file = os.path.join(
            self.learning_storage, "rest_reminder_state.json"
        )
        self._load_rest_reminder_state()

        self.uncertainty_words = ["也许", "可能", "看起来", "我猜", "像是", "大概", "说不定", "似乎"]

        # 解析用户偏好配置
        parse_user_preferences(self)

        # 加载学习数据
        if self.enable_learning:
            load_learning_data(self)

        self.task_semaphore = asyncio.Semaphore(2)  # 限制同时运行的任务数
        self.task_queue = asyncio.Queue()

        if str(getattr(self, "capture_source", "local") or "local").strip().lower() == "remote":
            self._remote_relay_task = asyncio.create_task(self._run_screen_relay_server())
            self.background_tasks.append(self._remote_relay_task)

        task = asyncio.create_task(self._task_scheduler())
        self.background_tasks.append(task)

        # 启动日记任务
        if self.enable_diary:
            task = asyncio.create_task(self._diary_task())
            self.background_tasks.append(task)

        # 启动 Web UI（如果启用）
        if self.webui_enabled:
            task = asyncio.create_task(self._start_webui())
            self.background_tasks.append(task)

        task = asyncio.create_task(self._custom_tasks_task())
        self.background_tasks.append(task)

        self._ensure_mic_monitor_background_task()
        task = asyncio.create_task(self._window_companion_task())
        self.background_tasks.append(task)
        self._shutdown_lock = asyncio.Lock()
        self._webui_lock = asyncio.Lock()
        self._is_stopping = False
        self._screen_assist_cooldowns = {}
        self.last_shared_activity_invite_time = 0.0
        if self._use_screen_recording_mode():
            self._safe_create_task(
                self._ensure_recording_ready(),
                name="screen_recording_bootstrap",
            )

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _as_context(self) -> Context:
        """Star 基类将 context 标成窄 Protocol；运行时为完整 Context（含 send_message 等）。"""
        return cast(Context, self.context)

    def _get_runtime_flag(self, name: str, default: bool = False) -> bool:
        return self._coerce_bool(getattr(self, name, default))

    def _resolve_vision_source(self) -> str:
        vs = str(getattr(self, "vision_source", "") or "").strip()
        if vs in ("仅外接", "仅框架", "外接+框架回退"):
            return vs
        return "仅外接" if self._get_runtime_flag("use_external_vision") else "仅框架"

    def _vision_prefers_external_sampling(self) -> bool:
        return self._resolve_vision_source() in ("仅外接", "外接+框架回退")

    def _sync_all_config(self) -> None:
        """将配置对象同步到插件运行时字段。"""
        sync_all_config(self)

    def _normalize_webui_port(self, port) -> int:
        return normalize_webui_port(self, port)

    def _parse_custom_presets(self) -> list:
        """解析自定义预设配置。"""
        self.parsed_custom_presets = []
        if not self.custom_presets:
            return self.parsed_custom_presets
        
        lines = self.custom_presets.strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split('|')
            if len(parts) >= 3:
                try:
                    preset = {
                        "name": parts[0].strip(),
                        "check_interval": max(10, int(parts[1].strip())),
                        "trigger_probability": max(0, min(100, int(parts[2].strip())))
                    }
                    self.parsed_custom_presets.append(preset)
                except ValueError:
                    continue
        return self.parsed_custom_presets

    def _get_current_preset_params(self) -> tuple:
        """获取当前生效的预设参数。"""
        if self.current_preset_index >= 0 and self.current_preset_index < len(self.parsed_custom_presets):
            preset = self.parsed_custom_presets[self.current_preset_index]
            return preset["check_interval"], preset["trigger_probability"]
        return self.check_interval, self.trigger_probability

    def _parse_window_companion_targets(self):
        """Parse window companion rules from config text."""
        raw_text = str(getattr(self, "window_companion_targets", "") or "").strip()
        self.parsed_window_companion_targets = parse_window_companion_targets(raw_text)
        return self.parsed_window_companion_targets

    def _list_open_window_titles(self) -> list[str]:
        """Return de-duplicated open window titles."""
        return list_open_window_titles(self)

    def _match_window_companion_target(self, window_titles):
        """Find the first configured window companion rule that matches."""
        return match_window_companion_target(self, window_titles)

    def _get_default_target(self) -> str:
        """Resolve the proactive message target."""
        return get_default_target(self)

    def _get_available_platforms(self) -> list[Any]:
        """Return loaded platform instances, preferring non-webchat adapters."""
        return get_available_platforms(self)

    def _get_preferred_platform_id(self) -> str:
        """Resolve the platform instance ID used for proactive messages."""
        return get_preferred_platform_id(self)

    def _build_private_target(self, session_id: str) -> str:
        """Build a private-chat target with the active platform instance ID."""
        return build_private_target(self, session_id)

    def _normalize_target(self, target: str) -> str:
        """Rewrite legacy proactive targets to the active platform instance ID."""
        return normalize_target(self, target)

    def _create_virtual_event(self, target: str) -> VirtualProactiveEvent:
        """Build a lightweight virtual event for proactive tasks."""
        return create_virtual_event(self, target)

    async def _send_proactive_message(
        self, target: str, message_chain: MessageChain
    ) -> bool:
        """Send a proactive message via the resolved platform instance."""
        return await send_proactive_message(self, target, message_chain)

    async def _send_plain_message(self, target: str, text: str) -> bool:
        """Send a plain proactive message if possible."""
        return await send_plain_message(self, target, text)

    def _resolve_proactive_target(self, fallback_event: Any = None) -> str:
        return resolve_proactive_target(self, fallback_event)

    def _build_message_chain(
        self, components: list[BaseMessageComponent] | None
    ) -> MessageChain:
        return build_message_chain(components)

    def _extract_plain_text(
        self, components: list[BaseMessageComponent] | None
    ) -> str:
        return extract_plain_text(components)

    async def _send_component_text(
        self,
        target: str,
        components: list[BaseMessageComponent] | None,
        *,
        prefix: str = "",
    ) -> bool:
        return await send_component_text(
            self, target, components, prefix=prefix
        )

    async def _send_segmented_text(
        self,
        target: str,
        text: str,
        *,
        max_length: int = 1000,
        delay_seconds: float = 0.5,
        should_continue: Any = None,
    ) -> bool:
        return await send_segmented_text(
            self,
            target,
            text,
            max_length=max_length,
            delay_seconds=delay_seconds,
            should_continue=should_continue,
        )

    def _build_window_companion_prompt(self, window_title: str, extra_prompt: str = "") -> str:
        """Build a focused prompt for window companion sessions."""
        return build_window_companion_prompt(window_title, extra_prompt)

    def _is_window_companion_session_active(self) -> bool:
        return is_window_companion_session_active(self)

    async def _start_window_companion_session(self, window_title: str, rule: dict) -> bool:
        """Start automatic companion mode for a matched window."""
        return await start_window_companion_session(self, window_title, rule)

    async def _stop_window_companion_session(self, reason: str = "window_closed") -> bool:
        """Stop the automatic companion session for the matched window."""
        return await stop_window_companion_session(self, reason)

    async def _window_companion_task(self):
        """Watch configured windows and start or stop companion sessions automatically."""
        await run_window_companion_task(self)

    def _ensure_webui_password(self) -> bool:
        return ensure_webui_password(self)

    def _snapshot_webui_runtime(self) -> tuple[bool, str, int, str, int]:
        return snapshot_webui_runtime(self)

    def _is_webui_runtime_changed(
        self, old_state: tuple[bool, str, int, str, int]
    ) -> bool:
        return is_webui_runtime_changed(self, old_state)

    async def _restart_webui(self) -> None:
        await restart_webui(self)

    def _apply_plugin_config_updates(self, config_dict: dict) -> None:
        """将配置字典写回插件配置对象。"""
        for k, v in config_dict.items():
            if k == "webui" and isinstance(v, dict):
                current_webui = self.plugin_config.webui
                # 检测密码是否被显式清空
                password_set_to_empty = "password" in v and not str(v["password"] or "").strip()
                for wk, wv in v.items():
                    if wk == "password" and not str(wv or "").strip():
                        # 允许显式清空密码
                        setattr(current_webui, wk, wv)
                    else:
                        setattr(current_webui, wk, wv)
                self.plugin_config.save_webui_config()
            elif k.startswith("webui_"):
                # 兼容旧版扁平 key，例如 webui_enabled -> webui.enabled
                wk = k[6:]
                if hasattr(self.plugin_config.webui, wk):
                    if wk == "password" and not str(v or "").strip():
                        # 允许显式清空密码
                        setattr(self.plugin_config.webui, wk, v)
                    else:
                        setattr(self.plugin_config.webui, wk, v)
                    self.plugin_config.save_webui_config()
            else:
                setattr(self.plugin_config, k, v)

    def _update_config_from_dict(self, config_dict: dict):
        """根据字典更新插件配置并处理运行时变更。"""
        if not config_dict:
            return

        try:
            # 使用配置服务更新配置
            if self.plugin_config:
                old_webui_state = self._snapshot_webui_runtime()
                old_recognition_mode = self._normalize_screen_recognition_mode(
                    getattr(self, "screen_recognition_mode", self.SCREENSHOT_MODE)
                )
                old_mic_monitor_enabled = bool(getattr(self, "enable_mic_monitor", False))
                self._apply_plugin_config_updates(config_dict)

                self._sync_all_config()
                
                if self.enable_mic_monitor:
                    self._ensure_mic_monitor_background_task()
                elif old_mic_monitor_enabled:
                    self._stop_mic_monitor_background_task()

                # 检查是否明确设置了空密码
                password_set_to_empty = False
                if "webui" in config_dict and isinstance(config_dict["webui"], dict):
                    password_set_to_empty = "password" in config_dict["webui"] and not str(config_dict["webui"]["password"] or "").strip()
                elif "webui_password" in config_dict:
                    password_set_to_empty = not str(config_dict["webui_password"] or "").strip()
                
                # 只有未显式清空密码时，才自动补齐密码
                if not password_set_to_empty and ensure_webui_password(self):
                    self._sync_all_config()

                if self._is_webui_runtime_changed(old_webui_state):
                    self._safe_create_task(self._restart_webui(), name="restart_webui")

                new_recognition_mode = self._normalize_screen_recognition_mode(
                    getattr(self, "screen_recognition_mode", self.SCREENSHOT_MODE)
                )
                if old_recognition_mode != new_recognition_mode:
                    self._safe_create_task(
                        self._handle_screen_recognition_mode_change(),
                        name="switch_screen_recognition_mode",
                    )

                logger.debug("配置更新完成")
        except Exception as e:
            logger.error(f"更新配置失败: {e}")

    @staticmethod
    def _safe_create_task(coro, *, name: str = "") -> asyncio.Task:
        """创建带异常兜底的后台任务。"""
        task = asyncio.create_task(coro, name=name or None)

        def _on_done(t: asyncio.Task):
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.error(f"后台任务 '{t.get_name()}' 异常: {exc}", exc_info=exc)

        task.add_done_callback(_on_done)
        return task

    async def _cancel_tasks(self, tasks: list[asyncio.Task], label: str) -> None:
        """取消并等待一组后台任务退出。"""
        alive_tasks = [task for task in tasks if task and not task.done()]
        if not alive_tasks:
            return

        for task in alive_tasks:
            task.cancel()

        for task in alive_tasks:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"等待{label}停止超时")
            except asyncio.CancelledError:
                logger.info(f"{label} cancelled")
            except Exception as e:
                logger.error(f"等待{label}停止时出错: {e}")

    def _normalize_screen_recognition_mode(self, value: Any) -> str:
        if isinstance(value, bool):
            return self.RECORDING_MODE if value else self.SCREENSHOT_MODE

        if isinstance(value, str):
            mode = value.strip().lower()
            if mode in {self.RECORDING_MODE, "video", "true", "1", "yes", "on"}:
                return self.RECORDING_MODE
            if mode in {self.SCREENSHOT_MODE, "image", "false", "0", "no", "off"}:
                return self.SCREENSHOT_MODE

        return self.SCREENSHOT_MODE

    def _use_screen_recording_mode(self) -> bool:
        return (
            self._normalize_screen_recognition_mode(
                getattr(self, "screen_recognition_mode", self.SCREENSHOT_MODE)
            )
            == self.RECORDING_MODE
        )

    @staticmethod
    def _normalize_clock_text(value: Any, default: str = "00:00") -> str:
        text = str(value or "").strip()
        if not text:
            return default
        try:
            hour_text, minute_text = text.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"
        except Exception:
            pass
        return default

    @staticmethod
    def _resolve_diary_target_date(
        now: datetime.datetime | None = None,
        *,
        early_morning_cutoff_hour: int = 2,
    ) -> datetime.date:
        return resolve_diary_target_date(
            now, early_morning_cutoff_hour=early_morning_cutoff_hour
        )

    def _get_capture_context_timeout(self, media_kind: str | None = None) -> float:
        normalized_kind = str(media_kind or "").strip().lower()
        if not normalized_kind:
            normalized_kind = "video" if self._use_screen_recording_mode() else "image"
        if normalized_kind == "video":
            duration = self._get_recording_duration_seconds()
            return float(max(duration + 35, 60))
        return 20.0

    def _get_interaction_timeout(
        self, media_kind: str, use_external_vision: bool
    ) -> float:
        normalized_kind = str(media_kind or "image").strip().lower()
        if normalized_kind == "video":
            return 300.0 if use_external_vision else 360.0
        return 180.0 if use_external_vision else 240.0

    def _get_screen_analysis_timeout(
        self,
        media_kind: str,
        use_external_vision: bool | None = None,
    ) -> float:
        if use_external_vision is None:
            use_external_vision = self._vision_prefers_external_sampling()
        interaction_timeout = self._get_interaction_timeout(
            media_kind,
            bool(use_external_vision),
        )
        base_timeout = 120.0 if str(media_kind or "image").strip().lower() == "video" else 45.0
        return interaction_timeout + base_timeout

    async def _handle_screen_recognition_mode_change(self) -> None:
        self._ensure_runtime_state()
        if self._use_screen_recording_mode():
            await self._ensure_recording_ready()
            return
        await self._stop_recording_if_running()

    def _ensure_runtime_state(self) -> None:
        ensure_plugin_runtime_state(self)

    def _ensure_recording_runtime_state(self) -> None:
        ensure_recording_runtime_state(self)

    def _register_process_instance(self) -> None:
        global _ACTIVE_INSTANCE_TOKEN
        token = uuid.uuid4().hex
        with _PROCESS_GUARD_LOCK:
            _ACTIVE_INSTANCE_TOKEN = token
        self._instance_token = token

    def _is_current_process_instance(self) -> bool:
        token = str(getattr(self, "_instance_token", "") or "").strip()
        if not token:
            return True
        with _PROCESS_GUARD_LOCK:
            return _ACTIVE_INSTANCE_TOKEN == token

    def _cleanup_legacy_default_custom_tasks(self) -> None:
        legacy_value = self.LEGACY_DEFAULT_CUSTOM_TASK.strip()
        current_value = str(getattr(self, "custom_tasks", "") or "").strip()
        if current_value != legacy_value:
            return

        logger.info("检测到旧版默认自定义监控任务，已自动清理")
        self.custom_tasks = ""
        try:
            self.plugin_config.custom_tasks = ""
        except Exception:
            pass

    def _try_enter_process_guard(
        self,
        guard_key: str,
        *,
        stale_seconds: float,
    ) -> bool:
        now_ts = time.time()
        with _PROCESS_GUARD_LOCK:
            expired_keys = [
                key
                for key, started_at in _PROCESS_GUARDS.items()
                if (now_ts - float(started_at or 0.0)) >= stale_seconds
            ]
            for key in expired_keys:
                _PROCESS_GUARDS.pop(key, None)
            if guard_key in _PROCESS_GUARDS:
                return False
            _PROCESS_GUARDS[guard_key] = now_ts
            return True

    def _leave_process_guard(self, guard_key: str) -> None:
        with _PROCESS_GUARD_LOCK:
            _PROCESS_GUARDS.pop(guard_key, None)

    def _try_mark_custom_task_dispatch(self, task_key: str) -> bool:
        return self._try_enter_process_guard(
            f"custom_task_dispatch:{task_key}",
            stale_seconds=self.CUSTOM_TASK_PROCESS_DEDUP_SECONDS,
        )

    def _get_screen_analysis_backoff_remaining(self) -> float:
        self._ensure_runtime_state()
        backoff_until = float(getattr(self, "_screen_analysis_backoff_until", 0.0) or 0.0)
        return max(0.0, backoff_until - time.time())

    def _record_screen_analysis_result(self, ok: bool, *, error_type: str = "") -> None:
        self._ensure_runtime_state()
        if ok:
            self._screen_analysis_failure_count = 0
            self._screen_analysis_backoff_until = 0.0
            return

        normalized_error_type = str(error_type or "").strip().lower()
        if normalized_error_type not in {"api", "timeout"}:
            return

        failure_count = int(getattr(self, "_screen_analysis_failure_count", 0) or 0) + 1
        self._screen_analysis_failure_count = failure_count
        backoff_seconds = min(
            self.SCREEN_ANALYSIS_FAILURE_BACKOFF_MAX_SECONDS,
            self.SCREEN_ANALYSIS_FAILURE_BACKOFF_BASE_SECONDS * (2 ** max(0, failure_count - 1)),
        )
        self._screen_analysis_backoff_until = time.time() + backoff_seconds
        logger.warning(
            f"识屏链路连续失败，进入退避 {backoff_seconds} 秒: error_type={normalized_error_type}, "
            f"failure_count={failure_count}"
        )

    def _try_begin_background_screen_job(self) -> tuple[bool, str]:
        remaining = self._get_screen_analysis_backoff_remaining()
        if remaining > 0:
            return False, f"识屏链路退避中，约 {max(1, int(remaining))} 秒后再试"

        acquired = self._try_enter_process_guard(
            "background_screen_job",
            stale_seconds=self.BACKGROUND_SCREEN_GUARD_STALE_SECONDS,
        )
        if not acquired:
            return False, "已有后台识屏任务正在执行"
        return True, ""

    def _finish_background_screen_job(self) -> None:
        self._leave_process_guard("background_screen_job")

    def _get_recording_fps(self) -> float:
        return get_recording_fps(self)

    def _get_recording_duration_seconds(self) -> int:
        return get_recording_duration_seconds(self)

    def _get_ffmpeg_path(self) -> str:
        return get_ffmpeg_path(self)

    def _get_ffmpeg_storage_dir(self, create: bool = False) -> str:
        return get_ffmpeg_storage_dir(self, create)

    def _get_recording_video_encoder(self) -> str:
        return get_recording_video_encoder(self)

    def _build_recording_video_args(self) -> list[str]:
        return build_recording_video_args(self)

    def _get_video_sampling_plan(
        self,
        scene: str,
        *,
        duration_seconds: int,
        use_external_vision: bool,
    ) -> dict[str, Any]:
        return get_video_sampling_plan(
            self,
            scene,
            duration_seconds=duration_seconds,
            use_external_vision=use_external_vision,
        )

    def _extract_video_sample_sheet_sync(
        self,
        video_bytes: bytes,
        *,
        sample_count: int = 3,
        sampling_strategy: str = "keyframe_sheet",
        latest_frame_bytes: bytes | None = None,
    ) -> dict[str, Any] | None:
        return extract_video_sample_sheet_sync(
            self,
            video_bytes,
            sample_count=sample_count,
            sampling_strategy=sampling_strategy,
            latest_frame_bytes=latest_frame_bytes,
        )

    async def _build_video_sample_capture_context(
        self,
        capture_context: dict[str, Any],
        *,
        scene: str,
        use_external_vision: bool,
    ) -> dict[str, Any] | None:
        return await build_video_sample_capture_context(
            self,
            capture_context,
            scene=scene,
            use_external_vision=use_external_vision,
        )

    def _should_keep_sampled_video_only(
        self,
        scene: str,
        *,
        use_external_vision: bool,
        preserve_full_video_for_audio: bool = False,
    ) -> bool:
        return should_keep_sampled_video_only(
            self,
            scene,
            use_external_vision=use_external_vision,
            preserve_full_video_for_audio=preserve_full_video_for_audio,
        )

    def _looks_uncertain_screen_result(self, text: str) -> bool:
        normalized = self._normalize_record_text(text)
        if not normalized or self._is_low_value_record_text(normalized):
            return True
        uncertain_markers = (
            "看不清",
            "不确定",
            "无法判断",
            "信息不足",
            "可能",
            "似乎",
        )
        return any(marker in str(text or "") for marker in uncertain_markers)

    def _get_recording_cache_dir(self) -> str:
        return get_recording_cache_dir(self)

    def _detect_system_audio_device(self) -> str | None:
        return detect_system_audio_device(self)

    def _cleanup_recording_cache(self, keep_latest: int = 3) -> None:
        cleanup_recording_cache(self, keep_latest)

    def _record_screen_clip_sync(self, duration_seconds: int) -> str:
        return record_screen_clip_sync(self, duration_seconds)

    def _start_screen_recording_sync(self) -> str:
        return start_screen_recording_sync(self)

    def _stop_screen_recording_sync(self) -> str:
        return stop_screen_recording_sync(self)

    async def _ensure_recording_ready(self) -> None:
        await ensure_recording_ready(self)

    async def _stop_recording_if_running(self) -> None:
        await stop_recording_if_running(self)

    def _get_active_window_info(self) -> tuple[str, tuple[int, int, int, int] | None]:
        title = ""
        region = None
        if sys.platform != "win32":
            return title, region

        try:
            import pygetwindow

            active_window = pygetwindow.getActiveWindow()
            if not active_window:
                return title, region

            title = str(active_window.title or "").strip()
            left = int(getattr(active_window, "left", 0) or 0)
            top = int(getattr(active_window, "top", 0) or 0)
            width = int(getattr(active_window, "width", 0) or 0)
            height = int(getattr(active_window, "height", 0) or 0)
            if width > 20 and height > 20:
                region = (left, top, width, height)
        except Exception as e:
            logger.debug(f"获取活动窗口信息失败: {e}")

        return title, region

    def _load_observations(self):
        """加载观察记录。"""
        load_observations(self)

    def _save_observations(self):
        """保存观察记录。"""
        save_observations(self)

    def _add_observation(
        self,
        scene,
        recognition_text,
        active_window_title,
        extra: dict[str, Any] | None = None,
    ):
        """添加一条观察记录。"""
        return add_observation(
            self, scene, recognition_text, active_window_title, extra=extra
        )

    def _load_diary_metadata(self):
        load_diary_metadata(self)

    def _save_diary_metadata(self):
        save_diary_metadata(self)

    def _update_diary_view_status(self, date_str):
        update_diary_view_status(self, date_str)

    def _load_long_term_memory(self):
        load_long_term_memory(self)

    def _save_long_term_memory(self):
        save_long_term_memory(self)

    @staticmethod
    def _normalize_scene_label(scene: str) -> str:
        return normalize_scene_label(scene)

    @staticmethod
    def _normalize_window_title(window_title: str) -> str:
        return normalize_window_title(window_title)

    @staticmethod
    def _normalize_record_text(text: str) -> str:
        return normalize_record_text(text)

    @staticmethod
    def _normalize_shared_activity_summary(summary: str) -> str:
        return normalize_shared_activity_summary(summary)

    def _ensure_long_term_memory_defaults(self) -> None:
        ensure_long_term_memory_defaults(self)

    def _extract_memory_focus(self, text: str, max_length: int = 48) -> str:
        return ltm_extract_memory_focus(text, max_length=max_length)

    def _remember_episodic_memory(
        self,
        *,
        scene: str,
        active_window: str,
        summary: str,
        response_preview: str = "",
        kind: str = "screen_observation",
    ) -> bool:
        return remember_episodic_memory(
            self,
            scene=scene,
            active_window=active_window,
            summary=summary,
            response_preview=response_preview,
            kind=kind,
        )

    def _remember_focus_pattern(
        self,
        *,
        scene: str,
        active_window: str,
        summary: str,
    ) -> bool:
        return remember_focus_pattern(
            self, scene=scene, active_window=active_window, summary=summary
        )

    def _is_low_value_record_text(self, text: str) -> bool:
        return is_low_value_record_text(text)

    def _is_screen_error_text(self, text: str) -> bool:
        return is_screen_error_text(text)

    def _is_similar_record(self, current_text: str, previous_text: str, threshold: float = 0.98) -> bool:
        return is_similar_record(current_text, previous_text, threshold=threshold)

    @staticmethod
    def _compress_recognition_text(text: str, max_length: int = 800) -> str:
        return compress_recognition_text(text, max_length=max_length)

    def _should_store_observation(self, scene: str, recognition_text: str, active_window_title: str) -> tuple[bool, str]:
        return should_store_observation(self, scene, recognition_text, active_window_title)

    def _should_store_diary_entry(self, content: str, active_window: str) -> tuple[bool, str]:
        return should_store_diary_entry(self, content, active_window)

    @staticmethod
    def _limit_ranked_dict_items(items: dict, limit: int, score_keys: tuple[str, ...]) -> dict:
        return limit_ranked_dict_items(items, limit, score_keys)

    @staticmethod
    def _sanitize_diary_section_text(text: str) -> str:
        return sanitize_diary_section_text(text)

    @staticmethod
    def _parse_clock_to_minutes(value: str | None) -> int | None:
        return parse_clock_to_minutes(value)

    def _compact_diary_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return compact_diary_entries(self, entries)

    def _is_continuing_memory_context(self, scene: str, active_window: str) -> bool:
        return is_continuing_memory_context(self, scene, active_window)

    @staticmethod
    def _build_diary_reflection_prompt(
        observation_text: str,
        viewed_count: int,
        reference_days: list[dict] | None = None,
    ) -> str:
        return build_diary_reflection_prompt(
            observation_text, viewed_count, reference_days=reference_days
        )

    def _build_vision_prompt(self, scene: str, active_window_title: str = "") -> str:
        return build_vision_prompt(self, scene, active_window_title)

    def _extract_screen_assist_prompt(self, message: str) -> str:
        return extract_screen_assist_prompt(self, message)

    def _build_diary_document(
        self,
        target_date,
        weekday: str,
        observation_text: str,
        reflection_text: str,
        structured_summary: dict[str, Any] | None = None,
        weather_info: str = "",
    ) -> str:
        return build_diary_document(
            self,
            target_date,
            weekday,
            observation_text,
            reflection_text,
            structured_summary=structured_summary,
            weather_info=weather_info,
        )

    def _extract_actionable_suggestions(
        self,
        reflection_text: str,
        *,
        limit: int = 3,
    ) -> list[str]:
        return extract_actionable_suggestions(reflection_text, limit=limit)

    def _build_diary_structured_summary(
        self,
        compacted_entries: list[dict[str, Any]],
        reflection_text: str,
    ) -> dict[str, Any]:
        return build_diary_structured_summary(compacted_entries, reflection_text)

    def _build_diary_summary_markdown(self, structured_summary: dict[str, Any]) -> list[str]:
        return build_diary_summary_markdown(structured_summary)

    @staticmethod
    def _build_diary_reflection_fallback(
        observation_text: str,
        structured_summary: dict[str, Any] | None = None,
    ) -> str:
        return build_diary_reflection_fallback(
            observation_text, structured_summary=structured_summary
        )

    def _ensure_diary_reflection_text(
        self,
        reflection_text: str,
        observation_text: str,
        structured_summary: dict[str, Any] | None = None,
    ) -> str:
        return ensure_diary_reflection_text(
            self,
            reflection_text,
            observation_text,
            structured_summary=structured_summary,
        )

    @staticmethod
    def _extract_diary_preview_text(diary_content: str) -> str:
        return extract_diary_preview_text(diary_content)

    def _get_diary_summary_path(self, target_date: datetime.date) -> str:
        return get_diary_summary_path(self, target_date)

    def _load_diary_structured_summary(self, target_date: datetime.date) -> dict[str, Any]:
        return load_diary_structured_summary(self, target_date)

    def _save_diary_structured_summary(
        self,
        target_date: datetime.date,
        structured_summary: dict[str, Any],
    ) -> None:
        save_diary_structured_summary(self, target_date, structured_summary)

    def _remember_diary_summary_memories(
        self,
        target_date: datetime.date,
        structured_summary: dict[str, Any],
    ) -> None:
        remember_diary_summary_memories(self, target_date, structured_summary)

    def _clean_long_term_memory_noise(self):
        clean_long_term_memory_noise(self)

    def _update_long_term_memory(
        self,
        scene,
        active_window,
        duration,
        user_preferences=None,
        memory_summary: str = "",
        response_preview: str = "",
    ):
        update_long_term_memory(
            self,
            scene,
            active_window,
            duration,
            user_preferences=user_preferences,
            memory_summary=memory_summary,
            response_preview=response_preview,
        )

    def _apply_memory_decay(self):
        apply_memory_decay(self)

    @staticmethod
    def _build_memory_priority_value(base_count: int | float, days_since: int) -> int:
        return build_memory_priority_value(base_count, days_since)

    def _build_memory_associations(self, scene, app_name):
        build_memory_associations(self, scene, app_name)

    def _update_memory_priorities(self):
        update_memory_priorities(self)

    def _extract_shared_activity_from_message(self, message_text: str) -> tuple[str, str] | tuple[None, None]:
        return extract_shared_activity_from_message(
            message_text,
            bot_name=str(getattr(self, "bot_name", "") or "").strip(),
        )

    def _remember_shared_activity(self, category: str, summary: str, source_text: str = "") -> bool:
        import datetime

        normalized_summary = self._normalize_shared_activity_summary(summary)
        if not normalized_summary:
            return False

        self._ensure_long_term_memory_defaults()
        today = datetime.date.today().isoformat()
        activity_memory = self.long_term_memory["shared_activities"].setdefault(
            normalized_summary,
            {
                "category": str(category or "other"),
                "count": 0,
                "last_shared": today,
                "priority": 0,
            },
        )
        activity_memory["category"] = str(category or activity_memory.get("category", "other") or "other")
        activity_memory["count"] = int(activity_memory.get("count", 0) or 0) + 1
        activity_memory["last_shared"] = today
        if source_text:
            activity_memory["example"] = str(source_text).strip()[:120]

        self._update_memory_priorities()
        self._save_long_term_memory()
        logger.info(f"已记录共同经历: {normalized_summary}")
        return True

    def _learn_shared_activity_from_message(self, message_text: str) -> bool:
        category, summary = self._extract_shared_activity_from_message(message_text)
        if not category or not summary:
            return False
        return self._remember_shared_activity(category, summary, source_text=message_text)

    def _update_activity(self, scene, active_window):
        """更新活动状态，记录工作/摸鱼时间。"""
        return update_activity(self, scene, active_window)

    def _load_activity_history(self) -> None:
        try:
            activity_history_file = getattr(self, "activity_history_file", "")
            if not activity_history_file:
                activity_history_file = os.path.join(self.learning_storage, "activity_history.json")
                self.activity_history_file = activity_history_file
            if not os.path.exists(activity_history_file):
                self.activity_history = []
                return
            with open(activity_history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.activity_history = data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"加载活动历史失败: {e}")
            self.activity_history = []

    def _save_activity_history(self) -> None:
        try:
            activity_history_file = getattr(self, "activity_history_file", "")
            if not activity_history_file:
                activity_history_file = os.path.join(self.learning_storage, "activity_history.json")
                self.activity_history_file = activity_history_file
            with open(activity_history_file, "w", encoding="utf-8") as f:
                json.dump(self.activity_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存活动历史失败: {e}")

    def _load_rest_reminder_state(self) -> None:
        self.last_rest_reminder_day = ""
        self.last_rest_reminder_time = None
        try:
            state_file = str(getattr(self, "rest_reminder_state_file", "") or "").strip()
            if not state_file or not os.path.exists(state_file):
                return
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            self.last_rest_reminder_day = str(
                data.get("last_rest_reminder_day", "") or ""
            ).strip()
            last_sent_at = str(data.get("last_rest_reminder_at", "") or "").strip()
            if last_sent_at:
                try:
                    self.last_rest_reminder_time = datetime.datetime.fromisoformat(
                        last_sent_at
                    )
                except Exception:
                    self.last_rest_reminder_time = None
        except Exception as e:
            logger.error(f"加载休息提醒状态失败: {e}")
            self.last_rest_reminder_day = ""
            self.last_rest_reminder_time = None

    def _save_rest_reminder_state(self) -> None:
        try:
            state_file = str(getattr(self, "rest_reminder_state_file", "") or "").strip()
            if not state_file:
                state_file = os.path.join(
                    self.learning_storage,
                    "rest_reminder_state.json",
                )
                self.rest_reminder_state_file = state_file
            last_reminder_time = getattr(self, "last_rest_reminder_time", None)
            payload = {
                "last_rest_reminder_day": str(
                    getattr(self, "last_rest_reminder_day", "") or ""
                ).strip(),
                "last_rest_reminder_at": (
                    last_reminder_time.isoformat()
                    if isinstance(last_reminder_time, datetime.datetime)
                    else ""
                ),
            }
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存休息提醒状态失败: {e}")

    def _get_rest_day_bucket(self, dt: datetime.datetime | None = None) -> datetime.date:
        dt = dt or datetime.datetime.now()
        if dt.hour < self.REST_REMINDER_CUTOFF_HOUR:
            return dt.date() - datetime.timedelta(days=1)
        return dt.date()

    def _to_extended_rest_minutes(self, value: datetime.datetime | datetime.time | str) -> int | None:
        if isinstance(value, datetime.datetime):
            hour = int(value.hour)
            minute = int(value.minute)
        elif isinstance(value, datetime.time):
            hour = int(value.hour)
            minute = int(value.minute)
        else:
            parsed_minutes = self._parse_clock_to_minutes(str(value or "").strip())
            if parsed_minutes is None:
                return None
            hour, minute = divmod(parsed_minutes, 60)
        total = hour * 60 + minute
        if total < self.REST_REMINDER_CUTOFF_HOUR * 60:
            total += 24 * 60
        return total

    def _format_extended_rest_minutes(self, minutes_value: int | float | None) -> str:
        if minutes_value is None:
            return ""
        total = int(round(float(minutes_value or 0)))
        total %= 24 * 60
        hour, minute = divmod(total, 60)
        return f"{hour:02d}:{minute:02d}"

    def _get_configured_rest_range(self) -> tuple[int, int] | None:
        time_range = str(getattr(self, "rest_time_range", "") or "").strip()
        if not time_range or "-" not in time_range:
            return None
        try:
            start_text, end_text = time_range.split("-", 1)
            start_minutes = self._parse_clock_to_minutes(start_text)
            end_minutes = self._parse_clock_to_minutes(end_text)
            if start_minutes is None or end_minutes is None:
                return None
            return start_minutes, end_minutes
        except Exception:
            return None

    def _collect_recent_rest_activity_samples(
        self,
        *,
        lookback_days: int | None = None,
        now: datetime.datetime | None = None,
    ) -> list[dict[str, Any]]:
        now = now or datetime.datetime.now()
        current_bucket = self._get_rest_day_bucket(now)
        lookback = max(1, int(lookback_days or self.REST_INFERENCE_LOOKBACK_DAYS))
        earliest_bucket = current_bucket - datetime.timedelta(days=lookback)
        nightly_start_minutes = self.REST_ACTIVITY_WINDOW_START_HOUR * 60
        nightly_end_minutes = (24 + self.REST_REMINDER_CUTOFF_HOUR) * 60

        daily_samples: dict[str, dict[str, Any]] = {}
        for item in self._get_activity_history_for_stats():
            if not isinstance(item, dict):
                continue
            for field_name in ("start_time", "end_time"):
                ts = float(item.get(field_name, 0) or 0)
                if ts <= 0:
                    continue
                dt = datetime.datetime.fromtimestamp(ts)
                bucket_day = self._get_rest_day_bucket(dt)
                if bucket_day >= current_bucket or bucket_day < earliest_bucket:
                    continue
                extended_minutes = self._to_extended_rest_minutes(dt)
                if extended_minutes is None:
                    continue
                if (
                    extended_minutes < nightly_start_minutes
                    or extended_minutes > nightly_end_minutes
                ):
                    continue
                key = bucket_day.isoformat()
                previous = daily_samples.get(key)
                if previous is None or ts > float(previous.get("timestamp", 0) or 0):
                    daily_samples[key] = {
                        "day": key,
                        "timestamp": ts,
                        "extended_minutes": extended_minutes,
                        "window": self._normalize_window_title(item.get("window", "") or ""),
                        "scene": self._normalize_scene_label(item.get("scene", "") or ""),
                    }

        return [
            daily_samples[key]
            for key in sorted(daily_samples.keys())
        ]

    def _infer_rest_behavior(self, now: datetime.datetime | None = None) -> dict[str, Any]:
        now = now or datetime.datetime.now()
        current_bucket = self._get_rest_day_bucket(now)
        samples = self._collect_recent_rest_activity_samples(now=now)
        info: dict[str, Any] = {
            "available": False,
            "source": "none",
            "rest_extended_minutes": None,
            "rest_clock": "",
            "reminder_extended_minutes": None,
            "reminder_clock": "",
            "sample_count": len(samples),
            "rest_bucket_day": current_bucket.isoformat(),
        }

        if len(samples) >= self.REST_INFERENCE_MIN_SAMPLES:
            import statistics

            recent_samples = samples[-self.REST_INFERENCE_LOOKBACK_DAYS :]
            inferred_rest_minutes = int(
                round(
                    statistics.median(
                        sample.get("extended_minutes", 0) for sample in recent_samples
                    )
                )
            )
            inferred_rest_minutes = max(
                self.REST_ACTIVITY_WINDOW_START_HOUR * 60,
                min(
                    inferred_rest_minutes,
                    (24 + self.REST_REMINDER_CUTOFF_HOUR) * 60,
                ),
            )
            reminder_minutes = max(
                self.REST_ACTIVITY_WINDOW_START_HOUR * 60,
                inferred_rest_minutes - self.REST_REMINDER_ADVANCE_MINUTES,
            )
            info.update(
                {
                    "available": True,
                    "source": "activity_history",
                    "samples": recent_samples,
                    "rest_extended_minutes": inferred_rest_minutes,
                    "rest_clock": self._format_extended_rest_minutes(
                        inferred_rest_minutes
                    ),
                    "reminder_extended_minutes": reminder_minutes,
                    "reminder_clock": self._format_extended_rest_minutes(
                        reminder_minutes
                    ),
                }
            )
            return info

        configured_range = self._get_configured_rest_range()
        if configured_range is None:
            return info

        start_minutes, _ = configured_range
        reminder_minutes = max(
            0,
            start_minutes - self.REST_REMINDER_ADVANCE_MINUTES,
        )
        info.update(
            {
                "available": True,
                "source": "configured_rest_range",
                "rest_extended_minutes": self._to_extended_rest_minutes(
                    datetime.time(start_minutes // 60, start_minutes % 60)
                ),
                "rest_clock": self._format_extended_rest_minutes(start_minutes),
                "reminder_extended_minutes": self._to_extended_rest_minutes(
                    datetime.time(reminder_minutes // 60, reminder_minutes % 60)
                ),
                "reminder_clock": self._format_extended_rest_minutes(reminder_minutes),
            }
        )
        return info

    def _should_send_rest_reminder(self, now: datetime.datetime | None = None) -> tuple[bool, dict[str, Any]]:
        now = now or datetime.datetime.now()
        info = self._infer_rest_behavior(now=now)
        if not info.get("available"):
            return False, info

        current_bucket = self._get_rest_day_bucket(now).isoformat()
        info["rest_bucket_day"] = current_bucket
        if str(getattr(self, "last_rest_reminder_day", "") or "").strip() == current_bucket:
            return False, info

        reminder_minutes = info.get("reminder_extended_minutes")
        rest_minutes = info.get("rest_extended_minutes")
        now_minutes = self._to_extended_rest_minutes(now)
        if reminder_minutes is None or rest_minutes is None or now_minutes is None:
            return False, info
        if now_minutes < reminder_minutes:
            return False, info
        if now_minutes > rest_minutes + self.REST_REMINDER_LATEST_AFTER_MINUTES:
            return False, info
        return True, info

    def _remember_inferred_rest_memory(self, info: dict[str, Any]) -> bool:
        if not isinstance(info, dict) or not info.get("available"):
            return False

        rest_clock = str(info.get("rest_clock", "") or "").strip()
        reminder_clock = str(info.get("reminder_clock", "") or "").strip()
        source = str(info.get("source", "") or "").strip()
        sample_count = int(info.get("sample_count", 0) or 0)
        summary = (
            f"用户最近的休息时间大约在 {rest_clock}，"
            f"提醒休息更适合放在 {reminder_clock} 左右。"
        )
        if source == "activity_history" and sample_count > 0:
            summary += f" 这是根据最近 {sample_count} 天最后一次窗口活动推测出来的。"
            latest_sample = (info.get("samples", []) or [])[-1] if isinstance(info.get("samples", []), list) else {}
            latest_window = self._normalize_window_title(latest_sample.get("window", "") or "")
            if latest_window:
                summary += f" 最近一次夜间收尾窗口是《{latest_window}》。"
        elif source == "configured_rest_range":
            summary += " 当前样本不足，先使用配置的休息时间作为兜底。"

        remembered = self._remember_episodic_memory(
            scene="休息",
            active_window="作息规律",
            summary=summary,
            response_preview=summary,
            kind="rest_pattern",
        )
        if remembered:
            self._save_long_term_memory()
        return remembered

    def _mark_rest_reminder_sent(self, info: dict[str, Any] | None = None) -> None:
        now = datetime.datetime.now()
        self.last_rest_reminder_time = now
        self.last_rest_reminder_day = self._get_rest_day_bucket(now).isoformat()
        self._save_rest_reminder_state()
        if isinstance(info, dict):
            self._remember_inferred_rest_memory(info)

    def _parse_activity_marker(self, activity: str) -> tuple[str, str, str]:
        parts = str(activity or "").split(":", 2)
        activity_type = parts[0] if len(parts) > 0 else "其他"
        scene = parts[1] if len(parts) > 1 else ""
        window = parts[2] if len(parts) > 2 else ""
        return activity_type, scene, window

    def _append_activity_record(
        self,
        *,
        activity: str,
        start_time: float,
        end_time: float,
        min_duration_seconds: int | None = None,
    ) -> bool:
        min_duration = (
            self.ACTIVITY_MIN_DURATION_SECONDS
            if min_duration_seconds is None
            else max(0, int(min_duration_seconds or 0))
        )
        duration = float(end_time or 0) - float(start_time or 0)
        if not activity or duration < min_duration:
            return False

        activity_type, scene, window = self._parse_activity_marker(activity)
        self.activity_history.append(
            {
                "type": activity_type,
                "scene": scene,
                "window": window,
                "start_time": float(start_time or 0),
                "end_time": float(end_time or 0),
                "duration": float(duration),
            }
        )
        if len(self.activity_history) > self.ACTIVITY_HISTORY_LIMIT:
            self.activity_history = self.activity_history[-self.ACTIVITY_HISTORY_LIMIT :]
        self._save_activity_history()
        return True

    def _build_current_activity_snapshot(self, now_ts: float | None = None) -> dict[str, Any] | None:
        current_activity = str(getattr(self, "current_activity", "") or "").strip()
        activity_start_time = float(getattr(self, "activity_start_time", 0) or 0)
        current_time = float(now_ts or time.time())
        if not current_activity or activity_start_time <= 0 or current_time <= activity_start_time:
            return None

        duration = current_time - activity_start_time
        if duration < self.LIVE_ACTIVITY_MIN_DURATION_SECONDS:
            return None

        activity_type, scene, window = self._parse_activity_marker(current_activity)
        return {
            "type": activity_type,
            "scene": scene,
            "window": window,
            "start_time": activity_start_time,
            "end_time": current_time,
            "duration": float(duration),
            "is_live": True,
        }

    def _get_activity_history_for_stats(self) -> list[dict[str, Any]]:
        activity_history = list(getattr(self, "activity_history", []) or [])
        current_snapshot = self._build_current_activity_snapshot()
        if current_snapshot:
            activity_history.append(current_snapshot)
        return activity_history

    def _detect_window_changes(self):
        """检测窗口变化，包括新打开的窗口。"""
        return detect_window_changes(self)

    def _ensure_auto_screen_runtime_state(self, task_id: str) -> dict[str, Any]:
        return ensure_auto_screen_runtime_state(self, task_id)

    def _build_auto_screen_change_snapshot(
        self,
        task_id: str,
        *,
        window_changed: bool = False,
        new_windows: list[str] | None = None,
        update_state: bool = True,
    ) -> dict[str, Any]:
        return build_auto_screen_change_snapshot(
            self,
            task_id,
            window_changed=window_changed,
            new_windows=new_windows,
            update_state=update_state,
        )

    def _is_idle_keepalive_due(self, task_id: str, check_interval: int) -> bool:
        return is_idle_keepalive_due(self, task_id, check_interval)

    def _decide_auto_screen_trigger(
        self,
        task_id: str,
        *,
        probability: int,
        check_interval: int,
        system_high_load: bool,
        change_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return decide_auto_screen_trigger(
            self,
            task_id,
            probability=probability,
            check_interval=check_interval,
            system_high_load=system_high_load,
            change_snapshot=change_snapshot,
        )

    def _should_skip_similar_auto_reply(
        self,
        task_id: str,
        *,
        active_window_title: str,
        text_content: str,
        check_interval: int,
    ) -> tuple[bool, str]:
        return should_skip_similar_auto_reply(
            self,
            task_id,
            active_window_title=active_window_title,
            text_content=text_content,
            check_interval=check_interval,
        )

    def _remember_auto_reply_state(
        self,
        task_id: str,
        *,
        active_window_title: str,
        text_content: str,
        sent: bool,
        scene: str = "",
        note: str = "",
    ) -> None:
        remember_auto_reply_state(
            self,
            task_id,
            active_window_title=active_window_title,
            text_content=text_content,
            sent=sent,
            scene=scene,
            note=note,
        )

    def _format_reply_interval_text(self, seconds: float) -> str:
        return format_reply_interval_text(seconds)

    def _build_reply_interval_guidance(self, task_id: str) -> tuple[str, dict[str, Any]]:
        return build_reply_interval_guidance(self, task_id)

    def _remember_recent_user_activity(self, event: AstrMessageEvent) -> None:
        self._ensure_runtime_state()
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not umo:
            return

        self.recent_user_activity[umo] = time.time()
        if len(self.recent_user_activity) > 100:
            sorted_items = sorted(
                self.recent_user_activity.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            self.recent_user_activity = dict(sorted_items[:100])

    def _get_recent_user_activity_at(self, target_or_event: Any = None) -> float:
        self._ensure_runtime_state()
        umo = ""
        if isinstance(target_or_event, str):
            umo = str(target_or_event or "").strip()
        elif target_or_event is not None:
            umo = str(getattr(target_or_event, "unified_msg_origin", "") or "").strip()

        if not umo:
            return 0.0
        return float(self.recent_user_activity.get(umo, 0.0) or 0.0)

    def _should_defer_for_recent_user_activity(
        self,
        event: AutoScreenSessionEvent,
        *,
        task_id: str,
        change_snapshot: dict[str, Any],
    ) -> tuple[bool, str]:
        return should_defer_for_recent_user_activity(
            self,
            event,
            task_id=task_id,
            change_snapshot=change_snapshot,
        )

    def _get_scene_behavior_profile(self, scene: str) -> dict[str, Any]:
        return get_scene_behavior_profile(self, scene)

    def _should_skip_same_window_followup(
        self,
        task_id: str,
        *,
        active_window_title: str,
        scene: str,
    ) -> tuple[bool, str]:
        return should_skip_same_window_followup(
            self,
            task_id,
            active_window_title=active_window_title,
            scene=scene,
        )

    def _truncate_preview_text(self, text: str, limit: int = 120) -> str:
        return truncate_preview_text(text, limit)

    def _get_recent_reply_openings(self, interaction_key: str) -> list[str]:
        self._ensure_runtime_state()
        key = str(interaction_key or "").strip()
        if not key:
            return []
        
        now_ts = time.time()
        records = self.recent_reply_variations.get(key, [])
        fresh_records = [
            item
            for item in records
            if isinstance(item, dict)
            and str(item.get("opening", "") or "").strip()
            and (now_ts - float(item.get("timestamp", 0.0) or 0.0))
            <= self.REPLY_VARIATION_TTL_SECONDS
        ]
        self.recent_reply_variations[key] = fresh_records[-self.REPLY_VARIATION_HISTORY_LIMIT :]

        unique_openings: list[str] = []
        for item in reversed(self.recent_reply_variations[key]):
            opening = str(item.get("opening", "") or "").strip()
            if opening and opening not in unique_openings:
                unique_openings.append(opening)
        return unique_openings

    def _record_reply_opening(self, interaction_key: str, opening: str, *, channel: str = "reply") -> None:
        self._ensure_runtime_state()
        key = str(interaction_key or "").strip()
        if not key:
            return
        
        opening = str(opening or "").strip()
        if not opening:
            return
        
        now_ts = time.time()
        records = self.recent_reply_variations.get(key, [])
        records = [
            item
            for item in records
            if isinstance(item, dict)
            and (now_ts - float(item.get("timestamp", 0.0) or 0.0))
            <= self.REPLY_VARIATION_TTL_SECONDS
        ]
        records.append(
            {
                "opening": opening,
                "timestamp": now_ts,
                "channel": str(channel or "reply"),
            }
        )
        self.recent_reply_variations[key] = records[-self.REPLY_VARIATION_HISTORY_LIMIT :]

    def _build_reply_variation_guidance(self, interaction_key: str) -> str:
        recent_openings = self._get_recent_reply_openings(interaction_key)
        if recent_openings:
            joined = " / ".join(f'"{item}"' for item in recent_openings)
            return (
                f"最近几次开头已经用过{joined}。这一轮换个切入点，"
                "必要时可以直接进入观察、判断或下一步，不要再复用这些固定句式。"
            )
        return (
            "开头尽量轮换，少用'我看了下''你现在像是在''可以试试'这类固定句式连着出现，"
            "如果没必要开场，直接切入最关键的信息。"
        )

    def _get_active_emotion_memory(self, interaction_key: str) -> dict[str, Any] | None:
        self._ensure_runtime_state()
        key = str(interaction_key or "").strip()
        if not key:
            return None

        memory = self.recent_emotion_memory.get(key)
        if not isinstance(memory, dict):
            return None

        now_ts = time.time()
        if (
            float(memory.get("expires_at", 0.0) or 0.0) <= now_ts
            or int(memory.get("remaining_turns", 0) or 0) <= 0
        ):
            self.recent_emotion_memory.pop(key, None)
            return None
        return memory

    def _remember_emotion_cue(
        self,
        interaction_key: str,
        *,
        label: str,
        detail: str,
        scene: str = "",
        active_window_title: str = "",
    ) -> dict[str, Any]:
        self._ensure_runtime_state()
        key = str(interaction_key or "").strip()
        if not key:
            return {}

        memory = {
            "label": str(label or "").strip(),
            "detail": self._truncate_preview_text(detail, limit=80),
            "scene": self._normalize_scene_label(scene),
            "window_title": self._normalize_window_title(active_window_title),
            "updated_at": time.time(),
            "expires_at": time.time() + self.SHORT_EMOTION_MEMORY_TTL_SECONDS,
            "remaining_turns": self.SHORT_EMOTION_MEMORY_TURNS,
        }
        self.recent_emotion_memory[key] = memory
        return memory

    def _consume_emotion_memory(self, interaction_key: str) -> None:
        memory = self._get_active_emotion_memory(interaction_key)
        if not memory:
            return

        remaining_turns = int(memory.get("remaining_turns", 0) or 0) - 1
        if remaining_turns <= 0:
            self.recent_emotion_memory.pop(str(interaction_key or "").strip(), None)
            return
        memory["remaining_turns"] = remaining_turns

    def _refresh_emotion_memory(
        self,
        interaction_key: str,
        *,
        contexts: list[str] | None = None,
        recognition_text: str = "",
        active_window_title: str = "",
        scene: str = "",
    ) -> dict[str, Any] | None:
        key = str(interaction_key or "").strip()
        if not key:
            return None

        user_contexts = [
            str(item or "").strip()
            for item in (contexts or [])
            if str(item or "").strip().startswith("用户:")
        ]
        recent_user_context = " ".join(user_contexts[-2:])
        combined_text = " ".join(
            part
            for part in (
                recent_user_context,
                str(recognition_text or "").strip(),
                str(active_window_title or "").strip(),
            )
            if part
        )
        normalized = self._normalize_record_text(combined_text)
        if normalized:
            detected_label = ""
            if any(
                keyword in normalized
                for keyword in (
                    "完成",
                    "搞定",
                    "解决了",
                    "成功",
                    "done",
                    "completed",
                    "resolved",
                    "passed",
                    "success",
                    "已提交",
                    "发布成功",
                    "导出完成",
                )
            ):
                detected_label = "完成"
            elif any(
                keyword in normalized
                for keyword in (
                    "卡住",
                    "卡关",
                    "报错",
                    "错误",
                    "失败",
                    "异常",
                    "没反应",
                    "stuck",
                    "error",
                    "exception",
                    "traceback",
                    "failed",
                    "bug",
                )
            ):
                detected_label = "卡关"
            elif any(
                keyword in normalized
                for keyword in (
                    "等下",
                    "稍等",
                    "等会",
                    "一会",
                    "先别",
                    "被打断",
                    "来电话",
                    "电话",
                    "开会",
                    "会议",
                    "先去",
                    "回来再",
                    "临时",
                    "突然",
                )
            ):
                detected_label = "被打断"

            if detected_label:
                return self._remember_emotion_cue(
                    key,
                    label=detected_label,
                    detail=combined_text,
                    scene=scene,
                    active_window_title=active_window_title,
                )

        return self._get_active_emotion_memory(key)

    def _build_emotion_continuity_guidance(self, emotion_memory: dict[str, Any] | None) -> str:
        if not isinstance(emotion_memory, dict):
            return ""

        label = str(emotion_memory.get("label", "") or "").strip()
        if label == "被打断":
            return (
                "用户刚刚像是被打断过，语气顺着刚才的节奏轻一点，"
                "别每次都像重新开场；如果这轮没有新帮助点，安静陪着也可以。"
            )
        if label == "卡关":
            return (
                "用户刚刚像是卡住过，继续一起排错的语气，先接住卡点，"
                "再给最有用的一步，不要突然变成空洞鼓励。"
            )
        if label == "完成":
            return (
                "用户刚刚像是完成了一段任务，语气可以带一点收尾后的松弛或确认感，"
                "但不要夸张祝贺，也不要像重新认识对方。"
            )
        return ""

    def _detect_sensitive_screen(
        self,
        *,
        scene: str,
        active_window_title: str,
        recognition_text: str = "",
    ) -> dict[str, str] | None:
        normalized_scene = self._normalize_scene_label(scene)
        title_lower = str(active_window_title or "").strip().lower()
        text_lower = str(recognition_text or "").strip().lower()
        combined_text = f"{title_lower} {text_lower}"

        checks = [
            (
                "payment",
                (
                    "支付",
                    "付款",
                    "转账",
                    "充值",
                    "支付宝",
                    "微信支付",
                    "pay",
                    "payment",
                    "checkout",
                    "信用卡",
                    "银行卡",
                    "验证码",
                    "密码",
                ),
            ),
            (
                "password",
                (
                    "密码管理器",
                    "密码管理",
                    "keepass",
                    "1password",
                    "bitwarden",
                    "lastpass",
                    "登录",
                    "登陆",
                    "login",
                    "signin",
                ),
            ),
            (
                "auth",
                (
                    "验证码",
                    "2fa",
                    "two-factor",
                    "二次验证",
                    "验证短信",
                    "手机验证码",
                ),
            ),
        ]

        chat_keywords = (
            "qq",
            "微信",
            "wechat",
            "telegram",
            "discord",
            "slack",
            "teams",
            "whatsapp",
        )
        if any(keyword in combined_text for keyword in chat_keywords):
            return {"category": "chat", "note": self.SENSITIVE_SKIP_NOTE}

        for category, keywords in checks:
            if any(keyword in combined_text for keyword in keywords):
                return {"category": category, "note": self.SENSITIVE_SKIP_NOTE}
        return None

    def _mark_sensitive_skip(
        self,
        *,
        analysis_trace: dict[str, Any],
        scene: str,
        active_window_title: str,
        sensitive_info: dict[str, str],
    ) -> None:
        note = str((sensitive_info or {}).get("note") or self.SENSITIVE_SKIP_NOTE).strip()
        observation_stored = self._add_observation(
            scene,
            note,
            active_window_title,
            extra={
                "analysis_trace": analysis_trace,
                "sensitive": sensitive_info,
                "reply": None,
            },
        )
        if observation_stored:
            logger.info(f"[屏幕助手] 敏感场景已记录，跳过回复: {note}")

    def _contains_rest_cue(self, text: str) -> bool:
        return contains_rest_cue(text)

    def _strip_repeated_companion_opening(self, text: str, *, has_recent_context: bool) -> str:
        return strip_repeated_companion_opening(
            text, has_recent_context=has_recent_context
        )

    def _strip_rest_cue_sentences(self, text: str) -> str:
        return strip_rest_cue_sentences(text)

    def _has_recent_rest_cue(
        self,
        contexts: list[str],
        *,
        task_id: str,
    ) -> bool:
        return has_recent_rest_cue(self, contexts, task_id=task_id)

    def _remember_screen_analysis_trace(self, trace: dict[str, Any] | None) -> None:
        if not isinstance(trace, dict):
            return

        cleaned = dict(trace)
        cleaned.setdefault("timestamp", datetime.datetime.now().isoformat())
        for key in (
            "task_id",
            "trigger_reason",
            "media_kind",
            "analysis_material_kind",
            "sampling_strategy",
            "recognition_summary",
            "reply_preview",
            "active_window_title",
            "scene",
            "status",
        ):
            cleaned[key] = str(cleaned.get(key, "") or "").strip()

        cleaned["stored_as_observation"] = bool(cleaned.get("stored_as_observation", False))
        cleaned["stored_in_diary"] = bool(cleaned.get("stored_in_diary", False))
        cleaned["memory_hints"] = list(cleaned.get("memory_hints", []) or [])[:4]
        cleaned["frame_labels"] = list(cleaned.get("frame_labels", []) or [])[:4]
        cleaned["frame_count"] = int(cleaned.get("frame_count", 0) or 0)
        cleaned["used_full_video"] = bool(cleaned.get("used_full_video", False))

        self.screen_analysis_traces.append(cleaned)
        if len(self.screen_analysis_traces) > self.SCREEN_TRACE_LIMIT:
            self.screen_analysis_traces = self.screen_analysis_traces[-self.SCREEN_TRACE_LIMIT :]

    def _get_recent_screen_analysis_traces(self, limit: int = 8) -> list[dict[str, Any]]:
        traces = list(getattr(self, "screen_analysis_traces", []) or [])
        if limit > 0:
            traces = traces[-limit:]
        return list(reversed(traces))

    @staticmethod
    def _format_runtime_timestamp(timestamp: float | int | None) -> str:
        try:
            value = float(timestamp or 0)
        except Exception:
            value = 0.0
        if value <= 0:
            return "未记录"
        return datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")

    def _adjust_interaction_frequency(self, user_response):
        """根据用户回应调整互动频率。"""
        # 简单估算参与度：结合回复长度与内容变化
        response_length = len(user_response)
        
        if response_length > 50:
            engagement = min(10, self.user_engagement + 1)
        elif response_length < 10:
            engagement = max(1, self.user_engagement - 1)
        else:
            engagement = self.user_engagement
        
        self.engagement_history.append(engagement)
        if len(self.engagement_history) > 10:
            self.engagement_history.pop(0)
        
        # 计算平均参与度
        avg_engagement = sum(self.engagement_history) / len(self.engagement_history)
        self.user_engagement = int(avg_engagement)
        
        # 根据参与度调整互动频率，参与度越高频率越高
        self.interaction_frequency = max(1, min(10, 5 + (self.user_engagement - 5) * 0.5))
        logger.info(f"用户参与度: {self.user_engagement}, 互动频率: {self.interaction_frequency}")



    async def stop(self):
        """Stop the plugin and cancel active tasks."""
        shutdown_lock = getattr(self, "_shutdown_lock", None)
        if shutdown_lock is None:
            self._shutdown_lock = asyncio.Lock()
            shutdown_lock = self._shutdown_lock

        async with shutdown_lock:
            if getattr(self, "_is_stopping", False):
                logger.info("插件关闭过程正在进行，跳过重复关闭请求")
                return

            self._is_stopping = True
            logger.info("开始停止插件并清理运行中的任务")

            try:
                self.running = False
                self.is_running = False
                self.state = "inactive"
                self.enable_mic_monitor = False
                self.window_companion_active_title = ""
                relay_task = getattr(self, "_remote_relay_task", None)
                if relay_task and not relay_task.done():
                    relay_task.cancel()
                    try:
                        await relay_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.debug(f"远程截图服务停止: {e}")
                self._remote_relay_task = None
                now_ts = time.time()
                if self.current_activity and self.activity_start_time:
                    self._append_activity_record(
                        activity=self.current_activity,
                        start_time=self.activity_start_time,
                        end_time=now_ts,
                        min_duration_seconds=self.LIVE_ACTIVITY_MIN_DURATION_SECONDS,
                    )
                    self.current_activity = None
                    self.activity_start_time = None
                
                # 停止 Web 服务器
                if self.web_server:
                    logger.info("正在停止 Web UI 服务器...")
                    await self.web_server.stop()
                    self.web_server = None
                    # 增加延迟时间，确保端口完全释放
                    await asyncio.sleep(1.0)
                await self._stop_recording_if_running()
                self.window_companion_active_target = ""
                self.window_companion_active_rule = {}

                await self._cancel_tasks(list(self.auto_tasks.values()), "自动任务")
                self.auto_tasks.clear()

                await self._cancel_tasks(list(self.temporary_tasks.values()), "临时任务")
                self.temporary_tasks.clear()

                await self._cancel_tasks(list(self.background_tasks), "后台任务")
                self.background_tasks.clear()

                await self._stop_webui()
                logger.info("插件停止完成，后台任务与 WebUI 已清理")
            finally:
                self._is_stopping = False

    def _check_dependencies(self, check_mic=False):
        return check_dependencies(self, check_mic=check_mic)

    def _check_env(self, check_mic=False):
        return check_env(self, check_mic=check_mic)

    def _generate_diary_image(self, diary_message: str) -> str:
        """将日记文本渲染为图片文件。"""
        return render_diary_message_to_png(diary_message)

    async def _capture_screen_bytes(self):
        """返回截图字节流与来源标签。"""
        return await capture_screen_bytes(self)

    async def _run_screen_relay_server(self) -> None:
        """监听 TCP，接收 Windows 端推送的截图（与旧版 screen_relay 协议兼容）。"""
        await run_screen_relay_server(self)

    async def _capture_screen_bytes_remote(self) -> tuple[bytes, str]:
        """远程模式：使用 Windows 端已推送的最新截图；若尚无数据则短暂等待。"""
        return await capture_screen_bytes_remote(self)

    async def _capture_recording_context(self) -> dict[str, Any]:
        return await capture_recording_context(self)

    async def _capture_screenshot_context(self) -> dict[str, Any]:
        return await capture_screenshot_context(self)

    async def _capture_latest_screen_anchor(
        self,
        *,
        fallback_window_title: str = "",
    ) -> tuple[bytes, str, str]:
        return await capture_latest_screen_anchor(self, fallback_window_title=fallback_window_title)

    async def _capture_one_shot_recording_context(
        self, duration_seconds: int | None = None
    ) -> dict[str, Any]:
        return await capture_one_shot_recording_context(self, duration_seconds)

    async def _capture_recognition_context(self) -> dict[str, Any]:
        return await capture_recognition_context(self)

    async def _capture_proactive_recognition_context(self) -> dict[str, Any]:
        return await capture_proactive_recognition_context(self)

    @staticmethod
    def _safe_unified_msg_origin(umo: str | None) -> str:
        if not (umo and str(umo).strip()):
            return ""
        s = str(umo).strip()
        return s if s.count(":") >= 2 else ""

    async def _run_screen_assist(
        self,
        event: AstrMessageEvent,
        task_id: str = "manual",
        custom_prompt: str = "",
        history_user_text: str = "/kp",
        capture_context: dict[str, Any] | None = None,
        capture_timeout: float | None = None,
        analysis_timeout: float | None = None,
    ) -> str | None:
        return await run_manual_screen_assist(
            self,
            event,
            task_id=task_id,
            custom_prompt=custom_prompt,
            history_user_text=history_user_text,
            capture_context=capture_context,
            capture_timeout=capture_timeout,
            analysis_timeout=analysis_timeout,
        )

    def _check_recording_env(self, check_mic: bool = False) -> tuple[bool, str]:
        return check_recording_env(self, check_mic=check_mic)

    def _check_screenshot_env(self, check_mic: bool = False) -> tuple[bool, str]:
        return check_screenshot_env(self, check_mic=check_mic)

    def _classify_browser_content(self, window_title: str) -> str:
        """根据浏览器窗口标题分类内容类型。"""
        return classify_browser_content(window_title)

    def _identify_scene(self, window_title: str) -> str:
        """Identify a coarse scene label from the current window title."""
        return identify_scene(window_title)

    def _get_time_prompt(self, allow_rest_hint: bool = False) -> str:
        """返回当前时间段对应的语气提示。"""
        now = datetime.datetime.now()
        hour = now.hour

        if 6 <= hour < 12:
            return "当前是早上，语气可以更清醒、轻快一些。"
        elif 12 <= hour < 18:
            return "当前是白天，建议以自然、直接、有帮助为主。"
        elif 18 <= hour < 22:
            return "当前是晚上，语气可以更放松，但建议仍要具体。"
        elif allow_rest_hint:
            return "当前已较晚，尽量低打扰、少用播报式开场；如本轮已明确触发休息提醒，可以顺带轻提一次，其余内容仍以当前任务为主。"
        else:
            return "当前已较晚，尽量低打扰、少用播报式开场；不要仅因为时间较晚就主动催用户休息或反复劝睡。"

    def _get_holiday_prompt(self) -> str:
        """获取节假日提示词。"""
        now = datetime.datetime.now()
        date = now.date()
        month = date.month
        day = date.day
        holidays = {
        }


        if (month, day) in holidays:
            holiday_prompt = holidays[(month, day)]
            logger.info(f"识别到节假日提示: {holiday_prompt}")
            return holiday_prompt
        return ""

    def _get_system_status_prompt(self) -> tuple:
        """获取系统状态提示词。"""
        system_prompt = ""
        system_high_load = False
        try:
            import psutil

            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            memory_percent = memory.percent

            battery = None
            if hasattr(psutil, "sensors_battery"):
                try:
                    battery = psutil.sensors_battery()
                except Exception as battery_error:
                    logger.debug(f"获取电池状态失败: {battery_error}")
            battery_threshold = getattr(self, "battery_threshold", 20)
            if battery and getattr(battery, "percent", None) is not None and battery.percent < battery_threshold:
                system_prompt += " 当前设备电量偏低，若建议涉及长时间操作，请顺手提醒保存进度。"

            memory_threshold = getattr(self, "memory_threshold", 80)
            if cpu_percent > 80 or memory_percent > memory_threshold:
                if system_prompt:
                    system_prompt += " "
                system_prompt += " 当前系统负载较高，请避免建议用户同时做太重的操作。"
                system_high_load = True
                logger.info(
                    f"系统资源使用过高: CPU={cpu_percent}%, 内存={memory_percent}%"
                )
        except ImportError:
            logger.debug("Debug event")
        except Exception as e:
            logger.debug(f"系统状态检测失败: {e}")
        return system_prompt, system_high_load

    async def _get_weather_prompt(
        self, target_date: datetime.date | None = None
    ) -> str:
        """获取天气提示词。"""
        weather_prompt = ""
        weather_api_key = self.weather_api_key
        weather_city = self.weather_city

        if weather_api_key and weather_city:
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    if target_date:
                        # 获取历史天气
                        timestamp = int(datetime.datetime.combine(target_date, datetime.datetime.min.time()).timestamp())
                        url = f"http://api.openweathermap.org/data/2.5/onecall/timemachine?lat={self.weather_lat}&lon={self.weather_lon}&dt={timestamp}&appid={weather_api_key}&units=metric&lang=zh_cn"
                    else:
                        # 获取当前天气
                        url = f"http://api.openweathermap.org/data/2.5/weather?q={weather_city}&appid={weather_api_key}&units=metric&lang=zh_cn"
                    
                    async with session.get(url) as response:
                        if response.status == 200:
                            weather_data = await response.json()
                            
                            if target_date:
                                # 解析历史天气数据
                                weather_main = weather_data.get("current", {}).get("weather", [{}])[0].get(
                                    "main", ""
                                )
                                weather_desc = weather_data.get("current", {}).get("weather", [{}])[0].get(
                                    "description", ""
                                )
                                temp = weather_data.get("current", {}).get("temp", 0)
                            else:
                                # 解析当前天气数据
                                weather_main = weather_data.get("weather", [{}])[0].get(
                                    "main", ""
                                )
                                weather_desc = weather_data.get("weather", [{}])[0].get(
                                    "description", ""
                                )
                                temp = weather_data.get("main", {}).get("temp", 0)

                            if target_date:
                                weather_prompt = f"当日天气 {weather_desc}，约 {temp}°C。"
                            else:
                                weather_prompt = f"当前天气 {weather_desc}，约 {temp}°C。"
                            
                            logger.info(f"天气信息获取成功: {weather_prompt}")
                        else:
                            logger.debug(f"获取天气信息失败: {response.status}")
            except Exception as e:
                logger.debug(f"天气感知失败: {e}")
        return weather_prompt

    async def _gather_screen_analysis_context(
        self,
        *,
        active_window_title: str,
        debug_mode: bool,
        allow_rest_hint: bool = False,
    ) -> dict[str, str]:
        return await gather_screen_analysis_context(
            self,
            active_window_title=active_window_title,
            debug_mode=debug_mode,
            allow_rest_hint=allow_rest_hint,
        )

    async def _collect_recent_conversation_context(
        self,
        session=None,
        *,
        debug_mode: bool,
    ) -> list[str]:
        return await collect_recent_conversation_context(self, session, debug_mode=debug_mode)

    def _remember_companion_outbound_for_umo(self, umo: Any, text: str) -> None:
        key = self._safe_unified_msg_origin(str(umo or "").strip())
        if not key:
            return
        body = str(text or "").strip()
        if not body:
            return
        snippet = self._truncate_preview_text(
            body, limit=self.companion_outbound_snippet_chars
        )
        if not snippet:
            return
        dq = self._companion_outbound_by_umo.setdefault(
            key, deque(maxlen=self.companion_outbound_memory_max)
        )
        dq.append(snippet)

    def _companion_outbound_context_lines(self, umo: Any) -> list[str]:
        key = self._safe_unified_msg_origin(str(umo or "").strip())
        if not key:
            return []
        dq = self._companion_outbound_by_umo.get(key)
        if not dq:
            return []
        return list(dq)

    async def _recognize_screen_material(
        self,
        *,
        capture_context: dict[str, Any],
        scene: str,
        active_window_title: str,
        session=None,
    ) -> str:
        return await recognize_screen_material(
            self,
            capture_context=capture_context,
            scene=scene,
            active_window_title=active_window_title,
            session=session,
        )

    async def _request_screen_interaction(
        self,
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
        return await request_screen_interaction(
            self,
            provider=provider,
            use_external_vision=use_external_vision,
            interaction_prompt=interaction_prompt,
            system_prompt=system_prompt,
            media_bytes=media_bytes,
            media_kind=media_kind,
            mime_type=mime_type,
            umo=umo,
        )

    async def _analyze_screen(
        self,
        capture_context: dict[str, Any],
        session=None,
        active_window_title: str = "",
        custom_prompt: str = "",
        task_id: str = "unknown",
    ) -> list[BaseMessageComponent]:
        """Analyze the current screenshot or recording context and generate a reply."""
        return await analyze_screen(
            self,
            capture_context,
            session=session,
            active_window_title=active_window_title,
            custom_prompt=custom_prompt,
            task_id=task_id,
        )

    @permission_type(PermissionType.ADMIN)
    @filter.command("kp")
    async def kp(self, event: AstrMessageEvent):
        """立即执行一次截图分析。"""
        async for x in run_kp(self, event):
            yield x

    @permission_type(PermissionType.ADMIN)
    @filter.command("kpr")
    async def kpr(self, event: AstrMessageEvent):
        """立即执行一次录屏分析。"""
        async for x in run_kpr(self, event):
            yield x

    @filter.event_message_type(filter.EventMessageType.ALL, priority=0)
    async def on_shared_activity_memory(self, event: AstrMessageEvent):
        """从用户明确提到的共同经历里学习。"""
        await run_on_shared_activity_memory(self, event)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def on_natural_language_screen_assist(self, event: AstrMessageEvent):
        """处理自然语言触发的识屏求助。"""
        async for x in run_natural_language_screen_assist(self, event):
            yield x

    @permission_type(PermissionType.ADMIN)
    @filter.command("kps")
    async def kps(self, event: AstrMessageEvent):
        """切换自动观察运行状态。"""
        async for x in run_kps(self, event):
            yield x

    @filter.command_group("kpi")
    def kpi_group(self):
        """管理自动观察屏幕任务。"""
        pass

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("ys")
    async def kpi_ys(self, event: AstrMessageEvent, preset_index: int | None = None):
        """切换预设。"""
        async for x in run_kpi_ys(self, event, preset_index):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("start")
    async def kpi_start(self, event: AstrMessageEvent):
        async for x in run_kpi_start(self, event):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("stop")
    async def kpi_stop(self, event: AstrMessageEvent, task_id: str | None = None):
        """停止自动观察任务。"""
        async for x in run_kpi_stop(self, event, task_id):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("status")
    async def kpi_status(self, event: AstrMessageEvent):
        """输出当前运行状态和关键诊断信息。"""
        async for x in run_kpi_status(self, event):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("list")
    async def kpi_list(self, event: AstrMessageEvent):
        """列出当前运行中的自动观察任务。"""
        async for x in run_kpi_list(self, event):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("ffmpeg")
    async def kpi_ffmpeg(self, event: AstrMessageEvent, ffmpeg_path: str | None = None):
        """设置 ffmpeg 路径并自动复制到插件数据目录。"""
        async for x in run_kpi_ffmpeg(self, event, ffmpeg_path):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("y")
    async def kpi_y(
        self,
        event: AstrMessageEvent,
        preset_index: int | None = None,
        interval: int | None = None,
        probability: int | None = None,
    ):
        """新增或修改自定义预设。"""
        async for x in run_kpi_y(self, event, preset_index, interval, probability):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("p")
    async def kpi_p(self, event: AstrMessageEvent):
        """列出全部自定义预设。"""
        async for x in run_kpi_p(self, event):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("add")
    async def kpi_add(self, event: AstrMessageEvent, interval: int, *prompt):
        """新增一个自定义观察任务。"""
        async for x in run_kpi_add(self, event, interval, *prompt):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("d")
    async def kpi_d(self, event: AstrMessageEvent, date: str | None = None):
        """查看指定日期的日记。"""
        async for x in run_kpi_d(self, event, date):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("correct")
    async def kpi_correct(self, event: AstrMessageEvent, *args):
        """纠正 Bot 的回复。"""
        async for x in run_kpi_correct(self, event, *args):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("preference")
    async def kpi_preference(self, event: AstrMessageEvent, category: str, *preference):
        """添加用户偏好。"""
        async for x in run_kpi_preference(self, event, category, *preference):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("recent")
    async def kpi_recent(self, event: AstrMessageEvent, days: int = 3):
        """查看最近几天的日记。"""
        async for x in run_kpi_recent(self, event, days):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("debug")
    async def kpi_debug(self, event: AstrMessageEvent, status: str | None = None):
        """切换调试模式 /kpi debug [on/off]"""
        async for x in run_kpi_debug(self, event, status):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("webui")
    async def kpi_webui(self, event: AstrMessageEvent, action: str = ""):
        """查看或控制 WebUI /kpi webui [start/stop]"""
        async for x in run_kpi_webui(self, event, action):
            yield x

    @permission_type(PermissionType.ADMIN)
    @kpi_group.command("cd")
    async def kpi_cd(self, event: AstrMessageEvent, date: str | None = None):
        """补写日记 /kpi cd [YYYYMMDD]"""
        async for x in run_kpi_cd(self, event, date):
            yield x

    def _is_in_active_time_range(self):
        """检查当前时间是否在活跃时间段内。"""
        # 使用配置中的活跃时间段
        time_range = self.active_time_range

        if not time_range:
            return True

        try:
            import datetime

            now = datetime.datetime.now().time()
            start_str, end_str = time_range.split("-")
            start_hour, start_minute = map(int, start_str.split(":"))
            end_hour, end_minute = map(int, end_str.split(":"))

            start_time = datetime.time(start_hour, start_minute)
            end_time = datetime.time(end_hour, end_minute)

            if start_time <= end_time:
                return start_time <= now <= end_time
            else:
                # 跨午夜的情况
                return now >= start_time or now <= end_time
        except Exception as e:
            logger.error(f"解析时间段失败: {e}")
            return True

    def _is_in_rest_time_range(self):
        """检查当前时间是否在休息时间段内。"""
        configured_range = self._get_configured_rest_range()
        if configured_range is None:
            return False

        try:
            now = datetime.datetime.now().time()
            start_minutes, end_minutes = configured_range
            inferred = self._infer_rest_behavior()
            effective_start_minutes = start_minutes
            inferred_rest_minutes = inferred.get("rest_extended_minutes")
            if inferred.get("available") and inferred_rest_minutes is not None:
                effective_start_minutes = int(inferred_rest_minutes) % (24 * 60)

            start_time = datetime.time(
                effective_start_minutes // 60,
                effective_start_minutes % 60,
            )
            end_time = datetime.time(end_minutes // 60, end_minutes % 60)

            if start_time <= end_time:
                return start_time <= now <= end_time
            else:
                # 跨午夜的情况
                return now >= start_time or now <= end_time
        except Exception as e:
            logger.error(f"解析休息时间段失败: {e}")
            return False

    def _is_in_rest_reminder_range(self):
        """检查当前是否应触发一次休息提醒。"""
        try:
            should_send, _ = self._should_send_rest_reminder()
            return should_send
        except Exception as e:
            logger.error(f"解析休息提醒时间段失败: {e}")
            return False

    def _add_diary_entry(self, content: str, active_window: str):
        """添加日记条目。"""
        return add_diary_entry(self, content, active_window)

    async def _generate_diary(self, target_date: datetime.date | None = None):
        """生成日记。"""
        await generate_diary(self, target_date=target_date)

    async def _start_webui(self):
        """启动 Web UI 服务器"""
        await start_webui(self)

    async def _stop_webui(self):
        """停止 Web UI 服务器"""
        await stop_webui(self)

    def _add_uncertainty(self, response):
        """为回复增加少量自然的不确定表达。"""
        # 不再添加不确定表达，直接返回原始回复
        return response

    def _polish_response_text(
        self,
        response_text,
        scene,
        *,
        contexts: list[str] | None = None,
        allow_rest_hint: bool = False,
        task_id: str = "",
    ):
        """清理沉浸感较差的播报式开场，尤其是视频和阅读场景。"""
        return polish_response_text(
            self,
            response_text,
            scene,
            contexts=contexts,
            allow_rest_hint=allow_rest_hint,
            task_id=task_id,
        )

    async def _task_scheduler(self):
        """后台任务调度器。"""
        self._ensure_runtime_state()
        while self.running and self._is_current_process_instance():
            try:
                    # 从队列中获取任务
                try:
                    task_func, task_args = await asyncio.wait_for(
                        self.task_queue.get(), timeout=1.0
                    )

                    # Run queued work under the task semaphore
                    async with self.task_semaphore:
                        try:
                            await task_func(*task_args)
                        except Exception as e:
                            logger.error(f"执行任务时出错: {e}")

                    # 标记任务完成
                    self.task_queue.task_done()
                except asyncio.TimeoutError:
                    # 超时，跳过检查running状态
                    pass
            except Exception as e:
                logger.error(f"任务调度器异常: {e}")
                await asyncio.sleep(1)

    def _parse_custom_tasks(self):
        """解析自定义定时监控任务。"""
        self.parsed_custom_tasks = []
        if not self.custom_tasks:
            return

        lines = self.custom_tasks.strip().split("\n")
        seen_tasks = set()  # 用于去重
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 解析时间和提示词
            parts = line.split(" ", 1)
            if len(parts) != 2:
                continue

            time_str, prompt = parts
            try:
                hour, minute = map(int, time_str.split(":"))
                if 0 <= hour < 24 and 0 <= minute < 60:
                    task_key = f"{hour}:{minute}:{prompt}"
                    # 去重：如果任务已存在，则跳过
                    if task_key in seen_tasks:
                        logger.warning(f"发现重复的自定义任务: {time_str} {prompt}，已跳过")
                        continue
                    seen_tasks.add(task_key)
                    self.parsed_custom_tasks.append(
                        {"hour": hour, "minute": minute, "prompt": prompt}
                    )
            except ValueError:
                pass

        logger.info(f"解析到 {len(self.parsed_custom_tasks)} 个自定义监控任务")

    def _resolve_microphone_input_device(self, pyaudio_instance):
        return resolve_microphone_input_device(self, pyaudio_instance)

    def _get_microphone_volume(self):
        """读取当前麦克风音量。"""
        return get_microphone_volume(self)

    def _ensure_mic_monitor_background_task(self) -> None:
        ensure_mic_monitor_background_task(self)

    def _stop_mic_monitor_background_task(self) -> None:
        stop_mic_monitor_background_task(self)

    async def _mic_monitor_task(self):
        """后台麦克风监听任务。"""
        await mic_monitor_task(self)

    async def _custom_tasks_task(self):
        """后台自定义任务调度循环。"""
        await run_custom_tasks_scheduler(self)

    async def _diary_task(self):
        """日记定时任务。"""
        await run_diary_scheduler(self)

    async def _auto_screen_task(
        self,
        event: AutoScreenSessionEvent,
        task_id: str = "default",
        custom_prompt: str = "",
        interval: int | None = None,
    ):
        """后台自动截图分析任务。

        参数:
        task_id: 任务 ID
        custom_prompt: 自定义提示词
        interval: 自定义检查间隔（秒）
        """
        await run_auto_screen_loop(
            self,
            event,
            task_id=task_id,
            custom_prompt=custom_prompt,
            interval=interval,
        )

    def _split_message(self, text: str, max_length: int = 1000) -> list[str]:
        """将较长文本拆分为适合发送的多段消息。"""
        segments = []
        current_segment = ""

        for line in text.split("\n"):
            if len(current_segment) + len(line) + 1 <= max_length:
                if current_segment:
                    current_segment += "\n" + line
                else:
                    current_segment = line
            else:
                if current_segment:
                    segments.append(current_segment)
                    current_segment = line
                else:
                    # 单行长度超过上限时，强制拆分
                    while len(line) > max_length:
                        segments.append(line[:max_length])
                        line = line[max_length:]
                    current_segment = line

        if current_segment:
            segments.append(current_segment)

        return segments
