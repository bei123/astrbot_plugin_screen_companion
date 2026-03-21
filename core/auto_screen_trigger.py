"""自动观察：窗口变化、运行时状态、触发概率、去重与用户活动避让。"""

from __future__ import annotations

import random
import re
import time
from typing import Any

from .auto_screen_loop import AutoScreenSessionEvent


def detect_window_changes(sc: Any) -> tuple[bool, list[str]]:
    """检测窗口变化，包括新打开的窗口。"""
    current_time = time.time()

    if not hasattr(sc, "window_change_cooldown"):
        sc.window_change_cooldown = 0
    if current_time < sc.window_change_cooldown:
        return False, []

    if not hasattr(sc, "previous_windows"):
        sc.previous_windows = set()
    if not hasattr(sc, "window_timestamps"):
        sc.window_timestamps = {}

    current_windows = set(sc._list_open_window_titles())
    current_windows = {w for w in current_windows if w and w.strip()}

    valid_new_windows: list[str] = []

    for window in current_windows:
        if window not in sc.window_timestamps:
            sc.window_timestamps[window] = current_time
        else:
            if current_time - sc.window_timestamps[window] >= 180:
                if window not in sc.previous_windows:
                    valid_new_windows.append(window)

    closed_windows = list(sc.window_timestamps.keys())
    for window in closed_windows:
        if window not in current_windows:
            del sc.window_timestamps[window]

    if current_windows != sc.previous_windows:
        sc.previous_windows = current_windows
        sc.window_change_cooldown = current_time + 5
        return True, valid_new_windows

    return False, []


def ensure_auto_screen_runtime_state(sc: Any, task_id: str) -> dict[str, Any]:
    sc._ensure_runtime_state()
    normalized_task_id = str(task_id or sc.AUTO_TASK_ID).strip() or sc.AUTO_TASK_ID
    runtime = sc.auto_screen_runtime
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


def build_auto_screen_change_snapshot(
    sc: Any,
    task_id: str,
    *,
    window_changed: bool = False,
    new_windows: list[str] | None = None,
    update_state: bool = True,
) -> dict[str, Any]:
    state = ensure_auto_screen_runtime_state(sc, task_id)
    active_window_title, _ = sc._get_active_window_info()
    active_window_title = sc._normalize_window_title(active_window_title)
    scene = ""
    if active_window_title:
        scene = sc._normalize_scene_label(sc._identify_scene(active_window_title))

    previous_window_title = str(state.get("last_seen_window_title", "") or "").strip()
    previous_scene = str(state.get("last_scene", "") or "").strip()
    normalized_new_windows = [
        title
        for title in (sc._normalize_window_title(t) for t in (new_windows or []))
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
        "task_id": str(task_id or sc.AUTO_TASK_ID).strip() or sc.AUTO_TASK_ID,
        "active_window_title": active_window_title,
        "scene": scene,
        "changed": changed,
        "reason": "、".join(dict.fromkeys(reasons)),
        "new_windows": normalized_new_windows[:3],
        "timestamp": now_ts,
    }


def is_idle_keepalive_due(sc: Any, task_id: str, check_interval: int) -> bool:
    state = ensure_auto_screen_runtime_state(sc, task_id)
    last_sent_at = float(state.get("last_sent_at", 0.0) or 0.0)
    if last_sent_at <= 0:
        return True

    threshold = max(
        int(check_interval or 0) * 3,
        sc.CHANGE_AWARE_IDLE_KEEPALIVE_SECONDS,
    )
    return (time.time() - last_sent_at) >= threshold


def decide_auto_screen_trigger(
    sc: Any,
    task_id: str,
    *,
    probability: int,
    check_interval: int,
    system_high_load: bool,
    change_snapshot: dict[str, Any],
) -> dict[str, Any]:
    state = ensure_auto_screen_runtime_state(sc, task_id)
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
        idle_keepalive_due = is_idle_keepalive_due(sc, task_id, check_interval)
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


