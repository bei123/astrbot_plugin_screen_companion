"""识屏一阶段：提示词、框架视觉、外接视觉 API、素材识别路由。"""

from __future__ import annotations

import asyncio
import base64
import inspect
import re
from typing import Any, Awaitable, cast

import aiohttp
from astrbot.api import logger

from .gemini_multimodal import get_current_chat_provider_id
from .llm_response import (
    extract_llm_completion_text,
    parse_sse_completion_text,
    strip_think_blocks,
)


def build_vision_prompt(host: Any, scene: str, active_window_title: str = "") -> str:
    base_prompt = str(getattr(host, "image_prompt", None) or "").strip()
    normalized_scene = host._normalize_scene_label(scene)
    normalized_window = host._normalize_window_title(active_window_title)

    prompt_parts: list[str] = []

    if base_prompt:
        prompt_parts.append(base_prompt)

    if normalized_window:
        prompt_parts.append(f"当前窗口标题：{normalized_window}")

    bot_self_info: list[str] = []
    if hasattr(host, "bot_appearance") and host.bot_appearance:
        bot_self_info.append(f"Bot的外形描述：{host.bot_appearance}")

    if hasattr(host, "long_term_memory") and host.long_term_memory.get("self_image"):
        self_image_memories = host.long_term_memory["self_image"]
        sorted_memories = sorted(
            self_image_memories, key=lambda x: x.get("count", 0), reverse=True
        )[:3]
        if sorted_memories:
            bot_self_info.append("关于Bot自身的已知信息：")
            for memory in sorted_memories:
                bot_self_info.append(f"- {memory['content']}")

    if bot_self_info:
        prompt_parts.extend(bot_self_info)
        prompt_parts.append("如果在屏幕中发现符合Bot外形描述的元素，请识别为Bot自己。")

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

    prompt_parts.extend(
        [
            "请对屏幕内容进行详细分析，提供以下信息：",
            "1. 屏幕的整体场景和主要内容",
            "2. 关键元素的详细信息（如文本、图像、界面元素等）",
            "3. 用户可能正在进行的任务或活动",
            "4. 可能的问题或挑战",
            "5. 具体、实用的建议或解决方案",
            "6. 相关的上下文信息或背景知识",
            "",
            "请提供详细、具体的分析结果，避免泛泛而谈或过于简略的描述。",
        ]
    )

    return "\n".join(part for part in prompt_parts if part).strip()


def extract_screen_assist_prompt(host: Any, message: str) -> str:
    text = str(message or "").strip()
    normalized = re.sub(r"\s+", "", text.lower())
    if not normalized or normalized.startswith("/"):
        return ""

    bot_name = getattr(host, "bot_name", "").strip().lower()
    if bot_name and bot_name in normalized:
        normalized = normalized.replace(bot_name, "")
        text = re.sub(re.escape(bot_name), "", text, flags=re.IGNORECASE)
        text = text.strip()
        normalized = re.sub(r"\s+", "", text.lower())

    if not (normalized.startswith("帮我") or normalized.startswith("你帮我")):
        return ""

    app_launcher_excludes = (
        "打开",
        "启动",
        "运行",
        "开启",
        "打开一下",
        "启动一下",
        "运行一下",
        "百度",
        "搜索",
        "查找",
        "查询",
        "搜索一下",
        "查一下",
        "搜一下",
        "浏览器",
        "网页",
        "网站",
        "网址",
        "网页链接",
        "网站链接",
        "http://",
        "https://",
        ".com",
        ".cn",
        ".org",
        ".net",
        ".io",
        "直播",
        "直播间",
        "直播页",
        "动态",
        "最新动态",
        "动态页",
        "视频",
        "最新视频",
        "投稿",
        "应用",
        "程序",
        "软件",
        "app",
    )

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

    if any(marker in normalized for marker in negative_markers):
        return ""

    has_request = any(marker in normalized for marker in request_markers)
    has_context = any(marker in normalized for marker in context_markers)

    has_help = "帮我" in normalized
    if has_help and len(text) >= 3 and len(text) <= 100:
        return text[:160]

    if not (has_request and has_context):
        return ""

    return text[:160]


