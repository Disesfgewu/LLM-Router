# -*- coding: utf-8 -*-
"""
Tool-calling helpers: web_search detection, query extraction, citation parsing,
LLM-based search decision, and related tool-flow utilities.
"""

import re
import json
import logging
from typing import List, Dict, Any, Optional

from fastapi import Request

from app.messages import normalize_content, normalize_messages, prune_messages
from app.search import _clean_query_text, _sanitize_search_query

logger = logging.getLogger("api")

# 有 tools 時優先嘗試的模型清單（確認支援 tool-calling 且 context 夠大）
_TOOL_PREFERRED_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
]


def _is_openclaw_web_search(raw: Dict[str, Any], request: Request) -> bool:
    """Detect OpenClaw built-in web_search requests routed via perplexity provider."""
    title = request.headers.get("x-title", "")
    model = str(raw.get("model", ""))
    return title == "OpenClaw Web Search" or model.startswith("perplexity/")


def _extract_last_user_query(raw_messages: List[Any]) -> str:
    """Extract last user text from OpenAI-style messages payload."""
    for msg in reversed(raw_messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue

        content = msg.get("content", "")
        if isinstance(content, str):
            text = content.strip()
        else:
            text = normalize_content(content).strip()

        text = _clean_query_text(text)

        if text:
            return text
    return ""


def _extract_citations_from_content(content: str) -> List[str]:
    """Extract URL lines from DDGS text output to populate perplexity-like citations."""
    citations: List[str] = []
    for line in content.splitlines():
        if line.startswith("URL: "):
            citations.append(line.replace("URL: ", "", 1).strip())
    return citations


def _extract_query_from_tool_payload(content: Any) -> str:
    """Extract search query from tool payload when client returns args instead of tool output."""
    text = content if isinstance(content, str) else normalize_content(content)
    if not isinstance(text, str) or not text.strip():
        return ""

    stripped = text.strip()

    # JSON payload case: {"query":"...","count":5}
    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            q = payload.get("query", "")
            if isinstance(q, str):
                return _sanitize_search_query(q)
    except Exception:
        pass

    # Best-effort regex fallback.
    m = re.search(r'"query"\s*:\s*"([^"]+)"', stripped)
    if m:
        return _sanitize_search_query(m.group(1))

    return ""


def _looks_like_search_results(text: str) -> bool:
    """Heuristic to determine whether tool message already contains search results."""
    if not isinstance(text, str):
        return False
    return "URL: " in text or "Snippet:" in text


def _extract_search_content_from_tool_result(tool_text: str) -> str:
    """Extract clean search result text from OpenClaw's JSON-wrapped tool result."""
    if not isinstance(tool_text, str):
        return ""
    stripped = tool_text.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                content = obj.get("content", "")
                if isinstance(content, str) and content.strip():
                    # Strip <<<EXTERNAL_UNTRUSTED_CONTENT>>> wrapper if present
                    inner = re.sub(
                        r"<<<EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>\s*",
                        "",
                        content,
                        flags=re.DOTALL,
                    )
                    inner = re.sub(
                        r"<<<END_EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>",
                        "",
                        inner,
                        flags=re.DOTALL,
                    )
                    return inner.strip()
        except Exception:
            pass
    return stripped


def _extract_citations_from_tool_result(tool_text: str) -> List[str]:
    """Extract citations from tool result payload (JSON citations field or URL lines)."""
    if not isinstance(tool_text, str):
        return []

    citations: List[str] = []
    stripped = tool_text.strip()

    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                raw_citations = obj.get("citations", [])
                if isinstance(raw_citations, list):
                    for c in raw_citations:
                        if isinstance(c, str) and c.strip().startswith(("http://", "https://")):
                            citations.append(c.strip())

                content = obj.get("content", "")
                if isinstance(content, str) and content.strip():
                    citations.extend(_extract_citations_from_content(content))
        except Exception:
            pass

    # Fallback: treat raw text as search content and parse URL lines.
    citations.extend(_extract_citations_from_content(stripped))

    deduped: List[str] = []
    seen = set()
    for c in citations:
        normalized = c.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)
    return deduped


def _has_tool_result(raw_messages: List[Any]) -> bool:
    """Whether the request already contains a tool result message."""
    return any(isinstance(m, dict) and m.get("role") == "tool" for m in raw_messages)


def _last_message_is_tool_result(raw_messages: List[Any]) -> bool:
    """Whether the latest message is a tool result (the post-tool round)."""
    if not raw_messages:
        return False
    last = raw_messages[-1]
    return isinstance(last, dict) and last.get("role") == "tool"


def _assistant_requested_tool_since_last_user(raw_messages: List[Any]) -> bool:
    """Detect if assistant already emitted tool_calls in the latest user turn."""
    for m in reversed(raw_messages):
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "user":
            return False
        if role == "assistant" and m.get("tool_calls"):
            return True
    return False


def _has_tool(raw_tools: List[Any], name: str) -> bool:
    """Check if OpenAI-style tools list declares a given function name."""
    for t in raw_tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or {}
        if isinstance(fn, dict) and fn.get("name") == name:
            return True
    return False


def _tool_label(tool_obj: Any) -> str:
    """Return a best-effort tool label for matching/logging."""
    if not isinstance(tool_obj, dict):
        return ""
    t_type = str(tool_obj.get("type", "")).strip()
    fn = tool_obj.get("function")
    if isinstance(fn, dict):
        fn_name = str(fn.get("name", "")).strip()
        if fn_name:
            return fn_name
    return t_type


