import asyncio
import base64
import datetime
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace
from typing import Any

DEFAULT_SYSTEM_PROMPT = """
你是一个会陪用户一起看屏幕、一起推进当下任务的屏幕伙伴。
请自然、克制、具体地回应用户，优先给当前任务真正有帮助的观察、判断和建议，避免机械播报和空泛说教。
"""

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import BaseMessageComponent, Image, Plain
from astrbot.api.star import Context, Star, StarTools

from .web_server import WebServer
from .core.config import PluginConfig

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
        self._screen_recording_lock = asyncio.Lock()
        self._screen_recording_process = None
        self._screen_recording_path = ""
        self._recording_audio_device = None
        self._recording_ffmpeg_path = None
        self._recording_video_encoder = None
        self._recording_video_encoder_source = ""
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
        self._ensure_webui_password()

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
        self._load_corrections()
        
        # 窗口变化检测相关
        self.previous_windows = set()
        self.window_change_cooldown = 0
        self.window_timestamps = {}  # 记录窗口首次出现的时间戳
        self.auto_screen_runtime = {}
        self.recent_user_activity = {}
        self.screen_analysis_traces = []
        
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
        self._parse_user_preferences()

        # 加载学习数据
        if self.enable_learning:
            self._load_learning_data()

        self.task_semaphore = asyncio.Semaphore(2)  # 限制同时运行的任务数
        self.task_queue = asyncio.Queue()

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

        task = asyncio.create_task(self._mic_monitor_task())
        self.background_tasks.append(task)
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

    def _get_runtime_flag(self, name: str, default: bool = False) -> bool:
        return self._coerce_bool(getattr(self, name, default))

    def _sync_all_config(self) -> None:
        """将配置对象同步到插件运行时字段。"""
        # 同步基础配置
        self.bot_name = self.plugin_config.bot_name
        self.enabled = self._coerce_bool(self.plugin_config.enabled)
        self.interaction_mode = self.plugin_config.interaction_mode
        self.check_interval = self.plugin_config.check_interval
        self.trigger_probability = self.plugin_config.trigger_probability
        self.active_time_range = self.plugin_config.active_time_range
        self.use_companion_mode = self._coerce_bool(self.plugin_config.use_companion_mode)
        self.companion_prompt = getattr(self.plugin_config, 'companion_prompt', '你是用户的专属屏幕伙伴，专注于提供持续、自然的陪伴。请保持对话的连续性，关注用户的任务进展，提供具体、实用的建议。')
        self.capture_active_window = self._coerce_bool(self.plugin_config.capture_active_window)
        self.bot_vision_quality = self.plugin_config.bot_vision_quality
        self.screen_recognition_mode = self._normalize_screen_recognition_mode(
            getattr(
                self.plugin_config,
                "screen_recognition_mode",
                self.SCREENSHOT_MODE,
            )
        )
        self.image_prompt = self.plugin_config.image_prompt
        self.ffmpeg_path = getattr(self.plugin_config, "ffmpeg_path", "")
        self.recording_fps = max(
            0.01, float(getattr(self.plugin_config, "recording_fps", self.RECORDING_FPS) or self.RECORDING_FPS)
        )
        self.recording_duration_seconds = max(
            1,
            int(
                getattr(
                    self.plugin_config,
                    "recording_duration_seconds",
                    self.RECORDING_DURATION_SECONDS,
                )
                or self.RECORDING_DURATION_SECONDS
            ),
        )
        self.use_external_vision = self._coerce_bool(
            getattr(self.plugin_config, "use_external_vision", False)
        )
        self.allow_unsafe_video_direct_fallback = self._coerce_bool(
            getattr(self.plugin_config, "allow_unsafe_video_direct_fallback", False)
        )
        self.vision_api_url = self.plugin_config.vision_api_url
        self.vision_api_key = self.plugin_config.vision_api_key
        self.vision_api_model = self.plugin_config.vision_api_model
        # 同步备用视觉API配置
        self.vision_api_url_backup = getattr(self.plugin_config, 'vision_api_url_backup', None)
        self.vision_api_key_backup = getattr(self.plugin_config, 'vision_api_key_backup', None)
        self.vision_api_model_backup = getattr(self.plugin_config, 'vision_api_model_backup', None)
        self.user_preferences = self.plugin_config.user_preferences
        self.use_llm_for_start_end = self._coerce_bool(self.plugin_config.use_llm_for_start_end)
        self.start_preset = self.plugin_config.start_preset
        self.end_preset = self.plugin_config.end_preset
        self.start_llm_prompt = self.plugin_config.start_llm_prompt
        self.end_llm_prompt = self.plugin_config.end_llm_prompt
        self.enable_diary = self._coerce_bool(self.plugin_config.enable_diary)
        raw_diary_time = getattr(self.plugin_config, "diary_time", "00:00")
        normalized_diary_time = self._normalize_clock_text(
            raw_diary_time,
            default="00:00",
        )
        self.diary_time = normalized_diary_time
        if normalized_diary_time != raw_diary_time:
            self.plugin_config.diary_time = normalized_diary_time
        self.diary_storage = self.plugin_config.diary_storage
        self.diary_reference_days = self.plugin_config.diary_reference_days
        self.diary_auto_recall = self._coerce_bool(self.plugin_config.diary_auto_recall)
        self.diary_recall_time = self.plugin_config.diary_recall_time
        self.diary_send_as_image = self._coerce_bool(self.plugin_config.diary_send_as_image)
        self.diary_generation_prompt = self.plugin_config.diary_generation_prompt
        self.weather_api_key = self.plugin_config.weather_api_key
        self.weather_city = self.plugin_config.weather_city
        self.enable_mic_monitor = self._coerce_bool(self.plugin_config.enable_mic_monitor)
        self.mic_threshold = self.plugin_config.mic_threshold
        self.mic_check_interval = self.plugin_config.mic_check_interval
        self.admin_qq = self.plugin_config.admin_qq
        self.proactive_target = self.plugin_config.proactive_target
        self.save_local = self._coerce_bool(self.plugin_config.save_local)
        self.enable_natural_language_screen_assist = (
            self._coerce_bool(self.plugin_config.enable_natural_language_screen_assist)
        )
        self.enable_window_companion = self._coerce_bool(self.plugin_config.enable_window_companion)
        self.window_companion_targets = self.plugin_config.window_companion_targets
        self.window_companion_check_interval = (
            self.plugin_config.window_companion_check_interval
        )
        self.use_shared_screenshot_dir = self._coerce_bool(self.plugin_config.use_shared_screenshot_dir)
        self.shared_screenshot_dir = self.plugin_config.shared_screenshot_dir
        self.custom_tasks = self.plugin_config.custom_tasks
        self.rest_time_range = self.plugin_config.rest_time_range
        self.enable_learning = self._coerce_bool(self.plugin_config.enable_learning)
        self.learning_storage = self.plugin_config.learning_storage
        self.interaction_kpi = self.plugin_config.interaction_kpi
        self.debug = self._coerce_bool(self.plugin_config.debug)
        self.custom_presets = self.plugin_config.custom_presets
        self.current_preset_index = self.plugin_config.current_preset_index
        self._parse_custom_presets()
        # 确保预设索引有效
        if self.current_preset_index >= len(self.parsed_custom_presets):
            self.current_preset_index = -1
            self.plugin_config.current_preset_index = -1
        # 同步配置
        self.observation_storage = self.plugin_config.observation_storage
        self.max_observations = self.plugin_config.max_observations
        self.interaction_frequency = self.plugin_config.interaction_frequency
        self.image_quality = self.plugin_config.image_quality
        self.system_prompt = self.plugin_config.system_prompt
        self.bot_appearance = self.plugin_config.bot_appearance

        # 同步 WebUI 配置
        self.webui_enabled = self._coerce_bool(self.plugin_config.webui.enabled)
        self.webui_host = self.plugin_config.webui.host
        normalized_port = self._normalize_webui_port(self.plugin_config.webui.port)
        if normalized_port != self.plugin_config.webui.port:
            self.plugin_config.webui.port = normalized_port
            self.plugin_config.save_webui_config()
        # 确保使用标准化后的端口值
        self.webui_port = normalized_port
        self.webui_auth_enabled = self._coerce_bool(self.plugin_config.webui.auth_enabled)
        self.webui_password = self.plugin_config.webui.password
        self.webui_session_timeout = self.plugin_config.webui.session_timeout
        self.webui_allow_external_api = self._coerce_bool(self.plugin_config.webui.allow_external_api)
        self._parse_window_companion_targets()

    def _normalize_webui_port(self, port) -> int:
        try:
            normalized = int(port)
        except Exception:
            normalized = self.DEFAULT_WEBUI_PORT

        if normalized < 1 or normalized > 65535:
            logger.warning(
                f"WebUI 端口 {port} 不在有效范围内，已自动回退到 {self.DEFAULT_WEBUI_PORT}"
            )
            return self.DEFAULT_WEBUI_PORT
        elif normalized < 1024:
            logger.warning(
                f"WebUI 端口 {port} 是系统保留端口，可能需要管理员权限"
            )
        return normalized

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
        self.parsed_window_companion_targets = []
        raw_text = str(getattr(self, "window_companion_targets", "") or "").strip()
        if not raw_text:
            return self.parsed_window_companion_targets

        for line in raw_text.splitlines():
            entry = line.strip()
            if not entry:
                continue

            keyword, prompt = entry, ""
            if "|" in entry:
                keyword, prompt = entry.split("|", 1)

            keyword = keyword.strip()
            prompt = prompt.strip()
            if not keyword:
                continue

            self.parsed_window_companion_targets.append(
                {
                    "keyword": keyword,
                    "keyword_lower": keyword.casefold(),
                    "prompt": prompt,
                }
            )

        return self.parsed_window_companion_targets

    def _list_open_window_titles(self) -> list[str]:
        """Return de-duplicated open window titles."""
        try:
            import pygetwindow
        except ImportError:
            return []
        except Exception as e:
            logger.debug(f"读取窗口列表失败: {e}")
            return []

        raw_titles = []
        try:
            raw_titles = list(pygetwindow.getAllTitles())
        except Exception:
            try:
                raw_titles = [getattr(window, "title", "") for window in pygetwindow.getAllWindows()]
            except Exception as e:
                logger.debug(f"读取窗口标题失败: {e}")
                return []

        titles = []
        seen = set()
        for title in raw_titles:
            normalized = self._normalize_window_title(title)
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            titles.append(normalized)
        return titles

    def _match_window_companion_target(self, window_titles):
        """Find the first configured window companion rule that matches."""
        if not window_titles or not getattr(self, "parsed_window_companion_targets", None):
            return None, ""

        for rule in self.parsed_window_companion_targets:
            keyword = rule.get("keyword_lower", "")
            if not keyword:
                continue
            for title in window_titles:
                if keyword in str(title or "").casefold():
                    return rule, title
        return None, ""

    def _get_default_target(self) -> str:
        """Resolve the proactive message target."""
        target = str(getattr(self, "proactive_target", "") or "").strip()
        if target:
            return self._normalize_target(target)

        admin_qq = str(getattr(self, "admin_qq", "") or "").strip()
        if admin_qq:
            return self._build_private_target(admin_qq)
        return ""

    def _get_available_platforms(self) -> list[Any]:
        """Return loaded platform instances, preferring non-webchat adapters."""
        platform_manager = getattr(self.context, "platform_manager", None)
        if not platform_manager:
            return []

        platforms = list(getattr(platform_manager, "platform_insts", []) or [])
        if not platforms:
            return []

        filtered = []
        for platform in platforms:
            try:
                meta = platform.meta()
                if str(getattr(meta, "name", "") or "").strip() == "webchat":
                    continue
            except Exception:
                pass
            filtered.append(platform)
        return filtered or platforms

    def _get_preferred_platform_id(self) -> str:
        """Resolve the platform instance ID used for proactive messages."""
        platforms = self._get_available_platforms()
        if platforms:
            try:
                platform_id = str(getattr(platforms[0].meta(), "id", "") or "").strip()
                if platform_id:
                    return platform_id
            except Exception as e:
                logger.debug(f"获取默认平台 ID 失败: {e}")
        return "default"

    def _build_private_target(self, session_id: str) -> str:
        """Build a private-chat target with the active platform instance ID."""
        session_id = str(session_id or "").strip()
        if not session_id:
            return ""
        return f"{self._get_preferred_platform_id()}:FriendMessage:{session_id}"

    def _normalize_target(self, target: str) -> str:
        """Rewrite legacy proactive targets to the active platform instance ID."""
        target = str(target or "").strip()
        if not target:
            return ""

        parts = target.split(":", 2)
        if len(parts) != 3:
            return target

        platform_token, message_type, session_id = parts
        platforms = self._get_available_platforms()
        if not platforms:
            return target

        for platform in platforms:
            try:
                meta = platform.meta()
                platform_id = str(getattr(meta, "id", "") or "").strip()
                platform_name = str(getattr(meta, "name", "") or "").strip()
            except Exception:
                continue

            if platform_token in {platform_id, platform_name}:
                normalized = f"{platform_id}:{message_type}:{session_id}"
                if normalized != target:
                    logger.info(f"主动消息目标已规范化: {target} -> {normalized}")
                return normalized

        legacy_platform_tokens = {
            "default",
            "aiocqhttp",
            "qq_official",
            "qq_official_webhook",
            "telegram",
            "discord",
            "wecom",
            "wecom_ai_bot",
            "weixin_official_account",
            "line",
            "kook",
            "satori",
            "lark",
            "dingtalk",
            "misskey",
            "slack",
        }
        if len(platforms) == 1 and platform_token in legacy_platform_tokens:
            try:
                platform_id = str(getattr(platforms[0].meta(), "id", "") or "").strip()
            except Exception:
                platform_id = ""
            if platform_id:
                normalized = f"{platform_id}:{message_type}:{session_id}"
                if normalized != target:
                    logger.info(f"主动消息目标已回退到当前平台实例 ID: {target} -> {normalized}")
                return normalized

        return target

    def _create_virtual_event(self, target: str):
        """Build a lightweight virtual event for proactive tasks."""
        event = type("VirtualEvent", (), {})()
        event.unified_msg_origin = self._normalize_target(target)
        event.config = self.plugin_config
        return event

    async def _send_proactive_message(
        self, target: str, message_chain: MessageChain
    ) -> bool:
        """Send a proactive message via the resolved platform instance."""
        target = self._normalize_target(target)
        if not target:
            return False

        session = None
        try:
            from astrbot.core.platform.message_session import MessageSesion

            session = MessageSesion.from_str(target)
        except Exception as e:
            logger.debug(f"解析主动消息目标失败，将回退到 context.send_message: {e}")

        if session is not None:
            platforms = self._get_available_platforms()
            matched_platform = None
            for platform in platforms:
                try:
                    meta = platform.meta()
                    platform_id = str(getattr(meta, "id", "") or "").strip()
                    platform_name = str(getattr(meta, "name", "") or "").strip()
                except Exception:
                    continue
                if session.platform_name in {platform_id, platform_name}:
                    matched_platform = platform
                    if session.platform_name != platform_id:
                        session = MessageSesion(
                            platform_id, session.message_type, session.session_id
                        )
                    break

            if matched_platform is None and platforms:
                matched_platform = platforms[0]
                try:
                    fallback_platform_id = str(
                        getattr(matched_platform.meta(), "id", "") or ""
                    ).strip()
                    if fallback_platform_id:
                        session = MessageSesion(
                            fallback_platform_id,
                            session.message_type,
                            session.session_id,
                        )
                        logger.info(
                            f"主动消息目标未命中平台，已回退为 {fallback_platform_id}:{session.message_type.value}:{session.session_id}"
                        )
                except Exception as e:
                    logger.debug(f"构造主动消息回退会话失败: {e}")

            if matched_platform is not None:
                try:
                    await matched_platform.send_by_session(session, message_chain)
                    return True
                except Exception as e:
                    logger.warning(f"主动消息直发失败，将回退到 context.send_message: {e}")

        try:
            await self.context.send_message(target, message_chain)
            return True
        except Exception as e:
            logger.error(f"发送主动消息失败: {e}")
            return False

    async def _send_plain_message(self, target: str, text: str) -> bool:
        """Send a plain proactive message if possible."""
        target = str(target or "").strip()
        text = str(text or "").strip()
        if not target or not text:
            return False

        return await self._send_proactive_message(
            target, MessageChain([Plain(text)])
        )

    def _resolve_proactive_target(self, fallback_event: Any = None) -> str:
        target = self._get_default_target()
        if not target and fallback_event is not None:
            try:
                target = str(getattr(fallback_event, "unified_msg_origin", "") or "").strip()
            except Exception as e:
                logger.debug(f"读取回退主动消息目标失败: {e}")
        return self._normalize_target(target)

    def _build_message_chain(
        self, components: list[BaseMessageComponent] | None
    ) -> MessageChain:
        chain = MessageChain()
        for comp in components or []:
            chain.chain.append(comp)
        return chain

    def _extract_plain_text(
        self, components: list[BaseMessageComponent] | None
    ) -> str:
        chunks: list[str] = []
        for comp in components or []:
            if isinstance(comp, Plain):
                text = str(getattr(comp, "text", "") or "")
                if text:
                    chunks.append(text)
        return "".join(chunks)

    async def _send_component_text(
        self,
        target: str,
        components: list[BaseMessageComponent] | None,
        *,
        prefix: str = "",
    ) -> bool:
        text = self._extract_plain_text(components)
        if not text:
            return False
        if prefix:
            text = f"{prefix}\n{text}"
        return await self._send_plain_message(target, text)

    async def _send_segmented_text(
        self,
        target: str,
        text: str,
        *,
        max_length: int = 1000,
        delay_seconds: float = 0.5,
        should_continue: Any = None,
    ) -> bool:
        target = str(target or "").strip()
        text = str(text or "").strip()
        if not target or not text:
            return False

        segments = self._split_message(text, max_length=max_length)
        if not segments:
            return False

        sent = False
        for index, segment in enumerate(segments):
            if should_continue is not None and not should_continue():
                break
            if not segment.strip():
                continue
            sent = await self._send_plain_message(target, segment) or sent
            if index < len(segments) - 1 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
        return sent

    def _build_window_companion_prompt(self, window_title: str, extra_prompt: str = "") -> str:
        """Build a focused prompt for window companion sessions."""
        pieces = [
            f"这是你被指定要陪伴的窗口：《{window_title}》。",
            "请更关注这个窗口里的当前任务、卡点和下一步，不要泛泛播报画面。",
            "如果适合给建议，优先给和当前任务直接相关、能立刻派上用场的建议。",
            "保持对话的连续性，关注用户的任务进展，提供具体的建议。",
            "注意观察窗口内容的变化，及时调整你的回应，确保与当前场景相关。",
            "如果发现用户遇到困难，提供具体的解决方案和步骤指导。",
        ]
        if extra_prompt:
            pieces.append(extra_prompt.strip())
        return "\n".join(piece for piece in pieces if piece)

    def _is_window_companion_session_active(self) -> bool:
        task = (getattr(self, "auto_tasks", {}) or {}).get(
            getattr(self, "WINDOW_COMPANION_TASK_ID", "")
        )
        return bool(task and not task.done())

    async def _start_window_companion_session(self, window_title: str, rule: dict) -> bool:
        """Start automatic companion mode for a matched window."""
        self._ensure_runtime_state()
        if not self.enabled or not self.enable_window_companion:
            return False
        if self._is_window_companion_session_active():
            return False

        target = self._get_default_target()
        if not target:
            logger.warning("窗口陪伴已匹配到目标窗口，但没有可用的主动消息目标，已跳过启动")
            return False

        ok, err_msg = self._check_env(check_mic=False)
        if not ok:
            logger.warning(f"窗口陪伴启动失败: {err_msg}")
            return False

        event = self._create_virtual_event(target)
        self.window_companion_active_title = window_title
        self.window_companion_active_target = target
        self.window_companion_active_rule = dict(rule or {})
        self.is_running = True
        self.state = "active"
        self.auto_tasks[self.WINDOW_COMPANION_TASK_ID] = asyncio.create_task(
            self._auto_screen_task(
                event,
                task_id=self.WINDOW_COMPANION_TASK_ID,
                custom_prompt=self._build_window_companion_prompt(
                    window_title, (rule or {}).get("prompt", "")
                ),
            )
        )

        start_response = await self._get_start_response(target)
        intro = f"检测到《{window_title}》已经打开，我来陪你。"
        await self._send_plain_message(target, f"{intro}\n{start_response}".strip())
        logger.info(f"窗口陪伴已启动: {window_title}")
        return True

    async def _stop_window_companion_session(self, reason: str = "window_closed") -> bool:
        """Stop the automatic companion session for the matched window."""
        self._ensure_runtime_state()
        task_id = getattr(self, "WINDOW_COMPANION_TASK_ID", "")
        task = (getattr(self, "auto_tasks", {}) or {}).get(task_id)
        if not task and not getattr(self, "window_companion_active_title", ""):
            return False

        active_title = str(getattr(self, "window_companion_active_title", "") or "").strip()
        target = str(getattr(self, "window_companion_active_target", "") or "").strip()

        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("等待窗口陪伴任务停止超时")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"停止窗口陪伴任务失败: {e}")

        self.auto_tasks.pop(task_id, None)
        self.window_companion_active_title = ""
        self.window_companion_active_target = ""
        self.window_companion_active_rule = {}

        if not self.auto_tasks:
            self.is_running = False
            self.state = "inactive"

        if target and active_title:
            end_response = await self._get_end_response(target)
            if reason == "disabled":
                outro = f"《{active_title}》的窗口陪伴已经关闭，我先退到旁边。"
            else:
                outro = f"《{active_title}》已经关掉了，我先退到旁边。"
            await self._send_plain_message(target, f"{outro}\n{end_response}".strip())

        logger.info(f"窗口陪伴已停止: {active_title or 'unknown'} ({reason})")
        return True

    async def _window_companion_task(self):
        """Watch configured windows and start or stop companion sessions automatically."""
        self._ensure_runtime_state()
        while self.running and self._is_current_process_instance():
            interval = max(2, int(getattr(self, "window_companion_check_interval", 5) or 5))
            try:
                if not self.enable_window_companion or not getattr(
                    self, "parsed_window_companion_targets", None
                ):
                    if self._is_window_companion_session_active() or getattr(
                        self, "window_companion_active_title", ""
                    ):
                        await self._stop_window_companion_session(reason="disabled")
                    await asyncio.sleep(interval)
                    continue

                window_titles = self._list_open_window_titles()
                matched_rule, matched_title = self._match_window_companion_target(window_titles)
                active_title = str(getattr(self, "window_companion_active_title", "") or "").strip()
                active_exists = bool(
                    active_title
                    and any(active_title.casefold() == title.casefold() for title in window_titles)
                )

                if matched_rule and matched_title and not self._is_window_companion_session_active():
                    await self._start_window_companion_session(matched_title, matched_rule)
                elif self._is_window_companion_session_active() and not active_exists:
                    await self._stop_window_companion_session(reason="window_closed")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"窗口陪伴监测异常: {e}")

            await asyncio.sleep(interval)

    def _ensure_webui_password(self) -> bool:
        """确保 WebUI 在需要认证时拥有可用密码。"""
        # 检查密码是否已经设置
        current_password = str(self.plugin_config.webui.password or "").strip()
        # 仅当开启认证且密码为空时，自动生成密码
        if (
            self.plugin_config.webui.enabled
            and self.plugin_config.webui.auth_enabled
            and not current_password
        ):
            # 生成随机密码
            generated = f"{secrets.randbelow(1000000):06d}"
            # 保存密码
            self.plugin_config.webui.password = generated
            self.plugin_config.save_webui_config()
            logger.info(f"WebUI 访问密码已自动生成: {generated}")
            logger.info("请在配置中查看或修改此密码")
            return True
        return False

    def _snapshot_webui_runtime(self) -> tuple[bool, str, int, str, int]:
        """返回当前 WebUI 运行时快照。"""
        return (
            getattr(self, "webui_enabled", False),
            getattr(self, "webui_host", "0.0.0.0"),
            getattr(self, "webui_port", 8898),
            getattr(self, "webui_password", ""),
            getattr(self, "webui_session_timeout", 3600),
        )

    def _is_webui_runtime_changed(
        self, old_state: tuple[bool, str, int, str, int]
    ) -> bool:
        return old_state != self._snapshot_webui_runtime()

    async def _restart_webui(self) -> None:
        self._ensure_runtime_state()
        webui_lock = getattr(self, "_webui_lock", None)
        if webui_lock is None:
            self._webui_lock = asyncio.Lock()
            webui_lock = self._webui_lock

        async with webui_lock:
            logger.info("检测到 WebUI 配置变更，正在重启 WebUI...")

            if not self.webui_enabled:
                # WebUI 已禁用，停止旧服务即可
                if self.web_server:
                    await self.web_server.stop()
                    self.web_server = None
                    await asyncio.sleep(0.6)
                return

            # 保存旧服务引用，在新服务启动成功后再停止
            old_server = self.web_server

            try:
                new_server = WebServer(self, host=self.webui_host, port=self.webui_port)
                success = await new_server.start()
                if success:
                    # 新服务启动成功，更新引用并停止旧服务
                    self.web_server = new_server
                    if old_server:
                        try:
                            await old_server.stop()
                            await asyncio.sleep(0.6)
                        except Exception as e:
                            logger.warning(f"停止旧 WebUI 服务时出错: {e}")
                    logger.info("WebUI 重启成功")
                else:
                    self.web_server = None
                    logger.error(
                        f"WebUI 重启失败，原因: 无法绑定 {self.webui_host}:{self.webui_port}"
                    )
                    # 启动失败，恢复旧服务引用
                    if old_server and self.web_server != old_server:
                        self.web_server = old_server
                        logger.info("已恢复旧的 WebUI 服务")
            except Exception as e:
                self.web_server = None
                logger.error(f"重启 WebUI 失败: {e}")
                # 启动失败，恢复旧服务引用
                if old_server and self.web_server != old_server:
                    self.web_server = old_server
                    logger.info("已恢复旧的 WebUI 服务")

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
                self._apply_plugin_config_updates(config_dict)

                self._sync_all_config()

                # 检查是否明确设置了空密码
                password_set_to_empty = False
                if "webui" in config_dict and isinstance(config_dict["webui"], dict):
                    password_set_to_empty = "password" in config_dict["webui"] and not str(config_dict["webui"]["password"] or "").strip()
                elif "webui_password" in config_dict:
                    password_set_to_empty = not str(config_dict["webui_password"] or "").strip()
                
                # 只有未显式清空密码时，才自动补齐密码
                if not password_set_to_empty and self._ensure_webui_password():
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
        current = now or datetime.datetime.now()
        target_date = current.date()
        if current.hour < max(0, int(early_morning_cutoff_hour)):
            target_date -= datetime.timedelta(days=1)
        return target_date

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
            use_external_vision = self._get_runtime_flag("use_external_vision")
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
        if not hasattr(self, "auto_tasks") or self.auto_tasks is None:
            self.auto_tasks = {}
        if not hasattr(self, "temporary_tasks") or self.temporary_tasks is None:
            self.temporary_tasks = {}
        if not hasattr(self, "background_tasks") or self.background_tasks is None:
            self.background_tasks = []
        if not hasattr(self, "active_tasks") or self.active_tasks is None:
            self.active_tasks = {}
        if not hasattr(self, "last_task_execution") or self.last_task_execution is None:
            self.last_task_execution = {}
        if not hasattr(self, "task_counter"):
            self.task_counter = 0
        if not hasattr(self, "is_running"):
            self.is_running = False
        if not hasattr(self, "running"):
            self.running = True
        if not hasattr(self, "state"):
            self.state = "inactive"
        if not hasattr(self, "web_server"):
            self.web_server = None
        if not hasattr(self, "task_semaphore") or self.task_semaphore is None:
            self.task_semaphore = asyncio.Semaphore(2)
        if not hasattr(self, "task_queue") or self.task_queue is None:
            self.task_queue = asyncio.Queue()
        if not hasattr(self, "_shutdown_lock") or self._shutdown_lock is None:
            self._shutdown_lock = asyncio.Lock()
        if not hasattr(self, "_webui_lock") or self._webui_lock is None:
            self._webui_lock = asyncio.Lock()
        if not hasattr(self, "_is_stopping"):
            self._is_stopping = False
        if not hasattr(self, "_screen_assist_cooldowns") or self._screen_assist_cooldowns is None:
            self._screen_assist_cooldowns = {}
        if not hasattr(self, "last_shared_activity_invite_time"):
            self.last_shared_activity_invite_time = 0.0
        if not hasattr(self, "previous_windows") or self.previous_windows is None:
            self.previous_windows = set()
        if not hasattr(self, "window_change_cooldown"):
            self.window_change_cooldown = 0
        if not hasattr(self, "window_timestamps") or self.window_timestamps is None:
            self.window_timestamps = {}
        if not hasattr(self, "auto_screen_runtime") or self.auto_screen_runtime is None:
            self.auto_screen_runtime = {}
        if not hasattr(self, "recent_user_activity") or self.recent_user_activity is None:
            self.recent_user_activity = {}
        if not hasattr(self, "screen_analysis_traces") or self.screen_analysis_traces is None:
            self.screen_analysis_traces = []
        if not hasattr(self, "_instance_token"):
            self._instance_token = ""
        if not hasattr(self, "_screen_analysis_failure_count"):
            self._screen_analysis_failure_count = 0
        if not hasattr(self, "_screen_analysis_backoff_until"):
            self._screen_analysis_backoff_until = 0.0
        if not hasattr(self, "window_companion_active_title"):
            self.window_companion_active_title = ""
        if not hasattr(self, "window_companion_active_target"):
            self.window_companion_active_target = ""
        if not hasattr(self, "window_companion_active_rule") or self.window_companion_active_rule is None:
            self.window_companion_active_rule = {}
        if not hasattr(self, "last_rest_reminder_time"):
            self.last_rest_reminder_time = None
        if not hasattr(self, "last_rest_reminder_day"):
            self.last_rest_reminder_day = ""
        if not hasattr(self, "rest_reminder_state_file"):
            self.rest_reminder_state_file = os.path.join(
                self.learning_storage,
                "rest_reminder_state.json",
            )
        self._ensure_recording_runtime_state()

    def _ensure_recording_runtime_state(self) -> None:
        if not hasattr(self, "_screen_recording_lock") or self._screen_recording_lock is None:
            self._screen_recording_lock = asyncio.Lock()
        if not hasattr(self, "_screen_recording_process"):
            self._screen_recording_process = None
        if not hasattr(self, "_screen_recording_path"):
            self._screen_recording_path = ""
        if not hasattr(self, "_recording_audio_device"):
            self._recording_audio_device = None
        if not hasattr(self, "_recording_ffmpeg_path"):
            self._recording_ffmpeg_path = None
        if not hasattr(self, "_recording_video_encoder"):
            self._recording_video_encoder = None
        if not hasattr(self, "_recording_video_encoder_source"):
            self._recording_video_encoder_source = ""

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
        return max(0.01, float(getattr(self, "recording_fps", self.RECORDING_FPS) or self.RECORDING_FPS))

    def _get_recording_duration_seconds(self) -> int:
        return max(
            1,
            int(
                getattr(
                    self,
                    "recording_duration_seconds",
                    self.RECORDING_DURATION_SECONDS,
                )
                or self.RECORDING_DURATION_SECONDS
            ),
        )

    def _get_ffmpeg_path(self) -> str:
        self._ensure_recording_runtime_state()
        cached_path = getattr(self, "_recording_ffmpeg_path", None)
        if cached_path and os.path.exists(cached_path):
            return cached_path

        candidate_paths: list[str] = []

        configured_path = str(getattr(self, "ffmpeg_path", "") or "").strip()
        if configured_path:
            candidate_paths.append(configured_path)

        data_ffmpeg_dir = self._get_ffmpeg_storage_dir()
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        candidate_paths.extend(
            [
                os.path.join(data_ffmpeg_dir, "ffmpeg.exe"),
                os.path.join(data_ffmpeg_dir, "ffmpeg"),
                os.path.join(plugin_dir, "bin", "ffmpeg.exe"),
                os.path.join(plugin_dir, "bin", "ffmpeg"),
                os.path.join(plugin_dir, "ffmpeg.exe"),
                os.path.join(plugin_dir, "ffmpeg"),
            ]
        )

        for candidate in candidate_paths:
            normalized = os.path.abspath(os.path.expanduser(candidate))
            if os.path.isfile(normalized):
                self._recording_ffmpeg_path = normalized
                return normalized

        ffmpeg_path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe") or ""
        self._recording_ffmpeg_path = ffmpeg_path or None
        return ffmpeg_path

    def _get_ffmpeg_storage_dir(self, create: bool = False) -> str:
        data_dir = str(getattr(self.plugin_config, "data_dir", "") or "").strip()
        if data_dir:
            ffmpeg_dir = os.path.join(data_dir, "bin")
        else:
            ffmpeg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
        if create:
            os.makedirs(ffmpeg_dir, exist_ok=True)
        return ffmpeg_dir

    def _get_recording_video_encoder(self) -> str:
        self._ensure_recording_runtime_state()
        ffmpeg_path = self._get_ffmpeg_path()
        if not ffmpeg_path:
            return "libx264"

        cached_encoder = str(getattr(self, "_recording_video_encoder", "") or "").strip()
        cached_source = str(getattr(self, "_recording_video_encoder_source", "") or "").strip()
        if cached_encoder and cached_source == ffmpeg_path:
            return cached_encoder

        encoder = "mpeg4"
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                [ffmpeg_path, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=12,
                creationflags=creationflags,
            )
            output = "\n".join(
                piece for piece in [result.stdout or "", result.stderr or ""] if piece
            )
            if "libx264" in output:
                encoder = "libx264"
        except Exception as e:
            logger.debug(f"检测 ffmpeg 编码器失败，将使用兼容编码器: {e}")

        self._recording_video_encoder = encoder
        self._recording_video_encoder_source = ffmpeg_path
        return encoder

    def _build_recording_video_args(self) -> list[str]:
        encoder = self._get_recording_video_encoder()
        args = ["-c:v", encoder]
        if encoder == "libx264":
            args.extend(["-preset", "ultrafast", "-crf", "32"])
        else:
            args.extend(["-q:v", "7"])
        args.extend(["-pix_fmt", "yuv420p"])
        return args

    @staticmethod
    def _build_evenly_spaced_indices(total_count: int, sample_count: int) -> list[int]:
        total = max(0, int(total_count or 0))
        target = max(1, int(sample_count or 1))
        if total <= 0:
            return []
        if total <= target:
            return list(range(total))
        if target == 1:
            return [total // 2]

        last_index = total - 1
        indices = []
        for position in range(target):
            ratio = position / max(1, target - 1)
            indices.append(int(round(last_index * ratio)))
        return sorted(set(max(0, min(last_index, value)) for value in indices))

    @staticmethod
    def _build_sample_frame_labels(total_count: int, chosen_indices: list[int]) -> list[str]:
        if not chosen_indices:
            return []
        if len(chosen_indices) == 1:
            return ["中段"]
        if len(chosen_indices) == 2:
            return ["开头", "结尾"]
        if len(chosen_indices) == 3:
            return ["开头", "中段", "结尾"]

        labels = []
        last_index = max(1, int(total_count) - 1)
        for index, frame_index in enumerate(chosen_indices):
            if index == 0:
                labels.append("开头")
                continue
            if index == len(chosen_indices) - 1:
                labels.append("结尾")
                continue
            percent = int(round((frame_index / last_index) * 100))
            labels.append(f"{percent}%")
        return labels

    def _get_video_sampling_plan(
        self,
        scene: str,
        *,
        duration_seconds: int,
        use_external_vision: bool,
    ) -> dict[str, Any]:
        normalized_duration = max(1, int(duration_seconds or self._get_recording_duration_seconds()))
        profile = self._get_scene_behavior_profile(scene)
        category = str(profile.get("category", "general") or "general")

        if normalized_duration <= 8:
            sample_count = 3
        elif normalized_duration <= 15:
            sample_count = 4
        elif normalized_duration <= 25:
            sample_count = 5
        else:
            sample_count = 6

        if category == "entertainment":
            sample_count = min(6, sample_count + 1)
        elif category == "work":
            sample_count = max(3, sample_count - 1)

        if use_external_vision:
            sample_count = max(sample_count, 4)

        if sample_count <= 3:
            sampling_strategy = "keyframe_sheet"
        elif category == "entertainment":
            sampling_strategy = "timeline_sheet_dense"
        elif category == "work":
            sampling_strategy = "timeline_sheet_compact"
        else:
            sampling_strategy = "timeline_sheet"

        return {
            "sample_count": sample_count,
            "sampling_strategy": sampling_strategy,
            "duration_seconds": normalized_duration,
            "scene_category": category,
        }

    def _extract_video_sample_sheet_sync(
        self,
        video_bytes: bytes,
        *,
        sample_count: int = 3,
        sampling_strategy: str = "keyframe_sheet",
        latest_frame_bytes: bytes | None = None,
    ) -> dict[str, Any] | None:
        ffmpeg_path = self._get_ffmpeg_path()
        if not ffmpeg_path or not video_bytes:
            return None

        from PIL import Image, ImageDraw, ImageFont

        with tempfile.TemporaryDirectory(prefix="screen_companion_sample_") as temp_dir:
            input_path = os.path.join(temp_dir, "input.mp4")
            with open(input_path, "wb") as f:
                f.write(video_bytes)

            frame_pattern = os.path.join(temp_dir, "frame_%03d.jpg")
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    input_path,
                    "-vf",
                    "fps=1",
                    frame_pattern,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=20,
                creationflags=creationflags,
            )
            if result.returncode != 0:
                return None

            frame_paths = sorted(
                os.path.join(temp_dir, filename)
                for filename in os.listdir(temp_dir)
                if filename.startswith("frame_") and filename.endswith(".jpg")
            )
            if not frame_paths:
                return None

            chosen_indices = self._build_evenly_spaced_indices(
                len(frame_paths),
                sample_count,
            )
            chosen_paths = [frame_paths[index] for index in chosen_indices]
            frame_labels = self._build_sample_frame_labels(len(frame_paths), chosen_indices)
            frames = []
            for index, frame_path in enumerate(chosen_paths):
                with Image.open(frame_path) as image:
                    frame = image.convert("RGB")
                    label = frame_labels[min(index, len(frame_labels) - 1)]
                    frames.append((label, frame.copy()))

            has_live_anchor_frame = False
            if latest_frame_bytes:
                try:
                    with Image.open(io.BytesIO(latest_frame_bytes)) as latest_image:
                        frames.append(("现在", latest_image.convert("RGB").copy()))
                        has_live_anchor_frame = True
                except Exception:
                    has_live_anchor_frame = False

            if not frames:
                return None

            target_width = min(960, max(frame.width for _, frame in frames))
            padding = 18
            gap = 12
            label_height = 34
            resized_frames = []
            for label, frame in frames:
                scale = target_width / max(1, frame.width)
                target_height = max(1, int(frame.height * scale))
                resized_frames.append(
                    (
                        label,
                        frame.resize((target_width, target_height)),
                    )
                )

            total_height = padding * 2 + sum(frame.height + label_height for _, frame in resized_frames) + gap * max(0, len(resized_frames) - 1)
            canvas = Image.new("RGB", (target_width + padding * 2, total_height), "#111418")
            draw = ImageDraw.Draw(canvas)
            try:
                font = ImageFont.truetype("msyh.ttc", 18)
            except Exception:
                font = ImageFont.load_default()

            current_y = padding
            for label, frame in resized_frames:
                draw.rounded_rectangle(
                    (padding, current_y, padding + target_width, current_y + label_height - 8),
                    radius=10,
                    fill="#1d232c",
                )
                draw.text(
                    (padding + 12, current_y + 5),
                    f"{label}关键帧",
                    fill="#f4f7fb",
                    font=font,
                )
                current_y += label_height
                canvas.paste(frame, (padding, current_y))
                current_y += frame.height + gap

            buffer = io.BytesIO()
            canvas.save(buffer, format="JPEG", quality=86)
            return {
                "media_kind": "image",
                "mime_type": "image/jpeg",
                "media_bytes": buffer.getvalue(),
                "frame_count": len(resized_frames),
                "frame_labels": [label for label, _ in resized_frames],
                "sampling_strategy": sampling_strategy,
                "has_live_anchor_frame": has_live_anchor_frame,
            }

    async def _build_video_sample_capture_context(
        self,
        capture_context: dict[str, Any],
        *,
        scene: str,
        use_external_vision: bool,
    ) -> dict[str, Any] | None:
        media_bytes = capture_context.get("media_bytes", b"") or b""
        duration_seconds = int(
            capture_context.get("duration_seconds", 0) or self._get_recording_duration_seconds()
        )
        sampling_plan = self._get_video_sampling_plan(
            scene,
            duration_seconds=duration_seconds,
            use_external_vision=use_external_vision,
        )
        sample_sheet = await asyncio.to_thread(
            self._extract_video_sample_sheet_sync,
            media_bytes,
            sample_count=int(sampling_plan.get("sample_count", 3) or 3),
            sampling_strategy=str(
                sampling_plan.get("sampling_strategy", "keyframe_sheet") or "keyframe_sheet"
            ),
            latest_frame_bytes=capture_context.get("latest_image_bytes", b"") or None,
        )
        if not sample_sheet:
            return None

        return {
            "media_kind": "image",
            "mime_type": sample_sheet["mime_type"],
            "media_bytes": sample_sheet["media_bytes"],
            "active_window_title": capture_context.get("active_window_title", ""),
            "source_label": "录屏关键帧拼图",
            "sampling_strategy": sample_sheet.get("sampling_strategy", "keyframe_sheet"),
            "frame_count": sample_sheet.get("frame_count", 0),
            "frame_labels": sample_sheet.get("frame_labels", []),
            "has_live_anchor_frame": bool(sample_sheet.get("has_live_anchor_frame")),
            "duration_seconds": duration_seconds,
            "original_media_kind": "video",
        }

    def _should_keep_sampled_video_only(
        self,
        scene: str,
        *,
        use_external_vision: bool,
        preserve_full_video_for_audio: bool = False,
    ) -> bool:
        if preserve_full_video_for_audio:
            return False
        profile = self._get_scene_behavior_profile(scene)
        if use_external_vision:
            return True
        return bool(profile.get("prefer_sample_only", False))

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
        cache_dir = os.path.join(str(self.plugin_config.data_dir), "cache")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    def _detect_system_audio_device(self) -> str | None:
        if sys.platform != "win32":
            return None
        if self._recording_audio_device is not None:
            return self._recording_audio_device

        import re

        ffmpeg_path = self._get_ffmpeg_path()
        if not ffmpeg_path:
            self._recording_audio_device = ""
            return self._recording_audio_device

        cmd = [
            ffmpeg_path,
            "-hide_banner",
            "-list_devices",
            "true",
            "-f",
            "dshow",
            "-i",
            "dummy",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=10,
                creationflags=creationflags,
            )
            output = f"{result.stdout or ''}\n{result.stderr or ''}"
        except Exception as e:
            logger.debug(f"检测系统音频设备失败: {e}")
            self._recording_audio_device = ""
            return self._recording_audio_device

        keywords = ("立体声混音", "stereo mix", "realtek")
        matched_devices: list[str] = []
        for line in output.splitlines():
            lower_line = line.lower()
            if not any(keyword in lower_line for keyword in keywords):
                continue
            match = re.search(r'"([^"]+)"', line)
            if match:
                matched_devices.append(match.group(1))

        self._recording_audio_device = matched_devices[0] if matched_devices else ""
        if self._recording_audio_device:
            logger.info(f"检测到系统音频设备: {self._recording_audio_device}")
        else:
            logger.info("未检测到可用的系统音频设备，将仅录制桌面画面")
        return self._recording_audio_device

    def _cleanup_recording_cache(self, keep_latest: int = 3) -> None:
        try:
            cache_dir = self._get_recording_cache_dir()
            candidates = []
            for filename in os.listdir(cache_dir):
                if not filename.startswith("rec_") or not filename.endswith(".mp4"):
                    continue
                path = os.path.join(cache_dir, filename)
                try:
                    candidates.append((os.path.getmtime(path), path))
                except OSError:
                    continue
            candidates.sort(key=lambda item: item[0], reverse=True)
            for _, path in candidates[keep_latest:]:
                try:
                    os.remove(path)
                except OSError:
                    pass
        except Exception as e:
            logger.debug(f"清理录屏缓存失败: {e}")

    def _record_screen_clip_sync(self, duration_seconds: int) -> str:
        ffmpeg_path = self._get_ffmpeg_path()
        if not ffmpeg_path:
            raise RuntimeError(
                "\u672a\u627e\u5230 ffmpeg\uff0c\u8bf7\u5c06 ffmpeg.exe \u653e\u5230\u63d2\u4ef6\u76ee\u5f55\u4e0b\u7684 bin \u6587\u4ef6\u5939\uff0c"
                "\u6216\u5728\u914d\u7f6e\u4e2d\u586b\u5199 ffmpeg_path\uff0c\u6216\u52a0\u5165 PATH\u3002"
            )
        if sys.platform != "win32":
            raise RuntimeError("\u5f55\u5c4f\u89c6\u9891\u8bc6\u522b\u76ee\u524d\u4ec5\u652f\u6301 Windows \u684c\u9762\u73af\u5883\u3002")

        duration = max(1, int(duration_seconds or self._get_recording_duration_seconds()))
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        clip_name = f"manual_rec_{timestamp}_{secrets.token_hex(4)}.mp4"
        output_path = os.path.join(self._get_recording_cache_dir(), clip_name)
        cmd = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "gdigrab",
            "-framerate",
            str(self._get_recording_fps()),
            "-i",
            "desktop",
        ]

        audio_device = self._detect_system_audio_device()
        if audio_device:
            cmd.extend(
                [
                    "-f",
                    "dshow",
                    "-i",
                    f"audio={audio_device}",
                    "-shortest",
                ]
            )

        cmd.extend(
            [
                "-t",
                str(duration),
            ]
        )
        cmd.extend(self._build_recording_video_args())
        cmd.append(output_path)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=max(duration + 30, 45),
            creationflags=creationflags,
        )
        if result.returncode != 0:
            stderr_text = (result.stderr or "").strip()
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            raise RuntimeError(
                "\u5355\u6b21\u5f55\u5c4f\u5931\u8d25\uff0cffmpeg \u5df2\u9000\u51fa\u3002"
                + (f" stderr: {stderr_text[:300]}" if stderr_text else "")
            )
        return output_path

    def _start_screen_recording_sync(self) -> str:
        ffmpeg_path = self._get_ffmpeg_path()
        if not ffmpeg_path:
            raise RuntimeError(
                "未找到 ffmpeg，请将 ffmpeg.exe 放到插件数据目录下的 bin 文件夹，"
                "或在配置中填写 ffmpeg_path，或加入 PATH。"
            )
        if sys.platform != "win32":
            raise RuntimeError("录屏视频识别目前仅支持 Windows 桌面环境")

        process = getattr(self, "_screen_recording_process", None)
        if process and process.poll() is None:
            return str(getattr(self, "_screen_recording_path", "") or "")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(self._get_recording_cache_dir(), f"rec_{timestamp}.mp4")
        cmd = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "gdigrab",
            "-framerate",
            str(self._get_recording_fps()),
            "-i",
            "desktop",
        ]

        audio_device = self._detect_system_audio_device()
        if audio_device:
            cmd.extend(
                [
                    "-f",
                    "dshow",
                    "-i",
                    f"audio={audio_device}",
                    "-shortest",
                ]
            )

        cmd.extend(
            [
                "-t",
                str(self._get_recording_duration_seconds()),
            ]
        )
        cmd.extend(self._build_recording_video_args())
        cmd.append(output_path)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._screen_recording_process = process
        self._screen_recording_path = output_path
        self._cleanup_recording_cache()
        logger.info(f"已启动桌面录屏: {output_path}")
        return output_path

    def _stop_screen_recording_sync(self) -> str:
        process = getattr(self, "_screen_recording_process", None)
        output_path = str(getattr(self, "_screen_recording_path", "") or "")
        self._screen_recording_process = None
        self._screen_recording_path = ""

        if process and process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write(b"q\n")
                    process.stdin.flush()
            except Exception:
                pass

            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

        return output_path

    async def _ensure_recording_ready(self) -> None:
        self._ensure_recording_runtime_state()
        async with self._screen_recording_lock:
            await asyncio.to_thread(self._start_screen_recording_sync)

    async def _stop_recording_if_running(self) -> None:
        self._ensure_recording_runtime_state()
        async with self._screen_recording_lock:
            await asyncio.to_thread(self._stop_screen_recording_sync)

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
        try:
            import json
            import os
            observations_file = os.path.join(self.observation_storage, "observations.json")
            if os.path.exists(observations_file):
                with open(observations_file, "r", encoding="utf-8") as f:
                    self.observations = json.load(f)
                    if len(self.observations) > self.max_observations:
                        # 每次达到上限时删除5条，保留15条
                        self.observations = self.observations[-15:]
        except Exception as e:
            logger.error(f"加载观察记录失败: {e}")
            self.observations = []

    def _save_observations(self):
        """保存观察记录。"""
        try:
            import json
            import os
            observations_file = os.path.join(self.observation_storage, "observations.json")
            if len(self.observations) > self.max_observations:
                # 每次达到上限时删除6条，保留3天的记录（每天最多3条）
                self.observations = self.observations[-9:]
            # 整理和补正未知观察记录
            self._cleanup_unknown_observations()
            with open(observations_file, "w", encoding="utf-8") as f:
                json.dump(self.observations, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存观察记录失败: {e}")

    def _cleanup_unknown_observations(self):
        """整理和补正观察记录中的"未知"场景。"""
        if not self.observations:
            return
        
        # 统计未知场景的数量
        unknown_count = sum(1 for obs in self.observations if obs.get("scene", "") == "未知")
        
        # 如果未知场景数量较多，进行整理
        if unknown_count > 5:
            logger.info(f"开始整理未知观察记录，共 {unknown_count} 条")
            
            # 遍历观察记录，尝试补正未知场景
            for obs in self.observations:
                if obs.get("scene", "") == "未知":
                    # 尝试根据窗口标题和描述补正场景
                    window_title = obs.get("window_title", "")
                    description = obs.get("description", "")
                    
                    # 首先尝试根据窗口标题识别场景
                    if window_title:
                        scene = self._identify_scene(window_title)
                        if scene != "未知":
                            obs["scene"] = scene
                            logger.info(f"已补正场景: {window_title} -> {scene}")
                            continue
                    
                    # 如果窗口标题识别失败，尝试根据描述识别场景
                    if description:
                        # 简单的描述匹配
                        description_lower = description.lower()
                        scene_keywords = {
                            "编程": ["code", "program", "开发", "编程", "debug", "代码"],
                            "设计": ["design", "设计", "美术", "绘图", "创意"],
                            "办公": ["document", "excel", "word", "办公", "工作"],
                            "游戏": ["game", "游戏", "play", "玩家", "关卡"],
                            "视频": ["video", "电影", "视频", "播放", "tv"],
                            "阅读": ["read", "book", "阅读", "书籍", "文档"],
                            "音乐": ["music", "歌曲", "音乐", "audio"],
                            "社交": ["chat", "社交", "聊天", "message"],
                        }
                        
                        for scene, keywords in scene_keywords.items():
                            if any(keyword in description_lower for keyword in keywords):
                                obs["scene"] = scene
                                logger.info(f"已根据描述补正场景: {description[:50]} -> {scene}")
                                break
        
        # 清理后再次统计未知场景数量
        new_unknown_count = sum(1 for obs in self.observations if obs.get("scene", "") == "未知")
        if new_unknown_count < unknown_count:
            logger.info(f"未知场景整理完成，从 {unknown_count} 条减少到 {new_unknown_count} 条")

    def _add_observation(
        self,
        scene,
        recognition_text,
        active_window_title,
        extra: dict[str, Any] | None = None,
    ):
        """添加一条观察记录。"""
        import datetime
        scene = self._normalize_scene_label(scene)
        active_window_title = self._normalize_window_title(active_window_title)
        should_store, reason = self._should_store_observation(
            scene, recognition_text, active_window_title
        )
        if not should_store:
            logger.info(f"跳过观察记录写入: {reason}")
            return False
        observation = {
            "timestamp": datetime.datetime.now().isoformat(),
            "scene": scene,
            "window_title": active_window_title,
            "description": recognition_text[:200],
        }
        if isinstance(extra, dict):
            for key, value in extra.items():
                if value in (None, "", [], {}):
                    continue
                observation[key] = value
        self.observations.append(observation)
        if len(self.observations) > self.max_observations:
            # 每次达到上限时删除6条，保留3天的记录（每天最多3条）
            self.observations = self.observations[-9:]
        self._save_observations()
        return True

    def _load_diary_metadata(self):
        """加载日记元数据。"""
        try:
            import json
            import os
            if os.path.exists(self.diary_metadata_file):
                with open(self.diary_metadata_file, "r", encoding="utf-8") as f:
                    self.diary_metadata = json.load(f)
        except Exception as e:
            logger.error(f"加载日记元数据失败: {e}")
            self.diary_metadata = {}

    def _save_diary_metadata(self):
        """保存日记元数据。"""
        try:
            import json
            import os
            with open(self.diary_metadata_file, "w", encoding="utf-8") as f:
                json.dump(self.diary_metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存日记元数据失败: {e}")

    def _update_diary_view_status(self, date_str):
        """记录某天日记已被查看。"""
        import datetime
        if date_str not in self.diary_metadata:
            self.diary_metadata[date_str] = {}
        self.diary_metadata[date_str]["viewed"] = True
        self.diary_metadata[date_str]["viewed_at"] = datetime.datetime.now().isoformat()
        self._save_diary_metadata()
        logger.info(f"更新日记查看状态: {date_str} - 已查看")

    def _load_long_term_memory(self):
        """加载长期记忆。"""
        try:
            import json
            import os
            if os.path.exists(self.long_term_memory_file):
                with open(self.long_term_memory_file, "r", encoding="utf-8") as f:
                    self.long_term_memory = json.load(f)
                self._clean_long_term_memory_noise()
                logger.info("长期记忆加载成功")
        except Exception as e:
            logger.error(f"加载长期记忆失败: {e}")
            self.long_term_memory = {}

    def _save_long_term_memory(self):
        """保存长期记忆。"""
        try:
            import json
            import os
            self._clean_long_term_memory_noise()
            with open(self.long_term_memory_file, "w", encoding="utf-8") as f:
                json.dump(self.long_term_memory, f, ensure_ascii=False, indent=2)
            logger.info("长期记忆保存成功")
        except Exception as e:
            logger.error(f"保存长期记忆失败: {e}")

    @staticmethod
    def _normalize_scene_label(scene: str) -> str:
        scene = str(scene or "").strip()
        invalid_labels = {"", "??", "unknown", "???", "?????", "none", "null", "未知"}
        return "" if scene.lower() in invalid_labels or scene in invalid_labels else scene

    @staticmethod
    def _normalize_window_title(window_title: str) -> str:
        window_title = str(window_title or "").strip()
        invalid_titles = {"", "未知", "unknown", "宿主机截图", "none", "null"}
        if window_title.lower() in invalid_titles or window_title in invalid_titles:
            return ""
        return window_title

    @staticmethod
    def _normalize_record_text(text: str) -> str:
        import re

        text = str(text or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"```[\s\S]*?```", " ", text)
        text = re.sub(r"`[^`]+`", " ", text)
        text = re.sub(r"[*#>\-_=~]+", " ", text)
        text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _normalize_shared_activity_summary(summary: str) -> str:
        import re

        summary = str(summary or "").strip()
        if not summary:
            return ""
        summary = re.sub(r"\s+", " ", summary)
        return summary[:60]

    def _ensure_long_term_memory_defaults(self) -> None:
        """确保长期记忆结构完整。"""
        if not isinstance(self.long_term_memory, dict):
            self.long_term_memory = {}

        self.long_term_memory.setdefault("applications", {})
        self.long_term_memory.setdefault("scenes", {})
        self.long_term_memory.setdefault(
            "user_preferences",
            {
                "music": {},
                "movies": {},
                "food": {},
                "hobbies": {},
                "other": {},
            },
        )
        self.long_term_memory.setdefault("memory_associations", {})
        self.long_term_memory.setdefault("memory_priorities", {})
        self.long_term_memory.setdefault("shared_activities", {})
        self.long_term_memory.setdefault("episodic_memories", [])
        self.long_term_memory.setdefault("focus_patterns", {})

    def _extract_memory_focus(self, text: str, max_length: int = 48) -> str:
        summary = self._compress_recognition_text(text, max_length=max_length)
        summary = str(summary or "").strip().strip(" .。!！?？,，:：;；")
        if not summary:
            return ""
        return summary[:max_length]

    def _remember_episodic_memory(
        self,
        *,
        scene: str,
        active_window: str,
        summary: str,
        response_preview: str = "",
        kind: str = "screen_observation",
    ) -> bool:
        normalized_summary = self._extract_memory_focus(summary, max_length=72)
        if not normalized_summary or self._is_low_value_record_text(normalized_summary):
            return False

        self._ensure_long_term_memory_defaults()
        scene = self._normalize_scene_label(scene)
        active_window = self._normalize_window_title(active_window)
        today = datetime.date.today().isoformat()
        now_ts = datetime.datetime.now().isoformat()
        memories = list(self.long_term_memory.get("episodic_memories", []) or [])

        matched_index = None
        for index, item in enumerate(memories):
            if not isinstance(item, dict):
                continue
            previous_scene = self._normalize_scene_label(item.get("scene", ""))
            previous_window = self._normalize_window_title(item.get("active_window", ""))
            previous_summary = self._extract_memory_focus(item.get("summary", ""), max_length=72)
            if scene and previous_scene and scene != previous_scene:
                continue
            if active_window and previous_window and active_window != previous_window:
                continue
            if self._is_similar_record(normalized_summary, previous_summary, threshold=0.82):
                matched_index = index
                break

        if matched_index is None:
            memories.append(
                {
                    "scene": scene,
                    "active_window": active_window,
                    "summary": normalized_summary,
                    "response_preview": self._truncate_preview_text(response_preview, limit=120),
                    "kind": str(kind or "screen_observation"),
                    "count": 1,
                    "first_seen": today,
                    "last_seen": today,
                    "updated_at": now_ts,
                    "priority": 1,
                }
            )
        else:
            target = memories[matched_index]
            target["count"] = int(target.get("count", 0) or 0) + 1
            target["last_seen"] = today
            target["updated_at"] = now_ts
            if response_preview:
                target["response_preview"] = self._truncate_preview_text(response_preview, limit=120)
            if not target.get("summary"):
                target["summary"] = normalized_summary

        self.long_term_memory["episodic_memories"] = memories
        return True

    def _remember_focus_pattern(
        self,
        *,
        scene: str,
        active_window: str,
        summary: str,
    ) -> bool:
        focus_text = self._extract_memory_focus(summary, max_length=40)
        if not focus_text or self._is_low_value_record_text(focus_text):
            return False

        self._ensure_long_term_memory_defaults()
        scene = self._normalize_scene_label(scene)
        active_window = self._normalize_window_title(active_window)
        if not scene and not active_window:
            return False

        pattern_key = f"{scene or 'general'}::{active_window or 'window'}::{focus_text}"
        today = datetime.date.today().isoformat()
        focus_patterns = self.long_term_memory.setdefault("focus_patterns", {})
        item = focus_patterns.setdefault(
            pattern_key,
            {
                "scene": scene,
                "active_window": active_window,
                "summary": focus_text,
                "count": 0,
                "last_seen": today,
                "priority": 0,
            },
        )
        item["count"] = int(item.get("count", 0) or 0) + 1
        item["last_seen"] = today
        return True

    def _is_low_value_record_text(self, text: str) -> bool:
        normalized = self._normalize_record_text(text)
        if len(normalized) < 12:
            return True

        if self._is_screen_error_text(normalized):
            return True

        low_value_patterns = (
            "看不清",
            "无法识别",
            "识别失败",
            "内容较少",
            "没有明显内容",
            "一个窗口",
            "一个界面",
            "屏幕截图",
            "当前屏幕",
            "未发现明确信息",
            "暂无更多信息",
            "未知内容",
            "不确定",
        )
        return any(pattern in normalized for pattern in low_value_patterns)

    def _is_screen_error_text(self, text: str) -> bool:
        normalized = self._normalize_record_text(text)
        if not normalized:
            return False

        error_patterns = (
            "[识屏异常",
            "识屏异常",
            "外部接口调用失败",
            "视觉分析服务暂时不可用",
            "当前模型暂时不支持这次多模态识别",
            "这次视觉分析没有成功",
            "vision api timeout",
            "vision api",
            "api调用失败",
            "检查配置或稍后再试",
        )
        return any(pattern in normalized for pattern in error_patterns)

    def _is_similar_record(self, current_text: str, previous_text: str, threshold: float = 0.98) -> bool:
        import difflib

        current = self._normalize_record_text(current_text)
        previous = self._normalize_record_text(previous_text)
        if not current or not previous:
            return False
        if current == previous:
            return True
        return difflib.SequenceMatcher(None, current, previous).ratio() >= threshold

    @staticmethod
    def _compress_recognition_text(text: str, max_length: int = 800) -> str:
        import re

        compressed = str(text or "").replace("\r\n", "\n").strip()
        if not compressed:
            return compressed

        compressed = re.sub(r"\n{3,}", "\n\n", compressed)
        lines = [line.strip() for line in compressed.split("\n") if line.strip()]
        if len(lines) > 8:
            compressed = "\n".join(lines[:8])
        else:
            compressed = "\n".join(lines)

        if len(compressed) > max_length:
            compressed = compressed[: max_length - 1].rstrip() + "…"

        return compressed

    def _should_store_observation(self, scene: str, recognition_text: str, active_window_title: str) -> tuple[bool, str]:
        normalized_scene = self._normalize_scene_label(scene)
        normalized_window = self._normalize_window_title(active_window_title)
        normalized_text = self._normalize_record_text(recognition_text)

        if self._is_low_value_record_text(normalized_text):
            return False, "low_value"

        recent_observations = list(getattr(self, "observations", []) or [])[-5:]
        for observation in reversed(recent_observations):
            previous_scene = self._normalize_scene_label(observation.get("scene", ""))
            previous_window = self._normalize_window_title(
                observation.get("active_window") or observation.get("window_title") or ""
            )
            previous_text = (
                observation.get("content")
                or observation.get("description")
                or observation.get("recognition")
                or ""
            )

            same_context = False
            if normalized_window and previous_window and normalized_window == previous_window:
                if normalized_scene and previous_scene and normalized_scene == previous_scene:
                    same_context = True

            if same_context and self._is_similar_record(normalized_text, previous_text):
                return False, "duplicate_observation"

        return True, "ok"

    def _should_store_diary_entry(self, content: str, active_window: str) -> tuple[bool, str]:
        normalized_window = self._normalize_window_title(active_window)
        if self._is_screen_error_text(content):
            return False, "screen_error"
        if self._is_low_value_record_text(content):
            return False, "low_value"

        recent_entries = list(getattr(self, "diary_entries", []) or [])[-3:]
        for entry in reversed(recent_entries):
            previous_window = self._normalize_window_title(entry.get("active_window", ""))
            if normalized_window and previous_window and normalized_window != previous_window:
                continue
            if self._is_similar_record(content, entry.get("content", ""), threshold=0.9):
                return False, "duplicate_diary_entry"

        return True, "ok"

    @staticmethod
    def _limit_ranked_dict_items(items: dict, limit: int, score_keys: tuple[str, ...]) -> dict:
        if not isinstance(items, dict) or len(items) <= limit:
            return items

        def score(entry: tuple[str, Any]) -> tuple:
            _, data = entry
            if not isinstance(data, dict):
                return (0,)
            return tuple(int(data.get(key, 0) or 0) for key in score_keys)

        ranked = sorted(items.items(), key=score, reverse=True)
        return dict(ranked[:limit])

    @staticmethod
    def _sanitize_diary_section_text(text: str) -> str:
        """清理日记段落中的重复标题和无效空行。"""
        import re

        lines = str(text or "").replace("\r\n", "\n").split("\n")
        cleaned_lines = []
        skip_heading_patterns = [
            re.compile(r"^\s*#\s*.+日记\s*$"),
            re.compile(r"^\s*##\s*\d{4}年\d{1,2}月\d{1,2}日.*$"),
            re.compile(r"^\s*##\s*今日感想\s*$"),
            re.compile(r"^\s*##\s*今日观察\s*$"),
        ]

        for raw_line in lines:
            line = raw_line.strip()
            if not line and not cleaned_lines:
                continue
            if any(pattern.match(line) for pattern in skip_heading_patterns):
                continue
            cleaned_lines.append(raw_line)

        cleaned_text = "\n".join(cleaned_lines).strip()
        cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)
        return cleaned_text
    @staticmethod
    def _parse_clock_to_minutes(value: str) -> int | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parts = text.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            return hour * 60 + minute
        except Exception:
            return None

    def _compact_diary_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compacted: list[dict[str, Any]] = []
        for raw_entry in entries or []:
            entry_text = str(raw_entry.get("content") or "").strip()
            normalized_text = self._normalize_record_text(entry_text)
            if self._is_low_value_record_text(normalized_text):
                continue

            active_window = self._normalize_window_title(raw_entry.get("active_window") or "") or "当前窗口"
            time_text = str(raw_entry.get("time") or "").strip() or "--:--"
            entry_minutes = self._parse_clock_to_minutes(time_text)

            if compacted:
                previous = compacted[-1]
                same_window = previous["active_window"] == active_window
                last_minutes = previous.get("last_minutes")
                close_in_time = (
                    entry_minutes is not None
                    and last_minutes is not None
                    and 0 <= entry_minutes - last_minutes <= 12
                )
                similar_to_previous = self._is_similar_record(
                    normalized_text,
                    previous.get("last_text", ""),
                    threshold=0.82,
                )
                if same_window and close_in_time and similar_to_previous:
                    previous["end_time"] = time_text
                    previous["last_minutes"] = entry_minutes
                    if not previous["points"] or not self._is_similar_record(
                        normalized_text,
                        previous["points"][-1],
                        threshold=0.9,
                    ):
                        previous["points"].append(entry_text)
                    previous["last_text"] = normalized_text
                    continue

            compacted.append(
                {
                    "start_time": time_text,
                    "end_time": time_text,
                    "active_window": active_window,
                    "points": [entry_text],
                    "last_text": normalized_text,
                    "last_minutes": entry_minutes,
                }
            )

        return compacted

    def _is_continuing_memory_context(self, scene: str, active_window: str) -> bool:
        normalized_scene = self._normalize_scene_label(scene)
        normalized_window = self._normalize_window_title(active_window)
        app_name = normalized_window.split(" - ")[-1] if " - " in normalized_window else normalized_window
        app_name = self._normalize_window_title(app_name)

        recent_observations = list(getattr(self, "observations", []) or [])[-3:]
        if len(recent_observations) < 3:
            return False

        for observation in recent_observations:
            previous_scene = self._normalize_scene_label(observation.get("scene", ""))
            previous_window = self._normalize_window_title(
                observation.get("active_window") or observation.get("window_title") or ""
            )
            previous_app = previous_window.split(" - ")[-1] if " - " in previous_window else previous_window
            previous_app = self._normalize_window_title(previous_app)

            if normalized_scene and previous_scene != normalized_scene:
                return False
            if app_name and previous_app != app_name:
                return False

        return bool(normalized_scene or app_name)

    def _build_diary_reflection_prompt(
        self,
        observation_text: str,
        viewed_count: int,
        reference_days: list[dict] | None = None,
    ) -> str:
        reference_days = reference_days or []
        mood_hint = {
            0: "今天还没有被查看过，语气可以更像刚写好的当日心绪。",
            1: "今天已经被查看过一次，语气自然一些，不要太用力重复。",
            2: "今天已经被查看过多次，重点放在新的感受和更有价值的总结。",
        }.get(viewed_count, "今天这篇日记已经被看过很多次了，请避免重复表达。")

        prompt_parts = [
            "请根据今天的观察记录，写一段自然、有温度、但信息密度足够的“今日感想”。",
            "控制在 2 到 3 段，不要写成流水账，也不要复述所有观察细节。",
            "优先总结今天在做什么、卡在什么地方、有哪些值得继续推进的点。",
            "如果能给建议，请给和当前任务直接相关、可以立刻使用的建议。",
            "字数控制在 220 到 420 字。",
            f"额外要求：{mood_hint}",
            "",
            "今日观察：",
            observation_text or "今天没有留下有效观察，请写得更克制一些。",
        ]

        if reference_days:
            prompt_parts.extend(["", "可参考前几天的日记风格："])
            for day in reference_days:
                prompt_parts.append(f"### {day['date']}")
                prompt_parts.append(str(day.get('content') or '')[:500])

        return "\n".join(prompt_parts)

    def _build_vision_prompt(self, scene: str, active_window_title: str = "") -> str:
        base_prompt = str(self.image_prompt or "").strip()
        normalized_scene = self._normalize_scene_label(scene)
        normalized_window = self._normalize_window_title(active_window_title)

        # 按重要性排序组织提示词部分
        prompt_parts = []
        
        # 1. 基础提示词（如果有）
        if base_prompt:
            prompt_parts.append(base_prompt)
        
        # 2. 关键上下文信息
        if normalized_window:
            prompt_parts.append(f"当前窗口标题：{normalized_window}")
        
        # 3. Bot自身识别信息（用于识别屏幕中的自己）
        bot_self_info = []
        if hasattr(self, 'bot_appearance') and self.bot_appearance:
            bot_self_info.append(f"Bot的外形描述：{self.bot_appearance}")
        
        if hasattr(self, 'long_term_memory') and self.long_term_memory.get('self_image'):
            self_image_memories = self.long_term_memory['self_image']
            # 按 count 排序，取最常见的几个记忆
            sorted_memories = sorted(self_image_memories, key=lambda x: x.get('count', 0), reverse=True)[:3]
            if sorted_memories:
                bot_self_info.append("关于Bot自身的已知信息：")
                for memory in sorted_memories:
                    bot_self_info.append(f"- {memory['content']}")
        
        if bot_self_info:
            prompt_parts.extend(bot_self_info)
            prompt_parts.append("如果在屏幕中发现符合Bot外形描述的元素，请识别为Bot自己。")
        
        # 4. 场景特定指导
        scene_prompts = {
            "编程": "重点分析代码结构、语法、逻辑流程、错误信息、开发环境等。识别用户正在实现的功能、遇到的问题、代码优化空间，并提供具体的技术建议和解决方案。",
            "设计": "重点分析设计元素、布局、色彩搭配、视觉层次、用户体验等。识别设计任务的目标、当前的视觉问题、可以优化的方向，并提供具体的设计建议和改进方案。",
            "浏览": "重点分析网页内容、搜索结果、信息结构、导航元素等。识别用户的信息需求、搜索目的、浏览行为，并提供相关的信息整理和使用建议。",
            "办公": "重点分析文档内容、表格数据、邮件信息、会议安排等。识别用户的办公任务、工作目标、当前进度，并提供具体的工作流程建议和效率提升方案。",
            "游戏": "重点分析游戏场景、角色状态、资源情况、任务目标、游戏机制等。识别当前游戏局势、玩家需求、可能的策略，并提供具体的游戏建议和技巧。",
            "视频": "重点分析视频内容、画面细节、人物表情、场景氛围、对话内容等。识别视频的主题、情感基调、关键信息，并提供相关的见解和讨论点。",
            "阅读": "重点分析文本内容、标题结构、段落大意、关键观点、图表数据等。识别阅读材料的主题、核心思想、重要信息，并提供相关的理解和应用建议。",
        }
        
        prompt_parts.append(
            scene_prompts.get(
                normalized_scene,
                "请全面分析屏幕内容，识别用户正在进行的活动，提取关键信息和细节，分析可能的问题或需求，并提供具体、实用的建议。",
            )
        )
        
        # 5. 通用分析要求
        prompt_parts.extend([
            "请对屏幕内容进行详细分析，提供以下信息：",
            "1. 屏幕的整体场景和主要内容",
            "2. 关键元素的详细信息（如文本、图像、界面元素等）",
            "3. 用户可能正在进行的任务或活动",
            "4. 可能的问题或挑战",
            "5. 具体、实用的建议或解决方案",
            "6. 相关的上下文信息或背景知识",
            "",
            "请提供详细、具体的分析结果，避免泛泛而谈或过于简略的描述。"
        ])

        return "\n".join(part for part in prompt_parts if part).strip()

    def _extract_screen_assist_prompt(self, message: str) -> str:
        import re

        text = str(message or "").strip()
        normalized = re.sub(r"\s+", "", text.lower())
        if not normalized or normalized.startswith("/"):
            return ""

        # 提取并忽略bot名称
        bot_name = getattr(self, "bot_name", "").strip().lower()
        if bot_name and bot_name in normalized:
            # 移除bot名称部分
            normalized = normalized.replace(bot_name, "")
            # 同时处理原文本，移除bot名称
            text = re.sub(re.escape(bot_name), "", text, flags=re.IGNORECASE)
            text = text.strip()
            normalized = re.sub(r"\s+", "", text.lower())

        # 检查是否以"帮我"开头（支持"帮我"和"你帮我"两种形式）
        if not (normalized.startswith("帮我") or normalized.startswith("你帮我")):
            return ""

        # 应用启动器相关的排除标记，避免与应用启动器插件冲突
        app_launcher_excludes = (
            "打开", "启动", "运行", "开启", "打开一下", "启动一下", "运行一下",
            "百度", "搜索", "查找", "查询", "搜索一下", "查一下", "搜一下",
            "浏览器", "网页", "网站", "网址", "网页链接", "网站链接",
            "http://", "https://", ".com", ".cn", ".org", ".net", ".io",
            "直播", "直播间", "直播页", "动态", "最新动态", "动态页", "视频", "最新视频", "投稿",
            "应用", "程序", "软件", "app",
        )

        # 检查是否包含应用启动器相关的关键词，避免冲突
        if any(marker in normalized for marker in app_launcher_excludes):
            return ""

        request_markers = (
            "帮我看看",
            "帮我看下",
            "你帮我看看",
            "看看这个",
            "看下这个",
            "帮我分析",
            "给点建议",
            "出什么装备",
            "这题怎么做",
            "这个报错",
            "这个页面",
            "帮我看一下",
            "你帮我看一下",
            "帮我看看屏幕",
            "帮我看下屏幕",
            "看看屏幕",
            "看下屏幕",
        )
        context_markers = (
            "屏幕",
            "画面",
            "窗口",
            "这题",
            "这个",
            "这一题",
            "这局",
            "装备",
            "报错",
            "代码",
            "页面",
            "界面",
            "文档",
            "作业",
            "游戏",
            "题目",
            "插件",
            "网页",
            "截图",
            "当前",
            "这个问题",
            "这个地方",
            "这里",
        )
        negative_markers = (
            "不用看",
            "别看",
            "不用截图",
            "别截图",
            "不用识屏",
            "不要识屏",
            "别帮我",
            "不用帮我",
            "不要帮我",
        )

        # 先检查否定标记，避免误触发
        if any(marker in normalized for marker in negative_markers):
            return ""

        has_request = any(marker in normalized for marker in request_markers)
        has_context = any(marker in normalized for marker in context_markers)
        
        # 优化：如果包含"帮我"且消息长度合理，即使没有明确的上下文标记也尝试识屏
        # 这样可以避免误触导致的空消息
        has_help = "帮我" in normalized
        if has_help and len(text) >= 3 and len(text) <= 100:
            # 如果只有"帮我"但没有上下文，仍然尝试处理，但返回原文本
            return text[:160]
        
        # 原有逻辑：需要同时有请求和上下文标记
        if not (has_request and has_context):
            return ""

        return text[:160]

    def _build_diary_document(
        self,
        target_date,
        weekday: str,
        observation_text: str,
        reflection_text: str,
        structured_summary: dict[str, Any] | None = None,
        weather_info: str = "",
    ) -> str:
        observation_text = str(observation_text or "").strip()
        reflection_text = self._sanitize_diary_section_text(reflection_text)
        structured_summary = structured_summary or {}

        parts = [
            f"# {self.bot_name} 的日记",
            "",
            f"## {target_date.strftime('%Y年%m月%d日')} {weekday}",
            "",
        ]
        if weather_info:
            parts.extend([f"**天气**: {weather_info}", ""])

        summary_lines = self._build_diary_summary_markdown(structured_summary)
        if summary_lines:
            parts.extend(["## 今日概览", "", *summary_lines, ""])

        parts.extend(
            [
                "## 今日观察",
                "",
                observation_text,
                "",
                "## 今日感想",
                "",
                reflection_text,
            ]
        )
        return "\n".join(parts).strip() + "\n"

    def _extract_actionable_suggestions(
        self,
        reflection_text: str,
        *,
        limit: int = 3,
    ) -> list[str]:
        text = str(reflection_text or "").strip()
        if not text:
            return []

        import re

        raw_sentences = [
            sentence.strip()
            for sentence in re.split(r"[。\n！？!?\r]+", text)
            if sentence.strip()
        ]
        prioritized = []
        fallback = []
        keywords = ("建议", "记得", "可以", "优先", "先", "下次", "别忘了", "不如")
        for sentence in raw_sentences:
            clean_sentence = sentence.lstrip("-• ").strip()
            if not clean_sentence:
                continue
            if any(keyword in clean_sentence for keyword in keywords):
                prioritized.append(clean_sentence)
            else:
                fallback.append(clean_sentence)

        picked = prioritized[:limit]
        if len(picked) < limit:
            picked.extend(fallback[: max(0, limit - len(picked))])

        deduped = []
        seen = set()
        for sentence in picked:
            normalized = self._normalize_record_text(sentence)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(sentence[:80])
        return deduped[:limit]

    def _build_diary_structured_summary(
        self,
        compacted_entries: list[dict[str, Any]],
        reflection_text: str,
    ) -> dict[str, Any]:
        summary = {
            "main_windows": [],
            "longest_task": {},
            "repeated_focuses": [],
            "suggestion_items": self._extract_actionable_suggestions(reflection_text, limit=3),
            "entry_count": len(compacted_entries or []),
        }
        if not compacted_entries:
            return summary

        window_stats: dict[str, dict[str, Any]] = {}
        repeated_focuses = []
        longest_task = None
        longest_span = -1

        for entry in compacted_entries:
            window_title = self._normalize_window_title(entry.get("active_window") or "") or "当前窗口"
            start_minutes = self._parse_clock_to_minutes(entry.get("start_time"))
            end_minutes = self._parse_clock_to_minutes(entry.get("end_time"))
            duration_minutes = 0
            if start_minutes is not None and end_minutes is not None and end_minutes >= start_minutes:
                duration_minutes = end_minutes - start_minutes

            stats = window_stats.setdefault(
                window_title,
                {"groups": 0, "duration_minutes": 0, "points": 0},
            )
            stats["groups"] += 1
            stats["duration_minutes"] += max(1, duration_minutes)
            stats["points"] += len(entry.get("points", []) or [])

            if duration_minutes > longest_span:
                longest_span = duration_minutes
                longest_task = {
                    "window_title": window_title,
                    "time_range": (
                        entry.get("start_time")
                        if entry.get("start_time") == entry.get("end_time")
                        else f"{entry.get('start_time')}-{entry.get('end_time')}"
                    ),
                    "focus": str((entry.get("points", []) or [""])[0] or "").strip()[:90],
                    "duration_minutes": max(1, duration_minutes),
                }

            if stats["groups"] >= 2 or len(entry.get("points", []) or []) >= 2:
                repeated_focuses.append(
                    {
                        "window_title": window_title,
                        "note": str((entry.get("points", []) or [""])[0] or "").strip()[:90],
                    }
                )

        ranked_windows = sorted(
            window_stats.items(),
            key=lambda item: (
                int((item[1] or {}).get("duration_minutes", 0) or 0),
                int((item[1] or {}).get("points", 0) or 0),
                int((item[1] or {}).get("groups", 0) or 0),
            ),
            reverse=True,
        )[:4]
        summary["main_windows"] = [
            {
                "window_title": window_title,
                "duration_minutes": data.get("duration_minutes", 0),
                "groups": data.get("groups", 0),
                "points": data.get("points", 0),
            }
            for window_title, data in ranked_windows
        ]
        summary["longest_task"] = longest_task or {}

        deduped_focuses = []
        seen_focuses = set()
        for item in repeated_focuses:
            key = self._normalize_record_text(
                f"{item.get('window_title', '')} {item.get('note', '')}"
            )
            if not key or key in seen_focuses:
                continue
            seen_focuses.add(key)
            deduped_focuses.append(item)
            if len(deduped_focuses) >= 3:
                break
        summary["repeated_focuses"] = deduped_focuses
        return summary

    def _build_diary_summary_markdown(self, structured_summary: dict[str, Any]) -> list[str]:
        if not isinstance(structured_summary, dict):
            return []

        lines = []
        main_windows = structured_summary.get("main_windows", []) or []
        if main_windows:
            main_window_text = "、".join(
                f"{item.get('window_title', '当前窗口')}（约 {int(item.get('duration_minutes', 0) or 0)} 分钟）"
                for item in main_windows[:3]
            )
            lines.append(f"- 今日主要窗口：{main_window_text}")

        longest_task = structured_summary.get("longest_task", {}) or {}
        if longest_task.get("window_title"):
            longest_focus = str(longest_task.get("focus", "") or "").strip()
            longest_line = (
                f"- 最长停留任务：{longest_task.get('window_title')}，大约 {int(longest_task.get('duration_minutes', 0) or 0)} 分钟"
            )
            if longest_focus:
                longest_line += f"，当时主要在：{longest_focus}"
            lines.append(longest_line)

        repeated_focuses = structured_summary.get("repeated_focuses", []) or []
        if repeated_focuses:
            repeated_text = "；".join(
                f"{item.get('window_title', '当前窗口')}：{item.get('note', '')}"
                for item in repeated_focuses[:2]
            )
            lines.append(f"- 重复卡点：{repeated_text}")

        suggestion_items = structured_summary.get("suggestion_items", []) or []
        if suggestion_items:
            lines.append("- 建议事项：")
            for item in suggestion_items[:3]:
                lines.append(f"  - {item}")

        return lines

    def _build_diary_reflection_fallback(
        self,
        observation_text: str,
        structured_summary: dict[str, Any] | None = None,
    ) -> str:
        structured_summary = structured_summary or {}

        def _clean_text(value: str, limit: int = 90) -> str:
            import re

            text = str(value or "").strip()
            if not text:
                return ""
            text = re.sub(r"^[-*#\s]+", "", text)
            text = re.sub(r"\s+", " ", text).strip(" .。!！?？,，:：;；")
            return text[:limit]

        paragraphs: list[str] = []
        main_windows = structured_summary.get("main_windows", []) or []
        longest_task = structured_summary.get("longest_task", {}) or {}
        repeated_focuses = structured_summary.get("repeated_focuses", []) or []
        suggestion_items = structured_summary.get("suggestion_items", []) or []

        if main_windows:
            window_text = "、".join(
                f"《{item.get('window_title') or '当前窗口'}》"
                for item in main_windows[:2]
            )
            paragraphs.append(
                f"今天主要在 {window_text} 之间切换，注意力基本都围着这些任务在转。"
            )

        if longest_task.get("window_title"):
            duration = int(longest_task.get("duration_minutes", 0) or 0)
            focus_text = _clean_text(longest_task.get("focus", ""))
            detail = f"今天停留最久的是《{longest_task.get('window_title')}》"
            if duration > 0:
                detail += f"，大约花了 {duration} 分钟"
            if focus_text:
                detail += f"，主要在处理：{focus_text}"
            paragraphs.append(detail + "。")

        if repeated_focuses:
            focus_text = "；".join(
                f"《{item.get('window_title') or '当前窗口'}》里的 {_clean_text(item.get('note', ''), limit=50) or '同类问题'}"
                for item in repeated_focuses[:2]
            )
            paragraphs.append(f"反复出现的卡点也比较明显，主要集中在 {focus_text}。")

        if suggestion_items:
            suggestion_text = "；".join(_clean_text(item, limit=60) for item in suggestion_items[:2] if _clean_text(item, limit=60))
            if suggestion_text:
                paragraphs.append(f"如果明天继续推进，比较值得优先处理的是：{suggestion_text}。")

        if not paragraphs:
            first_observation = ""
            for raw_line in str(observation_text or "").splitlines():
                cleaned = _clean_text(raw_line, limit=80)
                if cleaned:
                    first_observation = cleaned
                    break
            if first_observation:
                paragraphs.append(
                    f"今天留下的观察虽然不算多，但能看出来主要都围绕“{first_observation}”这一类事情在推进。"
                )
            else:
                paragraphs.append(
                    "今天留下来的记录比较零散，暂时还拼不出特别完整的长篇感想，但能感觉到用户一直在认真推进手头的事。"
                )

        if len(paragraphs) == 1:
            paragraphs.append("先把最明显的脉络记下来，至少明天回看时还能迅速接上今天的节奏。")

        return "\n\n".join(paragraphs[:3]).strip()

    def _ensure_diary_reflection_text(
        self,
        reflection_text: str,
        observation_text: str,
        structured_summary: dict[str, Any] | None = None,
    ) -> str:
        cleaned = self._sanitize_diary_section_text(reflection_text)
        if cleaned:
            return cleaned
        return self._build_diary_reflection_fallback(
            observation_text=observation_text,
            structured_summary=structured_summary,
        )

    def _extract_diary_preview_text(self, diary_content: str) -> str:
        import re

        text = str(diary_content or "").replace("\r\n", "\n").strip()
        if not text:
            return ""

        section_patterns = [
            r"##\s*今日感想\s*([\s\S]*?)(?=\n##\s*[^\n]+|$)",
            r"##\s*[^ \n]*总结\s*([\s\S]*?)(?=\n##\s*[^\n]+|$)",
            r"##\s*今日观察\s*([\s\S]*?)(?=\n##\s*[^\n]+|$)",
        ]
        for pattern in section_patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            section_text = self._sanitize_diary_section_text(match.group(1))
            if section_text:
                return section_text[:500]

        lines = []
        skip_patterns = [
            re.compile(r"^\s*#\s*.+日记\s*$"),
            re.compile(r"^\s*##\s*\d{4}年\d{1,2}月\d{1,2}日.*$"),
            re.compile(r"^\s*\*\*天气\*\*:\s*.*$"),
            re.compile(r"^\s*##\s*今日概览\s*$"),
            re.compile(r"^\s*##\s*今日观察\s*$"),
            re.compile(r"^\s*##\s*今日感想\s*$"),
        ]
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            if any(pattern.match(line) for pattern in skip_patterns):
                continue
            lines.append(raw_line)

        return "\n".join(lines).strip()[:500]

    def _get_diary_summary_path(self, target_date: datetime.date) -> str:
        return os.path.join(
            self.diary_storage,
            f"diary_{target_date.strftime('%Y%m%d')}.summary.json",
        )

    def _load_diary_structured_summary(self, target_date: datetime.date) -> dict[str, Any]:
        summary_path = self._get_diary_summary_path(target_date)
        if not os.path.exists(summary_path):
            return {}
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.debug(f"读取日记结构化摘要失败: {e}")
            return {}

    def _save_diary_structured_summary(
        self,
        target_date: datetime.date,
        structured_summary: dict[str, Any],
    ) -> None:
        summary_path = self._get_diary_summary_path(target_date)
        try:
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(structured_summary, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存日记结构化摘要失败: {e}")

    def _remember_diary_summary_memories(
        self,
        target_date: datetime.date,
        structured_summary: dict[str, Any],
    ) -> None:
        if not isinstance(structured_summary, dict):
            return

        diary_date = target_date.isoformat()
        main_windows = structured_summary.get("main_windows", []) or []
        for item in main_windows[:3]:
            window_title = self._normalize_window_title(item.get("window_title", ""))
            if not window_title:
                continue
            duration_minutes = int(item.get("duration_minutes", 0) or 0)
            focus_text = self._extract_memory_focus(item.get("focus", ""), max_length=56)
            summary = f"{diary_date} 主要停留在《{window_title}》约 {duration_minutes} 分钟"
            if focus_text:
                summary += f"，当时在处理：{focus_text}"
            self._remember_episodic_memory(
                scene="",
                active_window=window_title,
                summary=summary,
                kind="diary_summary",
            )
            if focus_text:
                self._remember_focus_pattern(
                    scene="",
                    active_window=window_title,
                    summary=focus_text,
                )

        longest_task = structured_summary.get("longest_task", {}) or {}
        if isinstance(longest_task, dict) and longest_task.get("window_title"):
            longest_summary = (
                f"{diary_date} 最长停留任务是《{longest_task.get('window_title')}》"
            )
            focus_text = self._extract_memory_focus(longest_task.get("focus", ""), max_length=56)
            if focus_text:
                longest_summary += f"，主要在：{focus_text}"
            self._remember_episodic_memory(
                scene="",
                active_window=str(longest_task.get("window_title", "") or ""),
                summary=longest_summary,
                kind="diary_summary",
            )

        repeated_focuses = structured_summary.get("repeated_focuses", []) or []
        for item in repeated_focuses[:3]:
            note_text = self._extract_memory_focus(item.get("note", ""), max_length=48)
            window_title = self._normalize_window_title(item.get("window_title", ""))
            if not note_text:
                continue
            self._remember_focus_pattern(
                scene="",
                active_window=window_title,
                summary=note_text,
            )

    def _clean_long_term_memory_noise(self):
        """Remove low-value labels from long-term memory."""
        memory = getattr(self, "long_term_memory", None)
        if not isinstance(memory, dict):
            return
        self._ensure_long_term_memory_defaults()

        # 保留 self_image 记忆
        self_image_memory = memory.get("self_image", [])

        applications = memory.get("applications", {})
        if isinstance(applications, dict):
            cleaned_applications = {}
            for app_name, data in applications.items():
                normalized_app = self._normalize_window_title(app_name)
                if not normalized_app:
                    continue
                app_data = dict(data or {})
                raw_scenes = app_data.get("scenes", {}) or {}
                cleaned_scenes = {}
                for scene_name, count in raw_scenes.items():
                    normalized_scene = self._normalize_scene_label(scene_name)
                    if normalized_scene:
                        cleaned_scenes[normalized_scene] = count
                app_data["scenes"] = self._limit_ranked_dict_items(
                    cleaned_scenes,
                    limit=20,
                    score_keys=("priority", "usage_count", "count"),
                )
                cleaned_applications[normalized_app] = app_data
            memory["applications"] = self._limit_ranked_dict_items(
                cleaned_applications,
                limit=80,
                score_keys=("priority", "usage_count", "total_duration"),
            )

        scenes = memory.get("scenes", {})
        if isinstance(scenes, dict):
            cleaned_scenes = {}
            for scene_name, data in scenes.items():
                normalized_scene = self._normalize_scene_label(scene_name)
                if normalized_scene:
                    scene_data = dict(data or {})
                    if "usage_count" not in scene_data and "count" in scene_data:
                        scene_data["usage_count"] = int(scene_data.get("count", 0) or 0)
                    cleaned_scenes[normalized_scene] = scene_data
            memory["scenes"] = self._limit_ranked_dict_items(
                cleaned_scenes,
                limit=40,
                score_keys=("priority", "usage_count"),
            )

        associations = memory.get("memory_associations", {})
        if isinstance(associations, dict):
            cleaned_associations = {}
            for assoc_name, data in associations.items():
                if "_" not in assoc_name:
                    continue
                scene_name, app_name = assoc_name.split("_", 1)
                normalized_scene = self._normalize_scene_label(scene_name)
                normalized_app = self._normalize_window_title(app_name)
                if normalized_scene and normalized_app:
                    cleaned_associations[f"{normalized_scene}_{normalized_app}"] = data
            memory["memory_associations"] = self._limit_ranked_dict_items(
                cleaned_associations,
                limit=120,
                score_keys=("count",),
            )

        preferences = memory.get("user_preferences", {})
        if isinstance(preferences, dict):
            cleaned_preferences = {}
            for category, values in preferences.items():
                if not isinstance(values, dict):
                    continue
                filtered = {
                    str(name).strip(): data
                    for name, data in values.items()
                    if str(name).strip()
                }
                cleaned_preferences[category] = self._limit_ranked_dict_items(
                    filtered,
                    limit=30,
                    score_keys=("priority", "count"),
                )
            memory["user_preferences"] = cleaned_preferences

        shared_activities = memory.get("shared_activities", {})
        if isinstance(shared_activities, dict):
            cleaned_shared_activities = {}
            for activity_name, data in shared_activities.items():
                normalized_activity = self._normalize_shared_activity_summary(activity_name)
                if not normalized_activity:
                    continue
                activity_data = dict(data or {})
                activity_data["category"] = str(activity_data.get("category", "other") or "other")
                cleaned_shared_activities[normalized_activity] = activity_data
            memory["shared_activities"] = self._limit_ranked_dict_items(
                cleaned_shared_activities,
                limit=60,
                score_keys=("priority", "count"),
            )

        episodic_memories = memory.get("episodic_memories", [])
        if isinstance(episodic_memories, list):
            cleaned_episodes = []
            seen_episode_keys = set()
            for item in episodic_memories:
                if not isinstance(item, dict):
                    continue
                summary = self._extract_memory_focus(item.get("summary", ""), max_length=72)
                if not summary:
                    continue
                scene = self._normalize_scene_label(item.get("scene", ""))
                active_window = self._normalize_window_title(item.get("active_window", ""))
                dedupe_key = (
                    scene.casefold(),
                    active_window.casefold(),
                    self._normalize_record_text(summary),
                )
                if dedupe_key in seen_episode_keys:
                    continue
                seen_episode_keys.add(dedupe_key)
                cleaned_episodes.append(
                    {
                        "scene": scene,
                        "active_window": active_window,
                        "summary": summary,
                        "response_preview": self._truncate_preview_text(
                            item.get("response_preview", ""),
                            limit=120,
                        ),
                        "kind": str(item.get("kind", "screen_observation") or "screen_observation"),
                        "count": int(item.get("count", 0) or 0),
                        "first_seen": str(item.get("first_seen", "") or ""),
                        "last_seen": str(item.get("last_seen", "") or ""),
                        "updated_at": str(item.get("updated_at", "") or ""),
                        "priority": int(item.get("priority", 0) or 0),
                    }
                )
            cleaned_episodes.sort(
                key=lambda item: (
                    int(item.get("priority", 0) or 0),
                    int(item.get("count", 0) or 0),
                    str(item.get("last_seen", "") or ""),
                ),
                reverse=True,
            )
            memory["episodic_memories"] = cleaned_episodes[: self.EPISODIC_MEMORY_LIMIT]

        focus_patterns = memory.get("focus_patterns", {})
        if isinstance(focus_patterns, dict):
            cleaned_focus_patterns = {}
            for pattern_key, data in focus_patterns.items():
                if not isinstance(data, dict):
                    continue
                summary = self._extract_memory_focus(data.get("summary", ""), max_length=48)
                scene = self._normalize_scene_label(data.get("scene", ""))
                active_window = self._normalize_window_title(data.get("active_window", ""))
                if not summary:
                    continue
                normalized_key = f"{scene or 'general'}::{active_window or 'window'}::{summary}"
                cleaned_focus_patterns[normalized_key] = {
                    "scene": scene,
                    "active_window": active_window,
                    "summary": summary,
                    "count": int(data.get("count", 0) or 0),
                    "last_seen": str(data.get("last_seen", "") or ""),
                    "priority": int(data.get("priority", 0) or 0),
                }
            memory["focus_patterns"] = self._limit_ranked_dict_items(
                cleaned_focus_patterns,
                limit=self.FOCUS_PATTERN_LIMIT,
                score_keys=("priority", "count"),
            )
        
        # 恢复 self_image 记忆
        if self_image_memory:
            memory["self_image"] = self_image_memory
        else:
            memory.pop("self_image", None)

    def _update_long_term_memory(
        self,
        scene,
        active_window,
        duration,
        user_preferences=None,
        memory_summary: str = "",
        response_preview: str = "",
    ):
        """更新长期记忆。"""
        import datetime
        today = datetime.date.today().isoformat()
        scene = self._normalize_scene_label(scene)
        active_window = self._normalize_window_title(active_window)

        self._ensure_long_term_memory_defaults()

        app_name = active_window.split(" - ")[-1] if " - " in active_window else active_window
        app_name = self._normalize_window_title(app_name)
        continuing_context = self._is_continuing_memory_context(scene, active_window)

        # 更新应用使用频率
        if app_name:
            if app_name not in self.long_term_memory["applications"]:
                self.long_term_memory["applications"][app_name] = {
                    "usage_count": 0,
                    "total_duration": 0,
                    "last_used": today,
                    "scenes": {},
                    "priority": 0
                }

            app_memory = self.long_term_memory["applications"][app_name]
            if not continuing_context:
                app_memory["usage_count"] += 1
            app_memory["total_duration"] += duration
            app_memory["last_used"] = today

            if scene:
                if scene not in app_memory["scenes"]:
                    app_memory["scenes"][scene] = 0
                if not continuing_context:
                    app_memory["scenes"][scene] += 1

        # 更新场景偏好
        if scene:
            if scene not in self.long_term_memory["scenes"]:
                self.long_term_memory["scenes"][scene] = {
                    "usage_count": 0,
                    "last_used": today,
                    "priority": 0
                }
            if not continuing_context:
                self.long_term_memory["scenes"][scene]["usage_count"] += 1
            self.long_term_memory["scenes"][scene]["last_used"] = today
        
        # 更新用户偏好（如果有）
        if user_preferences:
            for category, preferences in user_preferences.items():
                if category not in self.long_term_memory["user_preferences"]:
                    self.long_term_memory["user_preferences"][category] = {}
                for pref, value in preferences.items():
                    if pref not in self.long_term_memory["user_preferences"][category]:
                        self.long_term_memory["user_preferences"][category][pref] = {
                            "count": 0,
                            "last_mentioned": today,
                            "priority": 0
                        }
                    self.long_term_memory["user_preferences"][category][pref]["count"] += 1
                    self.long_term_memory["user_preferences"][category][pref]["last_mentioned"] = today
        
        # 建立记忆关联
        if scene and app_name and not continuing_context:
            self._build_memory_associations(scene, app_name)

        if memory_summary:
            self._remember_episodic_memory(
                scene=scene,
                active_window=active_window,
                summary=memory_summary,
                response_preview=response_preview,
            )
            self._remember_focus_pattern(
                scene=scene,
                active_window=active_window,
                summary=memory_summary,
            )
        
        self._update_memory_priorities()
        
        # 应用记忆衰减
        self._apply_memory_decay()
        
        # 保存长期记忆
        self._save_long_term_memory()

    def _apply_memory_decay(self):
        """对长期记忆做温和清理，避免短期未使用就被抹掉。"""
        import datetime
        today = datetime.date.today()

        if "applications" in self.long_term_memory:
            for app_name, app_data in list(self.long_term_memory["applications"].items()):
                last_used_text = str(app_data.get("last_used", "") or "").strip()
                if not last_used_text:
                    continue
                try:
                    last_used_date = datetime.date.fromisoformat(last_used_text)
                except ValueError:
                    continue

                days_since_used = (today - last_used_date).days
                usage_count = int(app_data.get("usage_count", 0) or 0)
                total_duration = int(app_data.get("total_duration", 0) or 0)
                if (
                    days_since_used > self.LONG_TERM_MEMORY_RETENTION_DAYS
                    and usage_count <= 1
                    and total_duration <= 5
                ):
                    del self.long_term_memory["applications"][app_name]

        if "scenes" in self.long_term_memory:
            for scene_name, scene_data in list(self.long_term_memory["scenes"].items()):
                last_used_text = str(scene_data.get("last_used", "") or "").strip()
                if not last_used_text:
                    continue
                try:
                    last_used_date = datetime.date.fromisoformat(last_used_text)
                except ValueError:
                    continue

                days_since_used = (today - last_used_date).days
                usage_count = int(scene_data.get("usage_count", 0) or 0)
                if (
                    days_since_used > self.LONG_TERM_MEMORY_RETENTION_DAYS
                    and usage_count <= 1
                ):
                    del self.long_term_memory["scenes"][scene_name]

        if "user_preferences" in self.long_term_memory:
            for category, preferences in list(self.long_term_memory["user_preferences"].items()):
                for pref, data in list(preferences.items()):
                    last_mentioned_text = str(data.get("last_mentioned", "") or "").strip()
                    if not last_mentioned_text:
                        continue
                    try:
                        last_mentioned_date = datetime.date.fromisoformat(last_mentioned_text)
                    except ValueError:
                        continue
                    days_since_mentioned = (today - last_mentioned_date).days

                    if (
                        days_since_mentioned > self.LIGHT_MEMORY_RETENTION_DAYS
                        and int(data.get("count", 0) or 0) <= 1
                    ):
                        del preferences[pref]

                if not preferences:
                    del self.long_term_memory["user_preferences"][category]

        if "shared_activities" in self.long_term_memory:
            for activity_name, activity_data in list(self.long_term_memory["shared_activities"].items()):
                last_shared = str(activity_data.get("last_shared", "") or "").strip()
                if not last_shared:
                    continue
                try:
                    last_shared_date = datetime.date.fromisoformat(last_shared)
                except ValueError:
                    continue

                days_since_shared = (today - last_shared_date).days
                if (
                    days_since_shared > self.LIGHT_MEMORY_RETENTION_DAYS
                    and int(activity_data.get("count", 0) or 0) <= 1
                ):
                    del self.long_term_memory["shared_activities"][activity_name]

        episodic_memories = self.long_term_memory.get("episodic_memories", [])
        if isinstance(episodic_memories, list):
            retained_episodes = []
            for item in episodic_memories:
                if not isinstance(item, dict):
                    continue
                last_seen_text = str(item.get("last_seen", "") or "").strip()
                if not last_seen_text:
                    retained_episodes.append(item)
                    continue
                try:
                    last_seen_date = datetime.date.fromisoformat(last_seen_text)
                except ValueError:
                    retained_episodes.append(item)
                    continue
                days_since_seen = (today - last_seen_date).days
                if (
                    days_since_seen > self.LIGHT_MEMORY_RETENTION_DAYS
                    and int(item.get("count", 0) or 0) <= 1
                ):
                    continue
                retained_episodes.append(item)
            self.long_term_memory["episodic_memories"] = retained_episodes

        focus_patterns = self.long_term_memory.get("focus_patterns", {})
        if isinstance(focus_patterns, dict):
            for pattern_key, item in list(focus_patterns.items()):
                if not isinstance(item, dict):
                    del focus_patterns[pattern_key]
                    continue
                last_seen_text = str(item.get("last_seen", "") or "").strip()
                if not last_seen_text:
                    continue
                try:
                    last_seen_date = datetime.date.fromisoformat(last_seen_text)
                except ValueError:
                    continue
                days_since_seen = (today - last_seen_date).days
                if (
                    days_since_seen > self.LIGHT_MEMORY_RETENTION_DAYS
                    and int(item.get("count", 0) or 0) <= 1
                ):
                    del focus_patterns[pattern_key]

    @staticmethod
    def _build_memory_priority_value(base_count: int | float, days_since: int) -> int:
        count = float(base_count or 0)
        days = max(0, int(days_since or 0))
        if count <= 0:
            return 0
        score = count * (1 / (1 + days))
        return max(1, int(round(score)))

    def _build_memory_associations(self, scene, app_name):
        """建立场景与应用之间的记忆关联。"""
        import datetime
        # 关联场景和应用
        association_key = f"{scene}_{app_name}"
        if association_key not in self.long_term_memory["memory_associations"]:
            self.long_term_memory["memory_associations"][association_key] = {
                "count": 0,
                "last_occurred": datetime.date.today().isoformat()
            }
        
        self.long_term_memory["memory_associations"][association_key]["count"] += 1
        self.long_term_memory["memory_associations"][association_key]["last_occurred"] = datetime.date.today().isoformat()

    def _build_companion_response_guide(self, scene: str, recognition_text: str, custom_prompt: str, context_count: int) -> str:
        """构建同伴响应指南"""
        guide = "# 同伴响应指南\n"
        guide += "\n## 核心原则\n"
        guide += "- 像真实同伴一样回应，避免机械感\n"
        guide += "- 优先关注用户当前正在做的事情\n"
        guide += "- 提供与场景相关的具体建议\n"
        guide += "- 保持自然的语言风格\n"
        guide += "- 把这次回复当成上一条的延续，不要每条都重新起头\n"
        guide += "- 不要反复使用相同称呼或相同提醒模板开场\n"
        guide += "- 如果前面已经提醒过休息，这条默认不要重复催睡\n"
        
        if scene in ("视频", "阅读"):
            guide += "\n## 视频/阅读场景建议\n"
            guide += "- 关注内容的情感和观点\n"
            guide += "- 避免过度干扰用户体验\n"
            guide += "- 提供与内容相关的见解\n"
        else:
            guide += "\n## 一般场景建议\n"
            guide += "- 关注用户的操作和进展\n"
            guide += "- 提供实用的建议和提醒\n"
            guide += "- 保持对话的自然流畅\n"
        
        if context_count > 0:
            guide += "\n## 对话历史参考\n"
            guide += "- 参考最近的对话内容\n"
            guide += "- 保持回应的连贯性\n"
            guide += "- 只补充新的观察、变化或下一步，不要复述上一条已经说过的话\n"

        return guide

    def _update_memory_priorities(self):
        """根据近期活跃度重新计算记忆优先级。"""
        import datetime
        today = datetime.date.today()
        
        if "applications" in self.long_term_memory:
            for app_name, app_data in self.long_term_memory["applications"].items():
                # 基于使用频率和最近使用时间计算优先级
                last_used_date = datetime.date.fromisoformat(app_data["last_used"])
                days_since_used = (today - last_used_date).days

                app_data["priority"] = self._build_memory_priority_value(
                    app_data.get("usage_count", 0),
                    days_since_used,
                )
        
        if "scenes" in self.long_term_memory:
            for scene_name, scene_data in self.long_term_memory["scenes"].items():
                last_used_date = datetime.date.fromisoformat(scene_data["last_used"])
                days_since_used = (today - last_used_date).days

                scene_data["priority"] = self._build_memory_priority_value(
                    scene_data.get("usage_count", 0),
                    days_since_used,
                )
        
        if "user_preferences" in self.long_term_memory:
            for category, preferences in self.long_term_memory["user_preferences"].items():
                for pref, data in preferences.items():
                    last_mentioned_date = datetime.date.fromisoformat(data["last_mentioned"])
                    days_since_mentioned = (today - last_mentioned_date).days

                    data["priority"] = self._build_memory_priority_value(
                        data.get("count", 0),
                        days_since_mentioned,
                    )

        if "shared_activities" in self.long_term_memory:
            for activity_name, data in self.long_term_memory["shared_activities"].items():
                last_shared = str(data.get("last_shared", "") or "").strip()
                if not last_shared:
                    data["priority"] = int(data.get("count", 0) or 0)
                    continue
                try:
                    last_shared_date = datetime.date.fromisoformat(last_shared)
                except ValueError:
                    data["priority"] = int(data.get("count", 0) or 0)
                    continue

                days_since_shared = (today - last_shared_date).days
                data["priority"] = self._build_memory_priority_value(
                    data.get("count", 0),
                    days_since_shared,
                )

        episodic_memories = self.long_term_memory.get("episodic_memories", [])
        if isinstance(episodic_memories, list):
            for item in episodic_memories:
                if not isinstance(item, dict):
                    continue
                last_seen_text = str(item.get("last_seen", "") or "").strip()
                if not last_seen_text:
                    item["priority"] = int(item.get("count", 0) or 0)
                    continue
                try:
                    last_seen_date = datetime.date.fromisoformat(last_seen_text)
                except ValueError:
                    item["priority"] = int(item.get("count", 0) or 0)
                    continue
                item["priority"] = self._build_memory_priority_value(
                    item.get("count", 0),
                    (today - last_seen_date).days,
                )

        focus_patterns = self.long_term_memory.get("focus_patterns", {})
        if isinstance(focus_patterns, dict):
            for _, item in focus_patterns.items():
                if not isinstance(item, dict):
                    continue
                last_seen_text = str(item.get("last_seen", "") or "").strip()
                if not last_seen_text:
                    item["priority"] = int(item.get("count", 0) or 0)
                    continue
                try:
                    last_seen_date = datetime.date.fromisoformat(last_seen_text)
                except ValueError:
                    item["priority"] = int(item.get("count", 0) or 0)
                    continue
                item["priority"] = self._build_memory_priority_value(
                    item.get("count", 0),
                    (today - last_seen_date).days,
                )

    def _trigger_related_memories(self, scene, app_name):
        """触发与当前场景相关的记忆。"""
        self._ensure_long_term_memory_defaults()
        normalized_scene = self._normalize_scene_label(scene)
        normalized_app = self._normalize_window_title(app_name)
        memory_candidates: list[tuple[float, str]] = []

        episodic_memories = self.long_term_memory.get("episodic_memories", []) or []
        for item in episodic_memories:
            if not isinstance(item, dict):
                continue
            item_scene = self._normalize_scene_label(item.get("scene", ""))
            item_window = self._normalize_window_title(item.get("active_window", ""))
            if normalized_scene and item_scene and normalized_scene != item_scene:
                continue
            if normalized_app and item_window and normalized_app != item_window:
                continue
            summary = self._extract_memory_focus(item.get("summary", ""), max_length=72)
            if not summary:
                continue
            count = int(item.get("count", 0) or 0)
            priority = int(item.get("priority", 0) or 0)
            if count <= 0 and priority <= 0:
                continue
            score = priority * 4 + count * 2
            if normalized_app and item_window and normalized_app == item_window:
                score += 3
            if normalized_scene and item_scene and normalized_scene == item_scene:
                score += 2
            memory_candidates.append(
                (
                    score,
                    f"你前几次在《{item_window or normalized_app or '这个窗口'}》里也在处理：{summary}。",
                )
            )

        focus_patterns = self.long_term_memory.get("focus_patterns", {}) or {}
        for _, item in focus_patterns.items():
            if not isinstance(item, dict):
                continue
            item_scene = self._normalize_scene_label(item.get("scene", ""))
            item_window = self._normalize_window_title(item.get("active_window", ""))
            if normalized_scene and item_scene and normalized_scene != item_scene:
                continue
            if normalized_app and item_window and normalized_app != item_window:
                continue
            summary = self._extract_memory_focus(item.get("summary", ""), max_length=48)
            if not summary:
                continue
            count = int(item.get("count", 0) or 0)
            priority = int(item.get("priority", 0) or 0)
            if count < 2 and priority <= 1:
                continue
            score = priority * 3 + count
            memory_candidates.append(
                (
                    score,
                    f"这个场景里你反复会关注：{summary}。",
                )
            )

        scene_memory = self.long_term_memory.get("scenes", {}).get(normalized_scene, {})
        if normalized_scene and isinstance(scene_memory, dict):
            usage_count = int(scene_memory.get("usage_count", 0) or 0)
            priority = int(scene_memory.get("priority", 0) or 0)
            if usage_count > 0 or priority > 0:
                score = priority * 2 + usage_count
                memory_candidates.append(
                    (
                        score,
                        f"你最近经常处在「{normalized_scene}」场景，适合沿着当前任务继续往前推。",
                    )
                )

        app_memory = self.long_term_memory.get("applications", {}).get(normalized_app, {})
        if normalized_app and isinstance(app_memory, dict):
            usage_count = int(app_memory.get("usage_count", 0) or 0)
            total_duration = int(app_memory.get("total_duration", 0) or 0)
            top_scenes = sorted(
                (app_memory.get("scenes", {}) or {}).items(),
                key=lambda item: item[1],
                reverse=True,
            )[:2]
            top_scene_text = "、".join(name for name, _ in top_scenes if name)
            if usage_count > 0 or total_duration > 0:
                score = int(app_memory.get("priority", 0) or 0) * 3 + usage_count + total_duration / 60
                summary = f"你之前经常在《{normalized_app}》里处理{top_scene_text or '当前这类'}任务。"
                memory_candidates.append((score, summary))

        association_key = f"{normalized_scene}_{normalized_app}"
        association_data = self.long_term_memory.get("memory_associations", {}).get(
            association_key,
            {},
        )
        if normalized_scene and normalized_app and isinstance(association_data, dict):
            association_count = int(association_data.get("count", 0) or 0)
            if association_count > 1:
                memory_candidates.append(
                    (
                        association_count * 4,
                        f"「{normalized_scene} + {normalized_app}」这个组合你最近反复出现，可能就是今天的主要任务线。",
                    )
                )

        profile = self._get_scene_behavior_profile(normalized_scene)
        preference_categories = ["hobbies", "other"]
        if profile["category"] == "entertainment":
            preference_categories = ["music", "movies", "hobbies", "other"]
        elif profile["category"] == "work":
            preference_categories = ["other", "hobbies"]

        user_preferences = self.long_term_memory.get("user_preferences", {}) or {}
        for category in preference_categories:
            preferences = user_preferences.get(category, {}) or {}
            ranked_preferences = sorted(
                preferences.items(),
                key=lambda item: (
                    int((item[1] or {}).get("priority", 0) or 0),
                    int((item[1] or {}).get("count", 0) or 0),
                ),
                reverse=True,
            )[:2]
            for pref_name, pref_data in ranked_preferences:
                priority = int((pref_data or {}).get("priority", 0) or 0)
                if priority <= 0:
                    continue
                label = {
                    "music": "偏爱的音乐",
                    "movies": "喜欢的内容",
                    "hobbies": "平时爱做的事",
                    "other": "你在意的点",
                }.get(category, "偏好")
                memory_candidates.append(
                    (
                        priority,
                        f"可以顺手呼应用户{label}：{pref_name}。",
                    )
                )

        deduped = []
        seen = set()
        for _, summary in sorted(memory_candidates, key=lambda item: item[0], reverse=True):
            normalized_summary = self._normalize_record_text(summary)
            if not normalized_summary or normalized_summary in seen:
                continue
            seen.add(normalized_summary)
            deduped.append(summary)
            if len(deduped) >= 4:
                break

        return deduped

    def _add_user_preference(self, category, preference):
        """添加一条用户偏好。"""
        import datetime
        today = datetime.date.today().isoformat()
        
        if "user_preferences" not in self.long_term_memory:
            self.long_term_memory["user_preferences"] = {
                "music": {},
                "movies": {},
                "food": {},
                "hobbies": {},
                "other": {}
            }
        
        if category not in self.long_term_memory["user_preferences"]:
            self.long_term_memory["user_preferences"][category] = {}
        
        if preference not in self.long_term_memory["user_preferences"][category]:
            self.long_term_memory["user_preferences"][category][preference] = {
                "count": 0,
                "last_mentioned": today,
                "priority": 0
            }
        
        self.long_term_memory["user_preferences"][category][preference]["count"] += 1
        self.long_term_memory["user_preferences"][category][preference]["last_mentioned"] = today
        
        self._update_memory_priorities()
        # 保存记忆
        self._save_long_term_memory()
        
        logger.info(f"已添加用户偏好: {category} - {preference}")

    @staticmethod
    def _shared_activity_category_label(category: str) -> str:
        labels = {
            "watch_media": "一起看过",
            "game": "一起玩过",
            "test": "一起做过测试",
            "screen_interaction": "一起进行过识屏互动",
            "other": "一起做过",
        }
        return labels.get(str(category or "other"), "一起做过")

    def _get_relevant_shared_activities(self, scene: str, limit: int = 3) -> list[tuple[str, dict]]:
        shared_activities = self.long_term_memory.get("shared_activities", {})
        if not isinstance(shared_activities, dict) or not shared_activities:
            return []

        scene = self._normalize_scene_label(scene)
        category_map = {
            "视频": {"watch_media", "screen_interaction"},
            "阅读": {"watch_media", "screen_interaction", "test"},
            "游戏": {"game", "screen_interaction"},
            "学习": {"test", "screen_interaction"},
            "浏览": {"watch_media", "screen_interaction", "test"},
            "浏览-娱乐": {"watch_media", "game", "screen_interaction"},
            "社交": {"screen_interaction"},
        }
        wanted_categories = category_map.get(scene, set())

        ranked_items = sorted(
            shared_activities.items(),
            key=lambda item: (
                int((item[1] or {}).get("priority", 0) or 0),
                int((item[1] or {}).get("count", 0) or 0),
                str((item[1] or {}).get("last_shared", "") or ""),
            ),
            reverse=True,
        )

        matched = []
        fallback = []
        for activity_name, data in ranked_items:
            if not isinstance(data, dict):
                continue
            if int(data.get("priority", 0) or 0) <= 0 and int(data.get("count", 0) or 0) <= 0:
                continue
            item = (activity_name, data)
            if wanted_categories and str(data.get("category", "other") or "other") in wanted_categories:
                matched.append(item)
            else:
                fallback.append(item)

        picked = matched[:limit]
        if len(picked) < limit:
            picked.extend(fallback[: max(0, limit - len(picked))])
        return picked[:limit]

    def _should_offer_shared_activity_invite(self, scene: str, custom_prompt: str = "") -> bool:
        leisure_scenes = {"视频", "阅读", "游戏", "音乐", "社交", "浏览", "浏览-娱乐"}
        if custom_prompt:
            return False
        if scene not in leisure_scenes and not self.long_term_memory.get("shared_activities"):
            return False

        now_ts = time.time()
        if now_ts - float(getattr(self, "last_shared_activity_invite_time", 0.0) or 0.0) < 7200:
            return False

        self.last_shared_activity_invite_time = now_ts
        return True

    def _extract_shared_activity_from_message(self, message_text: str) -> tuple[str, str] | tuple[None, None]:
        import re

        text = str(message_text or "").strip()
        if not text or text.startswith("/"):
            return None, None

        escaped_bot_name = re.escape(str(getattr(self, "bot_name", "") or "").strip())
        together_patterns = [
            r"和你",
            r"跟你",
            r"我们一起",
            r"咱们一起",
            r"你刚刚陪我",
            r"你刚刚帮我",
            r"你陪我",
            r"你帮我",
        ]
        if escaped_bot_name:
            together_patterns.extend(
                [
                    rf"和{escaped_bot_name}",
                    rf"跟{escaped_bot_name}",
                    rf"{escaped_bot_name}陪我",
                    rf"{escaped_bot_name}帮我",
                ]
            )

        if not any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in together_patterns):
            return None, None

        future_only_markers = (
            "想和你一起",
            "想跟你一起",
            "要不要一起",
            "一起吗",
            "改天一起",
            "下次一起",
            "等会一起",
            "待会一起",
        )
        past_markers = ("刚", "刚刚", "已经", "过", "了", "完", "通关")
        if any(marker in text for marker in future_only_markers) and not any(
            marker in text for marker in past_markers
        ):
            return None, None

        title_match = re.search(r"《[^》]{1,30}》", text)
        title = title_match.group(0) if title_match else ""

        watch_ready = re.search(r"(看|追|补|刷).{0,12}(过|了|完|完了)", text)
        game_ready = re.search(r"(玩|打|开黑|跑团|通关).{0,12}(过|了|完|通关)", text)
        test_ready = re.search(r"(做|测|试).{0,12}(过|了|完)", text)
        screen_ready = re.search(
            r"(看|分析|研究|判断|排查).{0,12}(过|了|完)",
            text,
        )

        watch_keywords = ("电影", "动漫", "番", "动画", "剧", "视频", "纪录片", "直播")
        if watch_ready and (title or any(keyword in text for keyword in watch_keywords)):
            if title:
                return "watch_media", f"一起看{title}"
            media_summary_map = {
                "电影": "一起看电影",
                "动漫": "一起看动漫",
                "番": "一起看动漫",
                "动画": "一起看动漫",
                "剧": "一起追剧",
                "纪录片": "一起看纪录片",
                "直播": "一起看直播",
                "视频": "一起看视频",
            }
            for keyword, summary in media_summary_map.items():
                if keyword in text:
                    return "watch_media", summary

        game_keywords = ("游戏", "开黑", "这局", "这一局")
        if game_ready and (title or any(keyword in text for keyword in game_keywords)):
            if title:
                return "game", f"一起玩{title}"
            if "开黑" in text:
                return "game", "一起开黑"
            if "这局" in text or "这一局" in text:
                return "game", "一起打这局游戏"
            return "game", "一起玩游戏"

        topic_match = re.search(r"([\u4e00-\u9fffA-Za-z0-9]{2,24}测试)", text)
        if test_ready and any(keyword in text for keyword in ("测试", "测评", "题", "问卷", "人格")):
            if topic_match:
                return "test", f"一起做{topic_match.group(1)}"
            if "人格" in text:
                return "test", "一起做人格测试"
            return "test", "一起做测试"

        screen_keywords = {
            "这题": "一起看这道题",
            "这道题": "一起看这道题",
            "这个页面": "一起看这个页面",
            "这个界面": "一起看这个界面",
            "这个截图": "一起看这个截图",
            "这张图": "一起看这张图",
            "这局": "一起看这局",
            "这一局": "一起看这局",
            "这个弹窗": "一起看这个弹窗",
        }
        if screen_ready:
            for keyword, summary in screen_keywords.items():
                if keyword in text:
                    return "screen_interaction", summary

        return None, None

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
        import time
        current_time = time.time()

        # 定义工作和摸鱼场景
        work_scenes = ["编程", "设计", "办公", "邮件", "浏览-工作"]
        play_scenes = ["游戏", "视频", "音乐", "社交", "浏览-娱乐"]

        # 确定当前活动类型
        activity_type = "其他"
        if scene in work_scenes:
            activity_type = "工作"
        elif scene in play_scenes:
            activity_type = "摸鱼"

        # 创建活动标识
        activity = f"{activity_type}:{scene}:{active_window[:50]}"

        # 如果活动发生变化，记录上一个活动的时间
        if self.current_activity != activity:
            if self.current_activity and self.activity_start_time:
                self._append_activity_record(
                    activity=self.current_activity,
                    start_time=self.activity_start_time,
                    end_time=current_time,
                )

            # 更新当前活动
            self.current_activity = activity
            self.activity_start_time = current_time

        return activity_type

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
            payload = {
                "last_rest_reminder_day": str(
                    getattr(self, "last_rest_reminder_day", "") or ""
                ).strip(),
                "last_rest_reminder_at": (
                    getattr(self, "last_rest_reminder_time", None).isoformat()
                    if isinstance(
                        getattr(self, "last_rest_reminder_time", None),
                        datetime.datetime,
                    )
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
        import time
        current_time = time.time()
        
        # 检查冷却时间
        if not hasattr(self, 'window_change_cooldown'):
            self.window_change_cooldown = 0
        if current_time < self.window_change_cooldown:
            return False, []
        
        # 检查窗口相关属性
        if not hasattr(self, 'previous_windows'):
            self.previous_windows = set()
        if not hasattr(self, 'window_timestamps'):
            self.window_timestamps = {}
        
        # 获取当前打开的窗口
        current_windows = set(self._list_open_window_titles())
        current_windows = {w for w in current_windows if w and w.strip()}
        
        # 更新窗口时间戳
        valid_new_windows = []
        
        # 处理当前存在的窗口
        for window in current_windows:
            if window not in self.window_timestamps:
                # 记录新窗口的首次出现时间
                self.window_timestamps[window] = current_time
            else:
                # 检查窗口是否持续存在3分钟
                if current_time - self.window_timestamps[window] >= 180:  # 3分钟 = 180秒
                    # 窗口持续存在3分钟，标记为有效新窗口
                    if window not in self.previous_windows:
                        valid_new_windows.append(window)
        
        # 清理已关闭的窗口记录
        closed_windows = list(self.window_timestamps.keys())
        for window in closed_windows:
            if window not in current_windows:
                del self.window_timestamps[window]
        
        # 更新窗口状态
        if current_windows != self.previous_windows:
            self.previous_windows = current_windows
            # 设置冷却时间，避免频繁触发
            self.window_change_cooldown = current_time + 5  # 5秒冷却
            return True, valid_new_windows
        
        return False, []

    def _ensure_auto_screen_runtime_state(self, task_id: str) -> dict[str, Any]:
        self._ensure_runtime_state()
        normalized_task_id = str(task_id or self.AUTO_TASK_ID).strip() or self.AUTO_TASK_ID
        runtime = self.auto_screen_runtime
        state = runtime.get(normalized_task_id)
        if not isinstance(state, dict):
            state = {
                "last_seen_window_title": "",
                "last_scene": "",
                "last_change_at": 0.0,
                "last_change_reason": "",
                "last_new_windows": [],
                "last_trigger_at": 0.0,
                "last_trigger_reason": "",
                "last_effective_probability": 0,
                "last_trigger_roll": None,
                "last_idle_keepalive_due": False,
                "last_sent_at": 0.0,
                "last_reply_signature": "",
                "last_reply_window_title": "",
                "last_reply_scene": "",
                "last_reply_preview": "",
                "last_skip_reason": "",
            }
            runtime[normalized_task_id] = state
        return state

    def _build_auto_screen_change_snapshot(
        self,
        task_id: str,
        *,
        window_changed: bool = False,
        new_windows: list[str] | None = None,
        update_state: bool = True,
    ) -> dict[str, Any]:
        state = self._ensure_auto_screen_runtime_state(task_id)
        active_window_title, _ = self._get_active_window_info()
        active_window_title = self._normalize_window_title(active_window_title)
        scene = ""
        if active_window_title:
            scene = self._normalize_scene_label(self._identify_scene(active_window_title))

        previous_window_title = str(state.get("last_seen_window_title", "") or "").strip()
        previous_scene = str(state.get("last_scene", "") or "").strip()
        normalized_new_windows = [
            title
            for title in (self._normalize_window_title(title) for title in (new_windows or []))
            if title
        ]

        reasons: list[str] = []
        if window_changed and normalized_new_windows:
            reasons.append("新窗口出现")
        elif window_changed:
            reasons.append("窗口列表变化")

        if active_window_title and active_window_title.casefold() != previous_window_title.casefold():
            reasons.append("活动窗口变化")

        if scene and previous_scene and scene != previous_scene:
            reasons.append("场景变化")

        changed = bool(reasons)
        now_ts = time.time()
        if update_state:
            state["last_seen_window_title"] = active_window_title
            state["last_scene"] = scene
            state["last_new_windows"] = normalized_new_windows[:3]
            if changed:
                state["last_change_at"] = now_ts
                state["last_change_reason"] = "、".join(dict.fromkeys(reasons))

        return {
            "task_id": str(task_id or self.AUTO_TASK_ID).strip() or self.AUTO_TASK_ID,
            "active_window_title": active_window_title,
            "scene": scene,
            "changed": changed,
            "reason": "、".join(dict.fromkeys(reasons)),
            "new_windows": normalized_new_windows[:3],
            "timestamp": now_ts,
        }

    def _is_idle_keepalive_due(self, task_id: str, check_interval: int) -> bool:
        state = self._ensure_auto_screen_runtime_state(task_id)
        last_sent_at = float(state.get("last_sent_at", 0.0) or 0.0)
        if last_sent_at <= 0:
            return True

        threshold = max(
            int(check_interval or 0) * 3,
            self.CHANGE_AWARE_IDLE_KEEPALIVE_SECONDS,
        )
        return (time.time() - last_sent_at) >= threshold

    def _decide_auto_screen_trigger(
        self,
        task_id: str,
        *,
        probability: int,
        check_interval: int,
        system_high_load: bool,
        change_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        import random

        state = self._ensure_auto_screen_runtime_state(task_id)
        now_ts = time.time()
        if system_high_load:
            decision = {
                "trigger": True,
                "reason": "系统负载较高，强制触发识屏",
                "effective_probability": 100,
                "random_number": None,
                "idle_keepalive_due": False,
            }
        else:
            idle_keepalive_due = self._is_idle_keepalive_due(task_id, check_interval)
            if change_snapshot.get("changed"):
                effective_probability = min(100, max(int(probability or 0), 85))
                reason = f"检测到{change_snapshot.get('reason') or '窗口变化'}，提升本轮触发概率"
            elif idle_keepalive_due:
                effective_probability = min(100, max(int(probability or 0), 30))
                reason = "当前窗口停留较久，保留一次低频跟进机会"
            else:
                effective_probability = min(int(probability or 0), 15)
                reason = "当前画面变化不大，降低本轮触发概率"

            random_number = random.randint(1, 100)
            decision = {
                "trigger": random_number <= effective_probability,
                "reason": reason,
                "effective_probability": effective_probability,
                "random_number": random_number,
                "idle_keepalive_due": idle_keepalive_due,
            }

        state["last_trigger_reason"] = decision["reason"]
        state["last_effective_probability"] = int(decision["effective_probability"] or 0)
        state["last_trigger_roll"] = decision["random_number"]
        state["last_idle_keepalive_due"] = bool(decision["idle_keepalive_due"])
        if decision["trigger"]:
            state["last_trigger_at"] = now_ts
            state["last_skip_reason"] = ""
        return decision

    def _should_skip_similar_auto_reply(
        self,
        task_id: str,
        *,
        active_window_title: str,
        text_content: str,
        check_interval: int,
    ) -> tuple[bool, str]:
        normalized_text = self._normalize_record_text(text_content)[:160]
        if not normalized_text:
            return False, ""

        state = self._ensure_auto_screen_runtime_state(task_id)
        last_signature = str(state.get("last_reply_signature", "") or "").strip()
        last_window_title = self._normalize_window_title(
            state.get("last_reply_window_title", "")
        )
        current_window_title = self._normalize_window_title(active_window_title)
        last_sent_at = float(state.get("last_sent_at", 0.0) or 0.0)
        cooldown_seconds = max(
            int(check_interval or 0) * 3,
            self.CHANGE_AWARE_SIMILAR_REPLY_COOLDOWN_SECONDS,
        )

        if (
            normalized_text
            and last_signature == normalized_text
            and current_window_title
            and current_window_title.casefold() == last_window_title.casefold()
            and last_sent_at > 0
            and (time.time() - last_sent_at) < cooldown_seconds
        ):
            return (
                True,
                f"同一窗口下识别结果相近，仍在 {cooldown_seconds} 秒冷却内",
            )

        return False, ""

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
        state = self._ensure_auto_screen_runtime_state(task_id)
        normalized_text = self._normalize_record_text(text_content)[:160]
        state["last_reply_window_title"] = self._normalize_window_title(active_window_title)
        state["last_reply_scene"] = self._normalize_scene_label(scene)
        if normalized_text:
            state["last_reply_signature"] = normalized_text
        state["last_reply_preview"] = self._truncate_preview_text(text_content, limit=120)
        state["last_skip_reason"] = str(note or "").strip()
        if sent:
            state["last_sent_at"] = time.time()

    def _format_reply_interval_text(self, seconds: float) -> str:
        total_seconds = max(0, int(seconds or 0))
        if total_seconds < 60:
            return f"{total_seconds}秒"

        total_minutes = total_seconds // 60
        if total_minutes < 60:
            if total_minutes < 5 and total_seconds % 60:
                return f"{total_minutes}分{total_seconds % 60}秒"
            return f"{total_minutes}分钟"

        total_hours = total_minutes // 60
        remaining_minutes = total_minutes % 60
        if total_hours < 24:
            if total_hours < 3 and remaining_minutes:
                return f"{total_hours}小时{remaining_minutes}分钟"
            return f"{total_hours}小时"

        total_days = total_hours // 24
        remaining_hours = total_hours % 24
        if total_days < 3 and remaining_hours:
            return f"{total_days}天{remaining_hours}小时"
        return f"{total_days}天"

    def _build_reply_interval_guidance(self, task_id: str) -> tuple[str, dict[str, Any]]:
        state = self._ensure_auto_screen_runtime_state(task_id)
        last_sent_at = float(state.get("last_sent_at", 0.0) or 0.0)
        if last_sent_at <= 0:
            return (
                "这是这段时间里较少见的一次主动靠近。可以自然一点，但仍要直接从当前画面切入，"
                "不要假装刚才已经接过话，也不要写得像固定问候。",
                {
                    "bucket": "first_touch",
                    "elapsed_seconds": 0,
                    "elapsed_text": "",
                },
            )

        elapsed_seconds = max(0, int(time.time() - last_sent_at))
        elapsed_text = self._format_reply_interval_text(elapsed_seconds)

        if elapsed_seconds < 3 * 60:
            return (
                f"距离上一次主动回复仅约 {elapsed_text}。这次更像顺着刚才的话补一句，"
                "只点出新的变化、判断或下一步，不要重新开场，也不要重复同一句提醒。",
                {
                    "bucket": "immediate_followup",
                    "elapsed_seconds": elapsed_seconds,
                    "elapsed_text": elapsed_text,
                },
            )

        if elapsed_seconds < 15 * 60:
            return (
                f"距离上一次主动回复约 {elapsed_text}。延续陪伴感即可，可以轻轻承接刚才到现在的新变化，"
                "但不要把语气写得像重新开始一轮对话。",
                {
                    "bucket": "recent_followup",
                    "elapsed_seconds": elapsed_seconds,
                    "elapsed_text": elapsed_text,
                },
            )

        if elapsed_seconds < 90 * 60:
            return (
                f"距离上一次主动回复约 {elapsed_text}。可以有一点重新跟上的感觉，"
                "先简短点出当前变化，再给一句观察、共鸣或建议；仍然不要太正式。",
                {
                    "bucket": "soft_reentry",
                    "elapsed_seconds": elapsed_seconds,
                    "elapsed_text": elapsed_text,
                },
            )

        return (
            f"距离上一次主动回复约 {elapsed_text}。可以带一点隔了一阵子后重新靠近的感觉，"
            "但仍要立刻落在当前画面，不要长篇回顾，也不要显得生硬客套。",
            {
                "bucket": "long_gap_reentry",
                "elapsed_seconds": elapsed_seconds,
                "elapsed_text": elapsed_text,
            },
        )

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
        event: AstrMessageEvent,
        *,
        task_id: str,
        change_snapshot: dict[str, Any],
    ) -> tuple[bool, str]:
        last_activity_at = self._get_recent_user_activity_at(event)
        if last_activity_at <= 0:
            return False, ""

        seconds_since = max(0, int(time.time() - last_activity_at))
        grace_seconds = self.USER_ACTIVITY_GRACE_SECONDS
        if change_snapshot.get("changed"):
            grace_seconds = self.USER_ACTIVITY_CHANGE_GRACE_SECONDS

        if seconds_since >= grace_seconds:
            return False, ""

        reason = (
            f"用户刚在 {seconds_since} 秒前发过消息，先暂缓这次主动打断"
        )
        self._ensure_auto_screen_runtime_state(task_id)["last_skip_reason"] = reason
        return True, reason

    def _get_scene_behavior_profile(self, scene: str) -> dict[str, Any]:
        normalized_scene = self._normalize_scene_label(scene)
        entertainment_scenes = {"视频", "游戏", "浏览-娱乐", "音乐", "社交"}
        work_scenes = {"编程", "设计", "办公", "学习", "阅读", "浏览", "浏览-工作"}

        if normalized_scene in entertainment_scenes:
            return {
                "category": "entertainment",
                "same_window_cooldown": self.ENTERTAINMENT_WINDOW_MESSAGE_COOLDOWN_SECONDS,
                "tone_instruction": "语气更像陪伴和轻提醒，不要频繁推进任务，也不要把用户从内容里拽出来。",
                "prefer_sample_only": False,
            }
        if normalized_scene in work_scenes:
            return {
                "category": "work",
                "same_window_cooldown": self.WORK_WINDOW_MESSAGE_COOLDOWN_SECONDS,
                "tone_instruction": "语气保持克制、直接、任务导向，优先指出卡点、下一步和可立即执行的建议。",
                "prefer_sample_only": True,
            }
        return {
            "category": "general",
            "same_window_cooldown": self.GENERAL_WINDOW_MESSAGE_COOLDOWN_SECONDS,
            "tone_instruction": "语气自然、简短，既给出帮助，也尽量避免抢占注意力。",
            "prefer_sample_only": True,
        }

    def _should_skip_same_window_followup(
        self,
        task_id: str,
        *,
        active_window_title: str,
        scene: str,
    ) -> tuple[bool, str]:
        state = self._ensure_auto_screen_runtime_state(task_id)
        current_window_title = self._normalize_window_title(active_window_title)
        last_window_title = self._normalize_window_title(
            state.get("last_reply_window_title", "")
        )
        if not current_window_title or current_window_title.casefold() != last_window_title.casefold():
            return False, ""

        last_sent_at = float(state.get("last_sent_at", 0.0) or 0.0)
        if last_sent_at <= 0:
            return False, ""

        profile = self._get_scene_behavior_profile(scene)
        cooldown_seconds = int(profile.get("same_window_cooldown", 0) or 0)
        elapsed = time.time() - last_sent_at
        if elapsed >= cooldown_seconds:
            return False, ""

        reason = (
            f"同一窗口《{current_window_title}》仍在冷却中，距离上次主动消息仅 {int(max(0, elapsed))} 秒"
        )
        state["last_skip_reason"] = reason
        return True, reason

    def _truncate_preview_text(self, text: str, limit: int = 120) -> str:
        preview = str(text or "").strip().replace("\r", " ").replace("\n", " ")
        if len(preview) <= limit:
            return preview
        return preview[: max(0, limit - 1)] + "…"

    def _contains_rest_cue(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        keywords = (
            "休息",
            "睡觉",
            "去睡",
            "早点睡",
            "快去睡",
            "先睡",
            "熬夜",
            "别熬夜",
            "关机睡",
            "关机吧",
            "凌晨",
            "太晚了",
        )
        return any(keyword in normalized for keyword in keywords)

    def _strip_repeated_companion_opening(self, text: str, *, has_recent_context: bool) -> str:
        if not has_recent_context:
            return str(text or "").strip()

        import re

        cleaned = str(text or "").strip()
        cleaned = re.sub(r"^(笨蛋|傻瓜|喂|欸|哎呀|哼)[，,、\s]+", "", cleaned, count=1)
        cleaned = re.sub(r"^(又在|还在|现在在)看", "在看", cleaned, count=1)
        return cleaned.strip()

    def _strip_rest_cue_sentences(self, text: str) -> str:
        import re

        original = str(text or "").strip()
        if not original:
            return ""

        parts = re.split(r"(?<=[。！？!?])\s*|\n+", original)
        kept_parts = [
            part.strip()
            for part in parts
            if part.strip() and not self._contains_rest_cue(part)
        ]
        if not kept_parts:
            return original
        cleaned = " ".join(kept_parts).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned or original

    def _has_recent_rest_cue(
        self,
        contexts: list[str],
        *,
        task_id: str,
    ) -> bool:
        assistant_contexts = [
            str(item or "").strip()
            for item in (contexts or [])
            if str(item or "").strip().startswith("助手:")
        ]
        recent_assistant_mentions = sum(
            1 for item in assistant_contexts[-3:] if self._contains_rest_cue(item)
        )
        if recent_assistant_mentions > 0:
            return True

        state = self._ensure_auto_screen_runtime_state(task_id)
        last_preview = str(state.get("last_reply_preview", "") or "").strip()
        last_sent_at = float(state.get("last_sent_at", 0.0) or 0.0)
        if (
            last_preview
            and self._contains_rest_cue(last_preview)
            and last_sent_at > 0
            and (time.time() - last_sent_at) < self.REST_CUE_REPLY_COOLDOWN_SECONDS
        ):
            return True
        return False

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

    def _resolve_webui_access_url(self) -> str:
        if not self.webui_enabled:
            return "未启用"
        if not self.web_server or not getattr(self.web_server, "_started", False):
            return "已启用但未运行"

        port = getattr(self.web_server, "port", self.webui_port)
        host = self.webui_host
        if host == "0.0.0.0":
            host = "127.0.0.1"
        return f"http://{host}:{port}"

    async def _build_kpi_doctor_report(self, event: AstrMessageEvent) -> str:
        self._ensure_runtime_state()

        current_check_interval, current_probability = self._get_current_preset_params()
        active_task_ids = list(self.auto_tasks.keys())
        focus_task_id = (
            self.AUTO_TASK_ID
            if self.AUTO_TASK_ID in self.auto_tasks
            else (active_task_ids[0] if active_task_ids else self.AUTO_TASK_ID)
        )
        auto_state = self._ensure_auto_screen_runtime_state(focus_task_id)
        current_change_snapshot = self._build_auto_screen_change_snapshot(
            focus_task_id,
            update_state=False,
        )
        active_window_title = (
            current_change_snapshot.get("active_window_title")
            or auto_state.get("last_seen_window_title")
            or "未知"
        )
        if auto_state.get("last_change_at"):
            latest_change_reason = auto_state.get("last_change_reason") or "最近有变化"
        elif self.is_running:
            latest_change_reason = (
                "当前窗口有变化"
                if current_change_snapshot.get("changed")
                else "最近未检测到明显变化"
            )
        else:
            latest_change_reason = "自动观察未运行，当前仅展示前台窗口"

        provider = self.context.get_using_provider()
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        provider_id = await self._get_current_chat_provider_id(umo=umo)
        provider_info = self._resolve_provider_runtime_info(provider_id=provider_id, provider=provider)
        model_label = provider_info.get("model") or getattr(provider, "model_name", "") or getattr(provider, "model", "") or "未知"
        provider_label = provider_info.get("provider_id") or getattr(provider, "id", "") or "未识别"

        env_ok, env_msg = self._check_env(check_mic=False)
        mode = "录屏" if self._use_screen_recording_mode() else "截图"
        ffmpeg_label = "未使用"
        encoder_label = "未使用"
        if self._use_screen_recording_mode():
            ffmpeg_path = self._get_ffmpeg_path()
            ffmpeg_label = ffmpeg_path if ffmpeg_path else "未检测到 ffmpeg"
            encoder_label = self._get_recording_video_encoder()

        diary_status = "开启" if self.enable_diary else "关闭"
        last_diary_label = (
            self.last_diary_date.strftime("%Y-%m-%d")
            if isinstance(self.last_diary_date, datetime.date)
            else "未生成"
        )
        target = self._resolve_proactive_target(event) or "未配置"
        webui_url = self._resolve_webui_access_url()
        custom_task_count = max(0, len(active_task_ids) - (1 if self.AUTO_TASK_ID in active_task_ids else 0))
        recent_user_activity_at = self._get_recent_user_activity_at(event)

        lines = [
            "屏幕伙伴自检",
            f"运行状态：{'已启用' if self.enabled else '未启用'} / 当前状态 {self.state} / 自动观察 {'运行中' if self.is_running else '未运行'}",
            f"任务概览：主任务 {focus_task_id} / 运行中 {len(active_task_ids)} 个 / 自定义任务 {custom_task_count} 个",
            f"识屏模式：{mode} / 间隔 {current_check_interval} 秒 / 基础概率 {current_probability}%",
            f"变化感知：当前窗口《{active_window_title}》 / 最近变化 {latest_change_reason} / 最近变化时间 {self._format_runtime_timestamp(auto_state.get('last_change_at'))}",
            f"最近判定：{auto_state.get('last_trigger_reason') or '暂未判定'} / 生效概率 {auto_state.get('last_effective_probability', 0)}% / 随机数 {auto_state.get('last_trigger_roll') if auto_state.get('last_trigger_roll') is not None else '未记录'}",
            f"最近手动消息：{self._format_runtime_timestamp(recent_user_activity_at)}",
            f"最近主动消息：{self._format_runtime_timestamp(auto_state.get('last_sent_at'))} / 预览 {auto_state.get('last_reply_preview') or '暂无'}",
            f"相似去重：{auto_state.get('last_skip_reason') or '最近没有命中去重'}",
            f"主动目标：{target}",
            f"模型提供方：{provider_label} / 模型 {model_label}",
            f"视觉链路：外部视觉 {'开启' if self._get_runtime_flag('use_external_vision') else '关闭'} / 视频直连兜底 {'开启' if self._get_runtime_flag('allow_unsafe_video_direct_fallback') else '关闭'}",
            f"录屏参数：{self._get_recording_duration_seconds()} 秒 @ {self._get_recording_fps():.2f} fps / 编码器 {encoder_label} / ffmpeg {ffmpeg_label}",
            f"观察与日记：观察 {len(self.observations)} 条 / 待写日记 {len(self.diary_entries)} 条 / 日记 {diary_status} / 计划时间 {self.diary_time} / 最近日记 {last_diary_label}",
            f"WebUI：{webui_url}",
            f"环境检查：{'正常' if env_ok else env_msg}",
        ]
        return "\n".join(lines)

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
        """Check optional runtime dependencies.

        Args:
            check_mic: Whether microphone-related dependencies are required.
        """
        self._ensure_runtime_state()
        missing_libs = []
        if self._use_screen_recording_mode():
            if not self._get_ffmpeg_path():
                missing_libs.append("ffmpeg")
        else:
            try:
                import pyautogui
            except ImportError:
                missing_libs.append("pyautogui")

            try:
                from PIL import Image as PILImage
            except ImportError:
                missing_libs.append("Pillow")

        if (
            sys.platform == "win32"
            and self.capture_active_window
            and not self._use_screen_recording_mode()
        ):
            try:
                import pygetwindow
            except ImportError:
                missing_libs.append("pygetwindow")

        # 检查麦克风监控依赖
        if check_mic and self.enable_mic_monitor:
            try:
                import pyaudio
            except ImportError:
                missing_libs.append("pyaudio")

            try:
                import numpy
            except ImportError:
                missing_libs.append("numpy")

        if missing_libs:
            if missing_libs == ["ffmpeg"]:
                return (
                    False,
                    "缺少 ffmpeg。你可以将 ffmpeg.exe 放到插件数据目录下的 bin 文件夹，"
                    "或在配置中填写 ffmpeg_path，或加入系统 PATH。"
                )
            return (
                False,
                f"缂哄皯蹇呰渚濊禆搴? {', '.join(missing_libs)}銆傝鎵ц: pip install {' '.join(missing_libs)}",
            )
        return True, ""

    def _check_env(self, check_mic=False):
        """Check whether the desktop environment is available.

        Args:
            check_mic: Whether microphone-related dependencies are required.
        """
        dep_ok, dep_msg = self._check_dependencies(check_mic=check_mic)
        if not dep_ok:
            return False, dep_msg

        if self._use_screen_recording_mode():
            if sys.platform != "win32":
                return False, "录屏视频识别目前仅支持 Windows 桌面环境。"
            ffmpeg_path = self._get_ffmpeg_path()
            if not ffmpeg_path:
                return (
                    False,
                    "未检测到 ffmpeg。请将 ffmpeg.exe 放到插件数据目录下的 bin 文件夹，"
                    "或在配置中填写 ffmpeg_path，或加入系统 PATH。"
                )
            return True, ""

        try:
            import pyautogui

            # 检查 Linux 下的 Display 环境变量
            if sys.platform.startswith("linux"):
                import os

                if not os.environ.get("DISPLAY") and not os.environ.get(
                    "WAYLAND_DISPLAY"
                ):
                    return (
                        False,
                        "Detected Linux without an available graphical display. Please run it in a desktop session or with X11 forwarding.",
                    )

            size = pyautogui.size()
            if size[0] <= 0 or size[1] <= 0:
                return False, "Unable to capture the screen properly."

            return True, ""
        except Exception as e:
            return False, f"自我检查失败: {str(e)}"

    async def _get_persona_prompt(self, umo: str = None) -> str:
        """获取屏幕伴侣的系统提示词"""
        base_prompt = ""
        try:
            if hasattr(self.context, "persona_manager"):
                persona = await self.context.persona_manager.get_default_persona_v3(
                    umo=umo
                )
                if persona and "prompt" in persona:
                    base_prompt = persona["prompt"]
        except Exception as e:
            logger.debug(f"获取屏幕尺寸失败: {e}")

        # 检查是否为陪伴模式
        if self.use_companion_mode:
            companion_prompt = getattr(self, 'companion_prompt', None)
            if companion_prompt:
                companion_supplemental_guide = (
                    "\n\n额外要求：保持对话的连续性，关注用户的任务进展，提供具体、实用的建议。"
                    "你可以偶尔轻轻表达自己也想和用户一起看点内容、玩一局游戏或做个小测试，"
                    "但必须低频、自然，不要打断正事，更不能凭空捏造共同经历。"
                )
                return f"{companion_prompt.rstrip()}{companion_supplemental_guide}"

        if not base_prompt:
            config_prompt = self.system_prompt
            if config_prompt:
                base_prompt = config_prompt

        if not base_prompt:
            base_prompt = DEFAULT_SYSTEM_PROMPT

        supplemental_guide = (
            "\n\n额外要求：少用旁白式开场，不要总是先叫用户名字。"
            "如果能提出建议，优先给和当前任务直接相关、能立刻用上的建议。"
            "可以偶尔表达自己也想和用户一起做点什么，但只限轻松自然的一句，"
            "并且任何共同经历都只能基于当前对话或已记录内容，不能虚构。"
        )

        return f"{base_prompt.rstrip()}{supplemental_guide}"

    def _build_start_end_prompt(self, raw_prompt: str, action: str) -> str:
        """为开始/结束消息补充更明确的人格化约束。"""
        base_prompt = str(raw_prompt or "").strip()
        if not base_prompt:
            if action == "start":
                base_prompt = "以你的性格向用户表达你会开始偶尔地陪着用户看屏幕了。"
            else:
                base_prompt = "以你的性格向用户表达你会先暂停看屏幕、退到旁边等用户再叫你。"

        supplemental = (
            "\n额外要求："
            "回复必须明显带有人格，不要像系统提示、状态播报或功能开关通知。"
            "语气要自然、亲近、有人味，像这个角色本人在开口。"
            "避免使用“已开始”“已停止”“任务已启动”“任务已结束”这种机械措辞。"
            "尽量简短，控制在 1 到 2 句话内。"
            "允许有一点角色感、小情绪或亲昵感，但不要夸张，也不要说得像说明书。"
        )
        return f"{base_prompt.rstrip()}{supplemental}"

    async def _get_start_response(self, umo: str = None) -> str:
        """Build the startup reply text."""
        mode = "llm" if self.use_llm_for_start_end else "preset"
        if mode == "preset" or (hasattr(mode, 'value') and mode.value == "preset"):
            return self.start_preset
        else:
            provider = self.context.get_using_provider()
            if provider:
                try:
                    system_prompt = await self._get_persona_prompt(umo)
                    prompt = self._build_start_end_prompt(
                        self.start_llm_prompt,
                        action="start",
                    )
                    response = await asyncio.wait_for(
                        provider.text_chat(prompt=prompt, system_prompt=system_prompt),
                        timeout=60.0
                    )
                    if response and hasattr(response, "completion_text") and response.completion_text:
                        return response.completion_text
                except asyncio.TimeoutError:
                    logger.warning("LLM 生成结束回复超时，将使用默认文案")
                except Exception as e:
                    logger.warning(f"Operation warning: {e}")
            return "我先退到旁边了，有需要再叫我。"

    async def _get_end_response(self, umo: str = None) -> str:
        """生成结束陪伴时的回复。"""
        mode = "llm" if self.use_llm_for_start_end else "preset"
        if mode == "preset" or (hasattr(mode, 'value') and mode.value == "preset"):
            return self.end_preset
        else:
            provider = self.context.get_using_provider()
            if provider:
                try:
                    system_prompt = await self._get_persona_prompt(umo)
                    prompt = self._build_start_end_prompt(
                        self.end_llm_prompt,
                        action="end",
                    )
                    response = await asyncio.wait_for(
                        provider.text_chat(prompt=prompt, system_prompt=system_prompt),
                        timeout=60.0
                    )
                    if response and hasattr(response, "completion_text") and response.completion_text:
                        return response.completion_text
                except asyncio.TimeoutError:
                    logger.warning("LLM 生成结束回复超时")
                except Exception as e:
                    logger.warning(f"Operation warning: {e}")
            return "我先不打扰你了，等你需要时我再过来。"

    def _generate_diary_image(self, diary_message: str) -> str:
        """将日记文本渲染为图片文件。"""
        from PIL import Image, ImageDraw, ImageFont
        import tempfile

        # 优化字体大小和行高
        font_size = 18
        line_height = int(font_size * 1.8)
        title_font_size = 24
        padding = 60
        max_width = 850

        chinese_fonts = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/STZHONGS.TTF",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]

        # 加载正文字体
        font = None
        for font_path in chinese_fonts:
            try:
                font = ImageFont.truetype(font_path, font_size)
                test_draw = ImageDraw.Draw(Image.new('RGB', (100, 100)))
                test_draw.text((0, 0), "娴嬭瘯涓枃", font=font)
                break
            except Exception:
                continue

        # 加载标题字体
        title_font = None
        for font_path in chinese_fonts:
            try:
                title_font = ImageFont.truetype(font_path, title_font_size)
                break
            except Exception:
                continue

        if font is None:
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None

        if title_font is None:
            title_font = font

        def get_text_width(text, use_title_font=False):
            if use_title_font and title_font:
                return title_font.getlength(text)
            elif font:
                return font.getlength(text)
            return len(text) * font_size

        lines = []
        max_text_width = max_width - padding * 2
        title_count = 0  # 统计标题行数量
        
        for paragraph in diary_message.split('\n'):
            if not paragraph:
                lines.append('')
                continue

            current_line = ""
            for char in paragraph:
                test_line = current_line + char
                if get_text_width(test_line) <= max_text_width:
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = char
            if current_line:
                lines.append(current_line)
                if current_line.startswith("#") and "日记" in current_line:
                    title_count += 1

        # 计算总高度，并为标题额外留白
        title_extra_height = title_count * 10  # 每个标题增加 10 像素
        total_height = padding * 2 + len(lines) * line_height + title_extra_height + 30
        total_height = max(400, total_height)  # 增加最小高度
        # 优化背景色和边框
        image = Image.new('RGB', (max_width, total_height), color=(255, 254, 250))
        draw = ImageDraw.Draw(image)

        # 绘制更柔和的边框
        border_color = (180, 160, 140)
        border_width = 3
        border_padding = 15
        draw.rectangle(
            [(padding - border_padding, padding - border_padding), (max_width - padding + border_padding, total_height - padding + border_padding)],
            outline=border_color,
            width=border_width
        )

        # Draw a simple divider line under the title area
        draw.line(
            [(padding, padding + 40), (max_width - padding, padding + 40)],
            fill=border_color,
            width=1
        )

        y = padding
        for line in lines:
            if line.startswith("#") and "日记" in line:
                # 标题居中显示
                title_width = get_text_width(line, use_title_font=True)
                title_x = (max_width - title_width) // 2
                draw.text((title_x, y), line, fill=(139, 69, 19), font=title_font)
                y += line_height + 10  # 标题行使用更大的行高
            elif line and line[0].isdigit() and "年" in line:
                # 日期居中显示
                date_width = get_text_width(line)
                date_x = (max_width - date_width) // 2
                draw.text((date_x, y), line, fill=(100, 100, 100), font=font)
                y += line_height + 5
            else:
                # 正文左对齐，首段额外缩进
                if line.strip():
                    if len(lines) > 0 and lines.index(line) > 0 and lines[lines.index(line) - 1].strip() == '':
                        # 首行缩进
                        draw.text((padding + 20, y), line, fill=(60, 60, 60), font=font)
                    else:
                        draw.text((padding, y), line, fill=(60, 60, 60), font=font)
                y += line_height

        temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        image.save(temp_file, format="PNG", quality=95)
        temp_file.close()

        return temp_file.name

    async def _capture_screen_bytes(self):
        """返回截图字节流与来源标签。"""

        def _core_task():
            import os
            from PIL import Image

            shared_dir_enabled = self._get_runtime_flag("use_shared_screenshot_dir")
            configured_shared_dir = str(getattr(self, "shared_screenshot_dir", "") or "").strip()

            def resolve_shared_screenshot_dir() -> str:
                if configured_shared_dir:
                    return os.path.normpath(configured_shared_dir)

                env_dir = str(os.environ.get("SCREENSHOT_DIR") or "").strip()
                if env_dir:
                    return os.path.normpath(env_dir)

                current_dir = os.path.dirname(os.path.abspath(__file__))
                return os.path.normpath(os.path.join(current_dir, "..", "..", "screenshots"))

            def persist_shared_screenshot(image_bytes: bytes) -> None:
                if not shared_dir_enabled:
                    return

                screenshots_dir = resolve_shared_screenshot_dir()
                try:
                    os.makedirs(screenshots_dir, exist_ok=True)
                    timestamp = int(time.time())
                    target_path = os.path.join(screenshots_dir, f"screenshot_{timestamp}.jpg")
                    latest_path = os.path.join(screenshots_dir, "screenshot_latest.jpg")
                    with open(target_path, "wb") as f:
                        f.write(image_bytes)
                    with open(latest_path, "wb") as f:
                        f.write(image_bytes)
                except Exception as e:
                    logger.warning(f"写入共享截图目录失败: {e}")

            def get_active_window_info():
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

            def encode_image_to_jpeg_bytes(image):
                if image.mode != "RGB":
                    image = image.convert("RGB")
                img_byte_arr = io.BytesIO()
                quality_val = self.image_quality
                try:
                    quality = max(10, min(100, int(quality_val)))
                except (ValueError, TypeError):
                    quality = 70
                image.save(img_byte_arr, format="JPEG", quality=quality)
                return img_byte_arr.getvalue()

            def capture_live_screenshot():
                import pyautogui

                active_title, active_region = self._get_active_window_info()
                screenshot = None

                if self.capture_active_window and active_region:
                    try:
                        screenshot = pyautogui.screenshot(region=active_region)
                    except Exception as e:
                        logger.warning(f"活动窗口截图失败，将回退为全屏截图: {e}")

                if screenshot is None:
                    screenshot = pyautogui.screenshot()

                source_label = active_title or ("活动窗口截图" if self.capture_active_window else "实时截图")
                image_bytes = encode_image_to_jpeg_bytes(screenshot)
                persist_shared_screenshot(image_bytes)
                return image_bytes, source_label

            if not shared_dir_enabled:
                try:
                    return capture_live_screenshot()
                except Exception as e:
                    logger.error(f"实时截图失败: {e}")
                    raise

            screenshots_dir = resolve_shared_screenshot_dir()

            if not os.path.exists(screenshots_dir):
                logger.warning(f"共享截图目录不存在，将回退为实时截图: {screenshots_dir}")
                try:
                    return capture_live_screenshot()
                except Exception as e:
                    logger.error(f"实时截图失败: {e}")
                    raise
            
            # 获取所有截图文件
            screenshot_files = [f for f in os.listdir(screenshots_dir) if f.startswith("screenshot_") and f.endswith(".jpg")]
            
            if not screenshot_files:
                logger.warning("共享截图目录中没有可用截图，将回退为实时截图")
                try:
                    return capture_live_screenshot()
                except Exception as e:
                    logger.error(f"实时截图失败: {e}")
                    raise

            screenshot_candidates = []
            for filename in screenshot_files:
                screenshot_path = os.path.join(screenshots_dir, filename)
                try:
                    stat = os.stat(screenshot_path)
                    screenshot_candidates.append((stat.st_mtime, filename, screenshot_path))
                except OSError as e:
                    logger.debug(f"读取截图文件信息失败 {screenshot_path}: {e}")

            if not screenshot_candidates:
                logger.warning("没有找到可读取的共享截图，将回退为实时截图")
                try:
                    return capture_live_screenshot()
                except Exception as e:
                    logger.error(f"实时截图失败: {e}")
                    raise

            screenshot_candidates.sort(key=lambda item: item[0], reverse=True)
            latest_mtime, latest_screenshot, screenshot_path = screenshot_candidates[0]
            screenshot_age = max(0.0, time.time() - float(latest_mtime))

            if screenshot_age > 20:
                logger.warning(
                    f"最新共享截图已过期 {screenshot_age:.1f} 秒: {screenshot_path}，将优先尝试实时截图"
                )
                try:
                    return capture_live_screenshot()
                except Exception as e:
                    logger.warning(f"实时截图失败，将回退到共享截图: {e}")

            logger.info(
                f"使用最新截图: {screenshot_path} (mtime={datetime.datetime.fromtimestamp(latest_mtime).isoformat(timespec='seconds')})"
            )

            # 读取截图文件
            try:
                with Image.open(screenshot_path) as screenshot:
                    screenshot.load()
                    return encode_image_to_jpeg_bytes(screenshot), f"共享截图:{latest_screenshot}"
            except Exception as e:
                logger.error(f"读取截图文件失败: {e}")
                try:
                    return capture_live_screenshot()
                except Exception as e:
                    logger.error(f"实时截图失败: {e}")
                    raise

        result = await asyncio.to_thread(_core_task)
        return result

    async def _capture_recording_context(self) -> dict[str, Any]:
        self._ensure_recording_runtime_state()
        clip_active_window_title, _ = await asyncio.to_thread(self._get_active_window_info)

        async with self._screen_recording_lock:
            current_path = str(getattr(self, "_screen_recording_path", "") or "")
            current_process = getattr(self, "_screen_recording_process", None)
            if not current_path:
                await asyncio.to_thread(self._start_screen_recording_sync)
                await asyncio.sleep(1.5)
                current_path = str(getattr(self, "_screen_recording_path", "") or "")
                current_process = getattr(self, "_screen_recording_process", None)

            if current_process and current_process.poll() is None:
                video_path = await asyncio.to_thread(self._stop_screen_recording_sync)
            else:
                video_path = current_path

            if not video_path or not os.path.exists(video_path):
                await asyncio.to_thread(self._start_screen_recording_sync)
                raise RuntimeError("录屏文件尚未准备好，请稍后再试一次。")

            def _read_video_bytes() -> bytes:
                with open(video_path, "rb") as f:
                    return f.read()

            video_bytes = await asyncio.to_thread(_read_video_bytes)
            if not video_bytes:
                await asyncio.to_thread(self._start_screen_recording_sync)
                raise RuntimeError("录屏文件为空，请稍后再试一次。")

            if self.save_local:
                try:
                    data_dir = StarTools.get_data_dir()
                    data_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(video_path, str(data_dir / "screen_record_latest.mp4"))
                except Exception as e:
                    logger.error(f"保存录屏文件失败: {e}")

            await asyncio.to_thread(self._start_screen_recording_sync)
            await asyncio.to_thread(self._cleanup_recording_cache)

        latest_image_bytes, latest_window_title, active_window_title = (
            await self._capture_latest_screen_anchor(
                fallback_window_title=clip_active_window_title
            )
        )
        return {
            "media_kind": "video",
            "mime_type": "video/mp4",
            "media_bytes": video_bytes,
            "active_window_title": active_window_title,
            "clip_active_window_title": clip_active_window_title,
            "latest_window_title": latest_window_title,
            "latest_image_bytes": latest_image_bytes,
            "duration_seconds": self._get_recording_duration_seconds(),
            "source_label": active_window_title or "最近一段桌面录屏",
        }

    async def _capture_screenshot_context(self) -> dict[str, Any]:
        image_bytes, active_window_title = await self._capture_screen_bytes()
        return {
            "media_kind": "image",
            "mime_type": "image/jpeg",
            "media_bytes": image_bytes,
            "active_window_title": active_window_title,
            "source_label": active_window_title,
        }

    async def _capture_latest_screen_anchor(
        self,
        *,
        fallback_window_title: str = "",
    ) -> tuple[bytes, str, str]:
        latest_image_bytes = b""
        latest_window_title = ""
        active_window_title = self._normalize_window_title(fallback_window_title)
        try:
            latest_image_bytes, latest_window_title = await self._capture_screen_bytes()
            active_window_title = (
                self._normalize_window_title(latest_window_title)
                or active_window_title
            )
        except Exception as e:
            logger.debug(f"录屏后补抓当前截图失败: {e}")
        return latest_image_bytes, latest_window_title, active_window_title

    async def _capture_one_shot_recording_context(
        self, duration_seconds: int | None = None
    ) -> dict[str, Any]:
        self._ensure_recording_runtime_state()
        clip_active_window_title, _ = await asyncio.to_thread(self._get_active_window_info)
        duration = max(1, int(duration_seconds or self._get_recording_duration_seconds()))

        async with self._screen_recording_lock:
            await asyncio.to_thread(self._stop_screen_recording_sync)
            video_path = await asyncio.to_thread(self._record_screen_clip_sync, duration)

        try:
            def _read_video_bytes() -> bytes:
                with open(video_path, "rb") as f:
                    return f.read()

            video_bytes = await asyncio.to_thread(_read_video_bytes)
            if not video_bytes:
                raise RuntimeError("\u5355\u6b21\u5f55\u5c4f\u6587\u4ef6\u4e3a\u7a7a\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u4e00\u6b21\u3002")

            if self.save_local:
                try:
                    data_dir = StarTools.get_data_dir()
                    data_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(video_path, str(data_dir / "screen_record_latest.mp4"))
                except Exception as e:
                    logger.error(f"\u4fdd\u5b58\u5355\u6b21\u5f55\u5c4f\u6587\u4ef6\u5931\u8d25: {e}")

            latest_image_bytes, latest_window_title, active_window_title = (
                await self._capture_latest_screen_anchor(
                    fallback_window_title=clip_active_window_title
                )
            )

            return {
                "media_kind": "video",
                "mime_type": "video/mp4",
                "media_bytes": video_bytes,
                "active_window_title": active_window_title,
                "clip_active_window_title": clip_active_window_title,
                "latest_window_title": latest_window_title,
                "latest_image_bytes": latest_image_bytes,
                "duration_seconds": duration,
                "source_label": active_window_title
                or "\u624b\u52a8\u5f55\u5236\u7684\u6700\u8fd1 10 \u79d2\u684c\u9762\u5f55\u5c4f",
            }
        finally:
            try:
                if os.path.exists(video_path):
                    os.remove(video_path)
            except OSError:
                pass

    async def _capture_recognition_context(self) -> dict[str, Any]:
        if self._use_screen_recording_mode():
            return await self._capture_recording_context()

        return await self._capture_screenshot_context()

    async def _capture_proactive_recognition_context(self) -> dict[str, Any]:
        if self._use_screen_recording_mode():
            return await self._capture_one_shot_recording_context(
                self._get_recording_duration_seconds()
            )

        return await self._capture_screenshot_context()

    async def _call_external_vision_api(
        self,
        media_bytes: bytes,
        media_kind: str = "image",
        mime_type: str = "image/jpeg",
        scene: str = "",
        active_window_title: str = "",
    ) -> str:
        """调用外部视觉 API 进行图像分析。"""
        import aiohttp

        # 构建请求数据
        base64_data = base64.b64encode(media_bytes).decode("utf-8")
        image_prompt = self._build_vision_prompt(scene, active_window_title)
        if media_kind == "video":
            image_prompt = (
                "以下为用户当前桌面录屏视频（最近约10秒），你可以参考此内容判断用户正在做什么、进行到哪一步、画面里的关键线索或异常，并给出最值得的一条建议。\n"
                f"{image_prompt}"
            )

        # 定义API调用函数
        async def call_api(api_url, api_key, api_model):
            if not api_url:
                return None, "未配置视觉 API 地址"

            payload = {
                "model": api_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": image_prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_data}"
                                },
                            },
                        ],
                    }
                ],
                "stream": False,
            }

            # 构建请求头
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            # 重试机制
            max_retries = 2  # 减少重试次数，避免总超时时间过长
            retry_delay = 1  # 秒，减少重试间隔
            for attempt in range(max_retries):
                try:
                    # 发送请求，并设置合理的超时
                    timeout = aiohttp.ClientTimeout(total=60.0)  # 增加超时时间，给视觉API更多响应时间
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(
                            api_url, json=payload, headers=headers
                        ) as response:
                            if response.status == 200:
                                result = await response.json()
                                if "choices" in result and len(result["choices"]) > 0:
                                    choice = result["choices"][0]
                                    if "message" in choice and "content" in choice["message"]:
                                        return choice["message"]["content"], None
                                    elif "text" in choice:
                                        return choice["text"], None
                                elif "response" in result:
                                    return result["response"], None
                                else:
                                    return None, "我刚才没能顺利读出画面内容。"
                            else:
                                error_text = await response.text()
                                logger.error(
                                    f"视觉 API 调用失败 (尝试 {attempt+1}/{max_retries}): {response.status} - {error_text}"
                                )
                                if attempt < max_retries - 1:
                                    logger.info(f"等待 {retry_delay} 秒后重试...")
                                    await asyncio.sleep(retry_delay)
                                    retry_delay *= 2
                                else:
                                    return None, "刚才没看清，我们再试一次？"
                except asyncio.TimeoutError:
                    logger.error(f"Vision API timeout (attempt {attempt+1}/{max_retries})")
                    if attempt < max_retries - 1:
                        logger.info(f"等待 {retry_delay} 秒后重试...")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        return None, "网络刚才有点卡，我们再试一次？"
                except Exception as e:
                    logger.error(f"调用视觉 API 异常 (尝试 {attempt+1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        logger.info(f"等待 {retry_delay} 秒后重试...")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        return None, "这次视觉分析没有成功，再给我一次机会。"

        # 获取主API配置
        main_api_url = self.vision_api_url
        main_api_key = self.vision_api_key
        main_api_model = self.vision_api_model

        # 首先尝试主API
        logger.info("尝试使用主视觉API")
        result, error = await call_api(main_api_url, main_api_key, main_api_model)
        if result:
            return result

        # 主API失败，尝试备用API
        backup_api_url = getattr(self, 'vision_api_url_backup', None)
        backup_api_key = getattr(self, 'vision_api_key_backup', None)
        backup_api_model = getattr(self, 'vision_api_model_backup', None)

        if backup_api_url:
            logger.info("主视觉API失败，尝试使用备用视觉API")
            result, error = await call_api(backup_api_url, backup_api_key, backup_api_model)
            if result:
                return result

        # 所有API都失败
        logger.error("所有视觉API调用都失败了")
        return error if error else "视觉分析服务暂时不可用，请稍后再试。"

    @staticmethod
    def _build_data_url(media_bytes: bytes, mime_type: str) -> str:
        base64_data = base64.b64encode(media_bytes).decode("utf-8")
        return f"data:{mime_type};base64,{base64_data}"

    def _get_astrbot_config_candidates(self) -> list[str]:
        home_dir = os.path.expanduser("~")
        data_dir = os.path.join(home_dir, ".astrbot", "data")
        candidates = [
            os.path.join(data_dir, "cmd_config.json"),
        ]

        config_dir = os.path.join(data_dir, "config")
        if os.path.isdir(config_dir):
            try:
                abconf_files = [
                    os.path.join(config_dir, name)
                    for name in os.listdir(config_dir)
                    if name.startswith("abconf_") and name.endswith(".json")
                ]
                abconf_files.sort(
                    key=lambda path: os.path.getmtime(path),
                    reverse=True,
                )
                candidates = abconf_files + candidates
            except Exception as e:
                logger.debug(f"读取 AstrBot 配置列表失败: {e}")

        return candidates

    def _load_astrbot_provider_registry(self) -> dict[str, Any]:
        for path in self._get_astrbot_config_candidates():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and (
                    isinstance(data.get("provider"), list)
                    or isinstance(data.get("provider_sources"), list)
                ):
                    return data
            except Exception as e:
                logger.debug(f"读取 AstrBot provider 配置失败 {path}: {e}")
        return {}

    @staticmethod
    def _looks_like_gemini_model(model_name: str) -> bool:
        return "gemini" in str(model_name or "").strip().lower()

    @staticmethod
    def _is_official_gemini_api_base(api_base: str) -> bool:
        normalized = str(api_base or "").strip().lower()
        return "generativelanguage.googleapis.com" in normalized

    async def _get_current_chat_provider_id(self, umo: str | None = None) -> str:
        try:
            getter = getattr(self.context, "get_current_chat_provider_id", None)
            if getter:
                provider_id = await getter(umo=umo)
                return str(provider_id or "").strip()
        except Exception as e:
            logger.debug(f"获取当前聊天 provider_id 失败: {e}")
        return ""

    async def _supports_native_gemini_video_audio(
        self,
        *,
        provider=None,
        umo: str | None = None,
    ) -> bool:
        try:
            provider_id = await self._get_current_chat_provider_id(umo=umo)
            runtime = self._resolve_provider_runtime_info(
                provider_id=provider_id,
                provider=provider,
            )
            model_name = str(runtime.get("model", "") or "").strip()
            api_key = str(runtime.get("api_key", "") or "").strip()
            api_base = str(runtime.get("api_base", "") or "").strip()
            return bool(
                self._looks_like_gemini_model(model_name)
                and api_key
                and self._is_official_gemini_api_base(api_base)
            )
        except Exception as e:
            logger.debug(f"判断 Gemini 原生视频能力失败: {e}")
            return False

    def _resolve_provider_runtime_info(
        self,
        provider_id: str = "",
        provider=None,
    ) -> dict[str, Any]:
        registry = self._load_astrbot_provider_registry()
        provider_entries = registry.get("provider", []) or []
        provider_sources = registry.get("provider_sources", []) or []
        provider_settings = registry.get("provider_settings", {}) or {}

        current_provider_id = str(provider_id or "").strip()
        if not current_provider_id:
            current_provider_id = str(
                provider_settings.get("default_provider_id", "") or ""
            ).strip()

        model_name = ""
        provider_entry = None
        if current_provider_id:
            provider_entry = next(
                (
                    item
                    for item in provider_entries
                    if str(item.get("id", "") or "").strip() == current_provider_id
                ),
                None,
            )

        if provider_entry is None and provider is not None:
            for attr_name in ("model", "model_name", "provider_id", "id"):
                attr_value = getattr(provider, attr_name, None)
                if not attr_value:
                    continue
                attr_str = str(attr_value).strip()
                if not model_name:
                    model_name = attr_str
                matched = next(
                    (
                        item
                        for item in provider_entries
                        if attr_str
                        and (
                            str(item.get("id", "") or "").strip() == attr_str
                            or str(item.get("model", "") or "").strip() == attr_str
                        )
                    ),
                    None,
                )
                if matched is not None:
                    provider_entry = matched
                    current_provider_id = str(matched.get("id", "") or "").strip()
                    break

        if provider_entry is not None and not model_name:
            model_name = str(provider_entry.get("model", "") or "").strip()

        provider_source_id = ""
        api_base = ""
        api_key = ""
        if provider_entry is not None:
            provider_source_id = str(provider_entry.get("provider_source_id", "") or "").strip()
            source_entry = next(
                (
                    item
                    for item in provider_sources
                    if str(item.get("id", "") or "").strip() == provider_source_id
                ),
                None,
            )
            if source_entry:
                api_base = str(source_entry.get("api_base", "") or "").strip()
                key_list = source_entry.get("key", []) or []
                if key_list:
                    api_key = str(key_list[0] or "").strip()

        env_api_key = str(os.environ.get("GEMINI_API_KEY") or "").strip()
        env_api_base = str(os.environ.get("GEMINI_API_BASE") or "").strip()
        if env_api_key:
            api_key = env_api_key
        if env_api_base:
            api_base = env_api_base

        if not api_base and api_key and self._looks_like_gemini_model(model_name):
            api_base = self.GEMINI_API_BASE

        return {
            "provider_id": current_provider_id,
            "model": model_name,
            "api_base": api_base,
            "api_key": api_key,
            "provider_source_id": provider_source_id,
        }

    async def _gemini_upload_file(
        self,
        *,
        api_base: str,
        api_key: str,
        media_bytes: bytes,
        mime_type: str,
        display_name: str,
    ) -> dict[str, Any]:
        import aiohttp

        start_headers = {
            "x-goog-api-key": api_key,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(media_bytes)),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json",
        }
        start_payload = {"file": {"display_name": display_name}}
        start_url = f"{api_base.rstrip('/')}/upload/v1beta/files"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                start_url,
                headers=start_headers,
                json=start_payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                response.raise_for_status()
                upload_url = response.headers.get("X-Goog-Upload-URL") or response.headers.get(
                    "x-goog-upload-url"
                )
                if not upload_url:
                    raise RuntimeError("Gemini Files API 未返回上传地址。")

            upload_headers = {
                "x-goog-api-key": api_key,
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
                "Content-Length": str(len(media_bytes)),
            }
            async with session.post(
                upload_url,
                headers=upload_headers,
                data=media_bytes,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as response:
                response.raise_for_status()
                result = await response.json()
        return result.get("file", result)

    async def _gemini_wait_file_active(
        self,
        *,
        api_base: str,
        api_key: str,
        file_name: str,
    ) -> dict[str, Any]:
        import aiohttp

        endpoint = file_name if str(file_name).startswith("files/") else f"files/{file_name}"
        url = f"{api_base.rstrip('/')}/v1beta/{endpoint}"
        deadline = time.time() + float(self.GEMINI_FILE_POLL_TIMEOUT_SECONDS)

        async with aiohttp.ClientSession() as session:
            while time.time() < deadline:
                async with session.get(
                    url,
                    headers={"x-goog-api-key": api_key},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    response.raise_for_status()
                    result = await response.json()

                state = str(
                    ((result.get("state") or {}) if isinstance(result.get("state"), dict) else {})
                    .get("name", result.get("state", ""))
                    or ""
                ).upper()
                if state == "ACTIVE":
                    return result
                if state == "FAILED":
                    raise RuntimeError("Gemini Files API 处理视频失败。")
                await asyncio.sleep(self.GEMINI_FILE_POLL_INTERVAL_SECONDS)

        raise RuntimeError("Gemini Files API 处理视频超时。")

    async def _gemini_delete_file(
        self,
        *,
        api_base: str,
        api_key: str,
        file_name: str,
    ) -> None:
        import aiohttp

        endpoint = file_name if str(file_name).startswith("files/") else f"files/{file_name}"
        url = f"{api_base.rstrip('/')}/v1beta/{endpoint}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    url,
                    headers={"x-goog-api-key": api_key},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status not in {200, 204}:
                        logger.debug(f"删除 Gemini 临时文件失败: HTTP {response.status}")
        except Exception as e:
            logger.debug(f"删除 Gemini 临时文件失败: {e}")

    @staticmethod
    def _extract_text_from_gemini_response(payload: dict[str, Any]) -> str:
        parts: list[str] = []
        for candidate in payload.get("candidates", []) or []:
            content = candidate.get("content", {}) or {}
            for part in content.get("parts", []) or []:
                text = str(part.get("text", "") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()

    async def _call_native_gemini_multimodal(
        self,
        *,
        provider_id: str,
        provider,
        interaction_prompt: str,
        system_prompt: str,
        media_bytes: bytes,
        media_kind: str,
        mime_type: str,
    ):
        import aiohttp

        runtime = self._resolve_provider_runtime_info(provider_id=provider_id, provider=provider)
        model_name = str(runtime.get("model", "") or "").strip()
        api_key = str(runtime.get("api_key", "") or "").strip()
        api_base = str(runtime.get("api_base", "") or "").strip()

        if not (
            self._looks_like_gemini_model(model_name)
            and api_key
            and self._is_official_gemini_api_base(api_base)
        ):
            return None

        if not interaction_prompt.strip():
            raise RuntimeError("Gemini 原生多模态调用缺少提示词。")

        uploaded_file_name = ""
        try:
            if media_kind == "video":
                uploaded_file = await self._gemini_upload_file(
                    api_base=api_base,
                    api_key=api_key,
                    media_bytes=media_bytes,
                    mime_type=mime_type,
                    display_name=f"screen-companion-{uuid.uuid4()}.mp4",
                )
                uploaded_file_name = str(uploaded_file.get("name", "") or "").strip()
                file_info = await self._gemini_wait_file_active(
                    api_base=api_base,
                    api_key=api_key,
                    file_name=uploaded_file_name,
                )
                media_part = {
                    "file_data": {
                        "mime_type": mime_type,
                        "file_uri": str(file_info.get("uri", "") or "").strip(),
                    }
                }
            else:
                media_part = {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(media_bytes).decode("utf-8"),
                    }
                }

            payload: dict[str, Any] = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            media_part,
                            {"text": interaction_prompt},
                        ],
                    }
                ]
            }
            if system_prompt.strip():
                payload["system_instruction"] = {
                    "parts": [{"text": system_prompt}],
                }

            url = f"{api_base.rstrip('/')}/v1beta/models/{model_name}:generateContent"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as response:
                    response.raise_for_status()
                    result = await response.json()

            response_text = self._extract_text_from_gemini_response(result)
            if not response_text:
                raise RuntimeError("Gemini 原生多模态返回为空。")
            return SimpleNamespace(completion_text=response_text)
        finally:
            if uploaded_file_name:
                await self._gemini_delete_file(
                    api_base=api_base,
                    api_key=api_key,
                    file_name=uploaded_file_name,
                )

    async def _call_provider_multimodal_direct(
        self,
        provider,
        interaction_prompt: str,
        system_prompt: str,
        media_bytes: bytes,
        media_kind: str = "image",
        mime_type: str = "image/jpeg",
        provider_id: str = "",
    ):
        native_response = await self._call_native_gemini_multimodal(
            provider_id=provider_id,
            provider=provider,
            interaction_prompt=interaction_prompt,
            system_prompt=system_prompt,
            media_bytes=media_bytes,
            media_kind=media_kind,
            mime_type=mime_type,
        )
        if native_response is not None:
            return native_response

        if media_kind == "video" and not self._coerce_bool(
            getattr(self, "allow_unsafe_video_direct_fallback", False)
        ):
            raise RuntimeError(
                "当前 provider 不支持原生视频上传，已拦截视频直发以避免过度消耗 token。"
                "请开启外部视觉 API，或切换到官方 Gemini API 并配置 GEMINI_API_KEY。"
            )
        if media_kind == "video":
            logger.warning(
                "当前 provider 不支持原生视频上传，但已按配置允许回退到兼容视频直发。"
                "这可能导致请求体很大，并带来较高的 token 消耗。"
            )

        data_url = self._build_data_url(media_bytes, mime_type)
        multimodal_contexts = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": interaction_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    },
                ],
            }
        ]

        try:
            return await provider.text_chat(
                prompt="",
                system_prompt=system_prompt,
                contexts=multimodal_contexts,
            )
        except TypeError:
            if media_kind == "image":
                return await provider.text_chat(
                    prompt=interaction_prompt,
                    system_prompt=system_prompt,
                    image_urls=[data_url],
                )
            raise RuntimeError(
                "当前 AstrBot provider 不支持直接视频多模态上下文，请开启外部视觉 API。"
            )

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
        debug_mode = self.debug
        if debug_mode:
            logger.info(f"[Task {task_id}] status update")

        if capture_context is None:
            effective_capture_timeout = (
                float(capture_timeout)
                if capture_timeout is not None
                else self._get_capture_context_timeout()
            )
            capture_context = await asyncio.wait_for(
                self._capture_recognition_context(),
                timeout=effective_capture_timeout,
            )
        capture_context.setdefault(
            "trigger_reason",
            "用户手动发起识屏请求" if task_id.startswith("manual") or task_id in {"manual", "manual_recording"} else f"任务 {task_id} 发起识屏",
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
            else self._get_screen_analysis_timeout(media_kind)
        )
        components = await asyncio.wait_for(
            self._analyze_screen(
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

            if hasattr(self.context, "conversation_manager"):
                conv_mgr = self.context.conversation_manager
                uid = event.unified_msg_origin
                curr_cid = await conv_mgr.get_curr_conversation_id(uid)

                if curr_cid:
                    user_msg = UserMessageSegment(
                        content=[TextPart(text=str(history_user_text or "/kp"))]
                    )
                    assistant_msg = AssistantMessageSegment(
                        content=[TextPart(text=screen_result)]
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

        self._remember_screen_analysis_trace(capture_context.get("_analysis_trace"))
        return screen_result

    def _check_recording_env(self, check_mic: bool = False) -> tuple[bool, str]:
        dep_ok, dep_msg = self._check_dependencies(check_mic=check_mic)
        if not dep_ok:
            return False, dep_msg

        if sys.platform != "win32":
            return False, "\u5f55\u5c4f\u89c6\u9891\u8bc6\u522b\u76ee\u524d\u4ec5\u652f\u6301 Windows \u684c\u9762\u73af\u5883\u3002"

        ffmpeg_path = self._get_ffmpeg_path()
        if not ffmpeg_path:
            return (
                False,
                "\u672a\u68c0\u6d4b\u5230 ffmpeg\uff0c\u8bf7\u5c06 ffmpeg.exe \u653e\u5230\u63d2\u4ef6\u76ee\u5f55\u4e0b\u7684 bin \u6587\u4ef6\u5939\uff0c"
                "\u6216\u5728\u914d\u7f6e\u4e2d\u586b\u5199 ffmpeg_path\uff0c\u6216\u52a0\u5165 PATH\u3002"
            )

        return True, ""

    def _check_screenshot_env(self, check_mic: bool = False) -> tuple[bool, str]:
        dep_ok, dep_msg = self._check_dependencies(check_mic=check_mic)
        if not dep_ok and "ffmpeg" not in str(dep_msg or "").lower():
            return False, dep_msg

        try:
            import pyautogui

            if sys.platform.startswith("linux"):
                if not os.environ.get("DISPLAY") and not os.environ.get(
                    "WAYLAND_DISPLAY"
                ):
                    return (
                        False,
                        "Detected Linux without an available graphical display. Please run it in a desktop session or with X11 forwarding.",
                    )

            size = pyautogui.size()
            if size[0] <= 0 or size[1] <= 0:
                return False, "Unable to capture the screen properly."

            return True, ""
        except Exception as e:
            if self._get_runtime_flag("use_shared_screenshot_dir"):
                shared_dir = str(getattr(self, "shared_screenshot_dir", "") or "").strip()
                if shared_dir:
                    return True, ""
            return False, f"自我检查失败: {str(e)}"

    def _classify_browser_content(self, window_title: str) -> str:
        """根据浏览器窗口标题分类内容类型。"""
        title_lower = window_title.lower()
        
        # 工作相关网站关键词
        work_keywords = [
            "google", "baidu", "bing", "search", "查询", "搜索",
            "github", "gitlab", "coding", "stackoverflow", "stackexchange",
            "docs", "documentation", "wiki", "教程", "guide", "manual",
            "office", "excel", "word", "powerpoint", "spreadsheet", "document",
            "gmail", "outlook", "email", "mail", "邮件",
            "jira", "trello", "asana", "project", "task", "todo",
            "slack", "teams", "discord", "chat", "沟通", "协作",
            "figma", "design", "photoshop", "illustrator", "原型", "设计",
            "analytics", "data", "report", "dashboard", "分析", "报表",
            "code", "programming", "developer", "dev", "编程", "开发",
            "cloud", "aws", "azure", "gcp", "cloudflare", "服务器", "云",
            "crm", "erp", "sap", "salesforce", "客户", "管理",
            "learning", "course", "education", "学习", "课程", "教育"
        ]
        
        # 娱乐相关网站关键词
        entertainment_keywords = [
            "youtube", "bilibili", "netflix", "hulu", "disney+", "视频", "电影", "剧集",
            "music", "spotify", "apple music", "网易云", "qq音乐", "音乐", "歌曲",
            "game", "gaming", "游戏", "steam", "epic", "游戏平台",
            "facebook", "instagram", "twitter", "x", "tiktok", "douyin", "社交", "微博",
            "news", "新闻", "头条", "资讯",
            "shopping", "电商", "淘宝", "京东", "拼多多", "购物", "商城",
            "sports", "体育", "足球", "篮球", "赛事",
            "entertainment", "娱乐", "明星", "综艺",
            "anime", "动画", "漫画", "番剧",
            "porn", "xxx", "色情", "成人"
        ]
        
        # 检查工作相关关键词
        for keyword in work_keywords:
            if keyword in title_lower:
                return "浏览-工作"
        
        # 检查娱乐相关关键词
        for keyword in entertainment_keywords:
            if keyword in title_lower:
                return "浏览-娱乐"
        
        # 默认返回普通浏览
        return "浏览"

    def _identify_scene(self, window_title: str) -> str:
        """Identify a coarse scene label from the current window title."""
        if not window_title:
            return "未知"

        title_lower = window_title.lower()

        keyword_groups = {
            "编程": [
                "code", "vscode", "visual studio", "intellij", "pycharm", "idea",
                "eclipse", "sublime", "atom", "notepad++", "vim", "emacs",
                "phpstorm", "webstorm", "goland", "rider", "android studio", "xcode",
                "terminal", "powershell", "cmd", "git", "github", "gitlab", "coding",
                "dev", "developer", "program", "programming", "debug", "compile", "build",
                "python", "java", "c++", "c#", "javascript", "typescript", "html", "css",
                "ide", "editor", "console", "shell", "bash", "zsh", "powershell"
            ],
            "设计": [
                "photoshop", "illustrator", "figma", "sketch", "xd", "gimp", "canva",
                "photopea", "coreldraw", "blender", "maya", "3d", "design",
                "creative", "art", "graphic", "ui", "ux", "wireframe", "prototype",
                "adobe", "affinity", "paint", "draw", "illustration", "animation"
            ],
            "浏览": [
                "chrome", "firefox", "edge", "safari", "opera", "browser", "???",
                "chrome.exe", "firefox.exe", "edge.exe", "safari.exe", "opera.exe",
                "browser", "web", "internet", "chrome", "firefox", "edge", "safari", "opera"
            ],
            "办公": [
                "word", "excel", "powerpoint", "office", "??", "??", "wps", "outlook",
                "office365", "onenote", "access", "project", "visio",
                "document", "spreadsheet", "presentation", "calendar", "task", "todo",
                "work", "office", "business", "report", "data", "analysis", "excel"
            ],
            "游戏": [
                "steam", "epic", "battle.net", "valorant", "csgo", "dota", "minecraft",
                "game", "league", "lol", "overwatch", "fortnite", "pubg", "apex",
                "genshin", "roblox", "warcraft", "diablo", "starcraft", "hearthstone",
                "fifa", "nba", "call of duty", "cod", "assassin's creed", "ac",
                "grand theft auto", "gta", "the witcher", "cyberpunk", "fallout",
                "game", "gaming", "play", "player", "level", "mission", "quest",
                "character", "weapon", "map", "server", "multiplayer", "singleplayer"
            ],
            "视频": [
                "youtube", "bilibili", "netflix", "vlc", "potplayer", "movie", "video", "??",
                "youku", "tudou", "iqiyi", "letv", "mkv", "mp4", "wmv", "avi",
                "media player", "kmplayer", "mplayer",
                "video", "movie", "film", "tv", "show", "series", "episode", "streaming",
                "watch", "player", "media", "video", "movie", "film", "tv", "show"
            ],
            "阅读": [
                "novel", "reader", "ebook", "pdf", "reading", "??", "???", "???",
                "adobe reader", "foxit", "kindle", "ibooks", "epub", "mobi",
                "book", "read", "reading", "novel", "story", "document", "pdf", "epub"
            ],
            "音乐": [
                "spotify", "apple music", "music", "itunes", "?????", "qq??", "musicbee",
                "网易云", "netease", "kuwo", "kugou", "qq music", "winamp", "foobar",
                "music", "song", "audio", "player", "music", "song", "audio", "playlist"
            ],
            "社交": [
                "discord", "wechat", "qq", "skype", "zoom", "teams", "slack",
                "whatsapp", "telegram", "signal", "messenger", "facebook", "instagram",
                "twitter", "x", "linkedin", "tiktok", "douyin",
                "chat", "message", "social", "contact", "friend", "conversation"
            ],
            "邮件": [
                "outlook", "gmail", "mail", "thunderbird", "mailchimp", "protonmail",
                "邮件", "email", "inbox", "mail", "email", "message", "inbox", "outbox"
            ],
            "工具": [
                "calculator", "notepad", "paint", "snip", "snipping", "screenshot",
                "explorer", "finder", "file explorer", "task manager", "control panel",
                "tool", "utility", "app", "application", "program", "software"
            ],
        }

        # 首先尝试精确匹配
        for scene, keywords in keyword_groups.items():
            if any(keyword in title_lower for keyword in keywords):
                # 如果是浏览器场景，进一步分类
                if scene == "浏览":
                    return self._classify_browser_content(window_title)
                return scene

        # 尝试更宽松的匹配，检查窗口标题中是否包含常见的场景相关词汇
        loose_match = {
            "编程": ["代码", "程序", "开发", "debug", "编译", "运行"],
            "设计": ["设计", "创意", "美术", "绘图", "编辑"],
            "办公": ["文档", "表格", "演示", "会议", "工作"],
            "游戏": ["游戏", "游玩", "关卡", "任务", "角色"],
            "视频": ["视频", "电影", "电视", "节目", "播放"],
            "阅读": ["阅读", "书籍", "小说", "文档", "文章"],
            "音乐": ["音乐", "歌曲", "音频", "播放"],
            "社交": ["聊天", "消息", "社交", "联系", "朋友"],
            "邮件": ["邮件", "邮箱", "邮件", "发送", "接收"],
        }

        for scene, keywords in loose_match.items():
            if any(keyword in title_lower for keyword in keywords):
                return scene

        # 最后，根据窗口标题的长度和内容进行判断
        if len(title_lower) > 10:
            # 如果标题较长，可能是浏览器或其他应用
            if any(browser in title_lower for browser in ["chrome", "firefox", "edge", "safari", "opera"]):
                return "浏览"
            elif any(video in title_lower for video in ["youtube", "bilibili", "netflix", "video", "movie"]):
                return "视频"
            elif any(game in title_lower for game in ["game", "steam", "epic"]):
                return "游戏"

        return "未知"

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
            
            # 检查电池状态（部分设备/系统可能不支持）
            battery = None
            if hasattr(psutil, "sensors_battery"):
                try:
                    battery = psutil.sensors_battery()
                except Exception as battery_error:
                    logger.debug(f"获取电池状态失败: {battery_error}")
            if battery and getattr(battery, "percent", None) is not None and battery.percent < 20:
                system_prompt += " 当前设备电量偏低，若建议涉及长时间操作，请顺手提醒保存进度。"

            if cpu_percent > 80 or memory_percent > 80:
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

    async def _get_weather_prompt(self) -> str:
        """获取天气提示词。"""
        weather_prompt = ""
        weather_api_key = self.weather_api_key
        weather_city = self.weather_city

        if weather_api_key and weather_city:
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    url = f"http://api.openweathermap.org/data/2.5/weather?q={weather_city}&appid={weather_api_key}&units=metric&lang=zh_cn"
                    async with session.get(url) as response:
                        if response.status == 200:
                            weather_data = await response.json()
                            weather_main = weather_data.get("weather", [{}])[0].get(
                                "main", ""
                            )
                            weather_desc = weather_data.get("weather", [{}])[0].get(
                                "description", ""
                            )
                            temp = weather_data.get("main", {}).get("temp", 0)

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
        scene = "未知"
        scene_prompt = ""
        time_prompt = ""
        holiday_prompt = ""
        system_status_prompt = ""
        weather_prompt = ""

        if active_window_title:
            try:
                scene = self._identify_scene(active_window_title)
                scene_prompt = self._get_scene_preference(scene)
            except Exception as e:
                if debug_mode:
                    logger.debug(f"场景识别失败: {e}")

        try:
            time_prompt = self._get_time_prompt(allow_rest_hint=allow_rest_hint)
        except Exception as e:
            if debug_mode:
                logger.debug(f"获取时间提示失败: {e}")

        try:
            holiday_prompt = self._get_holiday_prompt()
        except Exception as e:
            if debug_mode:
                logger.debug(f"获取节日提示失败: {e}")

        try:
            system_status_prompt, _ = self._get_system_status_prompt()
        except Exception as e:
            if debug_mode:
                logger.debug(f"获取系统状态失败: {e}")

        try:
            weather_prompt = await self._get_weather_prompt()
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

    async def _collect_recent_conversation_context(
        self,
        session=None,
        *,
        debug_mode: bool,
    ) -> list[str]:
        contexts: list[str] = []
        try:
            if not hasattr(self.context, "conversation_manager"):
                return contexts

            conv_mgr = self.context.conversation_manager
            uid = ""
            try:
                uid = session.unified_msg_origin if session else ""
            except Exception as e:
                if debug_mode:
                    logger.debug(f"读取会话 UID 失败: {e}")

            if not uid:
                return contexts

            try:
                curr_cid = await conv_mgr.get_curr_conversation_id(uid)
                if curr_cid:
                    conversation = await conv_mgr.get_conversation(uid, curr_cid)
                    if conversation and conversation.history:
                        for msg in conversation.history[-5:]:
                            if msg.get("role") in {"user", "assistant"}:
                                content = str(msg.get("content", "") or "").strip()
                                if content:
                                    role = "用户" if msg.get("role") == "user" else "助手"
                                    contexts.append(f"{role}: {content}")
            except Exception as e:
                if debug_mode:
                    logger.debug(f"读取对话上下文失败: {e}")
        except Exception as e:
            if debug_mode:
                logger.debug(f"收集上下文失败: {e}")
        return contexts

    async def _recognize_screen_material(
        self,
        *,
        capture_context: dict[str, Any],
        use_external_vision: bool,
        scene: str,
        active_window_title: str,
    ) -> str:
        if not use_external_vision:
            return ""

        recognition_text = await self._call_external_vision_api(
            capture_context.get("media_bytes", b"") or b"",
            media_kind=str(capture_context.get("media_kind", "image") or "image"),
            mime_type=str(capture_context.get("mime_type", "image/jpeg") or "image/jpeg"),
            scene=scene,
            active_window_title=active_window_title,
        )
        return self._compress_recognition_text(recognition_text)

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
        timeout_seconds = self._get_interaction_timeout(
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
            self._call_provider_multimodal_direct(
                provider=provider,
                interaction_prompt=interaction_prompt,
                system_prompt=system_prompt,
                media_bytes=media_bytes,
                media_kind=media_kind,
                mime_type=mime_type,
                provider_id=await self._get_current_chat_provider_id(umo=umo),
            ),
            timeout=timeout_seconds,
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
        should_send_rest_reminder, rest_reminder_info = self._should_send_rest_reminder()
        if self._is_in_rest_time_range() and not (should_send_rest_reminder and not custom_prompt):
            logger.info(f"[任务 {task_id}] 当前处于休息时段，跳过识屏。")
            return []

        if not self._is_in_active_time_range():
            logger.info(f"[任务 {task_id}] 当前不在主动互动时段，跳过识屏。")
            return []

        provider = self.context.get_using_provider()
        if not provider:
            return [Plain("当前没有可用的 AstrBot 模型提供方。")]

        umo = None
        if session and hasattr(session, "unified_msg_origin"):
            umo = session.unified_msg_origin

        system_prompt = await self._get_persona_prompt(umo)
        debug_mode = self._get_runtime_flag("debug")
        media_kind = str(capture_context.get("media_kind", "image") or "image")
        mime_type = str(capture_context.get("mime_type", "image/jpeg") or "image/jpeg")
        media_bytes = capture_context.get("media_bytes", b"") or b""
        use_external_vision = self._get_runtime_flag("use_external_vision")
        effective_use_external_vision = use_external_vision
        analysis_trace = {
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

        analysis_context = await self._gather_screen_analysis_context(
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

        contexts = await self._collect_recent_conversation_context(
            session,
            debug_mode=debug_mode,
        )
        reply_interval_guidance, reply_interval_info = self._build_reply_interval_guidance(
            task_id
        )
        analysis_trace["reply_interval_seconds"] = int(
            reply_interval_info.get("elapsed_seconds", 0) or 0
        )
        analysis_trace["reply_interval_bucket"] = str(
            reply_interval_info.get("bucket", "") or ""
        )
        preserve_full_video_for_audio = False
        if media_kind == "video" and not effective_use_external_vision:
            preserve_full_video_for_audio = await self._supports_native_gemini_video_audio(
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

            effective_capture_context = capture_context
            effective_media_kind = media_kind
            effective_mime_type = mime_type
            effective_media_bytes = media_bytes
            material_label = "录屏视频" if media_kind == "video" else "截图"
            sampling_profile = self._get_scene_behavior_profile(scene)
            sampled_capture_context = None
            recognition_capture_context = capture_context

            if media_kind == "video":
                sampled_capture_context = await self._build_video_sample_capture_context(
                    capture_context,
                    scene=scene,
                    use_external_vision=effective_use_external_vision,
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
                    if self._should_keep_sampled_video_only(
                        scene,
                        use_external_vision=use_external_vision,
                        preserve_full_video_for_audio=preserve_full_video_for_audio,
                    ):
                        effective_capture_context = sampled_capture_context
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
                        if use_external_vision:
                            recognition_capture_context = sampled_capture_context

            recognition_text = await self._recognize_screen_material(
                capture_context=recognition_capture_context,
                use_external_vision=effective_use_external_vision,
                scene=scene,
                active_window_title=active_window_title,
            )
            if (
                media_kind == "video"
                and effective_use_external_vision
                and sampled_capture_context is not None
                and recognition_capture_context is sampled_capture_context
                and self._looks_uncertain_screen_result(recognition_text)
            ):
                recognition_text = await self._recognize_screen_material(
                    capture_context=capture_context,
                    use_external_vision=effective_use_external_vision,
                    scene=scene,
                    active_window_title=active_window_title,
                )
                analysis_trace["analysis_material_kind"] = "video"
                analysis_trace["used_full_video"] = True
                material_label = "录屏视频"

            if effective_use_external_vision and self._is_screen_error_text(recognition_text):
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
            prompt_parts.append(f"回复节奏：{reply_interval_guidance}")

            related_memories = self._trigger_related_memories(scene, active_window_title)
            analysis_trace["memory_hints"] = related_memories[:4]
            if related_memories:
                memory_lines = "\n".join(f"- {memory}" for memory in related_memories[:3])
                prompt_parts.append("可参考的相关记忆：\n" + memory_lines)

            shared_activities = self._get_relevant_shared_activities(scene, limit=3)
            if shared_activities:
                activity_lines = []
                for activity_name, activity_data in shared_activities:
                    category = self._shared_activity_category_label(
                        activity_data.get("category", "other")
                    )
                    last_shared = activity_data.get("last_shared", "未知")
                    activity_lines.append(f"- {category}: {activity_name}（最近共同提到：{last_shared}）")
                prompt_parts.append("可引用的共同经历：\n" + "\n".join(activity_lines))

            if self.observations:
                observation_lines = []
                for obs in self.observations[-3:][::-1]:
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
                self._build_companion_response_guide(
                    scene=scene,
                    recognition_text=recognition_text,
                    custom_prompt=custom_prompt,
                    context_count=len(contexts),
                )
            )

            if self._should_offer_shared_activity_invite(scene, custom_prompt):
                prompt_parts.append(
                    "如果语气自然，可以轻轻表达你也想和用户一起做点轻松的事，但必须低频、顺势，不能打断正事。"
                )

            if sampling_profile["category"] == "entertainment":
                prompt_parts.append("更偏轻声陪伴和顺势提醒，不要过度推动任务。")
            elif sampling_profile["category"] == "work":
                prompt_parts.append("建议尽量收束成 1 到 2 个具体判断或下一步。")
            else:
                prompt_parts.append("回复尽量简短、具体、贴近当前任务。")

            latest_window_title = self._normalize_window_title(
                capture_context.get("latest_window_title", "")
            )
            clip_window_title = self._normalize_window_title(
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

            try:
                interaction_response = await self._request_screen_interaction(
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
                recognition_text = self._compress_recognition_text(response_text)

            analysis_trace["recognition_summary"] = self._truncate_preview_text(
                recognition_text or response_text,
                limit=120,
            )
            observation_stored = self._add_observation(
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
                self._update_long_term_memory(
                    scene,
                    active_window_title,
                    1,
                    memory_summary=recognition_text or response_text,
                    response_preview=response_text,
                )

            self._update_activity(scene, active_window_title)
            response_text = self._polish_response_text(
                response_text,
                scene,
                contexts=contexts,
                allow_rest_hint=bool(analysis_trace.get("rest_reminder_planned")),
                task_id=task_id,
            )
            analysis_trace["reply_preview"] = self._truncate_preview_text(
                response_text,
                limit=140,
            )
            analysis_trace["status"] = "ok"
            capture_context["_analysis_trace"] = analysis_trace
            self._adjust_interaction_frequency(response_text)
            self._record_screen_analysis_result(True)

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
            self._record_screen_analysis_result(False, error_type=error_type)
            return [Plain(error_text)]

        if media_kind != "image":
            return [Plain(response_text)]

        temp_dir = tempfile.gettempdir()
        temp_file_path = os.path.join(temp_dir, f"screen_shot_{uuid.uuid4()}.jpg")
        with open(temp_file_path, "wb") as f:
            f.write(media_bytes)

        if self.save_local:
            try:
                data_dir = StarTools.get_data_dir()
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

    @filter.command("kp")
    async def kp(self, event: AstrMessageEvent):
        """立即执行一次截图分析。"""
        ok, err_msg = self._check_screenshot_env()
        if not ok:
            yield event.plain_result(f"无法使用屏幕观察：\n{err_msg}")
            return

        try:
            capture_context = await asyncio.wait_for(
                self._capture_screenshot_context(), timeout=20.0
            )
            screen_result = await self._run_screen_assist(
                event,
                task_id="manual",
                custom_prompt="",
                history_user_text="/kp",
                capture_context=capture_context,
            )

            if not screen_result:
                yield event.plain_result("未获取到有效识别结果")
                return

            segments = self._split_message(screen_result)
            if len(segments) > 1:
                for i in range(len(segments) - 1):
                    segment = segments[i]
                    if segment.strip():
                        await self.context.send_message(
                            event.unified_msg_origin, MessageChain([Plain(segment)])
                        )
                        await asyncio.sleep(0.5)
                if segments[-1].strip():
                    yield event.plain_result(segments[-1])
            else:
                yield event.plain_result(screen_result)

            if self.debug:
                logger.info("处理完成")
        except asyncio.TimeoutError:
            logger.error("操作超时，请检查网络连接、模型响应速度或系统资源。")
            yield event.plain_result("操作超时，请稍后重试。")
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            import traceback

            logger.error(traceback.format_exc())
            yield event.plain_result("这次处理失败了，我先缓一口气，你可以再试一次。")

    @filter.command("kpr")
    async def kpr(self, event: AstrMessageEvent):
        """\u7acb\u5373\u6267\u884c\u4e00\u6b21\u5f55\u5c4f\u5206\u6790\u3002"""
        ok, err_msg = self._check_recording_env()
        if not ok:
            yield event.plain_result(f"\u65e0\u6cd5\u4f7f\u7528\u5f55\u5c4f\u8bc6\u522b\uff1a\n{err_msg}")
            return

        try:
            duration = self._get_recording_duration_seconds()
            capture_timeout = self._get_capture_context_timeout("video")
            yield event.plain_result(
                f"\u5f00\u59cb\u5f55\u5236\u6700\u8fd1 {duration} \u79d2\u684c\u9762\u753b\u9762\u4e86\u3002\n"
                "\u5f55\u5236\u5b8c\u6210\u540e\u6211\u4f1a\u7ee7\u7eed\u5206\u6790\u5185\u5bb9\uff0c\u6574\u4e2a\u8fc7\u7a0b\u4f1a\u6bd4 /kp \u6162\u4e00\u4e9b\u3002"
            )
            capture_context = await asyncio.wait_for(
                self._capture_one_shot_recording_context(duration),
                timeout=capture_timeout,
            )
            yield event.plain_result("\u5f55\u5236\u5b8c\u6210\uff0c\u6b63\u5728\u5206\u6790\u753b\u9762\u5185\u5bb9...")

            screen_result = await self._run_screen_assist(
                event,
                task_id="manual_recording",
                custom_prompt="",
                history_user_text="/kpr",
                capture_context=capture_context,
                analysis_timeout=self._get_screen_analysis_timeout("video"),
            )

            if not screen_result:
                yield event.plain_result("\u8fd9\u6b21\u5f55\u5c4f\u6ca1\u6709\u62ff\u5230\u6709\u6548\u8bc6\u522b\u7ed3\u679c\uff0c\u53ef\u4ee5\u7a0d\u540e\u518d\u8bd5\u4e00\u6b21\u3002")
                return

            segments = self._split_message(screen_result)
            if len(segments) > 1:
                for i in range(len(segments) - 1):
                    segment = segments[i]
                    if segment.strip():
                        await self.context.send_message(
                            event.unified_msg_origin, MessageChain([Plain(segment)])
                        )
                        await asyncio.sleep(0.5)
                if segments[-1].strip():
                    yield event.plain_result(segments[-1])
            else:
                yield event.plain_result(screen_result)

            if self.debug:
                logger.info("\u5355\u6b21\u5f55\u5c4f\u6307\u4ee4\u5904\u7406\u5b8c\u6210")
        except asyncio.TimeoutError:
            logger.error("\u5355\u6b21\u5f55\u5c4f\u6216\u8bc6\u522b\u64cd\u4f5c\u8d85\u65f6")
            yield event.plain_result(
                "\u8fd9\u6b21 /kpr \u8d85\u65f6\u4e86\u3002\n"
                f"\u5f53\u524d\u5f55\u5c4f\u65f6\u957f\u662f {self._get_recording_duration_seconds()} \u79d2\uff0c"
                "\u5982\u679c\u8fd9\u4e2a\u95ee\u9898\u7ecf\u5e38\u51fa\u73b0\uff0c\u5efa\u8bae\u4f18\u5148\u7f29\u77ed\u5f55\u5c4f\u65f6\u957f\u6216\u964d\u4f4e\u5e27\u7387\u540e\u518d\u8bd5\u3002"
            )
        except Exception as e:
            logger.error(f"\u5355\u6b21\u5f55\u5c4f\u8bc6\u522b\u5931\u8d25: {e}")
            import traceback

            logger.error(traceback.format_exc())
            yield event.plain_result(
                "\u8fd9\u6b21\u5f55\u5c4f\u8bc6\u522b\u5931\u8d25\u4e86\uff0c\u4f60\u53ef\u4ee5\u7a0d\u540e\u518d\u8bd5\u4e00\u6b21\u3002"
            )

    @filter.event_message_type(filter.EventMessageType.ALL, priority=0)
    async def on_shared_activity_memory(self, event: AstrMessageEvent):
        """从用户明确提到的共同经历里学习。"""
        try:
            message_text = str(getattr(event, "message_str", "") or "").strip()
            if not message_text or message_text.startswith("/"):
                return
            self._remember_recent_user_activity(event)
            self._learn_shared_activity_from_message(message_text)
        except Exception as e:
            logger.debug(f"记录共同经历失败: {e}")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def on_natural_language_screen_assist(self, event: AstrMessageEvent):
        """处理自然语言触发的识屏求助。"""
        if not getattr(self, "enable_natural_language_screen_assist", False):
            return

        try:
            message_text = str(getattr(event, "message_str", "") or "").strip()
            if not message_text or message_text.startswith("/"):
                return

            request_prompt = self._extract_screen_assist_prompt(message_text)
            if not request_prompt:
                return

            cooldown_key = str(getattr(event, "unified_msg_origin", "") or getattr(event, "get_sender_id", lambda: "")())
            now_ts = time.time()
            last_trigger = float((getattr(self, "_screen_assist_cooldowns", {}) or {}).get(cooldown_key, 0.0))
            if now_ts - last_trigger < 20:
                if self.debug:
                    logger.info("自然语言识屏求助命中过冷却时间，跳过触发")
                return

            ok, err_msg = self._check_env()
            if not ok:
                if self.debug:
                    logger.warning(f"自然语言识屏求助环境检查失败: {err_msg}")
                return
            custom_prompt = (
                "这是用户主动请求你看看当前屏幕并给建议。"
                "请直接回应眼前任务，不要提自动撤回或系统设定。"
            )
            screen_result = await self._run_screen_assist(
                event,
                task_id="nl_screen_assist",
                custom_prompt=custom_prompt,
                history_user_text=message_text,
            )
            if not screen_result:
                return

            event.stop_event()
            segments = self._split_message(screen_result)
            for index, segment in enumerate(segments):
                if not segment.strip():
                    continue
                if index == len(segments) - 1:
                    yield event.plain_result(segment)
                else:
                    await self.context.send_message(
                        event.unified_msg_origin, MessageChain([Plain(segment)])
                    )
                    await asyncio.sleep(0.4)
        except Exception as e:
            logger.error(f"自然语言识屏助手失败: {e}")

    @filter.command("kps")
    async def kps(self, event: AstrMessageEvent):
        """切换自动观察运行状态。"""
        self._ensure_runtime_state()
        if self.state == "active":
            # 停止自动观察
            self.state = "inactive"
            self.is_running = False
            logger.info("正在停止所有自动观察任务...")

            # 停止所有自动任务
            tasks_to_cancel = list(self.auto_tasks.items())
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

            self.auto_tasks.clear()
            logger.info("所有自动观察任务已停止")
            end_response = await self._get_end_response(event.unified_msg_origin)
            yield event.plain_result(end_response)
        else:
            # 启动自动观察
            if not self.enabled:
                yield event.plain_result(
                    "插件当前未启用，请先在配置中开启后再启动自动观察。"
                )
                return

            ok, err_msg = self._check_env(check_mic=False)
            if not ok:
                yield event.plain_result(f"启动失败：\n{err_msg}")
                return

            # 检查是否已有自动观察任务
            if self.AUTO_TASK_ID in self.auto_tasks or self.is_running:
                logger.info("自动观察任务已存在，无需重复启动")
                yield event.plain_result("自动观察任务已在运行中")
                return

            self.state = "active"
            self.is_running = True
            logger.info(f"启动任务 {self.AUTO_TASK_ID}")
            self.auto_tasks[self.AUTO_TASK_ID] = asyncio.create_task(
                self._auto_screen_task(event, task_id=self.AUTO_TASK_ID)
            )
            start_response = await self._get_start_response(event.unified_msg_origin)
            yield event.plain_result(start_response)

    @filter.command_group("kpi")
    def kpi_group(self):
        """管理自动观察屏幕任务。"""
        pass

    @kpi_group.command("ys")
    async def kpi_ys(self, event: AstrMessageEvent, preset_index: int = None):
        """切换预设。"""
        if preset_index is None:
            async for result in self._render_preset_list(event):
                yield result
            return
        
        if preset_index < 0:
            self.current_preset_index = -1
            self.plugin_config.current_preset_index = -1
            yield event.plain_result("已切换到手动配置模式。")
            return
        
        if preset_index >= len(self.parsed_custom_presets):
            yield event.plain_result(
                f"预设 {preset_index} 不存在。\n"
                f"当前共有 {len(self.parsed_custom_presets)} 个预设。\n"
                f"用法: /kpi y [序号] [间隔秒数] [触发概率]"
            )
            return
        
        self.current_preset_index = preset_index
        self.plugin_config.current_preset_index = preset_index
        
        preset = self.parsed_custom_presets[preset_index]
        yield event.plain_result(
            f"已切换到预设 {preset_index}: {preset['name']}，间隔 {preset['check_interval']} 秒，触发概率 {preset['trigger_probability']}%"
        )

    @kpi_group.command("start")
    async def kpi_start(self, event: AstrMessageEvent):
        self._ensure_runtime_state()
        if not self.enabled:
            yield event.plain_result(
                    "插件当前未启用，请先在配置中开启后再启动自动观察。"
            )
            return

        ok, err_msg = self._check_env(check_mic=False)
        if not ok:
            yield event.plain_result(f"启动失败：\n{err_msg}")
            return

        # 检查是否已有自动观察任务
        if self.AUTO_TASK_ID in self.auto_tasks:
            logger.info("自动观察任务已存在，无需重复启动")
            return

        self.state = "active"
        self.is_running = True
        logger.info(f"启动任务 {self.AUTO_TASK_ID}")
        self.auto_tasks[self.AUTO_TASK_ID] = asyncio.create_task(
            self._auto_screen_task(event, task_id=self.AUTO_TASK_ID)
        )
        start_response = await self._get_start_response(event.unified_msg_origin)
        yield event.plain_result(f"已启动自动观察任务 {self.AUTO_TASK_ID}。\n{start_response}")

    @kpi_group.command("stop")
    async def kpi_stop(self, event: AstrMessageEvent, task_id: str = None):
        """停止自动观察任务。"""
        self._ensure_runtime_state()
        if task_id:
            if task_id in self.auto_tasks:
                task = self.auto_tasks.pop(task_id)
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning(f"等待任务 {task_id} 停止超时")
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"停止任务 {task_id} 失败: {e}")
                yield event.plain_result(f"已停止自动观察任务 {task_id}。")
            else:
                yield event.plain_result(f"任务 {task_id} 不存在。")
        else:
            # 停止所有自动任务
            tasks_to_cancel = list(self.auto_tasks.items())
            for task_id, task in tasks_to_cancel:
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning(f"等待任务 {task_id} 停止超时")
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"停止任务 {task_id} 失败: {e}")
                self.auto_tasks.pop(task_id, None)
            
            # 停止窗口陪伴任务
            if hasattr(self, "window_companion_active_title") and self.window_companion_active_title:
                await self._stop_window_companion_session(reason="manual_stop")
            
            self.is_running = False
            self.state = "inactive"
            end_response = await self._get_end_response(event.unified_msg_origin)
            yield event.plain_result(f"已停止所有自动观察任务。\n{end_response}")

    async def _render_webui_status(self, event: AstrMessageEvent):
        """查看 WebUI 信息。"""
        self._ensure_runtime_state()
        if self.webui_enabled:
            # 检查 WebUI 服务是否正在运行
            webui_running = self.web_server is not None and getattr(self.web_server, "_started", False)
            
            if webui_running:
                # 获取实际使用的端口
                actual_port = getattr(self.web_server, "port", self.webui_port)
                host = self.webui_host
                if host == "0.0.0.0":
                    access_url = f"http://127.0.0.1:{actual_port}"
                else:
                    access_url = f"http://{host}:{actual_port}"
                
                auth_status = "已启用" if self.webui_auth_enabled else "未启用"
                password = self.webui_password or "（未设置，首次访问时会自动生成）"
                
                response = f"WebUI 状态：已启用\n"
                response += f"访问地址：{access_url}\n"
                response += f"认证状态：{auth_status}\n"
                response += f"访问密码：{password}\n"
                response += f"会话超时：{self.webui_session_timeout} 秒"
            else:
                # WebUI 已启用但服务未运行，尝试启动
                try:
                    await self._start_webui()
                    # 再次检查状态
                    webui_running = self.web_server is not None and getattr(self.web_server, "_started", False)
                    if webui_running:
                        actual_port = getattr(self.web_server, "port", self.webui_port)
                        host = self.webui_host
                        if host == "0.0.0.0":
                            access_url = f"http://127.0.0.1:{actual_port}"
                        else:
                            access_url = f"http://{host}:{actual_port}"
                        
                        auth_status = "已启用" if self.webui_auth_enabled else "未启用"
                        password = self.webui_password or "（未设置，首次访问时会自动生成）"
                        
                        response = f"WebUI 状态：已启用\n"
                        response += f"访问地址：{access_url}\n"
                        response += f"认证状态：{auth_status}\n"
                        response += f"访问密码：{password}\n"
                        response += f"会话超时：{self.webui_session_timeout} 秒"
                    else:
                        response = f"WebUI 已启用但启动失败，请检查配置和端口占用情况。\n"
                        response += f"配置的端口：{self.webui_port}\n"
                        response += f"配置的地址：{self.webui_host}"
                except Exception as e:
                    response = f"WebUI 已启用但启动失败：{str(e)}"
        else:
            response = "WebUI 未启用，请在配置中开启。"
        
        yield event.plain_result(response)

    async def _render_status_report(self, event: AstrMessageEvent):
        """输出当前运行状态和关键诊断信息。"""
        report = await self._build_kpi_doctor_report(event)
        yield event.plain_result(report)

    @kpi_group.command("status")
    async def kpi_status(self, event: AstrMessageEvent):
        """输出当前运行状态和关键诊断信息。"""
        async for result in self._render_status_report(event):
            yield result

    @kpi_group.command("list")
    async def kpi_list(self, event: AstrMessageEvent):
        """列出当前运行中的自动观察任务。"""
        self._ensure_runtime_state()
        if not self.auto_tasks:
            yield event.plain_result("当前没有运行中的自动观察任务。")
        else:
            msg = "当前运行中的任务：\n"
            for task_id in self.auto_tasks:
                msg += f"- {task_id}\n"
            yield event.plain_result(msg)

    @kpi_group.command("ffmpeg")
    async def kpi_ffmpeg(self, event: AstrMessageEvent, ffmpeg_path: str = None):
        """设置 ffmpeg 路径并自动复制到插件数据目录。"""
        import shutil
        
        if not ffmpeg_path:
            current_ffmpeg = self._get_ffmpeg_path()
            if current_ffmpeg:
                yield event.plain_result(f"当前 ffmpeg 路径：{current_ffmpeg}")
            else:
                storage_dir = self._get_ffmpeg_storage_dir()
                yield event.plain_result(
                    "未找到 ffmpeg。\n"
                    "用法: /kpi ffmpeg [ffmpeg.exe 所在路径]\n"
                    "例如: /kpi ffmpeg C:\\Users\\用户名\\Downloads\\ffmpeg\\bin\\ffmpeg.exe\n"
                    "\n"
                    f"插件会自动将 ffmpeg 复制到插件数据目录的 bin 文件夹：{storage_dir}"
                )
            return
        
        source_path = os.path.abspath(os.path.expanduser(ffmpeg_path.strip()))
        
        ffmpeg_bin_dir = self._get_ffmpeg_storage_dir(create=True)
        
        dest_path = os.path.join(ffmpeg_bin_dir, "ffmpeg.exe")
        
        if not os.path.exists(source_path):
            yield event.plain_result(f"源文件不存在：{source_path}")
            return
        
        try:
            shutil.copy2(source_path, dest_path)
            self._recording_ffmpeg_path = None  # 清除缓存，强制重新检测
            new_path = self._get_ffmpeg_path()
            yield event.plain_result(f"ffmpeg 已复制到：{new_path}")
        except Exception as e:
            yield event.plain_result(f"复制失败：{str(e)}")

    @kpi_group.command("y")
    async def kpi_y(self, event: AstrMessageEvent, preset_index: int = None, interval: int = None, probability: int = None):
        """新增或修改自定义预设。"""
        if preset_index is None:
            yield event.plain_result(
                "用法: /kpi y [预设序号] [间隔秒数] [触发概率]\n"
                "例如: /kpi y 1 90 30 表示把预设 1 设置为每 90 秒、30% 概率触发"
            )
            return
        
        if interval is None or probability is None:
            yield event.plain_result(
                "用法: /kpi y [预设序号] [间隔秒数] [触发概率]\n"
                "例如: /kpi y 1 90 30 表示把预设 1 设置为每 90 秒、30% 概率触发"
            )
            return
        
        if preset_index < 0:
            yield event.plain_result("预设序号不能小于 0。")
            return
        
        interval = max(10, int(interval))
        probability = max(0, min(100, int(probability)))
        
        lines = []
        if self.custom_presets:
            lines = self.custom_presets.strip().split('\n')
        
        preset_name = f"预设{preset_index}"
        new_preset = f"{preset_name}|{interval}|{probability}"
        
        while len(lines) <= preset_index:
            lines.append("")
        
        lines[preset_index] = new_preset
        
        self.custom_presets = "\n".join(lines)
        self.plugin_config.custom_presets = self.custom_presets
        
        self._parse_custom_presets()
        
        yield event.plain_result(
            f"已更新预设 {preset_index}：间隔 {interval} 秒，触发概率 {probability}%"
        )

    async def _render_preset_list(self, event: AstrMessageEvent):
        """列出所有自定义预设 /kpi p"""
        if not self.parsed_custom_presets:
            yield event.plain_result(
                "当前还没有自定义预设。\n"
                "用法: /kpi y [预设序号] [间隔秒数] [触发概率]\n"
                "例如: /kpi y 1 90 30"
            )
            return
        
        msg = "当前自定义预设：\n"
        for i, preset in enumerate(self.parsed_custom_presets):
            current_marker = ""
            if i == self.current_preset_index:
                current_marker = " <- 当前使用"
            msg += f"{i}. {preset['name']}: {preset['check_interval']} 秒间隔，{preset['trigger_probability']}% 触发概率{current_marker}\n"
        
        msg += f"\n当前使用: {'预设 ' + str(self.current_preset_index) if self.current_preset_index >= 0 else '手动配置'}"
        msg += "\n切换预设: /kpi [预设序号]，例如 /kpi 0"
        yield event.plain_result(msg)

    @kpi_group.command("p")
    async def kpi_p(self, event: AstrMessageEvent):
        """列出全部自定义预设。"""
        async for result in self._render_preset_list(event):
            yield result

    @kpi_group.command("add")
    async def kpi_add(self, event: AstrMessageEvent, interval: int, *prompt):
        """新增一个自定义观察任务。"""
        if not self.enabled:
            yield event.plain_result(
                "插件当前未启用，请先开启后再添加自定义任务。"
            )
            return

        custom_prompt = " ".join(prompt) if prompt else ""
        try:
            interval = max(30, int(interval))
            if not self.is_running:
                self.is_running = True
            task_id = f"task_{self.task_counter}"
            self.task_counter += 1
            self.auto_tasks[task_id] = asyncio.create_task(
                self._auto_screen_task(
                    event,
                    task_id=task_id,
                    custom_prompt=custom_prompt,
                    interval=interval,
                )
            )
            yield event.plain_result(
                f"已添加自定义任务 {task_id}，触发间隔为 {interval} 秒。"
            )
        except ValueError:
            yield event.plain_result("用法: /kpi add [间隔秒数] [自定义提示词]")

    @kpi_group.command("d")
    async def kpi_d(self, event: AstrMessageEvent, date: str = None):
        """查看指定日期的日记。"""
        async for result in self._handle_diary_command(event, date):
            yield result

    async def _handle_diary_command(self, event: AstrMessageEvent, date: str = None):
        """处理日记查看命令。"""
        import datetime
        import os

        if not self.enable_diary:
            yield event.plain_result("补写日记失败了，这次没有成功保存。")
            return

        # 确定要查看的日期
        if date:
            try:
                # 支持两种日期格式：YYYY-MM-DD 和 YYYYMMDD
                date_str = str(date)
                if len(date_str) == 8 and date_str.isdigit():
                    target_date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
                else:
                    target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                yield event.plain_result(
                    "日期格式错误，请使用 YYYY-MM-DD 或 YYYYMMDD，例如：/kpi d 20260302"
                )
                return
        else:
            now = datetime.datetime.now()
            target_date = self._resolve_diary_target_date(now)
            if now.hour < 2:
                yield event.plain_result(
                    f"当前时间还在凌晨两点前，默认查看 {target_date.strftime('%Y年%m月%d日')} 的日记。"
                )

        # 构建日记文件路径
        diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
        diary_path = os.path.join(self.diary_storage, diary_filename)

        if not os.path.exists(diary_path):
            yield event.plain_result(
                f"{target_date.strftime('%Y年%m月%d日')} 的日记还不存在。"
            )
            return

        try:
            with open(diary_path, encoding="utf-8") as f:
                diary_content = f.read()
            
            # 更新日记查看状态
            date_str = target_date.strftime("%Y%m%d")
            self._update_diary_view_status(date_str)

            # 提取感想部分
            summary_start = diary_content.find("## 今日感想")
            if summary_start != -1:
                summary_content = diary_content[summary_start:]
                # 提取感想文本并去除标题
                summary_lines = summary_content.split('\n')
                # 跳过标题行和空行
                start_idx = 2
                while start_idx < len(summary_lines) and (summary_lines[start_idx].strip().startswith('#') or not summary_lines[start_idx].strip()):
                    start_idx += 1
                summary_text = '\n'.join(summary_lines[start_idx:]).strip()
                summary_text = self._extract_diary_preview_text(summary_text)
                if len(summary_text) > 500:
                    summary_text = summary_text[:497] + "..."
                diary_message = f"{self.bot_name} 的日记\n{target_date.strftime('%Y年%m月%d日')}\n\n{summary_text or '这篇日记里还没有整理出完整感想。'}"
            else:
                # 尝试提取旧格式的总结部分
                summary_start = diary_content.find(f"## {self.bot_name}的总结")
                if summary_start == -1:
                    summary_start = diary_content.find("## 总结")
                if summary_start != -1:
                    summary_content = diary_content[summary_start:]
                    # 提取总结文本并去除标题
                    summary_lines = summary_content.split('\n')
                    summary_text = self._extract_diary_preview_text('\n'.join(summary_lines[2:]).strip())
                    if len(summary_text) > 500:
                        summary_text = summary_text[:497] + "..."
                    diary_message = f"{self.bot_name} 的日记\n{target_date.strftime('%Y年%m月%d日')}\n\n{summary_text or '这篇日记里还没有整理出完整感想。'}"
                else:
                    observation_start = diary_content.find("## 今日观察")
                    if observation_start != -1:
                        observation_content = diary_content[observation_start:]
                        observation_lines = observation_content.split('\n')
                        observation_text = self._extract_diary_preview_text('\n'.join(observation_lines[2:]).strip())
                        if len(observation_text) > 500:
                            observation_text = observation_text[:497] + "..."
                        diary_message = f"{self.bot_name} 的日记\n{target_date.strftime('%Y年%m月%d日')}\n\n{observation_text or '这篇日记里还没有可展示的内容。'}"
                    else:
                        diary_message = self._extract_diary_preview_text(diary_content) or "这篇日记里还没有可展示的内容。"

            if self.diary_auto_recall:
                logger.info(f"日记消息将在 {self.diary_recall_time} 秒后自动撤回")

                # 启动自动撤回任务
                async def recall_message():
                    await asyncio.sleep(self.diary_recall_time)
                    try:
                        logger.info(f"日记消息已到达自动撤回时间: {self.diary_recall_time} 秒")
                    except Exception as e:
                        logger.error(f"自动撤回日记记录失败: {e}")

                task = asyncio.create_task(recall_message())
                self.background_tasks.append(task)

            send_as_image = self.diary_send_as_image
            
            if send_as_image:
                try:
                    temp_file_path = self._generate_diary_image(diary_message)
                    yield event.image_result(temp_file_path)
                    os.unlink(temp_file_path)
                except Exception as e:
                    logger.error(f"生成日记图片失败: {e}")
                    yield event.plain_result(diary_message)
            else:
                yield event.plain_result(diary_message)

            # 同时生成日记被查看时的补充回复（异步进行）
            async def generate_blame():
                provider = self.context.get_using_provider()
                if provider:
                    try:
                        system_prompt = await self._get_persona_prompt(event.unified_msg_origin)
                        response = await provider.text_chat(
                            prompt=self.diary_response_prompt, system_prompt=system_prompt
                        )
                        if (
                            response
                            and hasattr(response, "completion_text")
                            and response.completion_text
                        ):
                            await self.context.send_message(
                                event.unified_msg_origin, 
                                MessageChain([Plain(response.completion_text)])
                            )
                        else:
                            await self.context.send_message(
                                event.unified_msg_origin, 
                                MessageChain([Plain("喂，你怎么又偷看我的日记呀，真是的……")])
                            )
                    except Exception as e:
                        logger.error(f"生成日记被偷看回复失败: {e}")
                        await self.context.send_message(
                            event.unified_msg_origin, 
                            MessageChain([Plain("喂，你怎么又偷看我的日记呀，真是的……")])
                        )
                else:
                    await self.context.send_message(
                        event.unified_msg_origin, 
                        MessageChain([Plain("喂，你怎么又偷看我的日记呀，真是的……")])
                    )

            # 异步生成这条补充回复
            blame_task = asyncio.create_task(generate_blame())
            self.background_tasks.append(blame_task)

        except Exception as e:
            logger.error(f"读取日记失败: {e}")
            yield event.plain_result("读取这篇日记时出了点问题。")

    @kpi_group.command("correct")
    async def kpi_correct(self, event: AstrMessageEvent, *args):
        """纠正 Bot 的回复。"""
        if len(args) < 2:
            yield event.plain_result("用法: /kpi correct [原回复] [纠正后的回复]")
            return
        
        # 提取原回复和纠正后的内容
        original = args[0]
        corrected = ' '.join(args[1:])
        
        # 记录纠正
        self._learn_from_correction(original, corrected)
        
        yield event.plain_result("已记录这次纠正，我会把它作为后续参考。")

    @kpi_group.command("preference")
    async def kpi_preference(self, event: AstrMessageEvent, category: str, *preference):
        """添加用户偏好。"""
        if not preference:
            yield event.plain_result("用法: /kpi preference [类别] [偏好内容]")
            yield event.plain_result("支持的类别: music, movies, food, hobbies, other")
            return
        
        # 验证类别
        valid_categories = ["music", "movies", "food", "hobbies", "other"]
        if category not in valid_categories:
            yield event.plain_result(f"无效类别，支持的类别有: {', '.join(valid_categories)}")
            return
        
        # 提取偏好内容
        preference_content = ' '.join(preference)
        
        # 添加偏好
        self._add_user_preference(category, preference_content)
        
        yield event.plain_result(f"已添加偏好: {category} - {preference_content}")

    @kpi_group.command("recent")
    async def kpi_recent(self, event: AstrMessageEvent, days: int = 3):
        """查看最近几天的日记。"""
        import datetime
        import os

        if not self.enable_diary:
            yield event.plain_result("日记功能当前未启用。")
            return

        days = max(1, min(7, int(days)))  # 限制 1-7 天
        # 获取日记文件列表
        today = datetime.date.today()
        found_diaries = []

        for i in range(days):
            target_date = today - datetime.timedelta(days=i)
            diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
            diary_path = os.path.join(self.diary_storage, diary_filename)

            if os.path.exists(diary_path):
                try:
                    with open(diary_path, encoding="utf-8") as f:
                        diary_content = f.read()
                    found_diaries.append(
                        {"date": target_date, "content": diary_content}
                    )
                except Exception as e:
                    logger.error(f"读取日记失败: {e}")

        if not found_diaries:
            yield event.plain_result("最近几天还没有找到可查看的日记。")
            return

        if self.diary_auto_recall:
            logger.info(f"日记消息将在 {self.diary_recall_time} 秒后自动撤回")

            # 启动自动撤回任务
            async def recall_message():
                await asyncio.sleep(self.diary_recall_time)
                try:
                    logger.info(f"最近日记消息已到达自动撤回时间: {self.diary_recall_time} 秒")
                except Exception as e:
                    logger.error(f"自动撤回日记记录失败: {e}")

            task = asyncio.create_task(recall_message())
            self.background_tasks.append(task)

        for diary in found_diaries:
            # 提取感想部分
            summary_start = diary['content'].find("## 今日感想")
            if summary_start != -1:
                summary_content = diary['content'][summary_start:]
                # 提取感想文本并去除标题
                summary_lines = summary_content.split('\n')
                summary_text = self._extract_diary_preview_text('\n'.join(summary_lines[2:]).strip())
                if len(summary_text) > 500:
                    summary_text = summary_text[:497] + "..."
                diary_message = f"{self.bot_name} 的日记\n{diary['date'].strftime('%Y年%m月%d日')}\n\n{summary_text or '这篇日记里还没有整理出完整感想。'}"
            else:
                # 尝试提取旧格式的总结部分
                summary_start = diary['content'].find(f"## {self.bot_name}的总结")
                if summary_start == -1:
                    summary_start = diary['content'].find("## 总结")
                if summary_start != -1:
                    summary_content = diary['content'][summary_start:]
                    # 提取总结文本并去除标题
                    summary_lines = summary_content.split('\n')
                    summary_text = self._extract_diary_preview_text('\n'.join(summary_lines[2:]).strip())
                    if len(summary_text) > 500:
                        summary_text = summary_text[:497] + "..."
                    diary_message = f"{self.bot_name} 的日记\n{diary['date'].strftime('%Y年%m月%d日')}\n\n{summary_text or '这篇日记里还没有整理出完整感想。'}"
                else:
                    # 如果没有总结段落，则回退到整篇日记内容
                    diary_text = self._extract_diary_preview_text(diary["content"])
                    if len(diary_text) > 500:
                        diary_text = diary_text[:497] + "..."
                    diary_message = f"{self.bot_name} 的日记\n{diary['date'].strftime('%Y年%m月%d日')}\n\n{diary_text or '这篇日记里还没有可展示的内容。'}"
            
            send_as_image = self.diary_send_as_image
            
            if send_as_image:
                try:
                    temp_file_path = self._generate_diary_image(diary_message)
                    yield event.image_result(temp_file_path)
                    os.unlink(temp_file_path)
                except Exception as e:
                    logger.error(f"生成日记图片失败: {e}")
                    yield event.plain_result(diary_message)
            else:
                yield event.plain_result(diary_message)
            
            await asyncio.sleep(0.5)  # 加一点小延迟，让发送更自然

        # 同时异步生成“被偷看日记”时的回复
        async def generate_blame():
            provider = self.context.get_using_provider()
            if provider:
                try:
                    system_prompt = await self._get_persona_prompt(event.unified_msg_origin)
                    response = await provider.text_chat(
                        prompt=self.diary_response_prompt, system_prompt=system_prompt
                    )
                    if (
                        response
                        and hasattr(response, "completion_text")
                        and response.completion_text
                    ):
                        await self.context.send_message(
                            event.unified_msg_origin, 
                            MessageChain([Plain(response.completion_text)])
                        )
                    else:
                        await self.context.send_message(
                            event.unified_msg_origin, 
                            MessageChain([Plain("喂，你怎么一下子翻了我这么多天的日记呀，真是的……")])
                        )
                except Exception as e:
                    logger.error(f"生成日记被偷看回复失败: {e}")
                    await self.context.send_message(
                        event.unified_msg_origin, 
                        MessageChain([Plain("喂，你怎么一下子翻了我这么多天的日记呀，真是的……")])
                    )
            else:
                await self.context.send_message(
                    event.unified_msg_origin, 
                    MessageChain([Plain("喂，你怎么一下子翻了我这么多天的日记呀，真是的……")])
                )

        # 异步生成这条吐槽式回复
        blame_task = asyncio.create_task(generate_blame())
        self.background_tasks.append(blame_task)

    @kpi_group.command("debug")
    async def kpi_debug(self, event: AstrMessageEvent, status: str = None):
        """切换调试模式 /kpi debug [on/off]"""
        if status is None:
            current_status = self.debug
            status_text = "开启" if current_status else "关闭"
            yield event.plain_result(f"当前调试模式状态：{status_text}")
            return
        
        status = status.lower()
        if status == "on":
            self.plugin_config.debug = True
            yield event.plain_result("调试模式已开启，后续会输出更多日志。")
        elif status == "off":
            self.plugin_config.debug = False
            yield event.plain_result("调试模式已关闭，将隐藏大部分调试日志。")
        else:
            yield event.plain_result("用法: /kpi debug [on/off]")

    @kpi_group.command("webui")
    async def kpi_webui(self, event: AstrMessageEvent, action: str = ""):
        """查看或控制 WebUI /kpi webui [start/stop]"""
        action_text = str(action or "").strip().lower()
        if not action_text:
            async for result in self._render_webui_status(event):
                yield result
            return
        if action_text == "start":
            if self.web_server:
                yield event.plain_result("WebUI 已经在运行中。")
            else:
                await self._start_webui()
                yield event.plain_result(f"WebUI 已启动，访问地址: http://127.0.0.1:{self.webui_port}")
        elif action_text == "stop":
            if not self.web_server:
                yield event.plain_result("WebUI 当前没有运行。")
            else:
                await self._stop_webui()
                self.web_server = None
                yield event.plain_result("WebUI 已停止。")
        else:
            yield event.plain_result("无效操作，请使用 /kpi webui start 或 /kpi webui stop")

    @kpi_group.command("cd")
    async def kpi_cd(self, event: AstrMessageEvent, date: str = None):
        """补写日记 /kpi cd [YYYYMMDD]"""
        async for result in self._handle_complete_command(event, date):
            yield result

    async def _handle_complete_command(self, event: AstrMessageEvent, date: str = None):
        """处理补写日记命令。"""
        import datetime
        import os

        if not self.enable_diary:
            yield event.plain_result("当前没有开启日记功能，暂时无法补写。")
            return

        # 确定要补写的日期
        if date:
            try:
                # 支持两种日期格式：YYYY-MM-DD 和 YYYYMMDD
                date_str = str(date)
                if len(date_str) == 8 and date_str.isdigit():
                    target_date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
                else:
                    target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                yield event.plain_result(
                    "日期格式错误，请使用 YYYY-MM-DD 或 YYYYMMDD，例如：/kpi cd 20260302"
                )
                return
        else:
            now = datetime.datetime.now()
            target_date = self._resolve_diary_target_date(now)
            if now.hour < 2:
                yield event.plain_result(
                    f"当前时间还在凌晨两点前，默认补写 {target_date.strftime('%Y年%m月%d日')} 的日记。"
                )

        # 检查这一天的日记是否已经存在
        diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
        diary_path = os.path.join(self.diary_storage, diary_filename)

        if os.path.exists(diary_path):
            yield event.plain_result(
                f"{target_date.strftime('%Y年%m月%d日')} 的日记已经存在，无需补写。"
            )
            return

        # 生成补写日记
        provider = self.context.get_using_provider()
        if not provider:
            yield event.plain_result("当前没有可用的模型提供商，暂时无法补写日记。")
            return

        try:
            # 获取人格设定
            umo = None
            if event and hasattr(event, "unified_msg_origin"):
                umo = event.unified_msg_origin
            system_prompt = await self._get_persona_prompt(umo)
            # 兜底默认值，避免分支调整时出现未定义变量
            weather_info = ""
            observation_text = ""
            
            completion_prompt = (
                f"请补写 {target_date.strftime('%Y年%m月%d日')} 的今日日记。\n"
                "要求：\n"
                "1. 保持和现有日记一致的自然口吻。\n"
                "2. 根据当天观察提炼重点，不要逐条堆叠流水账。\n"
                "3. 如果要给建议，优先给和当天任务直接相关的建议。\n"
                "4. 保留真实感，不要写成空泛鸡汤，也不要重复标题和日期。\n"
                "5. 字数控制在 220 到 420 字。\n"
            )


            reference_days = []
            for i in range(1, 3):  # 参考前两天的日记语气
                past_date = target_date - datetime.timedelta(days=i)
                past_diary_filename = f"diary_{past_date.strftime('%Y%m%d')}.md"
                past_diary_path = os.path.join(
                    self.diary_storage, past_diary_filename
                )
                if os.path.exists(past_diary_path):
                    try:
                        with open(past_diary_path, encoding="utf-8") as f:
                            past_diary_content = f.read()
                        reference_days.append(
                            {
                                "date": past_date.strftime("%Y-%m-%d"),
                                "content": past_diary_content,
                            }
                        )
                    except Exception as e:
                        logger.error(f"读取前几天日记失败: {e}")

            if reference_days:
                completion_prompt += "\n可参考前几天的日记语气：\n"
                for day in reference_days:
                    completion_prompt += f"\n### {day['date']}\n{str(day['content'])[:500]}\n"

            # 生成日记内容
            response = await provider.text_chat(
                prompt=completion_prompt, system_prompt=system_prompt
            )

            if (
                response
                and hasattr(response, "completion_text")
                and response.completion_text
            ):
                # 获取星期
                weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
                weekday = weekdays[target_date.weekday()]

                # 尝试获取天气信息
                try:
                    weather_info = await self._get_weather_prompt()
                except Exception as e:
                    logger.debug(f"获取天气信息失败: {e}")

                diary_content = self._build_diary_document(
                    target_date=target_date,
                    weekday=weekday,
                    weather_info=weather_info,
                    observation_text=observation_text,
                    reflection_text=self._ensure_diary_reflection_text(
                        response.completion_text,
                        observation_text,
                    ),
                )

                # 保存日记文件
                try:
                    with open(diary_path, "w", encoding="utf-8") as f:
                        f.write(diary_content)
                    logger.info(f"补写日记已保存到: {diary_path}")
                    yield event.plain_result(f"已补写并保存 {target_date.strftime('%Y年%m月%d日')} 的日记。")
                except Exception as e:
                    logger.error(f"保存补写日记失败: {e}")
                    yield event.plain_result("补写成功了，但保存日记时出了点问题。")
            else:
                yield event.plain_result("模型没有返回有效内容，这次补写没有成功。")
        except Exception as e:
            logger.error(f"补写日记失败: {e}")
            yield event.plain_result("补写日记时出了点问题，请稍后再试。")

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
        if not self.enable_diary:
            return False

        import datetime
        should_store, reason = self._should_store_diary_entry(content, active_window)
        if not should_store:
            logger.info(f"跳过写入日记条目: {reason}")
            return False

        now = datetime.datetime.now()
        entry = {
            "time": now.strftime("%H:%M:%S"),
            "content": content,
            "active_window": active_window,
        }
        self.diary_entries.append(entry)
        # 保留最多10条当天记录，避免日记生成时间在凌晨2点前时丢失数据
        if len(self.diary_entries) > 10:
            self.diary_entries = self.diary_entries[-10:]
        logger.info(f"添加日记条目: {entry}")
        return True

    async def _generate_diary(self, target_date: datetime.date | None = None):
        """生成日记。"""
        if not self.enable_diary or not self.diary_entries:
            return

        import datetime

        target_date = target_date or datetime.date.today()
        # 获取星期
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekdays[target_date.weekday()]

        # 尝试获取天气信息
        weather_info = ""
        try:
            weather_info = await self._get_weather_prompt()
        except Exception as e:
            logger.debug(f"获取天气信息失败: {e}")

        # 构建标准格式的日记内容
        compacted_entries = self._compact_diary_entries(self.diary_entries)
        if not compacted_entries:
            logger.info("今日日记没有可用的高质量观察条目，已跳过生成")
            # 生成一篇"没观察"的日记，bot 抱怨用户没给机会
            provider = self.context.get_using_provider()
            if provider:
                try:
                    system_prompt = await self._get_persona_prompt()
                    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
                    weekday = weekdays[target_date.weekday()]
                    weather_info = ""
                    try:
                        weather_info = await self._get_weather_prompt()
                    except Exception:
                        pass
                    no_observation_prompt = (
                        "今天的日记有些特别——你发现用户今天根本没给你看屏幕的机会！\n"
                        "请写一段带点委屈、带点撒娇的日记，抱怨用户今天忽略了你。\n"
                        "可以假装生气、假装吃醋，但最终要表达'明天也要来看你哦'的期待。\n"
                        "字数控制在 150-280 字，自然俏皮一点。\n"
                    )
                    response = await provider.text_chat(
                        prompt=no_observation_prompt, system_prompt=system_prompt
                    )
                    if response and hasattr(response, "completion_text") and response.completion_text:
                        reflection_text = self._ensure_diary_reflection_text(
                            response.completion_text,
                            "（今天用户没给我看屏幕的机会，呜呜）",
                        )
                        diary_content = self._build_diary_document(
                            target_date=target_date,
                            weekday=weekday,
                            weather_info=weather_info,
                            observation_text="（今天用户没给我看屏幕的机会，呜呜）",
                            reflection_text=reflection_text,
                        )
                        diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
                        diary_path = os.path.join(self.diary_storage, diary_filename)
                        with open(diary_path, "w", encoding="utf-8") as f:
                            f.write(diary_content)
                        logger.info(f"日记已保存（无观察）: {diary_path}")
                except Exception as e:
                    logger.error(f"生成无观察日记失败: {e}")
            self.diary_entries = []
            self.last_diary_date = target_date
            return
        observation_lines = []
        for entry in compacted_entries:
            time_label = entry["start_time"] if entry["start_time"] == entry["end_time"] else f"{entry['start_time']}-{entry['end_time']}"
            observation_lines.append(f"### {time_label} - {entry['active_window']}")
            if len(entry["points"]) == 1:
                observation_lines.append(entry["points"][0])
            else:
                for point in entry["points"]:
                    observation_lines.append(f"- {point}")
            observation_lines.append("")
        observation_text = "\n".join(observation_lines).strip()
        reflection_text = ""

        import datetime
        viewed_count = 0
        for i in range(1, 4):
            past_date = target_date - datetime.timedelta(days=i)
            past_date_str = past_date.strftime("%Y%m%d")
            if past_date_str in self.diary_metadata and self.diary_metadata[past_date_str].get("viewed", False):
                viewed_count += 1
        
        logger.info(f"最近三天日记查看次数: {viewed_count}")

        # 生成带风格的今日日记总结
        provider = self.context.get_using_provider()
        if provider:
            if len(compacted_entries) < 2:
                summary_prompt = (
                    "今天的观察还比较少，请写一段简短、自然、不过度脑补的今日日记。"
                    "可以更克制一点，但仍然要保留一点真实感和陪伴感。"
                    "字数控制在 180 到 320 字。"
                )
            else:
                # 根据最近查看次数调整提示词
                reference_days = []
                if self.diary_reference_days > 0:
                    for i in range(1, self.diary_reference_days + 1):
                        past_date = target_date - datetime.timedelta(days=i)
                        past_diary_filename = f"diary_{past_date.strftime('%Y%m%d')}.md"
                        past_diary_path = os.path.join(
                            self.diary_storage, past_diary_filename
                        )
                        if os.path.exists(past_diary_path):
                            try:
                                with open(past_diary_path, encoding="utf-8") as f:
                                    past_diary_content = f.read()
                                reference_days.append(
                                    {
                                        "date": past_date.strftime("%Y-%m-%d"),
                                        "content": past_diary_content,
                                    }
                                )
                            except Exception as e:
                                logger.error(f"读取前几天日记失败: {e}")

                summary_prompt = self._build_diary_reflection_prompt(
                    observation_text=observation_text,
                    viewed_count=viewed_count,
                    reference_days=reference_days,
                )

            try:
                system_prompt = await self._get_persona_prompt()
                response = await provider.text_chat(
                    prompt=summary_prompt, system_prompt=system_prompt
                )
                if (
                    response
                    and hasattr(response, "completion_text")
                    and response.completion_text
                ):
                    reflection_text = self._ensure_diary_reflection_text(
                        response.completion_text,
                        observation_text,
                    )
            except Exception as e:
                logger.error(f"生成日记总结失败: {e}")

        structured_summary = self._build_diary_structured_summary(
            compacted_entries,
            reflection_text,
        )
        reflection_text = self._ensure_diary_reflection_text(
            reflection_text,
            observation_text,
            structured_summary,
        )
        if not structured_summary.get("suggestion_items"):
            structured_summary["suggestion_items"] = self._extract_actionable_suggestions(
                reflection_text,
                limit=3,
            )

        diary_content = self._build_diary_document(
            target_date=target_date,
            weekday=weekday,
            weather_info=weather_info,
            observation_text=observation_text,
            reflection_text=reflection_text,
            structured_summary=structured_summary,
        )

        # 保存日记文件
        diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
        diary_path = os.path.join(self.diary_storage, diary_filename)

        try:
            with open(diary_path, "w", encoding="utf-8") as f:
                f.write(diary_content)
            self._save_diary_structured_summary(target_date, structured_summary)
            self._remember_diary_summary_memories(target_date, structured_summary)
            self._update_memory_priorities()
            self._save_long_term_memory()
            logger.info(f"日记已保存到: {diary_path}")

            # 重置日记条目
            self.diary_entries = []
            self.last_diary_date = target_date

            logger.info("日记生成完成，不自动发送，等待用户主动查看")
        except Exception as e:
            logger.error(f"保存日记失败: {e}")

    def _parse_user_preferences(self):
        """解析用户偏好设置。"""
        self.parsed_preferences = {}
        if not self.user_preferences:
            return

        lines = self.user_preferences.strip().split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue

            parts = line.split(" ", 1)
            if len(parts) != 2:
                continue

            scene, preference = parts
            self.parsed_preferences[scene] = preference

        logger.info("用户偏好设置解析完成")

    def _load_learning_data(self):
        """加载学习数据。"""
        try:
            learning_file = os.path.join(self.learning_storage, "learning_data.json")
            if os.path.exists(learning_file):
                with open(learning_file, encoding="utf-8") as f:
                    self.learning_data = json.load(f)
                logger.info("学习数据加载成功")
        except Exception as e:
            logger.error(f"加载学习数据失败: {e}")
            self.learning_data = {}

    async def _start_webui(self):
        """启动 Web UI 服务器"""
        self._ensure_runtime_state()
        webui_lock = getattr(self, "_webui_lock", None)
        if webui_lock is None:
            self._webui_lock = asyncio.Lock()
            webui_lock = self._webui_lock

        async with webui_lock:
            try:
                if self.web_server:
                    logger.info("检测到 Web UI 服务器已存在，正在停止旧实例...")
                    await self.web_server.stop()
                    self.web_server = None
                    # 增加延迟时间，确保端口完全释放
                    await asyncio.sleep(1.0)

                self.web_server = WebServer(self, host=self.webui_host, port=self.webui_port)
                success = await self.web_server.start()
                if not success:
                    self.web_server = None
                    logger.error(
                        f"WebUI 启动失败，原因: 无法绑定 {self.webui_host}:{self.webui_port}"
                    )
            except Exception as e:
                self.web_server = None
                logger.error(f"启动 Web UI 时出错: {e}")

    async def _stop_webui(self):
        """停止 Web UI 服务器"""
        self._ensure_runtime_state()
        webui_lock = getattr(self, "_webui_lock", None)
        if webui_lock is None:
            self._webui_lock = asyncio.Lock()
            webui_lock = self._webui_lock

        async with webui_lock:
            if self.web_server:
                try:
                    await self.web_server.stop()
                except Exception as e:
                    logger.error(f"停止 Web UI 时出错: {e}")
                finally:
                    self.web_server = None

    def _save_learning_data(self):
        """保存学习数据。"""
        if not self.enable_learning:
            return

        try:
            learning_file = os.path.join(self.learning_storage, "learning_data.json")
            with open(learning_file, "w", encoding="utf-8") as f:
                json.dump(self.learning_data, f, ensure_ascii=False, indent=2)
            logger.info("学习数据保存成功")
        except Exception as e:
            logger.error(f"保存学习数据失败: {e}")

    def _load_corrections(self):
        """加载用户纠正数据。"""
        try:
            import json
            import os
            corrections_file = getattr(self, "corrections_file", "")
            if not corrections_file:
                corrections_file = os.path.join(self.learning_storage, "corrections.json")
                self.corrections_file = corrections_file
            if os.path.exists(corrections_file):
                with open(corrections_file, "r", encoding="utf-8") as f:
                    self.corrections = json.load(f)
                logger.info("纠正数据加载成功")
        except Exception as e:
            logger.error(f"加载纠正数据失败: {e}")
            self.corrections = {}

    def _save_corrections(self):
        """保存用户纠正数据。"""
        try:
            import json
            import os
            corrections_file = getattr(self, "corrections_file", "")
            if not corrections_file:
                corrections_file = os.path.join(self.learning_storage, "corrections.json")
                self.corrections_file = corrections_file
            with open(corrections_file, "w", encoding="utf-8") as f:
                json.dump(self.corrections, f, ensure_ascii=False, indent=2)
            logger.info("纠正数据保存成功")
        except Exception as e:
            logger.error(f"保存纠正数据失败: {e}")

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
        response_text = str(response_text or "").strip()
        recent_contexts = list(contexts or [])
        has_recent_context = bool(recent_contexts)

        # 常见的播报式开场，需要清理
        opening_phrases = [
            "我看到你在",
            "你现在正在",
            "你在",
            "我观察到你在",
            "我注意到你在",
            "看到你在",
            "观察到你在",
            "注意到你在"
        ]
        
        # 针对视频和阅读场景的特殊处理
        if scene in ["视频", "阅读"]:
            # 对于这些场景，更需要减少播报感
            for phrase in opening_phrases:
                if response_text.startswith(phrase):
                    # 移除开场短语
                    response_text = response_text[len(phrase):].strip()
                    # 如果以"在"开头，也移除
                    if response_text.startswith("在"):
                        response_text = response_text[1:].strip()
                    break
        else:
            # 对于其他场景，适度清理
            for phrase in opening_phrases:
                if response_text.startswith(phrase):
                    # 移除开场短语
                    response_text = response_text[len(phrase):].strip()
                    break

        response_text = self._strip_repeated_companion_opening(
            response_text,
            has_recent_context=has_recent_context,
        )

        if (
            not allow_rest_hint
            and self._contains_rest_cue(response_text)
            and self._has_recent_rest_cue(recent_contexts, task_id=task_id)
        ):
            response_text = self._strip_rest_cue_sentences(response_text)

        return response_text.strip()

    def _learn_from_correction(self, original_response, corrected_response):
        """从用户纠正中学习。"""
        # 记录纠正信息
        import uuid
        import datetime
        correction_id = str(uuid.uuid4())
        self.corrections[correction_id] = {
            "original": original_response,
            "corrected": corrected_response,
            "timestamp": datetime.datetime.now().isoformat()
        }
        
        # 分析纠正内容，提取关键信息
        self._analyze_correction_content(original_response, corrected_response)
        
        # 保存纠正数据
        self._save_corrections()
        logger.info("已记录一条用户纠正数据")
    
    def _analyze_correction_content(self, original, corrected):
        """分析纠正内容，提取关键信息并更新长期记忆。"""
        import re
        
        # 转换为小写进行分析
        original_lower = original.lower()
        corrected_lower = corrected.lower()
        
        # 提取关于自身形象的纠正
        if "形象" in corrected_lower or "logo" in corrected_lower or "输入法" in corrected_lower:
            self._update_self_image_memory(corrected)
        
        # 提取关于场景的纠正
        scene_patterns = ["场景", "是在", "正在", "在做"]
        if any(pattern in corrected_lower for pattern in scene_patterns):
            self._update_scene_memory(corrected)
        
        # 提取关于应用的纠正
        app_patterns = ["应用", "程序", "软件", "工具"]
        if any(pattern in corrected_lower for pattern in app_patterns):
            self._update_application_memory(corrected)
    
    def _update_self_image_memory(self, correction):
        """更新关于自身形象的记忆。"""
        if "self_image" not in self.long_term_memory:
            self.long_term_memory["self_image"] = []
        
        # 检查是否已经存在类似的记忆
        correction_lower = correction.lower()
        for existing in self.long_term_memory["self_image"]:
            if correction_lower in existing["content"].lower() or existing["content"].lower() in correction_lower:
                # 更新现有记忆
                existing["timestamp"] = datetime.datetime.now().isoformat()
                existing["count"] = existing.get("count", 0) + 1
                break
        else:
            # 添加新记忆
            self.long_term_memory["self_image"].append({
                "content": correction,
                "timestamp": datetime.datetime.now().isoformat(),
                "count": 1
            })
        
        # 保存长期记忆
        self._save_long_term_memory()
        logger.info("已更新自身形象记忆")
    
    def _update_scene_memory(self, correction):
        """更新关于场景的记忆。"""
        # 简单实现，后续可以扩展更复杂的场景提取逻辑
        if "scenes" not in self.long_term_memory:
            self.long_term_memory["scenes"] = {}
        
        # 提取可能的场景名称
        scene_keywords = ["编程", "设计", "办公", "游戏", "视频", "阅读", "音乐", "社交", "浏览"]
        for keyword in scene_keywords:
            if keyword in correction:
                if keyword not in self.long_term_memory["scenes"]:
                    self.long_term_memory["scenes"][keyword] = {
                        "count": 0,
                        "last_used": datetime.datetime.now().isoformat()
                    }
                self.long_term_memory["scenes"][keyword]["count"] += 1
                self.long_term_memory["scenes"][keyword]["last_used"] = datetime.datetime.now().isoformat()
                break
        
        # 保存长期记忆
        self._save_long_term_memory()
    
    def _update_application_memory(self, correction):
        """更新关于应用的记忆。"""
        if "applications" not in self.long_term_memory:
            self.long_term_memory["applications"] = {}
        
        # 简单实现，后续可以扩展更复杂的应用提取逻辑
        # 这里只是一个示例，实际应用需要更复杂的解析
        app_name = correction.split(" ")[0]
        if app_name:
            if app_name not in self.long_term_memory["applications"]:
                self.long_term_memory["applications"][app_name] = {
                    "usage_count": 0,
                    "last_used": datetime.datetime.now().isoformat(),
                    "scenes": {}
                }
            self.long_term_memory["applications"][app_name]["usage_count"] += 1
            self.long_term_memory["applications"][app_name]["last_used"] = datetime.datetime.now().isoformat()
        
        # 保存长期记忆
        self._save_long_term_memory()

    def _update_learning_data(self, scene, feedback):
        """更新学习数据。"""
        if not self.enable_learning:
            return

        if scene not in self.learning_data:
            self.learning_data[scene] = {"feedback": []}

        self.learning_data[scene]["feedback"].append(
            {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "feedback": feedback}
        )

        # 保存学习数据
        self._save_learning_data()

    def _get_scene_preference(self, scene):
        """获取某个场景对应的默认互动偏好。"""
        if scene in self.parsed_preferences:
            return self.parsed_preferences[scene]

        # 优先使用学习到的偏好
        if self.enable_learning and scene in self.learning_data:
            # 简单的偏好学习逻辑
            feedbacks = self.learning_data[scene].get("feedback", [])
            if feedbacks:
                # 这里可以扩展更复杂的学习逻辑
                return feedbacks[-1]["feedback"]

        # 默认偏好
        default_preferences = {
            "编程": "更喜欢收到和实现思路、排查方向、结构优化相关的建议。",
            "设计": "更喜欢收到和布局、视觉层次、信息表达相关的建议。",
            "浏览": "更喜欢收到提炼重点和判断信息价值的建议。",
            "办公": "更喜欢收到和下一步动作、沟通表达、任务推进相关的建议。",
            "游戏": "更喜欢收到和局势判断、资源分配、装备路线相关的建议。",
            "视频": "更喜欢收到贴合内容的轻量回应，而不是打断式播报。",
            "阅读": "更喜欢收到理解思路、要点提炼和解题方向上的帮助。",
            "音乐": "更喜欢收到围绕氛围、感受和联想的轻量回应。",
            "社交": "更喜欢收到对聊天语气、表达方式和分寸感的建议。",
            "学习": "更喜欢收到能立刻执行的学习方法和拆解思路。",
            "通用": "更喜欢收到具体、自然、低打扰、真正有用的回应。",
        }

        return default_preferences.get(scene, "")

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

    def _get_microphone_volume(self):
        """读取当前麦克风音量。"""
        try:
            import numpy as np
            import pyaudio

            # 初始化 PyAudio
            p = pyaudio.PyAudio()

            # 打开麦克风输入流
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=44100,
                input=True,
                frames_per_buffer=1024,
            )

            # 读取音频数据
            data = stream.read(1024)

            # 关闭音频流
            stream.stop_stream()
            stream.close()
            p.terminate()

            # 计算音量
            audio_data = np.frombuffer(data, dtype=np.int16)
            
            # 检查音频数据是否为空
            if len(audio_data) == 0:
                logger.debug("闊抽鏁版嵁涓虹┖")
                return 0
                
            # 计算均方根，并处理可能的空数据
            try:
                square_data = np.square(audio_data)
                mean_square = np.mean(square_data)
                
                # 检查 mean_square 是否为 NaN
                if np.isnan(mean_square):
                    logger.debug("鍧囧间负NaN")
                    return 0
                    
                rms = np.sqrt(mean_square)
                
                # 检查 rms 是否为 NaN
                if np.isnan(rms):
                    logger.debug("RMS涓篘aN")
                    return 0

                # 将音量映射到 0-100 范围
                volume = min(100, int(rms / 32768 * 100 * 5))
                return volume
            except Exception as e:
                logger.error(f"计算音量时出错: {e}")
                return 0
        except ImportError:
            logger.debug("Debug event")
            return 0
        except Exception as e:
            logger.error(f"获取麦克风音量失败: {e}")
            return 0

    async def _mic_monitor_task(self):
        """后台麦克风监听任务。"""
        self._ensure_runtime_state()
        # 检查麦克风依赖
        mic_deps_ok = False
        try:
            import sys

            logger.info(f"[麦克风依赖检查] Python 路径: {sys.path}")
            logger.info(f"[麦克风依赖检查] Python 可执行文件: {sys.executable}")

            import pyaudio

            logger.info(f"[麦克风依赖检查] PyAudio 已加载: {pyaudio.__version__}")

            import numpy

            logger.info(f"[麦克风依赖检查] NumPy 已加载: {numpy.__version__}")

            mic_deps_ok = True
        except ImportError as e:
            logger.warning(f"[麦克风依赖检查] 未安装麦克风监听所需依赖: {e}")
            logger.warning("请执行 pip install pyaudio numpy 以启用麦克风监听功能")
            import traceback

            logger.warning(f"[麦克风依赖检查] 详细错误: {traceback.format_exc()}")

        while self.enable_mic_monitor and self._is_current_process_instance():
            try:
                if not mic_deps_ok:
                    await asyncio.sleep(60)
                    continue

                # 获取当前时间
                current_time = time.time()

                if current_time - self.last_mic_trigger < self.mic_debounce_time:
                    await asyncio.sleep(self.mic_check_interval)
                    continue

                # 获取麦克风音量
                volume = self._get_microphone_volume()
                logger.debug(f"麦克风音量: {volume}")

                if volume > self.mic_threshold:
                    logger.info(f"麦克风音量超过阈值: {volume} > {self.mic_threshold}")

                    # 检查环境
                    ok, err_msg = self._check_env(check_mic=True)
                    if not ok:
                        logger.error(f"麦克风触发失败: {err_msg}")
                        await asyncio.sleep(self.mic_check_interval)
                        continue

                    # 创建临时任务
                    try:
                        # 保存当前状态
                        current_state = self.state
                        if current_state == "inactive":
                            self.state = "temporary"
                        
                        # 创建临时任务 ID
                        temp_task_id = f"temp_mic_{int(time.time())}"
                        
                        # 定义临时任务函数
                        async def temp_mic_task():
                            background_job_started = False
                            try:
                                background_job_started, skip_reason = self._try_begin_background_screen_job()
                                if not background_job_started:
                                    logger.info(f"[{temp_task_id}] 跳过麦克风触发识屏: {skip_reason}")
                                    return
                                target = self._resolve_proactive_target()
                                event = self._create_virtual_event(target)

                                capture_timeout = self._get_capture_context_timeout(
                                    "video" if self._use_screen_recording_mode() else "image"
                                )
                                capture_context = await asyncio.wait_for(
                                    self._capture_proactive_recognition_context(),
                                    timeout=capture_timeout,
                                )
                                active_window_title = capture_context.get("active_window_title", "")
                                components = await asyncio.wait_for(
                                    self._analyze_screen(
                                        capture_context,
                                        session=event,
                                        active_window_title=active_window_title,
                                        custom_prompt="我听到你刚才声音有点大，像是发生了什么，帮你看看现在的情况。",
                                        task_id=temp_task_id,
                                    ),
                                    timeout=self._get_screen_analysis_timeout(
                                        capture_context.get("media_kind", "image")
                                    ),
                                )

                                # 确定消息发送目标
                                target = self._resolve_proactive_target()

                                if target and await self._send_component_text(
                                    target,
                                    components,
                                    prefix="【声音提醒】",
                                ):
                                        logger.info("麦克风提醒消息发送成功")
                                        if capture_context.get("_rest_reminder_planned"):
                                            self._mark_rest_reminder_sent(
                                                capture_context.get("_rest_reminder_info", {}) or {}
                                            )

                                # 更新上次触发时间
                                self.last_mic_trigger = current_time
                            finally:
                                # 任务完成后清理临时任务
                                if temp_task_id in self.temporary_tasks:
                                    del self.temporary_tasks[temp_task_id]
                                if background_job_started:
                                    self._finish_background_screen_job()
                                if not self.auto_tasks and not self.temporary_tasks:
                                    self.state = current_state

                        self.temporary_tasks[temp_task_id] = asyncio.create_task(temp_mic_task())
                        logger.info(f"已创建麦克风临时任务: {temp_task_id}")
                    except Exception as e:
                        logger.error(f"创建麦克风临时任务时出错: {e}")
                        if not self.auto_tasks and not self.temporary_tasks:
                            self.state = current_state

                await asyncio.sleep(self.mic_check_interval)
            except Exception as e:
                logger.error(f"麦克风监听任务异常: {e}")
                await asyncio.sleep(self.mic_check_interval)

    def _legacy_duplicate_init_unused(self, context: Context, config: dict):
        import os

        super().__init__(context)
        
        self.plugin_config = PluginConfig(config, context)
        
        self._sync_all_config()
        
        self.auto_tasks = {}
        self.is_running = False
        self.task_counter = 0
        self.running = True
        self.background_tasks = []
        self.state = "inactive"  # active, inactive, temporary
        self.temporary_tasks = {}
        # 固定自动观察任务 ID
        self.AUTO_TASK_ID = "task_0"

        # 日记功能相关
        self.diary_entries = []
        self.last_diary_date = None

        if not self.diary_storage:
            self.diary_storage = str(self.plugin_config.diary_dir)
        os.makedirs(self.diary_storage, exist_ok=True)

        self.parsed_custom_tasks = []
        self._parse_custom_tasks()
        self.last_task_execution = {}
        self.parsed_window_companion_targets = []
        self.window_companion_active_title = ""
        self.window_companion_active_target = ""
        self.window_companion_active_rule = {}
        self._parse_window_companion_targets()

        self.last_mic_trigger = 0  # 上次麦克风触发时间
        self.mic_debounce_time = 60  # 麦克风防抖时间，单位为秒

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
        self._ensure_webui_password()

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

        self.active_tasks = {}  # 记录用户正在进行的任务
        # 学习反馈系统
        self.corrections = {}
        self.corrections_file = os.path.join(self.learning_storage, "corrections.json")
        self._load_corrections()
        
        # 窗口变化检测相关
        self.previous_windows = set()
        self.window_change_cooldown = 0
        self.window_timestamps = {}  # 记录窗口首次出现的时间戳
        
        # 时间跟踪相关
        self.current_activity = None  # 当前活动
        self.activity_start_time = None  # 活动开始时间
        self.activity_history = []  # 活动历史记录
        self.activity_history_file = os.path.join(self.learning_storage, "activity_history.json")
        self._load_activity_history()

        self.uncertainty_words = ["也许", "可能", "看起来", "我猜", "像是", "大概", "说不定", "似乎"]

        # 解析用户偏好配置
        self._parse_user_preferences()

        # 加载学习数据
        if self.enable_learning:
            self._load_learning_data()

        self.task_semaphore = asyncio.Semaphore(2)  # 限制同时运行的任务数
        self.task_queue = asyncio.Queue()

        task = asyncio.create_task(self._task_scheduler())
        self.background_tasks.append(task)

        # 启动日记任务
        if self.enable_diary:
            task = asyncio.create_task(self._diary_task())
            self.background_tasks.append(task)

        # 启动 WebUI（如果启用）
        if self.webui_enabled:
            task = asyncio.create_task(self._start_webui())
            self.background_tasks.append(task)

        task = asyncio.create_task(self._custom_tasks_task())
        self.background_tasks.append(task)

        task = asyncio.create_task(self._mic_monitor_task())
        self.background_tasks.append(task)
        task = asyncio.create_task(self._window_companion_task())
        self.background_tasks.append(task)

    async def _custom_tasks_task(self):
        """后台自定义任务调度循环。"""
        self._ensure_runtime_state()
        while self.running and self._is_current_process_instance():
            try:
                now = datetime.datetime.now()
                current_date = now.date()
                current_hour = now.hour
                current_minute = now.minute

                for task in self.parsed_custom_tasks:
                    # 生成任务唯一标识
                    task_key = f"{task['hour']}:{task['minute']}:{task['prompt']}"
                    # 检查今天是否已经执行过
                    if self.last_task_execution.get(task_key) == current_date:
                        continue
                    if task["hour"] != current_hour or task["minute"] != current_minute:
                        continue
                    if not self._try_mark_custom_task_dispatch(task_key):
                        logger.info(f"跳过重复的自定义监控任务派发: {task['prompt']}")
                        self.last_task_execution[task_key] = current_date
                        continue
                    
                    if (
                        task["hour"] == current_hour
                        and task["minute"] == current_minute
                    ):
                        logger.info(f"执行自定义监控任务: {task['prompt']}")
                        self.last_task_execution[task_key] = current_date
                        # 检查环境
                        ok, err_msg = self._check_env()
                        if not ok:
                            logger.error(f"自定义任务执行失败: {err_msg}")
                            continue

                        # 创建临时任务
                        try:
                            # 保存当前状态
                            current_state = self.state
                            if current_state == "inactive":
                                self.state = "temporary"
                            
                            # 创建临时任务 ID
                            temp_task_id = f"temp_custom_{int(time.time())}"
                            
                            # 定义临时任务函数
                            async def temp_custom_task():
                                background_job_started = False
                                try:
                                    background_job_started, skip_reason = self._try_begin_background_screen_job()
                                    if not background_job_started:
                                        logger.info(f"[{temp_task_id}] 跳过自定义监控识屏: {skip_reason}")
                                        return
                                    capture_timeout = self._get_capture_context_timeout(
                                        "video" if self._use_screen_recording_mode() else "image"
                                    )
                                    capture_context = await asyncio.wait_for(
                                        self._capture_proactive_recognition_context(),
                                        timeout=capture_timeout,
                                    )
                                    capture_context["trigger_reason"] = f"定时提醒：{task['prompt']}"
                                    active_window_title = capture_context.get("active_window_title", "")
                                    components = await asyncio.wait_for(
                                        self._analyze_screen(
                                            capture_context,
                                            active_window_title=active_window_title,
                                            custom_prompt=task["prompt"],
                                            task_id=temp_task_id,
                                        ),
                                        timeout=self._get_screen_analysis_timeout(
                                            capture_context.get("media_kind", "image")
                                        ),
                                    )

                                    # 确定消息发送目标
                                    target = self._resolve_proactive_target()
                                    analysis_trace = capture_context.get("_analysis_trace", {}) or {}

                                    if target and await self._send_component_text(
                                        target,
                                        components,
                                        prefix="【定时提醒】",
                                    ):
                                            analysis_trace["status"] = "sent"
                                            analysis_trace["reply_preview"] = self._truncate_preview_text(
                                                self._extract_plain_text(components),
                                                limit=140,
                                            )
                                            self._remember_screen_analysis_trace(analysis_trace)
                                            logger.info("自定义任务提醒消息发送成功")
                                            if capture_context.get("_rest_reminder_planned"):
                                                self._mark_rest_reminder_sent(
                                                    capture_context.get("_rest_reminder_info", {}) or {}
                                                )
                                finally:
                                    # 任务完成后清理临时任务
                                    if temp_task_id in self.temporary_tasks:
                                        del self.temporary_tasks[temp_task_id]
                                    if background_job_started:
                                        self._finish_background_screen_job()
                                    if not self.auto_tasks and not self.temporary_tasks:
                                        self.state = current_state

                            self.temporary_tasks[temp_task_id] = asyncio.create_task(temp_custom_task())
                            logger.info(f"已创建自定义临时任务: {temp_task_id}")
                        except Exception as e:
                            logger.error(f"创建自定义临时任务时出错: {e}")
                            if not self.auto_tasks and not self.temporary_tasks:
                                self.state = current_state

                # 等待 1 分钟，期间持续检查 running 标志
                for _ in range(60):
                    if not self.running:
                        break
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"自定义任务异常: {e}")
                # 等待 1 分钟，期间持续检查 running 标志
                for _ in range(60):
                    if not self.running:
                        break
                    await asyncio.sleep(1)

    async def _diary_task(self):
        """日记定时任务。"""
        while self.running and self._is_current_process_instance():
            try:
                now = datetime.datetime.now()
                today = now.date()
                target_date = self._resolve_diary_target_date(now)

                if self.enable_diary and self.last_diary_date != target_date:
                    # 解析日记时间
                    try:
                        hour, minute = map(
                            int,
                            self._normalize_clock_text(self.diary_time, "00:00").split(":"),
                        )
                        if now.hour == hour and now.minute == minute:
                            await self._generate_diary(target_date=target_date)
                    except Exception as e:
                        logger.error(f"解析日记时间失败: {e}")

                # 等待 1 分钟，期间持续检查 running 标志
                for _ in range(60):
                    if not self.running:
                        break
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"日记任务异常: {e}")
                # 等待 1 分钟，期间持续检查 running 标志
                for _ in range(60):
                    if not self.running:
                        break
                    await asyncio.sleep(1)

    async def _auto_screen_task(
        self,
        event: AstrMessageEvent,
        task_id: str = "default",
        custom_prompt: str = "",
        interval: int = None,
    ):
        """后台自动截图分析任务。

        参数:
        task_id: 任务 ID
        custom_prompt: 自定义提示词
        interval: 自定义检查间隔（秒）
        """
        self._ensure_runtime_state()
        logger.info(f"[任务 {task_id}] 启动自动识屏任务")
        
        try:
            while self.is_running and self.state == "active" and self._is_current_process_instance():
                if not self._is_in_active_time_range():
                    logger.info(f"[任务 {task_id}] 当前不在活跃时间段，准备停止任务")
                    # 清理任务
                    if task_id in self.auto_tasks:
                        del self.auto_tasks[task_id]
                    # 检查是否还有其他任务在运行
                    if not self.auto_tasks:
                        self.is_running = False
                        self.state = "inactive"
                    break

                # 获取当前预设参数
                current_check_interval, current_trigger_probability = self._get_current_preset_params()
                
                # 使用预设参数
                check_interval = current_check_interval
                probability = current_trigger_probability

                # 优先使用任务级别的自定义间隔
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
                    if not self.is_running or self.state != "active":
                        logger.info(f"[任务 {task_id}] 任务状态已变化，停止等待")
                        break
                    try:
                        # 检测窗口变化
                        if elapsed % 3 == 0:  # 每3秒检测一次窗口变化
                            latest_window_changed, new_windows = self._detect_window_changes()
                            if latest_window_changed:
                                window_changed = True
                                latest_new_windows = list(new_windows or [])
                            if latest_window_changed and new_windows:
                                logger.info(f"[任务 {task_id}] 检测到新打开的窗口: {new_windows}")
                                # 可以在这里添加对新窗口的处理逻辑
                                # 例如：发送通知、自动开始陪伴等
                        
                        if elapsed > 0 and elapsed % 10 == 0 and interval is None:
                            new_check_interval, new_probability = self._get_current_preset_params()
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

                if not self.is_running or self.state != "active":
                    logger.info(f"[任务 {task_id}] 任务状态已变化，结束本轮")
                    break

                # 再次确认是否仍处于活跃时间段
                if not self._is_in_active_time_range():
                    logger.info(f"[任务 {task_id}] 已离开活跃时间段，停止任务")
                    # 清理任务
                    if task_id in self.auto_tasks:
                        del self.auto_tasks[task_id]
                    # 检查是否还有其他任务在运行
                    if not self.auto_tasks:
                        self.is_running = False
                        self.state = "inactive"
                    break

                if not self.is_running or self.state != "active":
                    logger.info(f"[任务 {task_id}] 任务状态已变化，结束本轮")
                    break

                # 检测系统负载
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

                # 高负载时强制触发一次识屏
                change_snapshot = self._build_auto_screen_change_snapshot(
                    task_id,
                    window_changed=window_changed,
                    new_windows=latest_new_windows,
                )
                logger.info(
                    f"[任务 {task_id}] 变化感知: changed={change_snapshot['changed']}, "
                    f"window={change_snapshot['active_window_title'] or '未知'}, "
                    f"reason={change_snapshot['reason'] or '无明显变化'}"
                )
                decision = self._decide_auto_screen_trigger(
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

                # 检查是否已经停止
                if not self.is_running or self.state != "active":
                    logger.info(f"[任务 {task_id}] 任务状态已变化，结束本轮")
                    break

                if not self.is_running or self.state != "active":
                    logger.info(f"[任务 {task_id}] 任务状态已变化，结束本轮")
                    break

                if trigger:
                    logger.info(f"[任务 {task_id}] 满足触发条件，准备执行识屏分析")
                    try:
                        should_defer, defer_reason = self._should_defer_for_recent_user_activity(
                            event,
                            task_id=task_id,
                            change_snapshot=change_snapshot,
                        )
                        if should_defer:
                            logger.info(f"[任务 {task_id}] 主动识屏暂缓: {defer_reason}")
                            continue

                        if not self.is_running or self.state != "active":
                            logger.info(
                                f"[任务 {task_id}] 任务停止标志已设置，取消本次屏幕分析"
                            )
                            break

                        if not self._is_in_active_time_range():
                            logger.info(
                                f"[Task {task_id}] outside active time range, stopping task"
                            )
                            # 清理任务
                            if task_id in self.auto_tasks:
                                del self.auto_tasks[task_id]
                            # 检查是否还有其他任务在运行
                            if not self.auto_tasks:
                                self.is_running = False
                            break

                        # 妫鏌ユ槸鍚﹁鍋滄
                        if not self.is_running or self.state != "active":
                            logger.info(
                                f"[Task {task_id}] stop flag detected, cancelling screen analysis"
                            )
                            break

                        capture_timeout = self._get_capture_context_timeout(
                            "video" if self._use_screen_recording_mode() else "image"
                        )
                        capture_context = await asyncio.wait_for(
                            self._capture_proactive_recognition_context(),
                            timeout=capture_timeout,
                        )
                        capture_context["trigger_reason"] = decision["reason"]
                        active_window_title = capture_context.get("active_window_title", "")

                        # 检查是否运行中
                        if not self.is_running or self.state != "active":
                            logger.info(
                                f"[任务 {task_id}] 任务运行状态被取消，取消屏幕分析"
                            )
                            break

                        components = await asyncio.wait_for(
                            self._analyze_screen(
                                capture_context,
                                session=event,
                                active_window_title=active_window_title,
                                custom_prompt=custom_prompt,
                                task_id=task_id,
                            ),
                            timeout=self._get_screen_analysis_timeout(
                                capture_context.get("media_kind", "image")
                            ),
                        )

                        # 检查任务是否已停止
                        if not self.is_running or self.state != "active":
                            logger.info(
                                f"[Task {task_id}] stop flag detected, canceling proactive send"
                            )
                            break

                        chain = self._build_message_chain(components)
                        target = self._resolve_proactive_target(event)
                        text_content = self._extract_plain_text(components)
                        analysis_trace = capture_context.get("_analysis_trace", {}) or {}
                        current_scene = str(
                            analysis_trace.get("scene")
                            or change_snapshot.get("scene")
                            or ""
                        ).strip()
                        skip_similar, skip_reason = self._should_skip_similar_auto_reply(
                            task_id,
                            active_window_title=active_window_title,
                            text_content=text_content,
                            check_interval=check_interval,
                        )

                        if skip_similar:
                            logger.info(f"[任务 {task_id}] 主动回复已跳过: {skip_reason}")
                            self._remember_auto_reply_state(
                                task_id,
                                active_window_title=active_window_title,
                                text_content=text_content,
                                sent=False,
                                scene=current_scene,
                                note=skip_reason,
                            )
                            analysis_trace["status"] = "skipped_similar"
                            analysis_trace["reply_preview"] = self._truncate_preview_text(
                                text_content,
                                limit=140,
                            )
                            self._remember_screen_analysis_trace(analysis_trace)
                            continue

                        skip_window_limit, window_limit_reason = self._should_skip_same_window_followup(
                            task_id,
                            active_window_title=active_window_title,
                            scene=current_scene,
                        )
                        if skip_window_limit:
                            logger.info(f"[任务 {task_id}] 主动回复已降频: {window_limit_reason}")
                            self._remember_auto_reply_state(
                                task_id,
                                active_window_title=active_window_title,
                                text_content=text_content,
                                sent=False,
                                scene=current_scene,
                                note=window_limit_reason,
                            )
                            analysis_trace["status"] = "skipped_window_cooldown"
                            analysis_trace["reply_preview"] = self._truncate_preview_text(
                                text_content,
                                limit=140,
                            )
                            self._remember_screen_analysis_trace(analysis_trace)
                            continue

                        # 添加日记条目
                        diary_stored = self._add_diary_entry(text_content, active_window_title)
                        analysis_trace["stored_in_diary"] = bool(diary_stored)

                        # 自动分段发送，参考 splitter 插件的思路
                        if text_content:
                            logger.info(
                                f"准备发送主动消息，目标: {target}, 文本内容: {text_content}"
                            )
                            sent = await self._send_segmented_text(
                                target,
                                text_content,
                                should_continue=lambda: self.is_running,
                            )
                            self._remember_auto_reply_state(
                                task_id,
                                active_window_title=active_window_title,
                                text_content=text_content,
                                sent=sent,
                                scene=current_scene,
                            )
                            if sent and capture_context.get("_rest_reminder_planned"):
                                self._mark_rest_reminder_sent(
                                    capture_context.get("_rest_reminder_info", {}) or {}
                                )
                        else:
                            sent = False
                            if self.is_running:
                                sent = await self._send_proactive_message(
                                    target, chain
                                )
                            self._remember_auto_reply_state(
                                task_id,
                                active_window_title=active_window_title,
                                text_content="[非纯文本回复]",
                                sent=sent,
                                scene=current_scene,
                            )
                            if sent and capture_context.get("_rest_reminder_planned"):
                                self._mark_rest_reminder_sent(
                                    capture_context.get("_rest_reminder_info", {}) or {}
                                )
                        analysis_trace["reply_preview"] = self._truncate_preview_text(
                            text_content or "[非纯文本回复]",
                            limit=140,
                        )
                        analysis_trace["status"] = "sent" if sent else "not_sent"
                        self._remember_screen_analysis_trace(analysis_trace)

                        # 尝试将消息加入到对话历史
                        try:
                            from astrbot.core.agent.message import (
                                AssistantMessageSegment,
                                TextPart,
                                UserMessageSegment,
                            )

                            if hasattr(self.context, "conversation_manager"):
                                conv_mgr = self.context.conversation_manager
                                uid = event.unified_msg_origin
                                curr_cid = await conv_mgr.get_curr_conversation_id(uid)

                                if curr_cid:
                                    # Create user and assistant message segments
                                    user_msg = UserMessageSegment(
                                        content=[TextPart(text="[主动识屏触发]")]
                                    )
                                    assistant_msg = AssistantMessageSegment(
                                        content=[TextPart(text=text_content)]
                                    )

                                    # 添加消息对到对话历史
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
                        import traceback

                        logger.error(traceback.format_exc())
        except asyncio.CancelledError:
            logger.info(f"任务 {task_id} 已被取消")
        except Exception as e:
            logger.error(f"任务 {task_id} 异常: {e}")
        finally:
            if task_id in self.auto_tasks:
                del self.auto_tasks[task_id]
                logger.info(f"[任务 {task_id}] 已从自动任务列表移除")
            # 检查是否还有其他任务在运行
            if not self.auto_tasks:
                self.is_running = False
                logger.info("所有自动观察任务已结束")
            logger.info(f"任务 {task_id} 已结束")

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
