"""人格系统提示、自动观察起止话术与隐私提示拼接。"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from astrbot.api import logger

DEFAULT_SYSTEM_PROMPT = """
你是一个会陪用户一起看屏幕、一起推进当下任务的屏幕伙伴。
请自然、克制、具体地回应用户，优先给当前任务真正有帮助的观察、判断和建议，避免机械播报和空泛说教。
"""

FALLBACK_PERSONA_PROMPT = (
    "角色设定：窥屏助手\n请在 AstrBot 人格设定管理器中配置默认人格（docs.astrbot.app）。"
)

_PRIVACY_GUARD_SUFFIX = (
    "\n\n补充要求：请注意保护隐私，不要逐字复述屏幕中可能出现的密码、验证码、"
    "身份证号、完整手机号、银行卡号等敏感信息；如与任务无关可概括或跳过。"
)


def _privacy_guard_enabled(host: Any) -> bool:
    fn = getattr(host, "_coerce_bool", None)
    raw = getattr(host, "enable_privacy_guard", True)
    if callable(fn):
        return cast(bool, fn(raw))
    return bool(raw)


def append_privacy_guard_prompt(host: Any, prompt: str) -> str:
    """在提示词末尾注入隐私保护要求（受 enable_privacy_guard 控制）。"""
    body = str(prompt or "").strip()
    if not body:
        return body
    if not _privacy_guard_enabled(host):
        return body
    return f"{body}{_PRIVACY_GUARD_SUFFIX}"


def build_start_end_prompt(raw_prompt: str, action: str) -> str:
    """为开始/结束消息补充更明确的人格化约束。"""
    base_prompt = str(raw_prompt or "").strip()
    if not base_prompt:
        if action == "start":
            base_prompt = "以你的性格向用户表达你会开始偶尔地陪着用户看屏幕了。"
        else:
            base_prompt = "以你的性格向用户表达你会先暂停看屏幕、退到旁边等用户再叫你。"

    supplemental = (
        "\n额外要求："
        "回复必须明显带有人格，不要像系统提示、状态播报或功能开关通知。"
        "语气要自然、亲近、有人味，像这个角色本人在开口。"
        "避免使用“已开始”“已停止”“任务已启动”“任务已结束”这种机械措辞。"
        "尽量简短，控制在 1 到 2 句话内。"
        "允许有一点角色感、小情绪或亲昵感，但不要夸张，也不要说得像说明书。"
    )
    return f"{base_prompt.rstrip()}{supplemental}"


async def get_persona_prompt(host: Any, umo: str | None = None) -> str:
    """屏幕伴侣系统提示：优先 AstrBot 人格，其次陪伴模式 / 配置 / 默认文案。"""
    base_prompt = ""
    persona_mgr = getattr(host.context, "persona_manager", None)
    if persona_mgr is not None:
        try:
            persona = await persona_mgr.get_default_persona_v3(umo=umo)
            if persona is not None:
                if isinstance(persona, dict):
                    raw_p = str(persona.get("prompt", "") or "").strip()
                else:
                    raw_p = str(getattr(persona, "prompt", "") or "").strip()
                if raw_p:
                    base_prompt = raw_p
        except Exception as e:
            logger.debug(f"获取默认人格失败: {e}")

    if getattr(host, "use_companion_mode", False):
        companion_prompt = getattr(host, "companion_prompt", None)
        if companion_prompt:
            companion_supplemental_guide = (
                "\n\n额外要求：保持对话的连续性，关注用户的任务进展，提供具体、实用的建议。"
                "你可以偶尔轻轻表达自己也想和用户一起看点内容、玩一局游戏或做个小测试，"
                "但必须低频、自然，不要打断正事，更不能凭空捏造共同经历。"
            )
            return f"{str(companion_prompt).rstrip()}{companion_supplemental_guide}"

    if not base_prompt:
        config_prompt = str(getattr(host, "system_prompt", "") or "").strip()
        if config_prompt:
            base_prompt = config_prompt
        elif persona_mgr is None:
            base_prompt = FALLBACK_PERSONA_PROMPT

    if not base_prompt:
        base_prompt = DEFAULT_SYSTEM_PROMPT

    supplemental_guide = (
        "\n\n额外要求：少用旁白式开场，不要总是先叫用户名字。"
        "如果能提出建议，优先给和当前任务直接相关、能立刻用上的建议。"
        "可以偶尔表达自己也想和用户一起做点什么，但只限轻松自然的一句，"
        "并且任何共同经历都只能基于当前对话或已记录内容，不能虚构。"
    )

    return f"{base_prompt.rstrip()}{supplemental_guide}"


async def get_start_response(host: Any, umo: str | None = None) -> str:
    """自动观察开启时发给用户的开场话术。"""
    mode = "llm" if getattr(host, "use_llm_for_start_end", True) else "preset"
    if mode == "preset":
        return str(getattr(host, "start_preset", "") or "")
    provider = host._as_context().get_using_provider()
    if provider:
        try:
            system_prompt = await get_persona_prompt(host, umo)
            prompt = build_start_end_prompt(
                str(getattr(host, "start_llm_prompt", "") or ""),
                action="start",
            )
            response = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, system_prompt=system_prompt),
                timeout=60.0,
            )
            if response and hasattr(response, "completion_text") and response.completion_text:
                return response.completion_text
        except asyncio.TimeoutError:
            logger.warning("LLM 生成开始回复超时，将使用预设文案")
        except Exception as e:
            logger.warning(f"Operation warning: {e}")
    fallback = str(getattr(host, "start_preset", "") or "").strip()
    return fallback or "知道啦~我会时不时过来看一眼的"


async def get_end_response(host: Any, umo: str | None = None) -> str:
    """自动观察停止时发给用户的结束话术。"""
    mode = "llm" if getattr(host, "use_llm_for_start_end", True) else "preset"
    if mode == "preset":
        return str(getattr(host, "end_preset", "") or "")
    provider = host._as_context().get_using_provider()
    if provider:
        try:
            system_prompt = await get_persona_prompt(host, umo)
            prompt = build_start_end_prompt(
                str(getattr(host, "end_llm_prompt", "") or ""),
                action="end",
            )
            response = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, system_prompt=system_prompt),
                timeout=60.0,
            )
            if response and hasattr(response, "completion_text") and response.completion_text:
                return response.completion_text
        except asyncio.TimeoutError:
            logger.warning("LLM 生成结束回复超时，将使用预设文案")
        except Exception as e:
            logger.warning(f"Operation warning: {e}")
    fallback = str(getattr(host, "end_preset", "") or "").strip()
    return fallback or "我先不打扰你了，等你需要时我再过来。"
