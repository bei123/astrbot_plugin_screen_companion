"""将 ``PluginConfig`` 同步到插件宿主上的可写运行时字段。"""

from __future__ import annotations

from typing import Any

from .learning_preferences import parse_user_preferences
from .webui_lifecycle import normalize_webui_port


def sync_all_config(host: Any) -> None:
    """将配置对象同步到插件运行时字段。"""
    pc = host.plugin_config

    host.bot_name = pc.bot_name
    host.enabled = host._coerce_bool(pc.enabled)
    host.interaction_mode = pc.interaction_mode
    host.check_interval = pc.check_interval
    host.trigger_probability = pc.trigger_probability
    host.active_time_range = pc.active_time_range
    host.use_companion_mode = host._coerce_bool(pc.use_companion_mode)
    host.companion_prompt = getattr(
        pc,
        "companion_prompt",
        "你是用户的专属屏幕伙伴，专注于提供持续、自然的陪伴。请保持对话的连续性，关注用户的任务进展，提供具体、实用的建议。",
    )
    host.recent_chat_context_messages = max(
        1,
        min(
            50,
            int(getattr(pc, "recent_chat_context_messages", 15) or 15),
        ),
    )
    host.companion_outbound_memory_max = max(
        1,
        min(
            30,
            int(getattr(pc, "companion_outbound_memory_max", 8) or 8),
        ),
    )
    host.companion_outbound_snippet_chars = max(
        80,
        min(
            2000,
            int(getattr(pc, "companion_outbound_snippet_chars", 220) or 220),
        ),
    )
    host.capture_active_window = host._coerce_bool(pc.capture_active_window)
    host.capture_source = str(getattr(pc, "capture_source", "local") or "local").strip().lower()
    if host.capture_source not in ("local", "remote"):
        host.capture_source = "local"
    host.screen_relay_port = max(
        1, min(65535, int(getattr(pc, "screen_relay_port", 8765)))
    )
    host.screen_relay_bind = str(
        getattr(pc, "screen_relay_bind", "0.0.0.0") or "0.0.0.0"
    ).strip() or "0.0.0.0"
    host.bot_vision_quality = pc.bot_vision_quality
    host.screen_recognition_mode = host._normalize_screen_recognition_mode(
        getattr(
            pc,
            "screen_recognition_mode",
            host.SCREENSHOT_MODE,
        )
    )
    host.image_prompt = pc.image_prompt
    host.ffmpeg_path = getattr(pc, "ffmpeg_path", "")
    host.recording_fps = max(
        0.01,
        float(
            getattr(pc, "recording_fps", host.RECORDING_FPS) or host.RECORDING_FPS
        ),
    )
    host.recording_duration_seconds = max(
        1,
        int(
            getattr(
                pc,
                "recording_duration_seconds",
                host.RECORDING_DURATION_SECONDS,
            )
            or host.RECORDING_DURATION_SECONDS
        ),
    )
    host.use_external_vision = host._coerce_bool(
        getattr(pc, "use_external_vision", False)
    )
    _raw_vs = str(getattr(pc, "vision_source", "") or "").strip()
    if _raw_vs in ("仅外接", "仅框架", "外接+框架回退"):
        host.vision_source = _raw_vs
    else:
        host.vision_source = "仅外接" if host.use_external_vision else "仅框架"
    host.allow_unsafe_video_direct_fallback = host._coerce_bool(
        getattr(pc, "allow_unsafe_video_direct_fallback", False)
    )
    host.vision_api_url = pc.vision_api_url
    host.vision_api_key = pc.vision_api_key
    host.vision_api_model = pc.vision_api_model
    host.vision_api_url_backup = getattr(pc, "vision_api_url_backup", None)
    host.vision_api_key_backup = getattr(pc, "vision_api_key_backup", None)
    host.vision_api_model_backup = getattr(pc, "vision_api_model_backup", None)
    host.user_preferences = pc.user_preferences
    host.use_llm_for_start_end = host._coerce_bool(pc.use_llm_for_start_end)
    host.start_preset = pc.start_preset
    host.end_preset = pc.end_preset
    host.start_llm_prompt = pc.start_llm_prompt
    host.end_llm_prompt = pc.end_llm_prompt
    host.enable_diary = host._coerce_bool(pc.enable_diary)
    raw_diary_time = getattr(pc, "diary_time", "00:00")
    normalized_diary_time = host._normalize_clock_text(
        raw_diary_time,
        default="00:00",
    )
    host.diary_time = normalized_diary_time
    if normalized_diary_time != raw_diary_time:
        pc.diary_time = normalized_diary_time
    host.diary_storage = pc.diary_storage
    host.diary_reference_days = pc.diary_reference_days
    host.diary_auto_recall = host._coerce_bool(pc.diary_auto_recall)
    host.diary_recall_time = pc.diary_recall_time
    host.diary_send_as_image = host._coerce_bool(pc.diary_send_as_image)
    host.diary_generation_prompt = pc.diary_generation_prompt
    host.diary_response_prompt = pc.diary_response_prompt
    host.weather_api_key = pc.weather_api_key
    host.weather_city = pc.weather_city
    host.weather_lat = float(pc.weather_lat)
    host.weather_lon = float(pc.weather_lon)
    host.enable_mic_monitor = host._coerce_bool(pc.enable_mic_monitor)
    host.mic_threshold = pc.mic_threshold
    _mic_iv = float(pc.mic_check_interval)
    if host.capture_source == "remote":
        host.mic_check_interval = max(0.3, min(10.0, _mic_iv))
    else:
        host.mic_check_interval = max(1.0, _mic_iv)
    host.memory_threshold = pc.memory_threshold
    host.battery_threshold = pc.battery_threshold
    host.admin_qq = pc.admin_qq
    host.proactive_target = pc.proactive_target
    host.save_local = host._coerce_bool(pc.save_local)
    host.enable_natural_language_screen_assist = host._coerce_bool(
        pc.enable_natural_language_screen_assist
    )
    host.enable_window_companion = host._coerce_bool(pc.enable_window_companion)
    host.window_companion_targets = pc.window_companion_targets
    host.window_companion_check_interval = pc.window_companion_check_interval
    host.use_shared_screenshot_dir = host._coerce_bool(pc.use_shared_screenshot_dir)
    host.shared_screenshot_dir = pc.shared_screenshot_dir
    host.custom_tasks = pc.custom_tasks
    host.rest_time_range = pc.rest_time_range
    host.enable_learning = host._coerce_bool(pc.enable_learning)
    host.learning_storage = pc.learning_storage
    host.interaction_kpi = pc.interaction_kpi
    host.debug = host._coerce_bool(pc.debug)
    host.custom_presets = pc.custom_presets
    host.current_preset_index = pc.current_preset_index
    host._parse_custom_presets()
    if host.current_preset_index >= len(host.parsed_custom_presets):
        host.current_preset_index = -1
        pc.current_preset_index = -1

    host.observation_storage = pc.observation_storage
    host.max_observations = pc.max_observations
    host.interaction_frequency = pc.interaction_frequency
    host.image_quality = pc.image_quality
    host.system_prompt = pc.system_prompt
    host.bot_appearance = pc.bot_appearance

    host.webui_enabled = host._coerce_bool(pc.webui.enabled)
    host.webui_host = pc.webui.host
    normalized_port = normalize_webui_port(host, pc.webui.port)
    if normalized_port != pc.webui.port:
        pc.webui.port = normalized_port
        pc.save_webui_config()
    host.webui_port = normalized_port
    host.webui_auth_enabled = host._coerce_bool(pc.webui.auth_enabled)
    host.webui_password = pc.webui.password
    host.webui_session_timeout = pc.webui.session_timeout
    host.webui_allow_external_api = host._coerce_bool(pc.webui.allow_external_api)
    host._parse_window_companion_targets()
    parse_user_preferences(host)
