"""`/kpi status` 自检报告。"""

from __future__ import annotations

import datetime
from typing import Any

from astrbot.api.event import AstrMessageEvent

from ..core.gemini_multimodal import (
    get_current_chat_provider_id,
    resolve_provider_runtime_info,
)


def resolve_webui_access_url(sc: Any) -> str:
    if not sc.webui_enabled:
        return "未启用"
    if not sc.web_server or not getattr(sc.web_server, "_started", False):
        return "已启用但未运行"

    port = getattr(sc.web_server, "port", sc.webui_port)
    host = sc.webui_host
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{port}"


async def build_kpi_doctor_report(sc: Any, event: AstrMessageEvent) -> str:
    sc._ensure_runtime_state()

    current_check_interval, current_probability = sc._get_current_preset_params()
    active_task_ids = list(sc.auto_tasks.keys())
    focus_task_id = (
        sc.AUTO_TASK_ID
        if sc.AUTO_TASK_ID in sc.auto_tasks
        else (active_task_ids[0] if active_task_ids else sc.AUTO_TASK_ID)
    )
    auto_state = sc._ensure_auto_screen_runtime_state(focus_task_id)
    current_change_snapshot = sc._build_auto_screen_change_snapshot(
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
    elif sc.is_running:
        latest_change_reason = (
            "当前窗口有变化"
            if current_change_snapshot.get("changed")
            else "最近未检测到明显变化"
        )
    else:
        latest_change_reason = "自动观察未运行，当前仅展示前台窗口"

    provider = sc._as_context().get_using_provider()
    umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
    provider_id = await get_current_chat_provider_id(sc, umo=umo)
    provider_info = resolve_provider_runtime_info(
        sc, provider_id=provider_id, provider=provider
    )
    model_label = (
        provider_info.get("model")
        or getattr(provider, "model_name", "")
        or getattr(provider, "model", "")
        or "未知"
    )
    provider_label = provider_info.get("provider_id") or getattr(provider, "id", "") or "未识别"

    env_ok, env_msg = sc._check_env(check_mic=False)
    mode = "录屏" if sc._use_screen_recording_mode() else "截图"
    ffmpeg_label = "未使用"
    encoder_label = "未使用"
    if sc._use_screen_recording_mode():
        ffmpeg_path = sc._get_ffmpeg_path()
        ffmpeg_label = ffmpeg_path if ffmpeg_path else "未检测到 ffmpeg"
        encoder_label = sc._get_recording_video_encoder()

    diary_status = "开启" if sc.enable_diary else "关闭"
    last_diary_label = (
        sc.last_diary_date.strftime("%Y-%m-%d")
        if isinstance(sc.last_diary_date, datetime.date)
        else "未生成"
    )
    target = sc._resolve_proactive_target(event) or "未配置"
    webui_url = resolve_webui_access_url(sc)
    custom_task_count = max(0, len(active_task_ids) - (1 if sc.AUTO_TASK_ID in active_task_ids else 0))
    recent_user_activity_at = sc._get_recent_user_activity_at(event)

    lines = [
        "屏幕伙伴自检",
        f"运行状态：{'已启用' if sc.enabled else '未启用'} / 当前状态 {sc.state} / 自动观察 {'运行中' if sc.is_running else '未运行'}",
        f"任务概览：主任务 {focus_task_id} / 运行中 {len(active_task_ids)} 个 / 自定义任务 {custom_task_count} 个",
        f"识屏模式：{mode} / 间隔 {current_check_interval} 秒 / 基础概率 {current_probability}%",
        f"变化感知：当前窗口《{active_window_title}》 / 最近变化 {latest_change_reason} / 最近变化时间 {sc._format_runtime_timestamp(auto_state.get('last_change_at'))}",
        f"最近判定：{auto_state.get('last_trigger_reason') or '暂未判定'} / 生效概率 {auto_state.get('last_effective_probability', 0)}% / 随机数 {auto_state.get('last_trigger_roll') if auto_state.get('last_trigger_roll') is not None else '未记录'}",
        f"最近手动消息：{sc._format_runtime_timestamp(recent_user_activity_at)}",
        f"最近主动消息：{sc._format_runtime_timestamp(auto_state.get('last_sent_at'))} / 预览 {auto_state.get('last_reply_preview') or '暂无'}",
        f"相似去重：{auto_state.get('last_skip_reason') or '最近没有命中去重'}",
        f"主动目标：{target}",
        f"模型提供方：{provider_label} / 模型 {model_label}",
        f"视觉链路：{getattr(sc, 'vision_source', '') or '未配置'} / 外部视觉开关 {'开' if sc._get_runtime_flag('use_external_vision') else '关'} / 视频直连兜底 {'开启' if sc._get_runtime_flag('allow_unsafe_video_direct_fallback') else '关闭'}",
        f"录屏参数：{sc._get_recording_duration_seconds()} 秒 @ {sc._get_recording_fps():.2f} fps / 编码器 {encoder_label} / ffmpeg {ffmpeg_label}",
        f"观察与日记：观察 {len(sc.observations)} 条 / 待写日记 {len(sc.diary_entries)} 条 / 日记 {diary_status} / 计划时间 {sc.diary_time} / 最近日记 {last_diary_label}",
        f"WebUI：{webui_url}",
        f"环境检查：{'正常' if env_ok else env_msg}",
    ]
    return "\n".join(lines)
