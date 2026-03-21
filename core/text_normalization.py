"""Shared text normalization and small string helpers (memory, diary, observations)."""

from __future__ import annotations

import re
from typing import Any


def normalize_scene_label(scene: str) -> str:
    scene = str(scene or "").strip()
    invalid_labels = {"", "??", "unknown", "???", "?????", "none", "null", "未知"}
    return "" if scene.lower() in invalid_labels or scene in invalid_labels else scene


def normalize_window_title(window_title: str) -> str:
    window_title = str(window_title or "").strip()
    invalid_titles = {"", "未知", "unknown", "宿主机截图", "none", "null"}
    if window_title.lower() in invalid_titles or window_title in invalid_titles:
        return ""
    return window_title


def normalize_record_text(text: str) -> str:
    text = str(text or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"[*#>\-_=~]+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_shared_activity_summary(summary: str) -> str:
    summary = str(summary or "").strip()
    if not summary:
        return ""
    summary = re.sub(r"\s+", " ", summary)
    return summary[:60]


def limit_ranked_dict_items(items: dict, limit: int, score_keys: tuple[str, ...]) -> dict:
    if not isinstance(items, dict) or len(items) <= limit:
        return items

    def score(entry: tuple[str, Any]) -> tuple:
        _, data = entry
        if not isinstance(data, dict):
            return (0,)
        return tuple(int(data.get(key, 0) or 0) for key in score_keys)

    ranked = sorted(items.items(), key=score, reverse=True)
    return dict(ranked[:limit])


def compress_recognition_text(text: str, max_length: int = 800) -> str:
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


def truncate_preview_text(text: str, limit: int = 120) -> str:
    preview = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(preview) <= limit:
        return preview
    return preview[: max(0, limit - 1)] + "…"
