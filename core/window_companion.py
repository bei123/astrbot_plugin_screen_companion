"""窗口陪伴：配置解析、窗口枚举与匹配、会话启停与后台巡检循环。"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

from .persona import get_end_response, get_start_response


def parse_window_companion_targets(raw_text: str) -> list[dict[str, Any]]:
    """Parse window companion rules from config text."""
    parsed: list[dict[str, Any]] = []
    raw_text = str(raw_text or "").strip()
    if not raw_text:
        return parsed

    for line in raw_text.splitlines():
        entry = line.strip()
        if not entry:
            continue

        keyword, prompt = entry, ""
        if "|" in entry:
            keyword, prompt = entry.split("|", 1)

        keyword = keyword.strip()
        prompt = prompt.strip()
        if not keyword:
            continue

        parsed.append(
            {
                "keyword": keyword,
                "keyword_lower": keyword.casefold(),
                "prompt": prompt,
            }
        )

    return parsed


def list_open_window_titles(host: Any) -> list[str]:
    """Return de-duplicated open window titles."""
    try:
        import pygetwindow
    except ImportError:
        return []
    except Exception as e:
        logger.debug(f"读取窗口列表失败: {e}")
        return []

    raw_titles = []
    try:
        raw_titles = list(pygetwindow.getAllTitles())
    except Exception:
        try:
            raw_titles = [getattr(window, "title", "") for window in pygetwindow.getAllWindows()]
        except Exception as e:
            logger.debug(f"读取窗口标题失败: {e}")
            return []

    titles = []
    seen = set()
    for title in raw_titles:
        normalized = host._normalize_window_title(title)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        titles.append(normalized)
    return titles


def match_window_companion_target(
    host: Any,
    window_titles: list[str],
) -> tuple[dict[str, Any] | None, str]:
    """Find the first configured window companion rule that matches."""
    if not window_titles or not getattr(host, "parsed_window_companion_targets", None):
        return None, ""

    for rule in host.parsed_window_companion_targets:
        keyword = rule.get("keyword_lower", "")
        if not keyword:
            continue
        for title in window_titles:
            if keyword in str(title or "").casefold():
                return rule, title
    return None, ""


def build_window_companion_prompt(window_title: str, extra_prompt: str = "") -> str:
    """Build a focused prompt for window companion sessions."""
    pieces = [
        f"这是你被指定要陪伴的窗口：《{window_title}》。",
        "请更关注这个窗口里的当前任务、卡点和下一步，不要泛泛播报画面。",
        "如果适合给建议，优先给和当前任务直接相关、能立刻派上用场的建议。",
        "保持对话的连续性，关注用户的任务进展，提供具体的建议。",
        "注意观察窗口内容的变化，及时调整你的回应，确保与当前场景相关。",
        "如果发现用户遇到困难，提供具体的解决方案和步骤指导。",
    ]
    if extra_prompt:
        pieces.append(extra_prompt.strip())
    return "\n".join(piece for piece in pieces if piece)


def is_window_companion_session_active(host: Any) -> bool:
    task_id = getattr(host, "WINDOW_COMPANION_TASK_ID", "")
    task = (getattr(host, "auto_tasks", {}) or {}).get(task_id)
    return bool(task and not task.done())


async def start_window_companion_session(host: Any, window_title: str, rule: dict) -> bool:
    """Start automatic companion mode for a matched window."""
    host._ensure_runtime_state()
    if not host.enabled or not host.enable_window_companion:
        return False
    if is_window_companion_session_active(host):
        return False

    target = host._get_default_target()
    if not target:
        logger.warning("窗口陪伴已匹配到目标窗口，但没有可用的主动消息目标，已跳过启动")
        return False

    ok, err_msg = host._check_env(check_mic=False)
    if not ok:
        logger.warning(f"窗口陪伴启动失败: {err_msg}")
        return False

    task_id = getattr(host, "WINDOW_COMPANION_TASK_ID", "window_companion_auto")
    event = host._create_virtual_event(target)
    host.window_companion_active_title = window_title
    host.window_companion_active_target = target
    host.window_companion_active_rule = dict(rule or {})
    host.is_running = True
    host.state = "active"
    host.auto_tasks[task_id] = asyncio.create_task(
        host._auto_screen_task(
            event,
            task_id=task_id,
            custom_prompt=build_window_companion_prompt(
                window_title, (rule or {}).get("prompt", "")
            ),
        )
    )

    start_response = await get_start_response(host, target)
    intro = f"检测到《{window_title}》已经打开，我来陪你。"
    await host._send_plain_message(target, f"{intro}\n{start_response}".strip())
    logger.info(f"窗口陪伴已启动: {window_title}")
    return True


async def stop_window_companion_session(host: Any, reason: str = "window_closed") -> bool:
    """Stop the automatic companion session for the matched window."""
    host._ensure_runtime_state()
    task_id = getattr(host, "WINDOW_COMPANION_TASK_ID", "window_companion_auto")
    task = (getattr(host, "auto_tasks", {}) or {}).get(task_id)
    if not task and not getattr(host, "window_companion_active_title", ""):
        return False

    active_title = str(getattr(host, "window_companion_active_title", "") or "").strip()
    target = str(getattr(host, "window_companion_active_target", "") or "").strip()

    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("等待窗口陪伴任务停止超时")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"停止窗口陪伴任务失败: {e}")

    host.auto_tasks.pop(task_id, None)
    host.window_companion_active_title = ""
    host.window_companion_active_target = ""
    host.window_companion_active_rule = {}

    if not host.auto_tasks:
        host.is_running = False
        host.state = "inactive"

    if target and active_title:
        end_response = await get_end_response(host, target)
        if reason == "disabled":
            outro = f"《{active_title}》的窗口陪伴已经关闭，我先退到旁边。"
        else:
            outro = f"《{active_title}》已经关掉了，我先退到旁边。"
        await host._send_plain_message(target, f"{outro}\n{end_response}".strip())

    logger.info(f"窗口陪伴已停止: {active_title or 'unknown'} ({reason})")
    return True


async def run_window_companion_task(host: Any) -> None:
    """Watch configured windows and start or stop companion sessions automatically."""
    host._ensure_runtime_state()
    while host.running and host._is_current_process_instance():
        interval = max(2, int(getattr(host, "window_companion_check_interval", 5) or 5))
        try:
            if not host.enable_window_companion or not getattr(
                host, "parsed_window_companion_targets", None
            ):
                if is_window_companion_session_active(host) or getattr(
                    host, "window_companion_active_title", ""
                ):
                    await stop_window_companion_session(host, reason="disabled")
                await asyncio.sleep(interval)
                continue

            window_titles = list_open_window_titles(host)
            matched_rule, matched_title = match_window_companion_target(host, window_titles)
            active_title = str(getattr(host, "window_companion_active_title", "") or "").strip()
            active_exists = bool(
                active_title
                and any(active_title.casefold() == title.casefold() for title in window_titles)
            )

            if matched_rule and matched_title and not is_window_companion_session_active(host):
                await start_window_companion_session(host, matched_title, matched_rule)
            elif is_window_companion_session_active(host) and not active_exists:
                await stop_window_companion_session(host, reason="window_closed")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"窗口陪伴监测异常: {e}")

        await asyncio.sleep(interval)
