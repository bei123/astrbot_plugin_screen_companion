"""识屏回复文案：去掉播报式开场、同伴重复开场、近期已出现的休息话术。"""

from __future__ import annotations

import re
from typing import Any

from .auto_screen_trigger import (
    contains_rest_cue,
    has_recent_rest_cue,
    strip_rest_cue_sentences,
)


def strip_repeated_companion_opening(text: str, *, has_recent_context: bool) -> str:
    if not has_recent_context:
        return str(text or "").strip()

    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^(笨蛋|傻瓜|喂|欸|哎呀|哼)[，,、\s]+", "", cleaned, count=1)
    cleaned = re.sub(r"^(又在|还在|现在在)看", "在看", cleaned, count=1)
    return cleaned.strip()


def polish_response_text(
    host: Any,
    response_text: Any,
    scene: Any,
    *,
    contexts: list[str] | None = None,
    allow_rest_hint: bool = False,
    task_id: str = "",
) -> str:
    """清理沉浸感较差的播报式开场，尤其是视频和阅读场景。"""
    text = str(response_text or "").strip()
    recent_contexts = list(contexts or [])
    has_recent_context = bool(recent_contexts)

    opening_phrases = [
        "我看到你在",
        "你现在正在",
        "你在",
        "我观察到你在",
        "我注意到你在",
        "看到你在",
        "观察到你在",
        "注意到你在",
    ]

    if scene in ("视频", "阅读"):
        for phrase in opening_phrases:
            if text.startswith(phrase):
                text = text[len(phrase) :].strip()
                if text.startswith("在"):
                    text = text[1:].strip()
                break
    else:
        for phrase in opening_phrases:
            if text.startswith(phrase):
                text = text[len(phrase) :].strip()
                break

    text = strip_repeated_companion_opening(
        text,
        has_recent_context=has_recent_context,
    )

    if (
        not allow_rest_hint
        and contains_rest_cue(text)
        and has_recent_rest_cue(host, recent_contexts, task_id=task_id)
    ):
        text = strip_rest_cue_sentences(text)

    return text.strip()
