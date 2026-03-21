"""Diary metadata, structured summaries, document building, and memory hooks."""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any

from astrbot.api import logger

from .long_term_memory import extract_memory_focus, remember_episodic_memory, remember_focus_pattern
from .memory_heuristics import is_low_value_record_text, is_screen_error_text, is_similar_record
from .text_normalization import normalize_record_text, normalize_window_title


def resolve_diary_target_date(
    now: datetime.datetime | None = None,
    *,
    early_morning_cutoff_hour: int = 2,
) -> datetime.date:
    current = now or datetime.datetime.now()
    target_date = current.date()
    if current.hour < max(0, int(early_morning_cutoff_hour)):
        target_date -= datetime.timedelta(days=1)
    return target_date


def load_diary_metadata(host: Any) -> None:
    try:
        if os.path.exists(host.diary_metadata_file):
            with open(host.diary_metadata_file, "r", encoding="utf-8") as f:
                host.diary_metadata = json.load(f)
    except Exception as e:
        logger.error(f"加载日记元数据失败: {e}")
        host.diary_metadata = {}


def save_diary_metadata(host: Any) -> None:
    try:
        with open(host.diary_metadata_file, "w", encoding="utf-8") as f:
            json.dump(host.diary_metadata, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存日记元数据失败: {e}")


def update_diary_view_status(host: Any, date_str: str) -> None:
    if date_str not in host.diary_metadata:
        host.diary_metadata[date_str] = {}
    host.diary_metadata[date_str]["viewed"] = True
    host.diary_metadata[date_str]["viewed_at"] = datetime.datetime.now().isoformat()
    save_diary_metadata(host)
    logger.info(f"更新日记查看状态: {date_str} - 已查看")


def get_diary_summary_path(host: Any, target_date: datetime.date) -> str:
    return os.path.join(
        host.diary_storage,
        f"diary_{target_date.strftime('%Y%m%d')}.summary.json",
    )


def load_diary_structured_summary(host: Any, target_date: datetime.date) -> dict[str, Any]:
    summary_path = get_diary_summary_path(host, target_date)
    if not os.path.exists(summary_path):
        return {}
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug(f"读取日记结构化摘要失败: {e}")
        return {}


def save_diary_structured_summary(
    host: Any,
    target_date: datetime.date,
    structured_summary: dict[str, Any],
) -> None:
    summary_path = get_diary_summary_path(host, target_date)
    try:
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(structured_summary, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存日记结构化摘要失败: {e}")


def sanitize_diary_section_text(text: str) -> str:
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    cleaned_lines = []
    skip_heading_patterns = [
        re.compile(r"^\s*#\s*.+日记\s*$"),
        re.compile(r"^\s*##\s*\d{4}年\d{1,2}月\d{1,2}日.*$"),
        re.compile(r"^\s*##\s*今日感想\s*$"),
        re.compile(r"^\s*##\s*今日观察\s*$"),
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


def parse_clock_to_minutes(value: str | None) -> int | None:
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


def should_store_diary_entry(host: Any, content: str, active_window: str) -> tuple[bool, str]:
    normalized_window = normalize_window_title(active_window)
    if is_screen_error_text(content):
        return False, "screen_error"
    if is_low_value_record_text(content):
        return False, "low_value"

    recent_entries = list(getattr(host, "diary_entries", []) or [])[-3:]
    for entry in reversed(recent_entries):
        previous_window = normalize_window_title(entry.get("active_window", ""))
        if normalized_window and previous_window and normalized_window != previous_window:
            continue
        if is_similar_record(content, entry.get("content", ""), threshold=0.9):
            return False, "duplicate_diary_entry"

    return True, "ok"


def compact_diary_entries(host: Any, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for raw_entry in entries or []:
        entry_text = str(raw_entry.get("content") or "").strip()
        normalized_text = normalize_record_text(entry_text)
        if is_low_value_record_text(normalized_text):
            continue

        active_window = normalize_window_title(raw_entry.get("active_window") or "") or "当前窗口"
        time_text = str(raw_entry.get("time") or "").strip() or "--:--"
        entry_minutes = parse_clock_to_minutes(time_text)

        if compacted:
            previous = compacted[-1]
            same_window = previous["active_window"] == active_window
            last_minutes = previous.get("last_minutes")
            close_in_time = (
                entry_minutes is not None
                and last_minutes is not None
                and 0 <= entry_minutes - last_minutes <= 12
            )
            similar_to_previous = is_similar_record(
                normalized_text,
                previous.get("last_text", ""),
                threshold=0.82,
            )
            if same_window and close_in_time and similar_to_previous:
                previous["end_time"] = time_text
                previous["last_minutes"] = entry_minutes
                if not previous["points"] or not is_similar_record(
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


def build_diary_reflection_prompt(
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
        "请根据今天的观察记录，写一段自然、有温度、但信息密度足够的“今日感想”。",
        "控制在 2 到 3 段，不要写成流水账，也不要复述所有观察细节。",
        "优先总结今天在做什么、卡在什么地方、有哪些值得继续推进的点。",
        "如果能给建议，请给和当前任务直接相关、可以立刻使用的建议。",
        "字数控制在 220 到 420 字。",
        f"额外要求：{mood_hint}",
        "",
        "今日观察：",
        observation_text or "今天没有留下有效观察，请写得更克制一些。",
    ]

    if reference_days:
        prompt_parts.extend(["", "可参考前几天的日记风格："])
        for day in reference_days:
            prompt_parts.append(f"### {day['date']}")
            prompt_parts.append(str(day.get("content") or "")[:500])

    return "\n".join(prompt_parts)


def build_diary_summary_markdown(structured_summary: dict[str, Any]) -> list[str]:
    if not isinstance(structured_summary, dict):
        return []

    lines = []
    main_windows = structured_summary.get("main_windows", []) or []
    if main_windows:
        main_window_text = "、".join(
            f"{item.get('window_title', '当前窗口')}（约 {int(item.get('duration_minutes', 0) or 0)} 分钟）"
            for item in main_windows[:3]
        )
        lines.append(f"- 今日主要窗口：{main_window_text}")

    longest_task = structured_summary.get("longest_task", {}) or {}
    if longest_task.get("window_title"):
        longest_focus = str(longest_task.get("focus", "") or "").strip()
        longest_line = (
            f"- 最长停留任务：{longest_task.get('window_title')}，大约 {int(longest_task.get('duration_minutes', 0) or 0)} 分钟"
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
        lines.append(f"- 重复卡点：{repeated_text}")

    suggestion_items = structured_summary.get("suggestion_items", []) or []
    if suggestion_items:
        lines.append("- 建议事项：")
        for item in suggestion_items[:3]:
            lines.append(f"  - {item}")

    return lines


def build_diary_document(
    host: Any,
    target_date,
    weekday: str,
    observation_text: str,
    reflection_text: str,
    structured_summary: dict[str, Any] | None = None,
    weather_info: str = "",
) -> str:
    observation_text = str(observation_text or "").strip()
    reflection_text = sanitize_diary_section_text(reflection_text)
    structured_summary = structured_summary or {}

    parts = [
        f"# {host.bot_name} 的日记",
        "",
        f"## {target_date.strftime('%Y年%m月%d日')} {weekday}",
        "",
    ]
    if weather_info:
        parts.extend([f"**天气**: {weather_info}", ""])

    summary_lines = build_diary_summary_markdown(structured_summary)
    if summary_lines:
        parts.extend(["## 今日概览", "", *summary_lines, ""])

    parts.extend(
        [
            "## 今日观察",
            "",
            observation_text,
            "",
            "## 今日感想",
            "",
            reflection_text,
        ]
    )
    return "\n".join(parts).strip() + "\n"


def extract_actionable_suggestions(
    reflection_text: str,
    *,
    limit: int = 3,
) -> list[str]:
    text = str(reflection_text or "").strip()
    if not text:
        return []

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
        normalized = normalize_record_text(sentence)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(sentence[:80])
    return deduped[:limit]


def build_diary_structured_summary(
    compacted_entries: list[dict[str, Any]],
    reflection_text: str,
) -> dict[str, Any]:
    summary = {
        "main_windows": [],
        "longest_task": {},
        "repeated_focuses": [],
        "suggestion_items": extract_actionable_suggestions(reflection_text, limit=3),
        "entry_count": len(compacted_entries or []),
    }
    if not compacted_entries:
        return summary

    window_stats: dict[str, dict[str, Any]] = {}
    repeated_focuses = []
    longest_task = None
    longest_span = -1

    for entry in compacted_entries:
        window_title = normalize_window_title(entry.get("active_window") or "") or "当前窗口"
        start_minutes = parse_clock_to_minutes(entry.get("start_time"))
        end_minutes = parse_clock_to_minutes(entry.get("end_time"))
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
        key = normalize_record_text(
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


def build_diary_reflection_fallback(
    observation_text: str,
    structured_summary: dict[str, Any] | None = None,
) -> str:
    structured_summary = structured_summary or {}

    def _clean_text(value: str, limit: int = 90) -> str:
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
            f"今天主要在 {window_text} 之间切换，注意力基本都围着这些任务在转。"
        )

    if longest_task.get("window_title"):
        duration = int(longest_task.get("duration_minutes", 0) or 0)
        focus_text = _clean_text(longest_task.get("focus", ""))
        detail = f"今天停留最久的是《{longest_task.get('window_title')}》"
        if duration > 0:
            detail += f"，大约花了 {duration} 分钟"
        if focus_text:
            detail += f"，主要在处理：{focus_text}"
        paragraphs.append(detail + "。")

    if repeated_focuses:
        focus_text = "；".join(
            f"《{item.get('window_title') or '当前窗口'}》里的 {_clean_text(item.get('note', ''), limit=50) or '同类问题'}"
            for item in repeated_focuses[:2]
        )
        paragraphs.append(f"反复出现的卡点也比较明显，主要集中在 {focus_text}。")

    if suggestion_items:
        suggestion_text = "；".join(
            _clean_text(item, limit=60) for item in suggestion_items[:2] if _clean_text(item, limit=60)
        )
        if suggestion_text:
            paragraphs.append(f"如果明天继续推进，比较值得优先处理的是：{suggestion_text}。")

    if not paragraphs:
        first_observation = ""
        for raw_line in str(observation_text or "").splitlines():
            cleaned = _clean_text(raw_line, limit=80)
            if cleaned:
                first_observation = cleaned
                break
        if first_observation:
            paragraphs.append(
                f"今天留下的观察虽然不算多，但能看出来主要都围绕“{first_observation}”这一类事情在推进。"
            )
        else:
            paragraphs.append(
                "今天留下来的记录比较零散，暂时还拼不出特别完整的长篇感想，但能感觉到用户一直在认真推进手头的事。"
            )

    if len(paragraphs) == 1:
        paragraphs.append("先把最明显的脉络记下来，至少明天回看时还能迅速接上今天的节奏。")

    return "\n\n".join(paragraphs[:3]).strip()


def ensure_diary_reflection_text(
    host: Any,
    reflection_text: str,
    observation_text: str,
    structured_summary: dict[str, Any] | None = None,
) -> str:
    cleaned = sanitize_diary_section_text(reflection_text)
    if cleaned:
        return cleaned
    return build_diary_reflection_fallback(
        observation_text=observation_text,
        structured_summary=structured_summary,
    )


def extract_diary_preview_text(diary_content: str) -> str:
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
        section_text = sanitize_diary_section_text(match.group(1))
        if section_text:
            return section_text[:500]

    lines = []
    skip_patterns = [
        re.compile(r"^\s*#\s*.+日记\s*$"),
        re.compile(r"^\s*##\s*\d{4}年\d{1,2}月\d{1,2}日.*$"),
        re.compile(r"^\s*\*\*天气\*\*:\s*.*$"),
        re.compile(r"^\s*##\s*今日概览\s*$"),
        re.compile(r"^\s*##\s*今日观察\s*$"),
        re.compile(r"^\s*##\s*今日感想\s*$"),
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


def remember_diary_summary_memories(
    host: Any,
    target_date: datetime.date,
    structured_summary: dict[str, Any],
) -> None:
    if not isinstance(structured_summary, dict):
        return

    diary_date = target_date.isoformat()
    main_windows = structured_summary.get("main_windows", []) or []
    for item in main_windows[:3]:
        window_title = normalize_window_title(item.get("window_title", ""))
        if not window_title:
            continue
        duration_minutes = int(item.get("duration_minutes", 0) or 0)
        focus_text = extract_memory_focus(item.get("focus", ""), max_length=56)
        summary = f"{diary_date} 主要停留在《{window_title}》约 {duration_minutes} 分钟"
        if focus_text:
            summary += f"，当时在处理：{focus_text}"
        remember_episodic_memory(
            host,
            scene="",
            active_window=window_title,
            summary=summary,
            kind="diary_summary",
        )
        if focus_text:
            remember_focus_pattern(
                host,
                scene="",
                active_window=window_title,
                summary=focus_text,
            )

    longest_task = structured_summary.get("longest_task", {}) or {}
    if isinstance(longest_task, dict) and longest_task.get("window_title"):
        longest_summary = (
            f"{diary_date} 最长停留任务是《{longest_task.get('window_title')}》"
        )
        focus_text = extract_memory_focus(longest_task.get("focus", ""), max_length=56)
        if focus_text:
            longest_summary += f"，主要在：{focus_text}"
        remember_episodic_memory(
            host,
            scene="",
            active_window=str(longest_task.get("window_title", "") or ""),
            summary=longest_summary,
            kind="diary_summary",
        )

    repeated_focuses = structured_summary.get("repeated_focuses", []) or []
    for item in repeated_focuses[:3]:
        note_text = extract_memory_focus(item.get("note", ""), max_length=48)
        window_title = normalize_window_title(item.get("window_title", ""))
        if not note_text:
            continue
        remember_focus_pattern(
            host,
            scene="",
            active_window=window_title,
            summary=note_text,
        )


def render_diary_message_to_png(diary_message: str) -> str:
    """将日记文本渲染为 PNG 临时文件路径。"""
    import tempfile

    from PIL import Image, ImageDraw, ImageFont

    font_size = 18
    line_height = int(font_size * 1.8)
    title_font_size = 24
    padding = 60
    max_width = 850

    chinese_fonts = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/STZHONGS.TTF",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]

    font = None
    for font_path in chinese_fonts:
        try:
            font = ImageFont.truetype(font_path, font_size)
            test_draw = ImageDraw.Draw(Image.new("RGB", (100, 100)))
            test_draw.text((0, 0), "测试中文", font=font)
            break
        except Exception:
            continue

    title_font = None
    for font_path in chinese_fonts:
        try:
            title_font = ImageFont.truetype(font_path, title_font_size)
            break
        except Exception:
            continue

    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

    if title_font is None:
        title_font = font

    def get_text_width(text, use_title_font=False):
        if use_title_font and title_font:
            return title_font.getlength(text)
        if font:
            return font.getlength(text)
        return len(text) * font_size

    lines = []
    max_text_width = max_width - padding * 2
    title_count = 0

    for paragraph in diary_message.split("\n"):
        if not paragraph:
            lines.append("")
            continue

        current_line = ""
        for char in paragraph:
            test_line = current_line + char
            if get_text_width(test_line) <= max_text_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = char
        if current_line:
            lines.append(current_line)
            if current_line.startswith("#") and "日记" in current_line:
                title_count += 1

    title_extra_height = title_count * 10
    total_height = padding * 2 + len(lines) * line_height + title_extra_height + 30
    total_height = max(400, total_height)
    image = Image.new("RGB", (max_width, total_height), color=(255, 254, 250))
    draw = ImageDraw.Draw(image)

    border_color = (180, 160, 140)
    border_width = 3
    border_padding = 15
    draw.rectangle(
        [
            (padding - border_padding, padding - border_padding),
            (max_width - padding + border_padding, total_height - padding + border_padding),
        ],
        outline=border_color,
        width=border_width,
    )

    draw.line(
        [(padding, padding + 40), (max_width - padding, padding + 40)],
        fill=border_color,
        width=1,
    )

    y = padding
    for line in lines:
        if line.startswith("#") and "日记" in line:
            title_width = get_text_width(line, use_title_font=True)
            title_x = (max_width - title_width) // 2
            draw.text((title_x, y), line, fill=(139, 69, 19), font=title_font)
            y += line_height + 10
        elif line and line[0].isdigit() and "年" in line:
            date_width = get_text_width(line)
            date_x = (max_width - date_width) // 2
            draw.text((date_x, y), line, fill=(100, 100, 100), font=font)
            y += line_height + 5
        else:
            if line.strip():
                if len(lines) > 0 and lines.index(line) > 0 and lines[lines.index(line) - 1].strip() == "":
                    draw.text((padding + 20, y), line, fill=(60, 60, 60), font=font)
                else:
                    draw.text((padding, y), line, fill=(60, 60, 60), font=font)
            y += line_height

    temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    image.save(temp_file, format="PNG", quality=95)
    temp_file.close()

    return temp_file.name