async def call_framework_vision(host: Any, image_bytes: bytes, session=None) -> str:
    """使用 AstrBot 默认图片转述 / 当前对话多模态 provider 做一阶段识屏。"""
    umo = ""
    try:
        raw = getattr(session, "unified_msg_origin", None) or "" if session else ""
        umo = host._safe_unified_msg_origin(raw)
    except Exception:
        pass
    get_config = getattr(host.context, "get_config", None)
    if get_config is None:
        provider_settings: dict[str, Any] = {}
    else:
        cfg = get_config(umo=umo)
        provider_settings = (
            (getattr(cfg, "get", lambda k, d=None: d)("provider_settings", {}) or {})
            if cfg
            else {}
        )
    vision_provider_id = provider_settings.get("default_image_caption_provider_id") or ""
    prompt = provider_settings.get(
        "image_caption_prompt",
        "请用中文简要描述这张屏幕截图的内容：界面元素、用户可能在做的事、关键信息。",
    )
    if not vision_provider_id:
        get_curr = getattr(host.context, "get_current_chat_provider_id", None)
        if not get_curr or not callable(get_curr):
            logger.warning("框架未提供 get_current_chat_provider_id")
            return ""
        vision_provider_id = await get_current_chat_provider_id(host, umo=umo)
        if not vision_provider_id:
            logger.warning("未配置图片转述模型且获取当前对话模型失败")
            return ""
    image_url = "base64://" + base64.b64encode(image_bytes).decode("utf-8")
    llm_generate: Any = getattr(host.context, "llm_generate", None)
    if not llm_generate or not callable(llm_generate):
        logger.warning("框架未提供 llm_generate 方法")
        return ""
    try:
        llm_result = llm_generate(
            chat_provider_id=vision_provider_id,
            prompt=prompt,
            image_urls=[image_url],
        )
        if inspect.isawaitable(llm_result):
            resp = await cast(Awaitable[Any], llm_result)
        else:
            resp = llm_result
        text = extract_llm_completion_text(resp)
        if text:
            text = strip_think_blocks(text)
        return text.strip() if text else ""
    except Exception as e:
        err_str = str(e)
        if "completion 类型错误" in err_str and ("data:" in err_str or "data: " in err_str):
            parsed = parse_sse_completion_text(err_str)
            if parsed:
                parsed = strip_think_blocks(parsed)
                if parsed:
                    logger.info("已从框架视觉流式返回中解析出屏幕描述")
                    return parsed
            return ""
        logger.warning("框架视觉模型调用失败: %s", e)
        return ""


