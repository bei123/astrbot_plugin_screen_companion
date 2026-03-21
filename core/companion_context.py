"""识屏对话里「同伴语气 + 长期记忆/共同经历」片段的构建与筛选。

与 ``screen_analysis.analyze_screen`` 的分工约定：

- **本模块**：只产出可嵌入识屏 ``interaction_prompt`` 的**短片段**（响应指南段落、
  记忆要点列表、共同经历条目、是否建议发起「一起做点什么」），不负责拼整段主提示。
- **screen_analysis**：负责整条识屏流程的 prompt 编排（素材说明、场景/时间/天气、
  观察记录、触发背景、语气与休息策略等），在需要处**调用**本模块函数并把返回结果
  作为若干 ``prompt_parts`` 加入；避免在本模块再拼一层完整 prompt，防止重复与漂移。
"""

from __future__ import annotations

import time
from typing import Any


def build_companion_response_guide(
    scene: str,
    recognition_text: str,
    custom_prompt: str,
    context_count: int,
) -> str:
    """同伴响应风格说明（与具体画面识别句解耦，供模型保持口吻与连贯性）。"""
    del recognition_text, custom_prompt  # 保留参数以稳定调用方 API
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


def trigger_related_memories(host: Any, scene: Any, app_name: Any) -> list[str]:
    """从长期记忆里挑出与当前场景/窗口相关的若干条自然语言提示。"""
    host._ensure_long_term_memory_defaults()
    normalized_scene = host._normalize_scene_label(scene)
    normalized_app = host._normalize_window_title(app_name)
    memory_candidates: list[tuple[float, str]] = []

    episodic_memories = host.long_term_memory.get("episodic_memories", []) or []
    for item in episodic_memories:
        if not isinstance(item, dict):
            continue
        item_scene = host._normalize_scene_label(item.get("scene", ""))
        item_window = host._normalize_window_title(item.get("active_window", ""))
        if normalized_scene and item_scene and normalized_scene != item_scene:
            continue
        if normalized_app and item_window and normalized_app != item_window:
            continue
        summary = host._extract_memory_focus(item.get("summary", ""), max_length=72)
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

    focus_patterns = host.long_term_memory.get("focus_patterns", {}) or {}
    for _, item in focus_patterns.items():
        if not isinstance(item, dict):
            continue
        item_scene = host._normalize_scene_label(item.get("scene", ""))
        item_window = host._normalize_window_title(item.get("active_window", ""))
        if normalized_scene and item_scene and normalized_scene != item_scene:
            continue
        if normalized_app and item_window and normalized_app != item_window:
            continue
        summary = host._extract_memory_focus(item.get("summary", ""), max_length=48)
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

    scene_memory = host.long_term_memory.get("scenes", {}).get(normalized_scene, {})
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

    app_memory = host.long_term_memory.get("applications", {}).get(normalized_app, {})
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
    association_data = host.long_term_memory.get("memory_associations", {}).get(
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

    profile = host._get_scene_behavior_profile(normalized_scene)
    preference_categories = ["hobbies", "other"]
    if profile["category"] == "entertainment":
        preference_categories = ["music", "movies", "hobbies", "other"]
    elif profile["category"] == "work":
        preference_categories = ["other", "hobbies"]

    user_preferences = host.long_term_memory.get("user_preferences", {}) or {}
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

    deduped: list[str] = []
    seen: set[str] = set()
    for _, summary in sorted(memory_candidates, key=lambda item: item[0], reverse=True):
        normalized_summary = host._normalize_record_text(summary)
        if not normalized_summary or normalized_summary in seen:
            continue
        seen.add(normalized_summary)
        deduped.append(summary)
        if len(deduped) >= 4:
            break

    return deduped


def shared_activity_category_label(category: str) -> str:
    labels = {
        "watch_media": "一起看过",
        "game": "一起玩过",
        "test": "一起做过测试",
        "screen_interaction": "一起进行过识屏互动",
        "other": "一起做过",
    }
    return labels.get(str(category or "other"), "一起做过")


def get_relevant_shared_activities(
    host: Any, scene: str, limit: int = 3
) -> list[tuple[str, dict]]:
    shared_activities = host.long_term_memory.get("shared_activities", {})
    if not isinstance(shared_activities, dict) or not shared_activities:
        return []

    scene = host._normalize_scene_label(scene)
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

    matched: list[tuple[str, dict]] = []
    fallback: list[tuple[str, dict]] = []
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


def should_offer_shared_activity_invite(
    host: Any, scene: str, custom_prompt: str = ""
) -> bool:
    leisure_scenes = {"视频", "阅读", "游戏", "音乐", "社交", "浏览", "浏览-娱乐"}
    if custom_prompt:
        return False
    if scene not in leisure_scenes and not host.long_term_memory.get("shared_activities"):
        return False

    now_ts = time.time()
    if now_ts - float(getattr(host, "last_shared_activity_invite_time", 0.0) or 0.0) < 7200:
        return False

    host.last_shared_activity_invite_time = now_ts
    return True
