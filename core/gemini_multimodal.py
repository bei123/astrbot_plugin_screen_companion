"""Gemini 官方 Files / generateContent 与 AstrBot provider 直连多模态回退。"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
import time
import uuid
from types import SimpleNamespace
from typing import Any, Awaitable, cast

import aiohttp
from astrbot.api import logger

DEFAULT_GEMINI_API_BASE = "https://generativelanguage.googleapis.com"


def build_data_url(media_bytes: bytes, mime_type: str) -> str:
    base64_data = base64.b64encode(media_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{base64_data}"


def get_astrbot_config_candidates() -> list[str]:
    home_dir = os.path.expanduser("~")
    data_dir = os.path.join(home_dir, ".astrbot", "data")
    candidates = [
        os.path.join(data_dir, "cmd_config.json"),
    ]

    config_dir = os.path.join(data_dir, "config")
    if os.path.isdir(config_dir):
        try:
            abconf_files = [
                os.path.join(config_dir, name)
                for name in os.listdir(config_dir)
                if name.startswith("abconf_") and name.endswith(".json")
            ]
            abconf_files.sort(
                key=lambda path: os.path.getmtime(path),
                reverse=True,
            )
            candidates = abconf_files + candidates
        except Exception as e:
            logger.debug(f"读取 AstrBot 配置列表失败: {e}")

    return candidates


def load_astrbot_provider_registry() -> dict[str, Any]:
    for path in get_astrbot_config_candidates():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and (
                isinstance(data.get("provider"), list)
                or isinstance(data.get("provider_sources"), list)
            ):
                return data
        except Exception as e:
            logger.debug(f"读取 AstrBot provider 配置失败 {path}: {e}")
    return {}


def looks_like_gemini_model(model_name: str) -> bool:
    return "gemini" in str(model_name or "").strip().lower()


def is_official_gemini_api_base(api_base: str) -> bool:
    normalized = str(api_base or "").strip().lower()
    return "generativelanguage.googleapis.com" in normalized


async def get_current_chat_provider_id(host: Any, umo: str | None = None) -> str:
    try:
        getter = getattr(host.context, "get_current_chat_provider_id", None)
        if getter and callable(getter):
            result = getter(umo=umo)
            if inspect.isawaitable(result):
                provider_id = await cast(Awaitable[Any], result)
            else:
                provider_id = result
            return str(provider_id or "").strip()
    except Exception as e:
        logger.debug(f"获取当前聊天 provider_id 失败: {e}")
    return ""


def resolve_provider_runtime_info(
    host: Any,
    provider_id: str = "",
    provider=None,
) -> dict[str, Any]:
    registry = load_astrbot_provider_registry()
    provider_entries = registry.get("provider", []) or []
    provider_sources = registry.get("provider_sources", []) or []
    provider_settings = registry.get("provider_settings", {}) or {}

    current_provider_id = str(provider_id or "").strip()
    if not current_provider_id:
        current_provider_id = str(
            provider_settings.get("default_provider_id", "") or ""
        ).strip()

    model_name = ""
    provider_entry = None
    if current_provider_id:
        provider_entry = next(
            (
                item
                for item in provider_entries
                if str(item.get("id", "") or "").strip() == current_provider_id
            ),
            None,
        )

    if provider_entry is None and provider is not None:
        for attr_name in ("model", "model_name", "provider_id", "id"):
            attr_value = getattr(provider, attr_name, None)
            if not attr_value:
                continue
            attr_str = str(attr_value).strip()
            if not model_name:
                model_name = attr_str
            matched = next(
                (
                    item
                    for item in provider_entries
                    if attr_str
                    and (
                        str(item.get("id", "") or "").strip() == attr_str
                        or str(item.get("model", "") or "").strip() == attr_str
                    )
                ),
                None,
            )
            if matched is not None:
                provider_entry = matched
                current_provider_id = str(matched.get("id", "") or "").strip()
                break

    if provider_entry is not None and not model_name:
        model_name = str(provider_entry.get("model", "") or "").strip()

    provider_source_id = ""
    api_base = ""
    api_key = ""
    if provider_entry is not None:
        provider_source_id = str(provider_entry.get("provider_source_id", "") or "").strip()
        source_entry = next(
            (
                item
                for item in provider_sources
                if str(item.get("id", "") or "").strip() == provider_source_id
            ),
            None,
        )
        if source_entry:
            api_base = str(source_entry.get("api_base", "") or "").strip()
            key_list = source_entry.get("key", []) or []
            if key_list:
                api_key = str(key_list[0] or "").strip()

    env_api_key = str(os.environ.get("GEMINI_API_KEY") or "").strip()
    env_api_base = str(os.environ.get("GEMINI_API_BASE") or "").strip()
    if env_api_key:
        api_key = env_api_key
    if env_api_base:
        api_base = env_api_base

    gemini_base = getattr(host, "GEMINI_API_BASE", DEFAULT_GEMINI_API_BASE)
    if not api_base and api_key and looks_like_gemini_model(model_name):
        api_base = gemini_base

    return {
        "provider_id": current_provider_id,
        "model": model_name,
        "api_base": api_base,
        "api_key": api_key,
        "provider_source_id": provider_source_id,
    }


async def supports_native_gemini_video_audio(
    host: Any,
    *,
    provider=None,
    umo: str | None = None,
) -> bool:
    try:
        provider_id = await get_current_chat_provider_id(host, umo=umo)
        runtime = resolve_provider_runtime_info(host, provider_id=provider_id, provider=provider)
        model_name = str(runtime.get("model", "") or "").strip()
        api_key = str(runtime.get("api_key", "") or "").strip()
        api_base = str(runtime.get("api_base", "") or "").strip()
        return bool(
            looks_like_gemini_model(model_name)
            and api_key
            and is_official_gemini_api_base(api_base)
        )
    except Exception as e:
        logger.debug(f"判断 Gemini 原生视频能力失败: {e}")
        return False


async def gemini_upload_file(
    *,
    api_base: str,
    api_key: str,
    media_bytes: bytes,
    mime_type: str,
    display_name: str,
) -> dict[str, Any]:
    start_headers = {
        "x-goog-api-key": api_key,
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(len(media_bytes)),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json",
    }
    start_payload = {"file": {"display_name": display_name}}
    start_url = f"{api_base.rstrip('/')}/upload/v1beta/files"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            start_url,
            headers=start_headers,
            json=start_payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            response.raise_for_status()
            upload_url = response.headers.get("X-Goog-Upload-URL") or response.headers.get(
                "x-goog-upload-url"
            )
            if not upload_url:
                raise RuntimeError("Gemini Files API 未返回上传地址。")

        upload_headers = {
            "x-goog-api-key": api_key,
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
            "Content-Length": str(len(media_bytes)),
        }
        async with session.post(
            upload_url,
            headers=upload_headers,
            data=media_bytes,
            timeout=aiohttp.ClientTimeout(total=180),
        ) as response:
            response.raise_for_status()
            result = await response.json()
    return result.get("file", result)


async def gemini_wait_file_active(
    host: Any,
    *,
    api_base: str,
    api_key: str,
    file_name: str,
) -> dict[str, Any]:
    endpoint = file_name if str(file_name).startswith("files/") else f"files/{file_name}"
    url = f"{api_base.rstrip('/')}/v1beta/{endpoint}"
    poll_timeout = float(getattr(host, "GEMINI_FILE_POLL_TIMEOUT_SECONDS", 120))
    poll_interval = float(getattr(host, "GEMINI_FILE_POLL_INTERVAL_SECONDS", 2))
    deadline = time.time() + poll_timeout

    async with aiohttp.ClientSession() as session:
        while time.time() < deadline:
            async with session.get(
                url,
                headers={"x-goog-api-key": api_key},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                response.raise_for_status()
                result = await response.json()

            state = str(
                ((result.get("state") or {}) if isinstance(result.get("state"), dict) else {})
                .get("name", result.get("state", ""))
                or ""
            ).upper()
            if state == "ACTIVE":
                return result
            if state == "FAILED":
                raise RuntimeError("Gemini Files API 处理视频失败。")
            await asyncio.sleep(poll_interval)

    raise RuntimeError("Gemini Files API 处理视频超时。")


async def gemini_delete_file(
    *,
    api_base: str,
    api_key: str,
    file_name: str,
) -> None:
    endpoint = file_name if str(file_name).startswith("files/") else f"files/{file_name}"
    url = f"{api_base.rstrip('/')}/v1beta/{endpoint}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                url,
                headers={"x-goog-api-key": api_key},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status not in {200, 204}:
                    logger.debug(f"删除 Gemini 临时文件失败: HTTP {response.status}")
    except Exception as e:
        logger.debug(f"删除 Gemini 临时文件失败: {e}")


def extract_text_from_gemini_response(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for candidate in payload.get("candidates", []) or []:
        content = candidate.get("content", {}) or {}
        for part in content.get("parts", []) or []:
            text = str(part.get("text", "") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


async def call_native_gemini_multimodal(
    host: Any,
    *,
    provider_id: str,
    provider,
    interaction_prompt: str,
    system_prompt: str,
    media_bytes: bytes,
    media_kind: str,
    mime_type: str,
):
    runtime = resolve_provider_runtime_info(host, provider_id=provider_id, provider=provider)
    model_name = str(runtime.get("model", "") or "").strip()
    api_key = str(runtime.get("api_key", "") or "").strip()
    api_base = str(runtime.get("api_base", "") or "").strip()

    if not (
        looks_like_gemini_model(model_name)
        and api_key
        and is_official_gemini_api_base(api_base)
    ):
        return None

    if not interaction_prompt.strip():
        raise RuntimeError("Gemini 原生多模态调用缺少提示词。")

    uploaded_file_name = ""
    try:
        if media_kind == "video":
            uploaded_file = await gemini_upload_file(
                api_base=api_base,
                api_key=api_key,
                media_bytes=media_bytes,
                mime_type=mime_type,
                display_name=f"screen-companion-{uuid.uuid4()}.mp4",
            )
            uploaded_file_name = str(uploaded_file.get("name", "") or "").strip()
            file_info = await gemini_wait_file_active(
                host,
                api_base=api_base,
                api_key=api_key,
                file_name=uploaded_file_name,
            )
            media_part = {
                "file_data": {
                    "mime_type": mime_type,
                    "file_uri": str(file_info.get("uri", "") or "").strip(),
                }
            }
        else:
            media_part = {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(media_bytes).decode("utf-8"),
                }
            }

        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        media_part,
                        {"text": interaction_prompt},
                    ],
                }
            ]
        }
        if system_prompt.strip():
            payload["system_instruction"] = {
                "parts": [{"text": system_prompt}],
            }

        url = f"{api_base.rstrip('/')}/v1beta/models/{model_name}:generateContent"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as response:
                response.raise_for_status()
                result = await response.json()

        response_text = extract_text_from_gemini_response(result)
        if not response_text:
            raise RuntimeError("Gemini 原生多模态返回为空。")
        return SimpleNamespace(completion_text=response_text)
    finally:
        if uploaded_file_name:
            await gemini_delete_file(
                api_base=api_base,
                api_key=api_key,
                file_name=uploaded_file_name,
            )


async def call_provider_multimodal_direct(
    host: Any,
    provider,
    interaction_prompt: str,
    system_prompt: str,
    media_bytes: bytes,
    media_kind: str = "image",
    mime_type: str = "image/jpeg",
    provider_id: str = "",
):
    native_response = await call_native_gemini_multimodal(
        host,
        provider_id=provider_id,
        provider=provider,
        interaction_prompt=interaction_prompt,
        system_prompt=system_prompt,
        media_bytes=media_bytes,
        media_kind=media_kind,
        mime_type=mime_type,
    )
    if native_response is not None:
        return native_response

    if media_kind == "video" and not host._coerce_bool(
        getattr(host, "allow_unsafe_video_direct_fallback", False)
    ):
        raise RuntimeError(
            "当前 provider 不支持原生视频上传，已拦截视频直发以避免过度消耗 token。"
            "请开启外部视觉 API，或切换到官方 Gemini API 并配置 GEMINI_API_KEY。"
        )
    if media_kind == "video":
        logger.warning(
            "当前 provider 不支持原生视频上传，但已按配置允许回退到兼容视频直发。"
            "这可能导致请求体很大，并带来较高的 token 消耗。"
        )

    data_url = build_data_url(media_bytes, mime_type)
    multimodal_contexts = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": interaction_prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                },
            ],
        }
    ]

    try:
        return await provider.text_chat(
            prompt="",
            system_prompt=system_prompt,
            contexts=multimodal_contexts,
        )
    except TypeError:
        if media_kind == "image":
            return await provider.text_chat(
                prompt=interaction_prompt,
                system_prompt=system_prompt,
                image_urls=[data_url],
            )
        raise RuntimeError(
            "当前 AstrBot provider 不支持直接视频多模态上下文，请开启外部视觉 API。"
        )
