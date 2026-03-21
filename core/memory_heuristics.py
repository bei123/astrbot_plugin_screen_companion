"""Heuristics for memory / diary record quality and similarity."""

from __future__ import annotations

import difflib

from .text_normalization import normalize_record_text


def is_screen_error_text(text: str) -> bool:
    normalized = normalize_record_text(text)
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


def is_low_value_record_text(text: str) -> bool:
    normalized = normalize_record_text(text)
    if len(normalized) < 12:
        return True

    if is_screen_error_text(text):
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


def is_similar_record(current_text: str, previous_text: str, threshold: float = 0.98) -> bool:
    current = normalize_record_text(current_text)
    previous = normalize_record_text(previous_text)
    if not current or not previous:
        return False
    if current == previous:
        return True
    return difflib.SequenceMatcher(None, current, previous).ratio() >= threshold