def should_skip_similar_auto_reply(
    sc: Any,
    task_id: str,
    *,
    active_window_title: str,
    text_content: str,
    check_interval: int,
) -> tuple[bool, str]:
    normalized_text = sc._normalize_record_text(text_content)[:160]
    if not normalized_text:
        return False, ""

    state = ensure_auto_screen_runtime_state(sc, task_id)
    last_signature = str(state.get("last_reply_signature", "") or "").strip()
    last_window_title = sc._normalize_window_title(state.get("last_reply_window_title", ""))
    current_window_title = sc._normalize_window_title(active_window_title)
    last_sent_at = float(state.get("last_sent_at", 0.0) or 0.0)
    cooldown_seconds = max(
        int(check_interval or 0) * 3,
        sc.CHANGE_AWARE_SIMILAR_REPLY_COOLDOWN_SECONDS,
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


def remember_auto_reply_state(
    sc: Any,
    task_id: str,
    *,
    active_window_title: str,
    text_content: str,
    sent: bool,
    scene: str = "",
    note: str = "",
) -> None:
    state = ensure_auto_screen_runtime_state(sc, task_id)
    normalized_text = sc._normalize_record_text(text_content)[:160]
    state["last_reply_window_title"] = sc._normalize_window_title(active_window_title)
    state["last_reply_scene"] = sc._normalize_scene_label(scene)
    if normalized_text:
        state["last_reply_signature"] = normalized_text
    state["last_reply_preview"] = sc._truncate_preview_text(text_content, limit=120)
    state["last_skip_reason"] = str(note or "").strip()
    if sent:
        state["last_sent_at"] = time.time()


def format_reply_interval_text(seconds: float) -> str:
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


def build_reply_interval_guidance(sc: Any, task_id: str) -> tuple[str, dict[str, Any]]:
    state = ensure_auto_screen_runtime_state(sc, task_id)
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
    elapsed_text = format_reply_interval_text(elapsed_seconds)

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


def should_defer_for_recent_user_activity(
    sc: Any,
    event: AutoScreenSessionEvent,
    *,
    task_id: str,
    change_snapshot: dict[str, Any],
) -> tuple[bool, str]:
    last_activity_at = sc._get_recent_user_activity_at(event)
    if last_activity_at <= 0:
        return False, ""

    seconds_since = max(0, int(time.time() - last_activity_at))
    grace_seconds = sc.USER_ACTIVITY_GRACE_SECONDS
    if change_snapshot.get("changed"):
        grace_seconds = sc.USER_ACTIVITY_CHANGE_GRACE_SECONDS

    if seconds_since >= grace_seconds:
        return False, ""

    reason = f"用户刚在 {seconds_since} 秒前发过消息，先暂缓这次主动打断"
    ensure_auto_screen_runtime_state(sc, task_id)["last_skip_reason"] = reason
    return True, reason


def get_scene_behavior_profile(sc: Any, scene: str) -> dict[str, Any]:
    normalized_scene = sc._normalize_scene_label(scene)
    entertainment_scenes = {"视频", "游戏", "浏览-娱乐", "音乐", "社交"}
    work_scenes = {"编程", "设计", "办公", "学习", "阅读", "浏览", "浏览-工作"}

    if normalized_scene in entertainment_scenes:
        return {
            "category": "entertainment",
            "same_window_cooldown": sc.ENTERTAINMENT_WINDOW_MESSAGE_COOLDOWN_SECONDS,
            "tone_instruction": "语气更像陪伴和轻提醒，不要频繁推进任务，也不要把用户从内容里拽出来。",
            "prefer_sample_only": False,
        }
    if normalized_scene in work_scenes:
        return {
            "category": "work",
            "same_window_cooldown": sc.WORK_WINDOW_MESSAGE_COOLDOWN_SECONDS,
            "tone_instruction": "语气保持克制、直接、任务导向，优先指出卡点、下一步和可立即执行的建议。",
            "prefer_sample_only": True,
        }
    return {
        "category": "general",
        "same_window_cooldown": sc.GENERAL_WINDOW_MESSAGE_COOLDOWN_SECONDS,
        "tone_instruction": "语气自然、简短，既给出帮助，也尽量避免抢占注意力。",
        "prefer_sample_only": True,
    }


def should_skip_same_window_followup(
    sc: Any,
    task_id: str,
    *,
    active_window_title: str,
    scene: str,
) -> tuple[bool, str]:
    state = ensure_auto_screen_runtime_state(sc, task_id)
    current_window_title = sc._normalize_window_title(active_window_title)
    last_window_title = sc._normalize_window_title(state.get("last_reply_window_title", ""))
    if not current_window_title or current_window_title.casefold() != last_window_title.casefold():
        return False, ""

    last_sent_at = float(state.get("last_sent_at", 0.0) or 0.0)
    if last_sent_at <= 0:
        return False, ""

    profile = get_scene_behavior_profile(sc, scene)
    cooldown_seconds = int(profile.get("same_window_cooldown", 0) or 0)
    elapsed = time.time() - last_sent_at
    if elapsed >= cooldown_seconds:
        return False, ""

    reason = (
        f"同一窗口《{current_window_title}》仍在冷却中，距离上次主动消息仅 {int(max(0, elapsed))} 秒"
    )
    state["last_skip_reason"] = reason
    return True, reason


def contains_rest_cue(text: str) -> bool:
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


def strip_rest_cue_sentences(text: str) -> str:
    original = str(text or "").strip()
    if not original:
        return ""

    parts = re.split(r"(?<=[。！？!?])\s*|\n+", original)
    kept_parts = [
        part.strip()
        for part in parts
        if part.strip() and not contains_rest_cue(part)
    ]
    if not kept_parts:
        return original
    cleaned = " ".join(kept_parts).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned or original


def has_recent_rest_cue(
    sc: Any,
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
        1 for item in assistant_contexts[-3:] if contains_rest_cue(item)
    )
    if recent_assistant_mentions > 0:
        return True

    state = ensure_auto_screen_runtime_state(sc, task_id)
    last_preview = str(state.get("last_reply_preview", "") or "").strip()
    last_sent_at = float(state.get("last_sent_at", 0.0) or 0.0)
    if (
        last_preview
        and contains_rest_cue(last_preview)
        and last_sent_at > 0
        and (time.time() - last_sent_at) < sc.REST_CUE_REPLY_COOLDOWN_SECONDS
    ):
        return True
    return False
