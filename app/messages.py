# -*- coding: utf-8 -*-
"""
Message normalization helpers: role normalization, content extraction, pruning.
"""

import re
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


def normalize_messages(raw_messages: List[Any]) -> List[Dict[str, str]]:
    """正規化 message 列表，相容 OpenClaw / LiteLLM / vLLM 等 OpenAI-style clients"""
    messages: List[Dict[str, str]] = []

    for m in raw_messages:
        if not isinstance(m, dict):
            continue

        raw_role = m.get("role", "user")
        role = normalize_role(raw_role)
        content = normalize_content(m.get("content", ""))

        # tool message 降級成 system transcript
        if raw_role == "tool":
            tool_name = m.get("name") or m.get("tool_name") or "tool"
            content = f"[Tool output: {tool_name}]\n{content}"

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
                content = f"{content}\n{tc_text}".strip()

        if content:
            messages.append({"role": role, "content": content})

    return messages


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _estimate_messages_tokens(messages: List[Dict[str, str]]) -> int:
    return sum(_estimate_tokens(m.get("content", "")) for m in messages)


def prune_messages(
    messages: List[Dict[str, str]],
    max_input_tokens: int = 6000,
    keep_last: int = 10,
    max_chars_per_message: int = 4000,
) -> List[Dict[str, str]]:
    """裁切 message 列表，避免 413 / input token 超限"""
    # 裁單則長度
    pruned = []
    for m in messages:
        content = m.get("content", "")
        if len(content) > max_chars_per_message:
            content = content[:max_chars_per_message] + "\n...[truncated]"
        pruned.append({"role": m.get("role", "user"), "content": content})

    # 只保留最近 N 輪
    pruned = pruned[-keep_last:]

    # 按估算 token 往前刪
    while len(pruned) > 1 and _estimate_messages_tokens(pruned) > max_input_tokens:
        pruned.pop(0)

    return pruned
