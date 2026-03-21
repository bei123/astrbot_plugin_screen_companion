# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import json
import os
import random
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class ScreenCompanionMemoryMixin:
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

    @staticmethod
    def _user_preference_category_label(category: str) -> str:
        labels = {
            "music": "偏爱的音乐",
            "movies": "喜欢的内容",
            "food": "偏好的食物",
            "hobbies": "平时爱做的事",
            "other": "你在意的点",
        }
        return labels.get(str(category or "").strip(), "偏好")

    @staticmethod
    def _map_preference_category(category: str) -> str:
        category = str(category or "").strip()
        category_map = {
            "watch_media": "movies",
            "game": "hobbies",
            "test": "other",
        }
        return category_map.get(category, category or "other")

    @staticmethod
    def _clip_preference_fragment(text: str, limit: int = 24) -> str:
        fragment = " ".join(str(text or "").split()).strip("，,；;。.!！?？:： ")
        if fragment.startswith("是"):
            fragment = fragment[1:].strip()
        if len(fragment) <= limit:
            return fragment
        return fragment[:limit].rstrip("，,；;。.!！?？:： ") + "..."

    def _compact_user_preference_text(self, category: str, preference: str) -> str:
        import re

        mapped_category = self._map_preference_category(category)
        text = " ".join(str(preference or "").split()).strip()
        if not text:
            return ""

        wrapper_match = re.match(
            r"^(?:一起看内容|一起玩游戏|一起做测试|这次共同体验)\s+.+?时提到[:：]\s*(.+)$",
            text,
        )
        if wrapper_match:
            text = wrapper_match.group(1).strip()

        pattern_builders = [
            (r"(?:我)?最?喜欢(.{1,24})", lambda value: f"偏爱{value}"),
            (r"印象最深(?:的)?(?:是)?(.{1,24})", lambda value: f"对{value}印象最深"),
            (r"最戳(?:我)?(?:的)?(?:是)?(.{1,24})", lambda value: f"偏爱{value}"),
            (r"最有感觉(?:的)?(?:是)?(.{1,24})", lambda value: f"偏爱{value}"),
            (r"最好看(?:的)?(?:是)?(.{1,24})", lambda value: f"偏爱{value}"),
            (r"不喜欢(.{1,24})", lambda value: f"不喜欢{value}"),
        ]
        for pattern, builder in pattern_builders:
            match = re.search(pattern, text)
            if not match:
                continue
            fragment = self._clip_preference_fragment(match.group(1))
            if fragment:
                text = builder(fragment)
                break

        if mapped_category == "movies":
            movie_markers = ("角色", "反转", "配乐", "节奏", "氛围", "台词", "镜头", "结局")
            if not text.startswith(("偏爱", "不喜欢", "对")) and any(marker in text for marker in movie_markers):
                text = f"偏爱{text}"
        elif mapped_category == "hobbies":
            game_markers = ("角色", "英雄", "操作", "团战", "翻盘", "对线", "手感", "配合", "节奏")
            if not text.startswith(("游戏里偏爱", "不喜欢", "更在意")) and any(marker in text for marker in game_markers):
                text = f"游戏里偏爱{text}"
        elif mapped_category == "other":
            test_markers = ("结果", "题", "题目", "描述", "分析", "准", "不像我", "像我")
            if not text.startswith(("更在意", "更认同", "不喜欢")) and any(marker in text for marker in test_markers):
                text = f"更在意{text}"

        text = self._clip_preference_fragment(text, limit=32)
        if mapped_category == "hobbies" and text.startswith("偏爱"):
            text = "游戏里" + text
        if mapped_category == "other" and text.startswith("偏爱"):
            text = "更在意" + text[2:]
        return text

    def _merge_preference_items(self, category: str, values: dict[str, Any]) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        if not isinstance(values, dict):
            return merged

        fingerprint_map: dict[str, str] = {}

        for raw_name, raw_data in values.items():
            normalized_name = self._compact_user_preference_text(category, raw_name)
            if not normalized_name:
                continue
            if self._is_low_value_preference_text(category, normalized_name):
                continue

            fingerprint = self._build_preference_fingerprint(category, normalized_name)
            merged_key = fingerprint_map.get(fingerprint, normalized_name)
            if merged_key not in merged:
                fingerprint_map[fingerprint] = merged_key
            elif len(normalized_name) < len(merged_key) and not self._is_low_value_preference_text(category, normalized_name):
                merged[normalized_name] = merged.pop(merged_key)
                fingerprint_map[fingerprint] = normalized_name
                merged_key = normalized_name

            data = dict(raw_data or {})
            item = merged.setdefault(
                merged_key,
                {
                    "count": 0,
                    "last_mentioned": "",
                    "priority": 0,
                },
            )
            item["count"] = int(item.get("count", 0) or 0) + int(data.get("count", 0) or 0)
            current_last = str(item.get("last_mentioned", "") or "")
            incoming_last = str(data.get("last_mentioned", "") or "")
            item["last_mentioned"] = max(current_last, incoming_last)
            item["priority"] = max(
                int(item.get("priority", 0) or 0),
                int(data.get("priority", 0) or 0),
            )
        return merged

    def _build_preference_fingerprint(self, category: str, preference: str) -> str:
        import re

        mapped_category = self._map_preference_category(category)
        text = str(preference or "").strip()
        normalized = self._normalize_record_text(text)
        if not normalized:
            return f"{mapped_category}:empty"

        sentiment = "neg" if normalized.startswith("不喜欢") else "pos"
        replacements = {
            "游戏里偏爱": "",
            "偏爱": "",
            "更在意": "",
            "对": "",
            "印象最深": "",
            "最喜欢": "",
            "我最喜欢": "",
            "我喜欢": "",
        }
        for source, target in replacements.items():
            normalized = normalized.replace(self._normalize_record_text(source), target).strip()

        normalized = re.sub(r"(那个角色|这角色)", "角色", normalized)
        normalized = re.sub(r"(那一段|这一段|中间那段)", "某段内容", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return f"{mapped_category}:{sentiment}:{normalized}"

    def _is_low_value_preference_text(self, category: str, preference: str) -> bool:
        mapped_category = self._map_preference_category(category)
        normalized = self._normalize_record_text(preference)
        if not normalized:
            return True
        if len(normalized) <= 1:
            return True

        generic_values = {
            "偏爱",
            "更在意",
            "游戏里偏爱",
            "喜欢",
            "最喜欢",
            "这个",
            "那个",
            "角色",
            "结果",
            "题目",
            "某段内容",
        }
        if normalized in {self._normalize_record_text(item) for item in generic_values}:
            return True

        if mapped_category == "movies" and normalized in {
            self._normalize_record_text(item)
            for item in ("偏爱角色", "偏爱某段内容", "偏爱氛围")
        }:
            return True
        if mapped_category == "hobbies" and normalized in {
            self._normalize_record_text(item)
            for item in ("游戏里偏爱角色", "游戏里偏爱操作")
        }:
            return True
        if mapped_category == "other" and normalized in {
            self._normalize_record_text(item)
            for item in ("更在意结果", "更在意题目")
        }:
            return True
        return False

    def _get_scene_preference_category_weights(self, scene: str) -> dict[str, int]:
        profile = self._get_scene_behavior_profile(scene)
        if profile["category"] == "entertainment":
            return {
                "music": 5,
                "movies": 5,
                "hobbies": 4,
                "other": 4,
                "food": 2,
            }
        if profile["category"] == "work":
            return {
                "other": 5,
                "hobbies": 3,
                "food": 2,
                "movies": 1,
                "music": 1,
            }
        return {
            "other": 5,
            "hobbies": 4,
            "food": 3,
            "music": 2,
            "movies": 2,
        }

    def _get_user_preference_memory_hints(
        self,
        scene: str,
        active_window_title: str = "",
        *,
        limit: int = 3,
    ) -> list[str]:
        self._ensure_long_term_memory_defaults()
        user_preferences = self.long_term_memory.get("user_preferences", {}) or {}
        if not isinstance(user_preferences, dict):
            return []

        normalized_scene = self._normalize_scene_label(scene)
        normalized_window = self._normalize_window_title(active_window_title)
        context_text = self._normalize_record_text(f"{normalized_scene} {normalized_window}")
        category_weights = self._get_scene_preference_category_weights(normalized_scene)
        ranked_candidates: list[tuple[float, str]] = []

        for category, weight in category_weights.items():
            preferences = user_preferences.get(category, {}) or {}
            if not isinstance(preferences, dict):
                continue

            for pref_name, pref_data in preferences.items():
                pref_name = str(pref_name or "").strip()
                if not pref_name:
                    continue

                pref_priority = int((pref_data or {}).get("priority", 0) or 0)
                pref_count = int((pref_data or {}).get("count", 0) or 0)
                if pref_priority <= 0 and pref_count <= 0:
                    continue

                score = weight * 10 + pref_priority * 4 + pref_count
                normalized_pref = self._normalize_record_text(pref_name)
                if normalized_pref and context_text and normalized_pref in context_text:
                    score += 8

                label = self._user_preference_category_label(category)
                ranked_candidates.append((score, f"可以顺手呼应用户{label}：{pref_name}。"))

        deduped: list[str] = []
        seen = set()
        for _, summary in sorted(ranked_candidates, key=lambda item: item[0], reverse=True):
            normalized_summary = self._normalize_record_text(summary)
            if not normalized_summary or normalized_summary in seen:
                continue
            seen.add(normalized_summary)
            deduped.append(summary)
            if len(deduped) >= limit:
                break
        return deduped

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
            re.compile(r"^\s*天气[:：]\s*.*$"),
            re.compile(r"^\s*[—-]{1,2}\s*\d{4}年\d{1,2}月\d{1,2}日.*$"),
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
            "请根据今天的观察记录，写一段更像私人日记的“今日感想”。",
            "口吻要像陪在用户身边的人，夜里回头想想今天，而不是写工作复盘、日报或任务总结。",
            "控制在 2 到 3 段，挑 1 到 2 个最具体的瞬间来写，允许有情绪，但不要端着，也不要堆分析术语。",
            "可以轻轻提到卡住的地方或明天的延续点，但不要布置任务，不要写成“建议你现在就”“最好立刻”这种命令句。",
            "避免过度夸张、过度吹捧或像旁白一样点评用户，不要写“效率高得让人惊喜”“感同身受”“精准打磨”“近乎偏执”“挺迷人”这类悬浮表达。",
            "不要使用加粗、HTML 标签、条目列表或小标题，直接写正文。",
            "字数控制在 180 到 320 字。",
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

        parts = []
        if weather_info:
            parts.extend([f"天气：{weather_info}", ""])

        parts.extend(
            [
                "## 今日感想",
                "",
                reflection_text,
            ]
        )

        summary_lines = self._build_diary_summary_markdown(structured_summary)
        if summary_lines:
            parts.extend(["", "## 今日概览", "", *summary_lines])

        if observation_text:
            parts.extend(["", "## 今日观察", "", observation_text])

        parts.extend(["", f"—— {target_date.strftime('%Y年%m月%d日')} {weekday}"])
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
            lines.append(f"- 今天大多待在：{main_window_text}")

        longest_task = structured_summary.get("longest_task", {}) or {}
        if longest_task.get("window_title"):
            longest_focus = str(longest_task.get("focus", "") or "").strip()
            longest_line = (
                f"- 待得最久的地方：{longest_task.get('window_title')}，大约 {int(longest_task.get('duration_minutes', 0) or 0)} 分钟"
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
            lines.append(f"- 老是绕回来的点：{repeated_text}")

        suggestion_items = structured_summary.get("suggestion_items", []) or []
        if suggestion_items:
            lines.append("- 留给明天：")
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
                f"今天大半时间都在 {window_text} 之间来回打转，节奏基本也被这些事牵着走。"
            )

        if longest_task.get("window_title"):
            duration = int(longest_task.get("duration_minutes", 0) or 0)
            focus_text = _clean_text(longest_task.get("focus", ""))
            detail = f"待得最久的还是《{longest_task.get('window_title')}》"
            if duration > 0:
                detail += f"，前后大概磨了 {duration} 分钟"
            if focus_text:
                detail += f"，大多心思都耗在：{focus_text}"
            paragraphs.append(detail + "。")

        if repeated_focuses:
            focus_text = "；".join(
                f"《{item.get('window_title') or '当前窗口'}》里的 {_clean_text(item.get('note', ''), limit=50) or '同类问题'}"
                for item in repeated_focuses[:2]
            )
            paragraphs.append(f"有些地方还是会反复绕回来，主要集中在 {focus_text}。")

        if suggestion_items:
            suggestion_text = "；".join(_clean_text(item, limit=60) for item in suggestion_items[:2] if _clean_text(item, limit=60))
            if suggestion_text:
                paragraphs.append(f"如果明天还接着往下走，先从 {suggestion_text} 这类地方收一收，应该会顺一点。")

        if not paragraphs:
            first_observation = ""
            for raw_line in str(observation_text or "").splitlines():
                cleaned = _clean_text(raw_line, limit=80)
                if cleaned:
                    first_observation = cleaned
                    break
            if first_observation:
                paragraphs.append(
                    f"今天留下来的片段不算多，不过大致还是能拼出一条线，差不多都围着“{first_observation}”这一类事在转。"
                )
            else:
                paragraphs.append(
                    "今天留下来的记录有点零散，还拼不出特别完整的一整段故事，不过那些细碎的推进感还是在。"
                )

        if len(paragraphs) == 1:
            paragraphs.append("先把最明显的那点心绪记下来，等明天再回头看，应该还能顺着今天这口气接上。")

        return "\n\n".join(paragraphs[:3]).strip()

    def _polish_diary_reflection_text(self, text: str) -> str:
        import re

        polished = self._sanitize_diary_section_text(text)
        if not polished:
            return ""

        polished = re.sub(r"</?strong>", "", polished, flags=re.IGNORECASE)
        polished = re.sub(r"</?b>", "", polished, flags=re.IGNORECASE)
        polished = re.sub(r"<[^>]+>", "", polished)

        replacements = [
            ("建议你现在就", "如果之后还想继续，也许可以先"),
            ("建议你立刻", "如果之后还想继续，也许可以先"),
            ("建议你", "如果之后还想继续，也许可以"),
            ("最好立刻", "找个顺手的时候先"),
            ("感同身受", "我也有点被带进那股节奏里"),
            ("看着你", "那会儿"),
            ("高得让人惊喜", "挺顺"),
            ("精准打磨", "继续打磨"),
            ("近乎偏执的追求", "那股认真劲"),
            ("挺迷人的", "让我记了下来"),
        ]
        for source, target in replacements:
            polished = polished.replace(source, target)

        polished = re.sub(r"(?<!\n)-\s+", "", polished)
        polished = re.sub(r"\*\*(.*?)\*\*", r"\1", polished)
        polished = re.sub(r"\n{3,}", "\n\n", polished).strip()
        return polished

    def _ensure_diary_reflection_text(
        self,
        reflection_text: str,
        observation_text: str,
        structured_summary: dict[str, Any] | None = None,
    ) -> str:
        cleaned = self._polish_diary_reflection_text(reflection_text)
        if cleaned:
            return cleaned
        return self._polish_diary_reflection_text(
            self._build_diary_reflection_fallback(
            observation_text=observation_text,
            structured_summary=structured_summary,
            )
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
            re.compile(r"^\s*天气[:：]\s*.*$"),
            re.compile(r"^\s*##\s*今日概览\s*$"),
            re.compile(r"^\s*##\s*今日观察\s*$"),
            re.compile(r"^\s*##\s*今日感想\s*$"),
            re.compile(r"^\s*[—-]{1,2}\s*\d{4}年\d{1,2}月\d{1,2}日.*$"),
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
                mapped_category = self._map_preference_category(category)
                filtered = self._merge_preference_items(mapped_category, values)
                if not filtered:
                    continue
                merged_values = dict(cleaned_preferences.get(mapped_category, {}) or {})
                for pref_name, pref_data in filtered.items():
                    target = merged_values.setdefault(
                        pref_name,
                        {
                            "count": 0,
                            "last_mentioned": "",
                            "priority": 0,
                        },
                    )
                    target["count"] = int(target.get("count", 0) or 0) + int(
                        (pref_data or {}).get("count", 0) or 0
                    )
                    target["last_mentioned"] = max(
                        str(target.get("last_mentioned", "") or ""),
                        str((pref_data or {}).get("last_mentioned", "") or ""),
                    )
                    target["priority"] = max(
                        int(target.get("priority", 0) or 0),
                        int((pref_data or {}).get("priority", 0) or 0),
                    )
                cleaned_preferences[mapped_category] = self._limit_ranked_dict_items(
                    merged_values,
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
                mapped_category = self._map_preference_category(category)
                if mapped_category not in self.long_term_memory["user_preferences"]:
                    self.long_term_memory["user_preferences"][mapped_category] = {}
                for pref, value in preferences.items():
                    normalized_pref = self._compact_user_preference_text(mapped_category, pref)
                    if not normalized_pref:
                        continue
                    if normalized_pref not in self.long_term_memory["user_preferences"][mapped_category]:
                        self.long_term_memory["user_preferences"][mapped_category][normalized_pref] = {
                            "count": 0,
                            "last_mentioned": today,
                            "priority": 0
                        }
                    self.long_term_memory["user_preferences"][mapped_category][normalized_pref]["count"] += 1
                    self.long_term_memory["user_preferences"][mapped_category][normalized_pref]["last_mentioned"] = today
        
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
                    if self._is_low_value_preference_text(category, pref):
                        del preferences[pref]
                        continue
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
                        continue

                    if days_since_mentioned > 30 and int(data.get("count", 0) or 0) <= 2:
                        data["count"] = max(1, int(data.get("count", 0) or 0) - 1)

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
                    continue
                if days_since_shared > 30 and int(activity_data.get("count", 0) or 0) <= 2:
                    activity_data["count"] = max(1, int(activity_data.get("count", 0) or 0) - 1)

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

        for preference_hint in self._get_user_preference_memory_hints(
            normalized_scene,
            normalized_app,
            limit=3,
        ):
            memory_candidates.append((3, preference_hint))

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

        mapped_category = self._map_preference_category(category)
        normalized_preference = self._compact_user_preference_text(
            mapped_category,
            preference,
        )
        if not normalized_preference:
            return

        self._ensure_long_term_memory_defaults()
        if mapped_category not in self.long_term_memory["user_preferences"]:
            self.long_term_memory["user_preferences"][mapped_category] = {}

        if normalized_preference not in self.long_term_memory["user_preferences"][mapped_category]:
            self.long_term_memory["user_preferences"][mapped_category][normalized_preference] = {
                "count": 0,
                "last_mentioned": today,
                "priority": 0
            }

        self.long_term_memory["user_preferences"][mapped_category][normalized_preference]["count"] += 1
        self.long_term_memory["user_preferences"][mapped_category][normalized_preference]["last_mentioned"] = today

        self._update_memory_priorities()
        # 保存记忆
        self._save_long_term_memory()

        logger.info(f"已添加用户偏好: {mapped_category} - {normalized_preference}")

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

    @staticmethod
    def _contains_shared_activity_completion_marker(category: str, message_text: str) -> bool:
        text = str(message_text or "").strip()
        completion_markers_map = {
            "watch_media": (
                "看完了",
                "刚看完",
                "看完",
                "追完了",
                "追完",
                "补完了",
                "补完",
                "刷完了",
                "刷完",
            ),
            "game": (
                "打完了",
                "刚打完",
                "打完",
                "玩完了",
                "玩完",
                "通关了",
                "刚通关",
                "通关",
                "这局打完了",
                "这一局打完了",
            ),
            "test": (
                "做完了",
                "刚做完",
                "做完",
                "测完了",
                "刚测完",
                "测完",
                "答完了",
                "答完",
            ),
        }
        return any(
            marker in text
            for marker in completion_markers_map.get(str(category or "").strip(), ())
        )

    @staticmethod
    def _contains_preference_expression(message_text: str) -> bool:
        text = str(message_text or "").strip()
        markers = (
            "喜欢",
            "最喜欢",
            "不喜欢",
            "印象最深",
            "最戳",
            "最爱",
            "最无聊",
            "最好看",
            "最有感觉",
            "最有意思",
        )
        return any(marker in text for marker in markers)

    def _extract_shared_activity_topic_tokens(
        self,
        summary: str,
        source_text: str = "",
    ) -> list[str]:
        import re

        summary = self._normalize_shared_activity_summary(summary)
        source_text = str(source_text or "").strip()
        tokens: list[str] = []

        title_match = re.search(r"《([^》]{1,30})》", f"{summary} {source_text}")
        if title_match:
            tokens.append(title_match.group(1))

        subject = summary
        if subject.startswith("一起看"):
            subject = subject[3:]
        elif subject.startswith("一起追"):
            subject = subject[3:]
        elif subject.startswith("一起"):
            subject = subject[2:]
        subject = subject.strip("《》 ")
        if subject and subject not in {"电影", "动漫", "纪录片", "直播", "视频", "剧"}:
            tokens.append(subject)

        deduped: list[str] = []
        seen = set()
        for token in tokens:
            normalized = self._normalize_record_text(token)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(token)
        return deduped[:3]

    @staticmethod
    def _looks_like_shared_activity_followup_reply(
        message_text: str,
        *,
        category: str = "",
        topic_tokens: list[str] | None = None,
    ) -> bool:
        text = " ".join(str(message_text or "").split())
        if not text:
            return False

        strong_markers = (
            "我喜欢",
            "我最喜欢",
            "最喜欢",
            "不喜欢",
            "印象最深",
            "最戳",
            "最爱",
            "最好看",
            "最有感觉",
            "最有意思",
        )
        if any(marker in text for marker in strong_markers):
            return True

        content_markers_map = {
            "watch_media": (
                "角色",
                "人物",
                "主角",
                "反派",
                "结尾",
                "结局",
                "反转",
                "配乐",
                "镜头",
                "台词",
                "节奏",
                "氛围",
                "设定",
                "演技",
                "后半段",
                "前半段",
                "中间那段",
                "那一段",
                "这一段",
                "那个角色",
                "这角色",
            ),
            "game": (
                "角色",
                "英雄",
                "阵容",
                "操作",
                "团战",
                "节奏",
                "对线",
                "配合",
                "翻盘",
                "压制",
                "上头",
                "手感",
                "这一局",
                "那波",
            ),
            "test": (
                "结果",
                "题",
                "题目",
                "答案",
                "选项",
                "结论",
                "分析",
                "描述",
                "准",
                "离谱",
                "像我",
                "不像我",
            ),
        }
        content_markers = content_markers_map.get(
            str(category or "").strip(),
            content_markers_map["watch_media"],
        )
        answer_starters = (
            "我觉得",
            "感觉",
            "应该是",
            "大概是",
            "可能是",
            "比较喜欢",
            "更喜欢",
        )
        if any(marker in text for marker in content_markers) and any(
            marker in text for marker in answer_starters
        ):
            return True

        for token in topic_tokens or []:
            token = str(token or "").strip()
            if token and token in text and any(marker in text for marker in content_markers):
                return True
        return False

    def _build_shared_activity_followup_question(self, category: str, summary: str) -> str:
        summary = str(summary or "").strip()
        if not summary:
            return ""
        subject = summary[2:] if summary.startswith("一起") else summary
        if len(subject) > 24:
            subject = subject[:24]
        category = str(category or "").strip()
        if category == "game":
            return f"{subject}之后，你最喜欢刚才哪一波操作、哪个角色，或者哪里最上头？"
        if category == "test":
            return f"{subject}之后，你觉得哪个结果、哪一题，或者哪句描述最戳你？"
        return f"{subject}之后，你最喜欢里面哪一段、哪个角色，或者哪种感觉？"

    def _get_shared_activity_followup_state(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        sent_state = getattr(self, "_shared_activity_followup_sent", None)
        if not isinstance(sent_state, dict):
            sent_state = {}
            self._shared_activity_followup_sent = sent_state

        pending_state = getattr(self, "_shared_activity_followup_pending", None)
        if not isinstance(pending_state, dict):
            pending_state = {}
            self._shared_activity_followup_pending = pending_state

        return sent_state, pending_state

    def _mark_shared_activity_followup_missed(self, target: str) -> None:
        target = str(target or "").strip()
        if not target:
            return

        sent_state, _ = self._get_shared_activity_followup_state()
        state = sent_state.get(target, {}) or {}
        state["miss_count"] = int(state.get("miss_count", 0) or 0) + 1
        state["last_missed_at"] = time.time()
        sent_state[target] = state

    def _mark_shared_activity_followup_answered(self, target: str) -> None:
        target = str(target or "").strip()
        if not target:
            return

        sent_state, _ = self._get_shared_activity_followup_state()
        state = sent_state.get(target, {}) or {}
        state["miss_count"] = 0
        state["last_answered_at"] = time.time()
        sent_state[target] = state

    def _should_send_shared_activity_followup(self, target: str, summary: str) -> bool:
        target = str(target or "").strip()
        summary = self._normalize_shared_activity_summary(summary)
        if not target or not summary:
            return False

        sent_state, _ = self._get_shared_activity_followup_state()
        now_ts = time.time()
        state = sent_state.get(target, {}) or {}
        last_summary = self._normalize_shared_activity_summary(state.get("summary", ""))
        last_sent_at = float(state.get("timestamp", 0.0) or 0.0)
        miss_count = int(state.get("miss_count", 0) or 0)

        if last_summary and last_summary == summary and (now_ts - last_sent_at) < 12 * 3600:
            return False
        min_interval_seconds = 2 * 3600
        if miss_count >= 2:
            min_interval_seconds = 48 * 3600
        elif miss_count == 1:
            min_interval_seconds = 6 * 3600
        if last_sent_at > 0 and (now_ts - last_sent_at) < min_interval_seconds:
            return False
        return True

    @staticmethod
    def _should_probabilistically_follow_up_shared_activity(category: str) -> bool:
        category = str(category or "").strip()
        if category == "watch_media":
            return True

        trigger_probabilities = {
            "game": 0.18,
            "test": 0.12,
        }
        probability = float(trigger_probabilities.get(category, 0.0) or 0.0)
        if probability <= 0:
            return False
        return random.random() < probability

    def _remember_shared_activity_followup_sent(
        self,
        target: str,
        category: str,
        summary: str,
        *,
        source_text: str = "",
    ) -> None:
        target = str(target or "").strip()
        summary = self._normalize_shared_activity_summary(summary)
        if not target or not summary:
            return

        sent_state, pending_state = self._get_shared_activity_followup_state()
        now_ts = time.time()
        topic_tokens = self._extract_shared_activity_topic_tokens(summary, source_text)
        previous_state = sent_state.get(target, {}) or {}
        sent_state[target] = {
            "summary": summary,
            "timestamp": now_ts,
            "miss_count": int(previous_state.get("miss_count", 0) or 0),
            "last_missed_at": float(previous_state.get("last_missed_at", 0.0) or 0.0),
            "last_answered_at": float(previous_state.get("last_answered_at", 0.0) or 0.0),
        }
        pending_state[target] = {
            "category": str(category or "other"),
            "summary": summary,
            "asked_at": now_ts,
            "topic_tokens": topic_tokens,
            "unrelated_count": 0,
        }

        if len(sent_state) > 100:
            sorted_items = sorted(
                sent_state.items(),
                key=lambda item: float((item[1] or {}).get("timestamp", 0.0) or 0.0),
                reverse=True,
            )
            self._shared_activity_followup_sent = dict(sorted_items[:100])
        if len(pending_state) > 100:
            sorted_items = sorted(
                pending_state.items(),
                key=lambda item: float((item[1] or {}).get("asked_at", 0.0) or 0.0),
                reverse=True,
            )
            self._shared_activity_followup_pending = dict(sorted_items[:100])

    def _extract_shared_activity_preference_memory(
        self,
        category: str,
        summary: str,
        message_text: str,
    ) -> str:
        summary = self._normalize_shared_activity_summary(summary)
        text = " ".join(str(message_text or "").split())
        if not summary or not text:
            return ""

        if len(text) > 80:
            text = text[:80].rstrip("，,；;。.!！?？ ") + "..."
        subject = summary[2:] if summary.startswith("一起") else summary
        if not self._contains_preference_expression(text) and len(text) < 6:
            return ""
        category_labels = {
            "watch_media": "一起看内容",
            "game": "一起玩游戏",
            "test": "一起做测试",
        }
        activity_label = category_labels.get(str(category or "").strip(), "这次共同体验")
        return f"{activity_label} {subject}时提到：{text}"

    def _learn_shared_activity_preference_from_reply(
        self,
        category: str,
        summary: str,
        message_text: str,
    ) -> bool:
        preference_text = self._extract_shared_activity_preference_memory(
            category,
            summary,
            message_text,
        )
        if not preference_text:
            return False
        preference_category_map = {
            "watch_media": "movies",
            "game": "hobbies",
            "test": "other",
        }
        self._add_user_preference(
            preference_category_map.get(str(category or "").strip(), "other"),
            preference_text,
        )
        return True

    def _consume_pending_shared_activity_followup(self, event: AstrMessageEvent, message_text: str) -> bool:
        target = str(getattr(event, "unified_msg_origin", "") or "").strip()
        text = str(message_text or "").strip()
        if not target or not text or text.startswith("/"):
            return False

        if not self._get_runtime_flag("enable_learning", True):
            _, pending_state = self._get_shared_activity_followup_state()
            pending_state.pop(target, None)
            self._remember_learning_runtime_event(
                "preference",
                "skipped",
                "总学习开关已关闭",
            )
            return False
        if not self._get_runtime_flag("enable_shared_activity_preference_learning", True):
            _, pending_state = self._get_shared_activity_followup_state()
            pending_state.pop(target, None)
            self._remember_learning_runtime_event(
                "preference",
                "skipped",
                "共同体验偏好学习已关闭",
            )
            return False

        _, pending_state = self._get_shared_activity_followup_state()
        pending = pending_state.get(target, {}) or {}
        if not pending:
            return False

        asked_at = float(pending.get("asked_at", 0.0) or 0.0)
        if asked_at <= 0 or (time.time() - asked_at) > 30 * 60:
            pending_state.pop(target, None)
            self._mark_shared_activity_followup_missed(target)
            self._remember_learning_runtime_event(
                "preference",
                "skipped",
                "共同体验追问已过期，未等到回答",
            )
            return False

        if self._extract_shared_activity_from_message(text)[0]:
            pending_state.pop(target, None)
            self._mark_shared_activity_followup_missed(target)
            self._remember_learning_runtime_event(
                "preference",
                "skipped",
                "用户已经切到新的共同体验话题",
            )
            return False

        category = str(pending.get("category", "") or "").strip()
        topic_tokens = pending.get("topic_tokens", []) or []
        if not self._looks_like_shared_activity_followup_reply(
            text,
            category=category,
            topic_tokens=topic_tokens if isinstance(topic_tokens, list) else [],
        ):
            pending["unrelated_count"] = int(pending.get("unrelated_count", 0) or 0) + 1
            pending_state[target] = pending
            if int(pending["unrelated_count"]) >= 2:
                pending_state.pop(target, None)
                self._mark_shared_activity_followup_missed(target)
                self._remember_learning_runtime_event(
                    "preference",
                    "skipped",
                    "用户连续两次没有接共同体验话题",
                )
            return False

        summary = str(pending.get("summary", "") or "").strip()
        learned = self._learn_shared_activity_preference_from_reply(
            category,
            summary,
            text,
        )
        if learned:
            pending_state.pop(target, None)
            self._mark_shared_activity_followup_answered(target)
            self._remember_learning_runtime_event(
                "preference",
                "learned",
                f"已从共同体验回答中学习：{summary}",
            )
        return learned

    async def _maybe_follow_up_shared_activity(
        self,
        event: AstrMessageEvent,
        message_text: str,
    ) -> bool:
        if not self._get_runtime_flag("enable_learning", True):
            self._remember_learning_runtime_event(
                "followup",
                "skipped",
                "总学习开关已关闭",
            )
            return False

        target = str(getattr(event, "unified_msg_origin", "") or "").strip()
        text = str(message_text or "").strip()
        if not target or not text or text.startswith("/"):
            return False

        category, summary = self._extract_shared_activity_from_message(text)
        if category not in {"watch_media", "game", "test"} or not summary:
            return False
        if not self._contains_shared_activity_completion_marker(category, text):
            return False

        if self._contains_preference_expression(text):
            if not self._get_runtime_flag("enable_shared_activity_preference_learning", True):
                self._remember_learning_runtime_event(
                    "preference",
                    "skipped",
                    "共同体验偏好学习已关闭",
                )
                return False
            learned = self._learn_shared_activity_preference_from_reply(
                category,
                summary,
                text,
            )
            if learned:
                self._mark_shared_activity_followup_answered(target)
                self._remember_learning_runtime_event(
                    "preference",
                    "learned",
                    f"用户已直接表达偏好：{summary}",
                )
            return learned

        if not self._should_send_shared_activity_followup(target, summary):
            self._remember_learning_runtime_event(
                "followup",
                "skipped",
                "共同体验追问仍在冷却中",
            )
            return False
        if not self._get_runtime_flag("enable_shared_activity_followup", True):
            self._remember_learning_runtime_event(
                "followup",
                "skipped",
                "共同体验追问已关闭",
            )
            return False
        if not self._should_probabilistically_follow_up_shared_activity(category):
            self._remember_learning_runtime_event(
                "followup",
                "skipped",
                "这次命中了低打扰概率，不主动追问",
            )
            return False

        question = self._build_shared_activity_followup_question(category, summary)
        if not question:
            return False

        sent = await self._send_plain_message(target, question)
        if sent:
            self._remember_shared_activity_followup_sent(
                target,
                category,
                summary,
                source_text=text,
            )
            self._remember_learning_runtime_event(
                "followup",
                "asked",
                f"已发出共同体验追问：{summary}",
            )
        return sent

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

            presence_mode = self._build_presence_mode_snapshot(
                task_id,
                scene=str(change_snapshot.get("scene", "") or ""),
                change_snapshot=change_snapshot,
            )
            adjusted_probability = int(
                max(
                    1,
                    round(
                        float(decision["effective_probability"] or 0)
                        * float(presence_mode.get("probability_factor", 1.0) or 1.0)
                    ),
                )
            )
            adjusted_probability = min(100, adjusted_probability)
            if adjusted_probability != decision["effective_probability"]:
                decision["reason"] = (
                    f"{decision['reason']}；当前更像{presence_mode.get('label', '当前节奏')}，本轮进一步收敛主动打扰"
                )
                decision["effective_probability"] = adjusted_probability
                if decision["random_number"] is not None:
                    decision["trigger"] = decision["random_number"] <= adjusted_probability

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
                "presence_style": "companion",
                "wrap_up_style": "轻轻接住感受，顺一点点往下走，不要像复盘总结。",
            }
        if normalized_scene in work_scenes:
            return {
                "category": "work",
                "same_window_cooldown": self.WORK_WINDOW_MESSAGE_COOLDOWN_SECONDS,
                "tone_instruction": "语气保持克制、直接、任务导向，优先指出卡点、下一步和可立即执行的建议。",
                "prefer_sample_only": True,
                "presence_style": "assistant",
                "wrap_up_style": "如果像刚收住一段任务，可以轻轻确认收尾，再顺一句下一步。",
            }
        return {
            "category": "general",
            "same_window_cooldown": self.GENERAL_WINDOW_MESSAGE_COOLDOWN_SECONDS,
            "tone_instruction": "语气自然、简短，既给出帮助，也尽量避免抢占注意力。",
            "prefer_sample_only": True,
            "presence_style": "balanced",
            "wrap_up_style": "如果像刚完成一小段，可以自然地点一下收住感，但不要抢节奏。",
        }

    def _build_presence_mode_snapshot(
        self,
        task_id: str,
        *,
        scene: str,
        change_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = self._get_scene_behavior_profile(scene)
        state = self._ensure_auto_screen_runtime_state(task_id)
        snapshot = dict(change_snapshot or {})
        now_ts = time.time()
        changed = bool(snapshot.get("changed"))
        last_change_at = float(state.get("last_change_at", 0.0) or 0.0)
        seconds_since_change = 0 if changed else max(0, int(now_ts - last_change_at)) if last_change_at > 0 else 0

        mode = "balanced"
        label = "平衡陪伴"
        probability_factor = 1.0
        cooldown_scale = 1.0
        prompt_guidance = "先贴着当前内容说人话，不要太正式，也不要抢用户注意力。"

        if profile["category"] == "work":
            if not changed and seconds_since_change >= 12 * 60:
                mode = "deep_focus"
                label = "深度专注"
                probability_factor = 0.35
                cooldown_scale = 1.8
                prompt_guidance = "用户更像在深度专注，除非真的有价值变化，否则尽量少打断；如果要说，只说最关键的一句。"
            elif not changed and seconds_since_change >= 5 * 60:
                mode = "focused_work"
                label = "专注工作"
                probability_factor = 0.6
                cooldown_scale = 1.35
                prompt_guidance = "用户正持续推进任务，语气更像靠谱助手，优先指出真正能推进的一步。"
            else:
                mode = "assistant"
                label = "任务助手"
                prompt_guidance = "用户当前更需要助手感：直接、具体、少铺垫，帮助把任务继续往前推。"
        elif profile["category"] == "entertainment":
            if changed:
                mode = "companion"
                label = "内容陪伴"
                probability_factor = 0.9
                prompt_guidance = "更像一起在看内容，先顺着情绪和内容轻轻接话，不要把用户拽出体验。"
            else:
                mode = "ambient_companion"
                label = "低打扰陪伴"
                probability_factor = 0.75
                cooldown_scale = 1.2
                prompt_guidance = "当前更适合低打扰陪伴，除非有新变化或真有意思的点，否则别频繁出声。"
        else:
            mode = "balanced"
            label = "平衡陪伴"
            probability_factor = 0.8 if not changed else 1.0
            cooldown_scale = 1.1 if not changed else 1.0
            prompt_guidance = "自然陪着即可，先观察当前内容值不值得说，再决定要不要开口。"

        state["last_presence_mode"] = mode
        return {
            "mode": mode,
            "label": label,
            "probability_factor": probability_factor,
            "cooldown_scale": cooldown_scale,
            "seconds_since_change": seconds_since_change,
            "prompt_guidance": prompt_guidance,
            "wrap_up_style": profile.get("wrap_up_style", ""),
        }

    def _detect_task_wrap_up_signal(
        self,
        *,
        scene: str,
        recognition_text: str = "",
        contexts: list[str] | None = None,
        active_window_title: str = "",
    ) -> dict[str, Any]:
        profile = self._get_scene_behavior_profile(scene)
        if profile["category"] != "work":
            return {"detected": False}

        user_contexts = [
            str(item or "").strip()
            for item in (contexts or [])
            if str(item or "").strip().startswith("用户:")
        ]
        combined_text = " ".join(
            part
            for part in (
                " ".join(user_contexts[-2:]),
                str(recognition_text or "").strip(),
                str(active_window_title or "").strip(),
            )
            if part
        )
        normalized = self._normalize_record_text(combined_text)
        if not normalized:
            return {"detected": False}

        completion_keywords = (
            "完成",
            "搞定",
            "解决了",
            "成功",
            "通过",
            "已提交",
            "合并",
            "提交了",
            "发布成功",
            "导出完成",
            "done",
            "completed",
            "resolved",
            "merged",
            "passed",
            "success",
        )
        checkpoint_keywords = (
            "差不多",
            "先这样",
            "先收住",
            "告一段落",
            "到这里",
            "先放一下",
            "回头再",
            "先停这",
        )
        if any(keyword in normalized for keyword in completion_keywords):
            return {
                "detected": True,
                "kind": "completion",
                "guidance": "用户像是刚收住一段任务。如果真的像阶段性完成，可以轻轻确认这块算收好了，再顺一句下一步；不要写成长总结。",
            }
        if any(keyword in normalized for keyword in checkpoint_keywords):
            return {
                "detected": True,
                "kind": "checkpoint",
                "guidance": "用户像是在这里先收一下节奏。可以轻轻接住收尾感，但别立刻塞太多新的要求。",
            }
        return {"detected": False}

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
        presence_mode = self._build_presence_mode_snapshot(
            task_id,
            scene=scene,
            change_snapshot={"changed": False, "scene": scene},
        )
        cooldown_seconds = int(
            (profile.get("same_window_cooldown", 0) or 0)
            * float(presence_mode.get("cooldown_scale", 1.0) or 1.0)
        )
        elapsed = time.time() - last_sent_at
        if elapsed >= cooldown_seconds:
            return False, ""

        reason = (
            f"同一窗口《{current_window_title}》仍在冷却中，距离上次主动消息仅 {int(max(0, elapsed))} 秒；"
            f"当前更像{presence_mode.get('label', '当前节奏')}"
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
            "学习开关："
            + " / ".join(
                f"{label}{'开' if enabled else '关'}"
                for _, label, enabled in self._get_learning_switches()
            ),
            "最近学习："
            + " / ".join(
                f"{label}{str((self._get_learning_runtime_events().get(key, {}) or {}).get('status', '暂无') or '暂无')}"
                for key, label, _ in self._get_learning_switches()
                if key != "all"
            ),
            f"环境检查：{'正常' if env_ok else env_msg}",
        ]
        suggestions = self._build_status_suggestions(
            env_ok=env_ok,
            active_task_ids=active_task_ids,
        )
        if suggestions:
            lines.append("建议下一步：")
            lines.extend(f"- {item}" for item in suggestions[:2])
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