async def call_external_vision_api(
    host: Any,
    media_bytes: bytes,
    media_kind: str = "image",
    mime_type: str = "image/jpeg",
    scene: str = "",
    active_window_title: str = "",
) -> str:
    """调用外部视觉 API 进行图像分析。"""
    base64_data = base64.b64encode(media_bytes).decode("utf-8")
    image_prompt = build_vision_prompt(host, scene, active_window_title)
    if media_kind == "video":
        image_prompt = (
            "以下为用户当前桌面录屏视频（最近约10秒），你可以参考此内容判断用户正在做什么、进行到哪一步、画面里的关键线索或异常，并给出最值得的一条建议。\n"
            f"{image_prompt}"
        )

    async def call_api(
        api_url, api_key, api_model
    ) -> tuple[str | None, str | None]:
        if not api_url:
            return None, "未配置视觉 API 地址"

        payload = {
            "model": api_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": image_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{base64_data}"
                            },
                        },
                    ],
                }
            ],
            "stream": False,
        }

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        max_retries = 2
        retry_delay = 1
        for attempt in range(max_retries):
            try:
                timeout = aiohttp.ClientTimeout(total=60.0)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        api_url, json=payload, headers=headers
                    ) as response:
                        if response.status == 200:
                            result = await response.json()
                            if "choices" in result and len(result["choices"]) > 0:
                                choice = result["choices"][0]
                                if "message" in choice and "content" in choice["message"]:
                                    return choice["message"]["content"], None
                                if "text" in choice:
                                    return choice["text"], None
                                return None, "我刚才没能顺利读出画面内容。"
                            if "response" in result:
                                return result["response"], None
                            return None, "我刚才没能顺利读出画面内容。"
                        error_text = await response.text()
                        logger.error(
                            f"视觉 API 调用失败 (尝试 {attempt+1}/{max_retries}): {response.status} - {error_text}"
                        )
                        if attempt < max_retries - 1:
                            logger.info(f"等待 {retry_delay} 秒后重试...")
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2
                        else:
                            return None, "刚才没看清，我们再试一次？"
            except asyncio.TimeoutError:
                logger.error(f"Vision API timeout (attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    logger.info(f"等待 {retry_delay} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    return None, "网络刚才有点卡，我们再试一次？"
            except Exception as e:
                logger.error(f"调用视觉 API 异常 (尝试 {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    logger.info(f"等待 {retry_delay} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    return None, "这次视觉分析没有成功，再给我一次机会。"
        return None, "视觉 API 多次尝试后仍无有效响应。"

    main_api_url = host.vision_api_url
    main_api_key = host.vision_api_key
    main_api_model = host.vision_api_model

    logger.info("尝试使用主视觉API")
    result, error = await call_api(main_api_url, main_api_key, main_api_model)
    if result:
        return result

    backup_api_url = getattr(host, "vision_api_url_backup", None)
    backup_api_key = getattr(host, "vision_api_key_backup", None)
    backup_api_model = getattr(host, "vision_api_model_backup", None)

    if backup_api_url:
        logger.info("主视觉API失败，尝试使用备用视觉API")
        result, error = await call_api(backup_api_url, backup_api_key, backup_api_model)
        if result:
            return result

    logger.error("所有视觉API调用都失败了")
    return error if error else "视觉分析服务暂时不可用，请稍后再试。"


async def recognize_screen_material(
    host: Any,
    *,
    capture_context: dict[str, Any],
    scene: str,
    active_window_title: str,
    session=None,
) -> str:
    vs = host._resolve_vision_source()
    media_bytes = capture_context.get("media_bytes", b"") or b""
    media_kind = str(capture_context.get("media_kind", "image") or "image")
    mime_type = str(capture_context.get("mime_type", "image/jpeg") or "image/jpeg")

    fw_bytes = media_bytes
    if media_kind == "video":
        fw_bytes = capture_context.get("latest_image_bytes") or b""
        if not fw_bytes:
            logger.warning("视频素材下缺少锚点/关键帧图，框架视觉将跳过，必要时仅走外接 API")

    if vs == "仅框架":
        if not fw_bytes:
            return ""
        text = await call_framework_vision(host, fw_bytes, session=session)
        return host._compress_recognition_text(text or "")

    if vs == "外接+框架回退":
        if fw_bytes:
            text = await call_framework_vision(host, fw_bytes, session=session)
            if text and text.strip():
                logger.info("使用框架视觉模型识屏结果")
                return host._compress_recognition_text(text)
        logger.info("框架视觉无有效结果，回退到外接视觉 API")
        text = await call_external_vision_api(
            host,
            media_bytes,
            media_kind=media_kind,
            mime_type=mime_type,
            scene=scene,
            active_window_title=active_window_title,
        )
        return host._compress_recognition_text(text or "")

    text = await call_external_vision_api(
        host,
        media_bytes,
        media_kind=media_kind,
        mime_type=mime_type,
        scene=scene,
        active_window_title=active_window_title,
    )
    return host._compress_recognition_text(text or "")