def _has_web_search_tool(raw_tools: List[Any]) -> bool:
    """Detect web-search-like tools across different client naming conventions."""
    for t in raw_tools:
        label = _tool_label(t).lower()
        if not label:
            continue
        if label == "web_search":
            return True
        if "web" in label and "search" in label:
            return True
    return False


def _pick_web_search_tool_name(raw_tools: List[Any]) -> str:
    """Pick the declared web-search tool name so returned tool_calls matches client declaration."""
    for t in raw_tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function")
        if not isinstance(fn, dict):
            continue
        fn_name = str(fn.get("name", "")).strip()
        lowered = fn_name.lower()
        if not fn_name:
            continue
        if lowered == "web_search" or ("web" in lowered and "search" in lowered):
            return fn_name

    # fallback: keep backward-compatible default
    return "web_search"


def _tool_choice_requires_web_search(tool_choice: Any) -> bool:
    """Detect if caller explicitly requires web_search tool usage."""
    if tool_choice == "required":
        return True
    if not isinstance(tool_choice, dict):
        return False
    if tool_choice.get("type") != "function":
        return False
    fn = tool_choice.get("function")
    if not isinstance(fn, dict):
        return False
    fn_name = str(fn.get("name", "")).lower()
    return fn_name == "web_search" or ("web" in fn_name and "search" in fn_name)


def _requires_web_search_by_prompt(raw_messages: List[Any]) -> bool:
    """Detect explicit user/developer instructions requesting web search tool usage."""
    trigger_phrases = [
        "use web_search",
        "use web search",
        "must use web_search",
        "must use web search",
        "please use web_search",
        "please use web search",
        "請使用web_search",
        "請使用 web_search",
        "請使用 web search",
        "請用 web_search",
        "請用 web search",
        "請用web工具",
        "請使用web工具",
        "請先搜尋網路",
        "請上網查",
        "查詢最新",
        "即時資訊",
    ]

    for m in raw_messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", ""))
        if role not in ("user", "developer", "system"):
            continue
        content = m.get("content", "")
        text = content if isinstance(content, str) else normalize_content(content)
        lowered = text.lower()
        if any(p in lowered for p in trigger_phrases):
            return True

    return False


def _should_search(query: str, tool_choice: Any, raw_messages: List[Any]) -> bool:
    """Minimal heuristic for deciding whether to emit a web_search tool call."""
    if _tool_choice_requires_web_search(tool_choice):
        return True
    if _requires_web_search_by_prompt(raw_messages):
        return True
    if not query:
        return False
    keywords = ["天氣", "即時", "最新", "今天", "news", "weather"]
    return any(k in query for k in keywords)


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort JSON object extraction from model text output."""
    if not isinstance(text, str):
        return None

    stripped = text.strip()
    candidates = [stripped]

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start:end + 1])

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue

    return None


def _llm_decide_web_search(
    router: Any,
    raw_messages: List[Any],
    query: str,
) -> tuple:
    """Ask LLM to decide whether web_search is needed and suggest the query + alternates."""
    clean_query = _sanitize_search_query(query)
    if not clean_query:
        return None, "", []

    transcript = normalize_messages(raw_messages)
    transcript = prune_messages(
        transcript,
        max_input_tokens=1800,
        keep_last=6,
        max_chars_per_message=1200,
    )
    transcript_text = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in transcript
    )

    decision_messages = [
        {
            "role": "system",
            "content": (
                "你是工具決策器。請判斷是否需要呼叫 web_search。"
                "只有在需要即時、最新、需外部查證資訊時才 use_web_search=true。"
                "你只能輸出單一 JSON 物件，不要輸出任何其他文字。"
                "JSON 格式："
                '{"use_web_search": true|false, "query": "<最佳搜尋關鍵字>", "alternates": ["<備援關鍵字1>", "<備援關鍵字2>"]}'
                "alternates 最多 2 個，語言可以與 query 不同以擴展覆蓋範圍。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"最近對話：\n{transcript_text}\n\n"
                f"最新使用者問題：{clean_query}\n\n"
                "請回傳 JSON。"
            ),
        },
    ]

    try:
        response = router.chat(
            messages=decision_messages,
            target_category="TextOnlyHigh",
            include_chat_only=True,
            temperature=0.0,
            max_tokens=220,
        )
        content = ""
        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content or ""

        parsed = _extract_json_object(content)
        if not parsed:
            logger.info("[ToolShim] LLM decision parse failed; content_preview=%s", content[:120])
            return None, "", []

        use_search_raw = parsed.get("use_web_search")
        query_raw = parsed.get("query", "")
        use_search = bool(use_search_raw) if isinstance(use_search_raw, bool) else None
        parsed_query = _sanitize_search_query(str(query_raw)) if isinstance(query_raw, str) else ""
        alternates_raw = parsed.get("alternates", [])
        alternates = [
            _sanitize_search_query(str(a))
            for a in alternates_raw
            if isinstance(a, str)
        ]
        alternates = [a for a in alternates if a and a != parsed_query]

        if use_search is True and not parsed_query:
            parsed_query = clean_query

        return use_search, parsed_query, alternates
    except Exception:
        logger.exception("[ToolShim] LLM decision call failed")
        return None, "", []
