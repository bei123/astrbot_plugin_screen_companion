"""Long-term memory persistence, episodic/focus patterns, decay, and updates."""

from __future__ import annotations

import datetime
import json
import os
from typing import Any

from astrbot.api import logger

from .memory_heuristics import is_low_value_record_text, is_similar_record
from .text_normalization import (
    compress_recognition_text,
    limit_ranked_dict_items,
    normalize_record_text,
    normalize_scene_label,
    normalize_shared_activity_summary,
    normalize_window_title,
    truncate_preview_text,
)


def load_long_term_memory(host: Any) -> None:
    try:
        if os.path.exists(host.long_term_memory_file):
            with open(host.long_term_memory_file, "r", encoding="utf-8") as f:
                host.long_term_memory = json.load(f)
            clean_long_term_memory_noise(host)
            logger.info("长期记忆加载成功")
    except Exception as e:
        logger.error(f"加载长期记忆失败: {e}")
        host.long_term_memory = {}


def save_long_term_memory(host: Any) -> None:
    try:
        clean_long_term_memory_noise(host)
        with open(host.long_term_memory_file, "w", encoding="utf-8") as f:
            json.dump(host.long_term_memory, f, ensure_ascii=False, indent=2)
        logger.info("长期记忆保存成功")
    except Exception as e:
        logger.error(f"保存长期记忆失败: {e}")


def ensure_long_term_memory_defaults(host: Any) -> None:
    if not isinstance(host.long_term_memory, dict):
        host.long_term_memory = {}

    m = host.long_term_memory
    m.setdefault("applications", {})
    m.setdefault("scenes", {})
    m.setdefault(
        "user_preferences",
        {
            "music": {},
            "movies": {},
            "food": {},
            "hobbies": {},
            "other": {},
        },
    )
    m.setdefault("memory_associations", {})
    m.setdefault("memory_priorities", {})
    m.setdefault("shared_activities", {})
    m.setdefault("episodic_memories", [])
    m.setdefault("focus_patterns", {})


def extract_memory_focus(text: str, max_length: int = 48) -> str:
    summary = compress_recognition_text(text, max_length=max_length)
    summary = str(summary or "").strip().strip(" .。!！?？,，:：;；")
    if not summary:
        return ""
    return summary[:max_length]


