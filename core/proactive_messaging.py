"""主动消息：目标解析、规范化、MessageChain 构建与发送。"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import BaseMessageComponent, Plain

from .config import PluginConfig


class VirtualProactiveEvent:
    """主动任务用的轻量事件替身，仅需 unified_msg_origin 与 config。"""

    __slots__ = ("unified_msg_origin", "config")

    def __init__(self, unified_msg_origin: str, config: PluginConfig) -> None:
        self.unified_msg_origin = unified_msg_origin
        self.config = config


def get_available_platforms(host: Any) -> list[Any]:
    """Return loaded platform instances, preferring non-webchat adapters."""
    platform_manager = getattr(host.context, "platform_manager", None)
    if not platform_manager:
        return []

    platforms = list(getattr(platform_manager, "platform_insts", []) or [])
    if not platforms:
        return []

    filtered = []
    for platform in platforms:
        try:
            meta = platform.meta()
            if str(getattr(meta, "name", "") or "").strip() == "webchat":
                continue
        except Exception:
            pass
        filtered.append(platform)
    return filtered or platforms


def get_preferred_platform_id(host: Any) -> str:
    """Resolve the platform instance ID used for proactive messages."""
    platforms = get_available_platforms(host)
    if platforms:
        try:
            platform_id = str(getattr(platforms[0].meta(), "id", "") or "").strip()
            if platform_id:
                return platform_id
        except Exception as e:
            logger.debug(f"获取默认平台 ID 失败: {e}")
    return "default"


def build_private_target(host: Any, session_id: str) -> str:
    """Build a private-chat target with the active platform instance ID."""
    session_id = str(session_id or "").strip()
    if not session_id:
        return ""
    return f"{get_preferred_platform_id(host)}:FriendMessage:{session_id}"


def normalize_target(host: Any, target: str) -> str:
    """Rewrite legacy proactive targets to the active platform instance ID."""
    target = str(target or "").strip()
    if not target:
        return ""

    parts = target.split(":", 2)
    if len(parts) != 3:
        return target

    platform_token, message_type, session_id = parts
    platforms = get_available_platforms(host)
    if not platforms:
        return target

    for platform in platforms:
        try:
            meta = platform.meta()
            platform_id = str(getattr(meta, "id", "") or "").strip()
            platform_name = str(getattr(meta, "name", "") or "").strip()
        except Exception:
            continue

        if platform_token in {platform_id, platform_name}:
            normalized = f"{platform_id}:{message_type}:{session_id}"
            if normalized != target:
                logger.info(f"主动消息目标已规范化: {target} -> {normalized}")
            return normalized

    legacy_platform_tokens = {
        "default",
        "aiocqhttp",
        "qq_official",
        "qq_official_webhook",
        "telegram",
        "discord",
        "wecom",
        "wecom_ai_bot",
        "weixin_official_account",
        "line",
        "kook",
        "satori",
        "lark",
        "dingtalk",
        "misskey",
        "slack",
    }
    if len(platforms) == 1 and platform_token in legacy_platform_tokens:
        try:
            platform_id = str(getattr(platforms[0].meta(), "id", "") or "").strip()
        except Exception:
            platform_id = ""
        if platform_id:
            normalized = f"{platform_id}:{message_type}:{session_id}"
            if normalized != target:
                logger.info(f"主动消息目标已回退到当前平台实例 ID: {target} -> {normalized}")
            return normalized

    return target


def get_default_target(host: Any) -> str:
    """Resolve the proactive message target."""
    target = str(getattr(host, "proactive_target", "") or "").strip()
    if target:
        return normalize_target(host, target)

    admin_qq = str(getattr(host, "admin_qq", "") or "").strip()
    if admin_qq:
        return build_private_target(host, admin_qq)
    return ""


def create_virtual_event(host: Any, target: str) -> VirtualProactiveEvent:
    """Build a lightweight virtual event for proactive tasks."""
    return VirtualProactiveEvent(
        unified_msg_origin=normalize_target(host, target),
        config=host.plugin_config,
    )


def resolve_proactive_target(host: Any, fallback_event: Any = None) -> str:
    target = get_default_target(host)
    if not target and fallback_event is not None:
        try:
            target = str(getattr(fallback_event, "unified_msg_origin", "") or "").strip()
        except Exception as e:
            logger.debug(f"读取回退主动消息目标失败: {e}")
    return normalize_target(host, target)


def build_message_chain(
    components: list[BaseMessageComponent] | None,
) -> MessageChain:
    chain = MessageChain()
    for comp in components or []:
        chain.chain.append(comp)
    return chain


def extract_plain_text(
    components: list[BaseMessageComponent] | None,
) -> str:
    chunks: list[str] = []
    for comp in components or []:
        if isinstance(comp, Plain):
            text = str(getattr(comp, "text", "") or "")
            if text:
                chunks.append(text)
    return "".join(chunks)


async def send_proactive_message(
    host: Any,
    target: str,
    message_chain: MessageChain,
) -> bool:
    """Send a proactive message via the resolved platform instance."""
    target = normalize_target(host, target)
    if not target:
        return False

    session = None
    try:
        from astrbot.core.platform.message_session import MessageSesion

        session = MessageSesion.from_str(target)
    except Exception as e:
        logger.debug(f"解析主动消息目标失败，将回退到 context.send_message: {e}")

    if session is not None:
        platforms = get_available_platforms(host)
        matched_platform = None
        for platform in platforms:
            try:
                meta = platform.meta()
                platform_id = str(getattr(meta, "id", "") or "").strip()
                platform_name = str(getattr(meta, "name", "") or "").strip()
            except Exception:
                continue
            if session.platform_name in {platform_id, platform_name}:
                matched_platform = platform
                if session.platform_name != platform_id:
                    session = MessageSesion(
                        platform_id, session.message_type, session.session_id
                    )
                break

        if matched_platform is None and platforms:
            matched_platform = platforms[0]
            try:
                fallback_platform_id = str(
                    getattr(matched_platform.meta(), "id", "") or ""
                ).strip()
                if fallback_platform_id:
                    session = MessageSesion(
                        fallback_platform_id,
                        session.message_type,
                        session.session_id,
                    )
                    logger.info(
                        f"主动消息目标未命中平台，已回退为 {fallback_platform_id}:{session.message_type.value}:{session.session_id}"
                    )
            except Exception as e:
                logger.debug(f"构造主动消息回退会话失败: {e}")

        if matched_platform is not None:
            try:
                await matched_platform.send_by_session(session, message_chain)
                return True
            except Exception as e:
                logger.warning(f"主动消息直发失败，将回退到 context.send_message: {e}")

    try:
        await host._as_context().send_message(target, message_chain)
        return True
    except Exception as e:
        logger.error(f"发送主动消息失败: {e}")
        return False


async def send_plain_message(host: Any, target: str, text: str) -> bool:
    """Send a plain proactive message if possible."""
    target = str(target or "").strip()
    text = str(text or "").strip()
    if not target or not text:
        return False

    return await send_proactive_message(
        host, target, MessageChain([Plain(text)])
    )


async def send_component_text(
    host: Any,
    target: str,
    components: list[BaseMessageComponent] | None,
    *,
    prefix: str = "",
) -> bool:
    text = extract_plain_text(components)
    if not text:
        return False
    if prefix:
        text = f"{prefix}\n{text}"
    return await send_plain_message(host, target, text)


async def send_segmented_text(
    host: Any,
    target: str,
    text: str,
    *,
    max_length: int = 1000,
    delay_seconds: float = 0.5,
    should_continue: Any = None,
) -> bool:
    target = str(target or "").strip()
    text = str(text or "").strip()
    if not target or not text:
        return False

    segments = host._split_message(text, max_length=max_length)
    if not segments:
        return False

    sent = False
    for index, segment in enumerate(segments):
        if should_continue is not None and not should_continue():
            break
        if not segment.strip():
            continue
        sent = await send_plain_message(host, target, segment) or sent
        if index < len(segments) - 1 and delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
    return sent
