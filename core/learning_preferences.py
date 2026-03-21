"""学习数据、用户纠正与配置偏好解析（与 ``/kpi correct``、``/kpi preference`` 行为对齐）。"""

from __future__ import annotations

import datetime
import json
import os
import time
import uuid
from typing import Any

from astrbot.api import logger

LEARNING_DATA_FILENAME = "learning_data.json"
CORRECTIONS_FILENAME = "corrections.json"


def parse_user_preferences(host: Any) -> None:
    """解析 ``host.user_preferences`` 文本到 ``host.parsed_preferences``。"""
    host.parsed_preferences = {}
    raw = str(getattr(host, "user_preferences", "") or "").strip()
    if not raw:
        return

    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        scene, preference = parts
        host.parsed_preferences[scene] = preference

    logger.info("用户偏好设置解析完成")


def load_learning_data(host: Any) -> None:
    try:
        learning_file = os.path.join(host.learning_storage, LEARNING_DATA_FILENAME)
        if os.path.exists(learning_file):
            with open(learning_file, encoding="utf-8") as f:
                host.learning_data = json.load(f)
            logger.info("学习数据加载成功")
    except Exception as e:
        logger.error(f"加载学习数据失败: {e}")
        host.learning_data = {}


def save_learning_data(host: Any) -> None:
    if not getattr(host, "enable_learning", False):
        return
    try:
        learning_file = os.path.join(host.learning_storage, LEARNING_DATA_FILENAME)
        with open(learning_file, "w", encoding="utf-8") as f:
            json.dump(host.learning_data, f, ensure_ascii=False, indent=2)
        logger.info("学习数据保存成功")
    except Exception as e:
        logger.error(f"保存学习数据失败: {e}")


def _corrections_path(host: Any) -> str:
    path = str(getattr(host, "corrections_file", "") or "").strip()
    if not path:
        path = os.path.join(host.learning_storage, CORRECTIONS_FILENAME)
        host.corrections_file = path
    return path


def load_corrections(host: Any) -> None:
    try:
        corrections_file = _corrections_path(host)
        if os.path.exists(corrections_file):
            with open(corrections_file, "r", encoding="utf-8") as f:
                host.corrections = json.load(f)
            logger.info("纠正数据加载成功")
    except Exception as e:
        logger.error(f"加载纠正数据失败: {e}")
        host.corrections = {}


def save_corrections(host: Any) -> None:
    try:
        corrections_file = _corrections_path(host)
        with open(corrections_file, "w", encoding="utf-8") as f:
            json.dump(host.corrections, f, ensure_ascii=False, indent=2)
        logger.info("纠正数据保存成功")
    except Exception as e:
        logger.error(f"保存纠正数据失败: {e}")


def add_user_preference(host: Any, category: Any, preference: Any) -> None:
    """写入长期记忆 ``user_preferences`` 并落盘。"""
    today = datetime.date.today().isoformat()

    if "user_preferences" not in host.long_term_memory:
        host.long_term_memory["user_preferences"] = {
            "music": {},
            "movies": {},
            "food": {},
            "hobbies": {},
            "other": {},
        }

    if category not in host.long_term_memory["user_preferences"]:
        host.long_term_memory["user_preferences"][category] = {}

    cat = host.long_term_memory["user_preferences"][category]
    if preference not in cat:
        cat[preference] = {
            "count": 0,
            "last_mentioned": today,
            "priority": 0,
        }

    cat[preference]["count"] += 1
    cat[preference]["last_mentioned"] = today

    host._update_memory_priorities()
    host._save_long_term_memory()

    logger.info(f"已添加用户偏好: {category} - {preference}")


def update_self_image_memory(host: Any, correction: str) -> None:
    if "self_image" not in host.long_term_memory:
        host.long_term_memory["self_image"] = []

    correction_lower = correction.lower()
    for existing in host.long_term_memory["self_image"]:
        if (
            correction_lower in existing["content"].lower()
            or existing["content"].lower() in correction_lower
        ):
            existing["timestamp"] = datetime.datetime.now().isoformat()
            existing["count"] = existing.get("count", 0) + 1
            break
    else:
        host.long_term_memory["self_image"].append(
            {
                "content": correction,
                "timestamp": datetime.datetime.now().isoformat(),
                "count": 1,
            }
        )

    host._save_long_term_memory()
    logger.info("已更新自身形象记忆")


def update_scene_memory(host: Any, correction: str) -> None:
    if "scenes" not in host.long_term_memory:
        host.long_term_memory["scenes"] = {}

    scene_keywords = [
        "编程",
        "设计",
        "办公",
        "游戏",
        "视频",
        "阅读",
        "音乐",
        "社交",
        "浏览",
    ]
    for keyword in scene_keywords:
        if keyword in correction:
            if keyword not in host.long_term_memory["scenes"]:
                host.long_term_memory["scenes"][keyword] = {
                    "count": 0,
                    "last_used": datetime.datetime.now().isoformat(),
                }
            host.long_term_memory["scenes"][keyword]["count"] += 1
            host.long_term_memory["scenes"][keyword]["last_used"] = (
                datetime.datetime.now().isoformat()
            )
            break

    host._save_long_term_memory()


def update_application_memory(host: Any, correction: str) -> None:
    if "applications" not in host.long_term_memory:
        host.long_term_memory["applications"] = {}

    app_name = correction.split(" ")[0]
    if app_name:
        if app_name not in host.long_term_memory["applications"]:
            host.long_term_memory["applications"][app_name] = {
                "usage_count": 0,
                "last_used": datetime.datetime.now().isoformat(),
                "scenes": {},
            }
        host.long_term_memory["applications"][app_name]["usage_count"] += 1
        host.long_term_memory["applications"][app_name]["last_used"] = (
            datetime.datetime.now().isoformat()
        )

    host._save_long_term_memory()


def analyze_correction_content(host: Any, original: str, corrected: str) -> None:
    del original  # 保留参数以兼容调用形态
    corrected_lower = corrected.lower()

    if "形象" in corrected_lower or "logo" in corrected_lower or "输入法" in corrected_lower:
        update_self_image_memory(host, corrected)

    scene_patterns = ["场景", "是在", "正在", "在做"]
    if any(pattern in corrected_lower for pattern in scene_patterns):
        update_scene_memory(host, corrected)

    app_patterns = ["应用", "程序", "软件", "工具"]
    if any(pattern in corrected_lower for pattern in app_patterns):
        update_application_memory(host, corrected)


def learn_from_correction(
    host: Any, original_response: Any, corrected_response: Any
) -> None:
    correction_id = str(uuid.uuid4())
    host.corrections[correction_id] = {
        "original": original_response,
        "corrected": corrected_response,
        "timestamp": datetime.datetime.now().isoformat(),
    }

    analyze_correction_content(host, str(original_response or ""), str(corrected_response or ""))
    save_corrections(host)
    logger.info("已记录一条用户纠正数据")


def update_learning_data(host: Any, scene: Any, feedback: Any) -> None:
    if not getattr(host, "enable_learning", False):
        return

    if scene not in host.learning_data:
        host.learning_data[scene] = {"feedback": []}

    host.learning_data[scene]["feedback"].append(
        {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "feedback": feedback}
    )

    save_learning_data(host)


def get_scene_preference(host: Any, scene: Any) -> str:
    if scene in getattr(host, "parsed_preferences", {}):
        return host.parsed_preferences[scene]

    if getattr(host, "enable_learning", False) and scene in getattr(
        host, "learning_data", {}
    ):
        feedbacks = host.learning_data[scene].get("feedback", [])
        if feedbacks:
            return feedbacks[-1]["feedback"]

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