def remember_episodic_memory(
    host: Any,
    *,
    scene: str,
    active_window: str,
    summary: str,
    response_preview: str = "",
    kind: str = "screen_observation",
) -> bool:
    normalized_summary = extract_memory_focus(summary, max_length=72)
    if not normalized_summary or is_low_value_record_text(normalized_summary):
        return False

    ensure_long_term_memory_defaults(host)
    scene_n = normalize_scene_label(scene)
    active_window_n = normalize_window_title(active_window)
    today = datetime.date.today().isoformat()
    now_ts = datetime.datetime.now().isoformat()
    memories = list(host.long_term_memory.get("episodic_memories", []) or [])

    matched_index = None
    for index, item in enumerate(memories):
        if not isinstance(item, dict):
            continue
        previous_scene = normalize_scene_label(item.get("scene", ""))
        previous_window = normalize_window_title(item.get("active_window", ""))
        previous_summary = extract_memory_focus(item.get("summary", ""), max_length=72)
        if scene_n and previous_scene and scene_n != previous_scene:
            continue
        if active_window_n and previous_window and active_window_n != previous_window:
            continue
        if is_similar_record(normalized_summary, previous_summary, threshold=0.82):
            matched_index = index
            break

    if matched_index is None:
        memories.append(
            {
                "scene": scene_n,
                "active_window": active_window_n,
                "summary": normalized_summary,
                "response_preview": truncate_preview_text(response_preview, limit=120),
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
            target["response_preview"] = truncate_preview_text(response_preview, limit=120)
        if not target.get("summary"):
            target["summary"] = normalized_summary

    host.long_term_memory["episodic_memories"] = memories
    return True


def remember_focus_pattern(
    host: Any,
    *,
    scene: str,
    active_window: str,
    summary: str,
) -> bool:
    focus_text = extract_memory_focus(summary, max_length=40)
    if not focus_text or is_low_value_record_text(focus_text):
        return False

    ensure_long_term_memory_defaults(host)
    scene_n = normalize_scene_label(scene)
    active_window_n = normalize_window_title(active_window)
    if not scene_n and not active_window_n:
        return False

    pattern_key = f"{scene_n or 'general'}::{active_window_n or 'window'}::{focus_text}"
    today = datetime.date.today().isoformat()
    focus_patterns = host.long_term_memory.setdefault("focus_patterns", {})
    item = focus_patterns.setdefault(
        pattern_key,
        {
            "scene": scene_n,
            "active_window": active_window_n,
            "summary": focus_text,
            "count": 0,
            "last_seen": today,
            "priority": 0,
        },
    )
    item["count"] = int(item.get("count", 0) or 0) + 1
    item["last_seen"] = today
    return True


def is_continuing_memory_context(host: Any, scene: str, active_window: str) -> bool:
    normalized_scene = normalize_scene_label(scene)
    normalized_window = normalize_window_title(active_window)
    app_name = normalized_window.split(" - ")[-1] if " - " in normalized_window else normalized_window
    app_name = normalize_window_title(app_name)

    recent_observations = list(getattr(host, "observations", []) or [])[-3:]
    if len(recent_observations) < 3:
        return False

    for observation in recent_observations:
        previous_scene = normalize_scene_label(observation.get("scene", ""))
        previous_window = normalize_window_title(
            observation.get("active_window") or observation.get("window_title") or ""
        )
        previous_app = previous_window.split(" - ")[-1] if " - " in previous_window else previous_window
        previous_app = normalize_window_title(previous_app)

        if normalized_scene and previous_scene != normalized_scene:
            return False
        if app_name and previous_app != app_name:
            return False

    return bool(normalized_scene or app_name)


def clean_long_term_memory_noise(host: Any) -> None:
    memory = getattr(host, "long_term_memory", None)
    if not isinstance(memory, dict):
        return
    ensure_long_term_memory_defaults(host)

    self_image_memory = memory.get("self_image", [])

    applications = memory.get("applications", {})
    if isinstance(applications, dict):
        cleaned_applications = {}
        for app_name, data in applications.items():
            normalized_app = normalize_window_title(app_name)
            if not normalized_app:
                continue
            app_data = dict(data or {})
            raw_scenes = app_data.get("scenes", {}) or {}
            cleaned_scenes = {}
            for scene_name, count in raw_scenes.items():
                normalized_scene = normalize_scene_label(scene_name)
                if normalized_scene:
                    cleaned_scenes[normalized_scene] = count
            app_data["scenes"] = limit_ranked_dict_items(
                cleaned_scenes,
                limit=20,
                score_keys=("priority", "usage_count", "count"),
            )
            cleaned_applications[normalized_app] = app_data
        memory["applications"] = limit_ranked_dict_items(
            cleaned_applications,
            limit=80,
            score_keys=("priority", "usage_count", "total_duration"),
        )

    scenes = memory.get("scenes", {})
    if isinstance(scenes, dict):
        cleaned_scenes = {}
        for scene_name, data in scenes.items():
            normalized_scene = normalize_scene_label(scene_name)
            if normalized_scene:
                scene_data = dict(data or {})
                if "usage_count" not in scene_data and "count" in scene_data:
                    scene_data["usage_count"] = int(scene_data.get("count", 0) or 0)
                cleaned_scenes[normalized_scene] = scene_data
        memory["scenes"] = limit_ranked_dict_items(
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
            normalized_scene = normalize_scene_label(scene_name)
            normalized_app = normalize_window_title(app_name)
            if normalized_scene and normalized_app:
                cleaned_associations[f"{normalized_scene}_{normalized_app}"] = data
        memory["memory_associations"] = limit_ranked_dict_items(
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
            cleaned_preferences[category] = limit_ranked_dict_items(
                filtered,
                limit=30,
                score_keys=("priority", "count"),
            )
        memory["user_preferences"] = cleaned_preferences

    shared_activities = memory.get("shared_activities", {})
    if isinstance(shared_activities, dict):
        cleaned_shared_activities = {}
        for activity_name, data in shared_activities.items():
            normalized_activity = normalize_shared_activity_summary(activity_name)
            if not normalized_activity:
                continue
            activity_data = dict(data or {})
            activity_data["category"] = str(activity_data.get("category", "other") or "other")
            cleaned_shared_activities[normalized_activity] = activity_data
        memory["shared_activities"] = limit_ranked_dict_items(
            cleaned_shared_activities,
            limit=60,
            score_keys=("priority", "count"),
        )

    episodic_limit = int(getattr(host, "EPISODIC_MEMORY_LIMIT", 120) or 120)
    focus_limit = int(getattr(host, "FOCUS_PATTERN_LIMIT", 80) or 80)

    episodic_memories = memory.get("episodic_memories", [])
    if isinstance(episodic_memories, list):
        cleaned_episodes = []
        seen_episode_keys = set()
        for item in episodic_memories:
            if not isinstance(item, dict):
                continue
            summary = extract_memory_focus(item.get("summary", ""), max_length=72)
            if not summary:
                continue
            scene = normalize_scene_label(item.get("scene", ""))
            active_window = normalize_window_title(item.get("active_window", ""))
            dedupe_key = (
                scene.casefold(),
                active_window.casefold(),
                normalize_record_text(summary),
            )
            if dedupe_key in seen_episode_keys:
                continue
            seen_episode_keys.add(dedupe_key)
            cleaned_episodes.append(
                {
                    "scene": scene,
                    "active_window": active_window,
                    "summary": summary,
                    "response_preview": truncate_preview_text(
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
        memory["episodic_memories"] = cleaned_episodes[:episodic_limit]

    focus_patterns = memory.get("focus_patterns", {})
    if isinstance(focus_patterns, dict):
        cleaned_focus_patterns = {}
        for pattern_key, data in focus_patterns.items():
            if not isinstance(data, dict):
                continue
            summary = extract_memory_focus(data.get("summary", ""), max_length=48)
            scene = normalize_scene_label(data.get("scene", ""))
            active_window = normalize_window_title(data.get("active_window", ""))
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
        memory["focus_patterns"] = limit_ranked_dict_items(
            cleaned_focus_patterns,
            limit=focus_limit,
            score_keys=("priority", "count"),
        )

    if self_image_memory:
        memory["self_image"] = self_image_memory
    else:
        memory.pop("self_image", None)


def build_memory_priority_value(base_count: int | float, days_since: int) -> int:
    count = float(base_count or 0)
    days = max(0, int(days_since or 0))
    if count <= 0:
        return 0
    score = count * (1 / (1 + days))
    return max(1, int(round(score)))


def build_memory_associations(host: Any, scene: str, app_name: str) -> None:
    import datetime as _dt

    association_key = f"{scene}_{app_name}"
    if association_key not in host.long_term_memory["memory_associations"]:
        host.long_term_memory["memory_associations"][association_key] = {
            "count": 0,
            "last_occurred": _dt.date.today().isoformat(),
        }

    host.long_term_memory["memory_associations"][association_key]["count"] += 1
    host.long_term_memory["memory_associations"][association_key]["last_occurred"] = (
        _dt.date.today().isoformat()
    )


def update_memory_priorities(host: Any) -> None:
    today = datetime.date.today()
    m = host.long_term_memory

    if "applications" in m:
        for app_name, app_data in m["applications"].items():
            last_used_date = datetime.date.fromisoformat(app_data["last_used"])
            days_since_used = (today - last_used_date).days

            app_data["priority"] = build_memory_priority_value(
                app_data.get("usage_count", 0),
                days_since_used,
            )

    if "scenes" in m:
        for scene_name, scene_data in m["scenes"].items():
            last_used_date = datetime.date.fromisoformat(scene_data["last_used"])
            days_since_used = (today - last_used_date).days

            scene_data["priority"] = build_memory_priority_value(
                scene_data.get("usage_count", 0),
                days_since_used,
            )

    if "user_preferences" in m:
        for category, preferences in m["user_preferences"].items():
            for pref, data in preferences.items():
                last_mentioned_date = datetime.date.fromisoformat(data["last_mentioned"])
                days_since_mentioned = (today - last_mentioned_date).days

                data["priority"] = build_memory_priority_value(
                    data.get("count", 0),
                    days_since_mentioned,
                )

    if "shared_activities" in m:
        for activity_name, data in m["shared_activities"].items():
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
            data["priority"] = build_memory_priority_value(
                data.get("count", 0),
                days_since_shared,
            )

    episodic_memories = m.get("episodic_memories", [])
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
            item["priority"] = build_memory_priority_value(
                item.get("count", 0),
                (today - last_seen_date).days,
            )

    focus_patterns = m.get("focus_patterns", {})
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
            item["priority"] = build_memory_priority_value(
                item.get("count", 0),
                (today - last_seen_date).days,
            )


def apply_memory_decay(host: Any) -> None:
    today = datetime.date.today()
    long_ret = int(getattr(host, "LONG_TERM_MEMORY_RETENTION_DAYS", 45) or 45)
    light_ret = int(getattr(host, "LIGHT_MEMORY_RETENTION_DAYS", 90) or 90)
    m = host.long_term_memory

    if "applications" in m:
        for app_name, app_data in list(m["applications"].items()):
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
                days_since_used > long_ret
                and usage_count <= 1
                and total_duration <= 5
            ):
                del m["applications"][app_name]

    if "scenes" in m:
        for scene_name, scene_data in list(m["scenes"].items()):
            last_used_text = str(scene_data.get("last_used", "") or "").strip()
            if not last_used_text:
                continue
            try:
                last_used_date = datetime.date.fromisoformat(last_used_text)
            except ValueError:
                continue

            days_since_used = (today - last_used_date).days
            usage_count = int(scene_data.get("usage_count", 0) or 0)
            if days_since_used > long_ret and usage_count <= 1:
                del m["scenes"][scene_name]

    if "user_preferences" in m:
        for category, preferences in list(m["user_preferences"].items()):
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
                    days_since_mentioned > light_ret
                    and int(data.get("count", 0) or 0) <= 1
                ):
                    del preferences[pref]

            if not preferences:
                del m["user_preferences"][category]

    if "shared_activities" in m:
        for activity_name, activity_data in list(m["shared_activities"].items()):
            last_shared = str(activity_data.get("last_shared", "") or "").strip()
            if not last_shared:
                continue
            try:
                last_shared_date = datetime.date.fromisoformat(last_shared)
            except ValueError:
                continue

            days_since_shared = (today - last_shared_date).days
            if (
                days_since_shared > light_ret
                and int(activity_data.get("count", 0) or 0) <= 1
            ):
                del m["shared_activities"][activity_name]

    episodic_memories = m.get("episodic_memories", [])
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
                days_since_seen > light_ret
                and int(item.get("count", 0) or 0) <= 1
            ):
                continue
            retained_episodes.append(item)
        m["episodic_memories"] = retained_episodes

    focus_patterns = m.get("focus_patterns", {})
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
                days_since_seen > light_ret
                and int(item.get("count", 0) or 0) <= 1
            ):
                del focus_patterns[pattern_key]


def update_long_term_memory(
    host: Any,
    scene,
    active_window,
    duration,
    user_preferences=None,
    memory_summary: str = "",
    response_preview: str = "",
) -> None:
    today = datetime.date.today().isoformat()
    scene = normalize_scene_label(scene)
    active_window = normalize_window_title(active_window)

    ensure_long_term_memory_defaults(host)
    m = host.long_term_memory

    app_name = active_window.split(" - ")[-1] if " - " in active_window else active_window
    app_name = normalize_window_title(app_name)
    continuing_context = is_continuing_memory_context(host, scene, active_window)

    if app_name:
        if app_name not in m["applications"]:
            m["applications"][app_name] = {
                "usage_count": 0,
                "total_duration": 0,
                "last_used": today,
                "scenes": {},
                "priority": 0,
            }

        app_memory = m["applications"][app_name]
        if not continuing_context:
            app_memory["usage_count"] += 1
        app_memory["total_duration"] += duration
        app_memory["last_used"] = today

        if scene:
            if scene not in app_memory["scenes"]:
                app_memory["scenes"][scene] = 0
            if not continuing_context:
                app_memory["scenes"][scene] += 1

    if scene:
        if scene not in m["scenes"]:
            m["scenes"][scene] = {
                "usage_count": 0,
                "last_used": today,
                "priority": 0,
            }
        if not continuing_context:
            m["scenes"][scene]["usage_count"] += 1
        m["scenes"][scene]["last_used"] = today

    if user_preferences:
        for category, preferences in user_preferences.items():
            if category not in m["user_preferences"]:
                m["user_preferences"][category] = {}
            for pref, value in preferences.items():
                if pref not in m["user_preferences"][category]:
                    m["user_preferences"][category][pref] = {
                        "count": 0,
                        "last_mentioned": today,
                        "priority": 0,
                    }
                m["user_preferences"][category][pref]["count"] += 1
                m["user_preferences"][category][pref]["last_mentioned"] = today

    if scene and app_name and not continuing_context:
        build_memory_associations(host, scene, app_name)

    if memory_summary:
        remember_episodic_memory(
            host,
            scene=scene,
            active_window=active_window,
            summary=memory_summary,
            response_preview=response_preview,
        )
        remember_focus_pattern(
            host,
            scene=scene,
            active_window=active_window,
            summary=memory_summary,
        )

    update_memory_priorities(host)
    apply_memory_decay(host)
    save_long_term_memory(host)
