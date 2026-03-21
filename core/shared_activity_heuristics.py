"""从用户消息中启发式提取「共同经历」类别与摘要（与长期记忆 shared_activities 对齐）。"""

from __future__ import annotations

import re


def extract_shared_activity_from_message(
    message_text: str,
    *,
    bot_name: str = "",
) -> tuple[str, str] | tuple[None, None]:
    """若消息像已发生的「和你一起做了某事」，返回 (category, summary)；否则 (None, None)。"""
    text = str(message_text or "").strip()
    if not text or text.startswith("/"):
        return None, None

    escaped_bot_name = re.escape(str(bot_name or "").strip())
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
