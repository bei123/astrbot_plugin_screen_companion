"""从 AstrBot / OpenAI 风格响应中抽取可读文本（含 SSE 与 think 块清理）。"""

from __future__ import annotations

import json
import re
from typing import Any


def strip_think_blocks(text: str) -> str:
    if not text or not text.strip():
        return ""
    text = re.sub(
        r"\x3cthink\x3e.*?\x3c/think\x3e", "", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"code_execution\s*\{[^}]*\}", "", text, flags=re.IGNORECASE)
    return text.strip()


def parse_sse_completion_text(raw: str) -> str:
    parts: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:") or line in {"data:", "data: [DONE]"}:
            continue
        try:
            json_str = line[5:].strip()
            if not json_str:
                continue
            data = json.loads(json_str)
            choices = data.get("choices") or []
            if choices and isinstance(choices[0], dict):
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
    return "".join(parts).strip() if parts else ""


def extract_llm_completion_text(response: Any) -> str:
    if response is None:
        return ""
    if hasattr(response, "completion_text") and response.completion_text:
        return (response.completion_text or "").strip()
    if hasattr(response, "result_chain") and response.result_chain:
        chain = response.result_chain
        if hasattr(chain, "message") and chain.message:
            return (chain.message or "").strip()
        if hasattr(chain, "chain") and chain.chain:
            parts: list[str] = []
            for c in chain.chain:
                if hasattr(c, "text"):
                    parts.append(getattr(c, "text", "") or "")
            if parts:
                return "".join(parts).strip()
    if isinstance(response, str) and response.strip():
        return parse_sse_completion_text(response)
    return ""
