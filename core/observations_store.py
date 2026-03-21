"""观察记录 JSON 持久化：加载、裁剪、去重写入与「未知」场景补全。"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any

from astrbot.api import logger

OBSERVATIONS_FILE = "observations.json"


def _observations_file_path(host: Any) -> str:
    return os.path.join(str(host.observation_storage), OBSERVATIONS_FILE)


def load_observations(host: Any) -> None:
    """从磁盘填充 ``host.observations``；失败时置空列表。"""
    try:
        path = _observations_file_path(host)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                host.observations = json.load(f)
                max_obs = getattr(host, "max_observations", None)
                if max_obs is not None and len(host.observations) > int(max_obs):
                    host.observations = host.observations[-15:]
    except Exception as e:
        logger.error(f"加载观察记录失败: {e}")
        host.observations = []


def cleanup_unknown_observations(host: Any) -> None:
    """整理观察记录中场景为「未知」的条目，尝试按窗口标题或描述补全。"""
    observations = getattr(host, "observations", None) or []
    if not observations:
        return

    unknown_count = sum(1 for obs in observations if obs.get("scene", "") == "未知")

    if unknown_count > 5:
        logger.info(f"开始整理未知观察记录，共 {unknown_count} 条")

        for obs in observations:
            if obs.get("scene", "") == "未知":
                window_title = obs.get("window_title", "")
                description = obs.get("description", "")

                if window_title:
                    scene = host._identify_scene(window_title)
                    if scene != "未知":
                        obs["scene"] = scene
                        logger.info(f"已补正场景: {window_title} -> {scene}")
                        continue

                if description:
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

        new_unknown_count = sum(1 for obs in observations if obs.get("scene", "") == "未知")
        if new_unknown_count < unknown_count:
            logger.info(f"未知场景整理完成，从 {unknown_count} 条减少到 {new_unknown_count} 条")


def save_observations(host: Any) -> None:
    """裁剪条数、整理未知场景后写入 ``observations.json``。"""
    try:
        observations = getattr(host, "observations", None)
        if observations is None:
            host.observations = []
            observations = host.observations
        max_obs = getattr(host, "max_observations", None)
        if max_obs is not None and len(observations) > int(max_obs):
            host.observations = observations[-9:]
        cleanup_unknown_observations(host)
        path = _observations_file_path(host)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(host.observations, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存观察记录失败: {e}")


def should_store_observation(
    host: Any, scene: str, recognition_text: str, active_window_title: str
) -> tuple[bool, str]:
    normalized_scene = host._normalize_scene_label(scene)
    normalized_window = host._normalize_window_title(active_window_title)
    normalized_text = host._normalize_record_text(recognition_text)

    if host._is_low_value_record_text(normalized_text):
        return False, "low_value"

    recent_observations = list(getattr(host, "observations", []) or [])[-5:]
    for observation in reversed(recent_observations):
        previous_scene = host._normalize_scene_label(observation.get("scene", ""))
        previous_window = host._normalize_window_title(
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

        if same_context and host._is_similar_record(normalized_text, previous_text):
            return False, "duplicate_observation"

    return True, "ok"


def add_observation(
    host: Any,
    scene: Any,
    recognition_text: Any,
    active_window_title: Any,
    extra: dict[str, Any] | None = None,
) -> bool:
    """追加一条观察并保存；不通过 ``should_store_observation`` 时返回 False。"""
    scene = host._normalize_scene_label(scene)
    active_window_title = host._normalize_window_title(active_window_title)
    should_store, reason = should_store_observation(
        host, scene, str(recognition_text or ""), active_window_title
    )
    if not should_store:
        logger.info(f"跳过观察记录写入: {reason}")
        return False
    observation = {
        "timestamp": datetime.datetime.now().isoformat(),
        "scene": scene,
        "window_title": active_window_title,
        "description": str(recognition_text or "")[:200],
    }
    if isinstance(extra, dict):
        for key, value in extra.items():
            if value in (None, "", [], {}):
                continue
            observation[key] = value
    if not hasattr(host, "observations") or host.observations is None:
        host.observations = []
    host.observations.append(observation)
    max_obs = getattr(host, "max_observations", None)
    if max_obs is not None and len(host.observations) > int(max_obs):
        host.observations = host.observations[-9:]
    save_observations(host)
    return True
