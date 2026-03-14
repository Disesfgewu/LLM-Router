# -*- coding: utf-8 -*-
"""
Message normalization helpers: role normalization, content extraction, pruning.
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger("api")


def normalize_role(role: str) -> str:
    """將 OpenAI-extended role 正規化為 router 能吃的標準 role"""
    if role in ("user", "assistant", "system"):
        return role
    if role == "developer":   # OpenAI o-series
        return "system"
    if role == "tool":        # tool result
        return "system"
    return "user"


def normalize_content(content: Any) -> str:
    """將 OpenAI-style content 正規化成純文字。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
                continue
            if part_type == "input_text":
                text = part.get("text") or part.get("input_text")
                if isinstance(text, str):
                    parts.append(text)
                continue
            if part_type == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
                continue
        return "\n".join(part for part in parts if part).strip()
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), str):
            return content["content"]
    return str(content)


def normalize_multimodal_content(content: Any) -> Any:
    """Preserve multimodal-compatible content parts for model calls."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[Dict[str, Any]] = []
        for part in content:
            if isinstance(part, str):
                if part.strip():
                    parts.append({"type": "text", "text": part})
                continue
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"text", "input_text", "output_text"}:
                text = part.get("text") or part.get("input_text")
                if isinstance(text, str) and text.strip():
                    parts.append({"type": "text", "text": text})
                continue
            if part_type == "image_url":
                image_url = part.get("image_url")
                if isinstance(image_url, dict) and image_url.get("url"):
                    parts.append({"type": "image_url", "image_url": image_url})
                elif isinstance(image_url, str) and image_url:
                    parts.append({"type": "image_url", "image_url": {"url": image_url}})
                continue

        if not parts:
            return ""
        if all(p.get("type") == "text" for p in parts):
            return "\n".join(p.get("text", "") for p in parts if p.get("text", "")).strip()
        return parts
    if isinstance(content, dict):
        if content.get("type") == "image_url" and content.get("image_url"):
            return [content]
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), str):
            return content["content"]
    return str(content)


def normalize_messages(raw_messages: List[Any], preserve_multimodal: bool = False) -> List[Dict[str, Any]]:
    """正規化 message 列表，相容 OpenClaw / LiteLLM / vLLM 等 OpenAI-style clients"""
    messages: List[Dict[str, Any]] = []

    for m in raw_messages:
        if not isinstance(m, dict):
            continue

        raw_role = m.get("role", "user")
        role = normalize_role(raw_role)
        content = (
            normalize_multimodal_content(m.get("content", ""))
            if preserve_multimodal else
            normalize_content(m.get("content", ""))
        )

        # tool message 降級成 system transcript
        if raw_role == "tool":
            tool_name = m.get("name") or m.get("tool_name") or "tool"
            content = f"[Tool output: {tool_name}]\n{normalize_content(content)}"

        # assistant tool_calls 降級成可讀文字 transcript
        if raw_role == "assistant" and m.get("tool_calls"):
            tcalls = m.get("tool_calls", [])
            call_lines = []
            for tc in tcalls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function", {})
                name = fn.get("name", "unknown_tool")
                args = fn.get("arguments", "")
                call_lines.append(f"[Assistant requested tool: {name}] args={args}")
            tc_text = "\n".join(call_lines).strip()
            if tc_text:
                existing_text = normalize_content(content)
                content = f"{existing_text}\n{tc_text}".strip()

        if content:
            messages.append({"role": role, "content": content})

    return messages


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            total += _estimate_tokens(content)
            continue
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "image_url":
                    total += 512
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    total += _estimate_tokens(text)
            continue
        total += _estimate_tokens(str(content))
    return total


def prune_messages(
    messages: List[Dict[str, Any]],
    max_input_tokens: int = 6000,
    keep_last: int = 10,
    max_chars_per_message: int = 4000,
) -> List[Dict[str, Any]]:
    """裁切 message 列表，避免 413 / input token 超限"""
    # 裁單則長度
    pruned = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str) and len(content) > max_chars_per_message:
            content = content[:max_chars_per_message] + "\n...[truncated]"
        elif isinstance(content, list):
            truncated_parts: List[Dict[str, Any]] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                cloned = dict(part)
                text = cloned.get("text")
                if isinstance(text, str) and len(text) > max_chars_per_message:
                    cloned["text"] = text[:max_chars_per_message] + "\n...[truncated]"
                truncated_parts.append(cloned)
            content = truncated_parts
        pruned.append({"role": m.get("role", "user"), "content": content})

    # 只保留最近 N 輪
    pruned = pruned[-keep_last:]

    # 按估算 token 往前刪
    while len(pruned) > 1 and _estimate_messages_tokens(pruned) > max_input_tokens:
        pruned.pop(0)

    return pruned
