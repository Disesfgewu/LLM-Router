#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ModelRouter API Gateway (OpenAI-compatible)

多模型智慧路由 API 閘道，對外提供 OpenAI 相容介面，
自動在 GitHub Models、Google Gemini、Ollama 之間做 failover 和配額管理。

Endpoints:
  POST /v1/chat/completions     OpenAI Chat Completions API
  POST /v1/completions          OpenAI Completions API (legacy)
  POST /v1/direct_query         直接查詢指定模型 (model_name + provider)
  GET  /v1/models               列出可用模型
  GET  /health                  健康檢查
  GET  /                        服務資訊
  POST /admin/reset_quotas      重置所有配額 (每日)
  POST /admin/refresh_rpm       重置優先順序指標 (每半小時)
  GET  /admin/status            查看配額狀態

Usage:
  curl -X POST http://localhost:8000/v1/chat/completions \\
    -H "Content-Type: application/json" \\
    -d '{"model": "auto", "messages": [{"role": "user", "content": "Hello!"}]}'

Environment variables:
  GOOGLE_API_KEY          Google Gemini API Key
  GITHUB_MODELS_API_KEY   GitHub Models API Key
  API_HOST                0.0.0.0
  API_PORT                8000
"""

import os
import time
import logging
import tempfile
import json
import io
import base64
import re
import importlib
from typing import List, Optional, Dict, Any, Literal, cast

from dotenv import load_dotenv
load_dotenv()  # 載入 .env 檔案

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import contextlib
from huggingface_hub import InferenceClient

try:
    genai = importlib.import_module("google.generativeai")
except ImportError:
    genai = None

try:
    from opencc import OpenCC  # type: ignore
    _opencc_converter = OpenCC("s2twp")
except Exception:
    _opencc_converter = None

import mcp.server
from mcp.server.sse import SseServerTransport
import mcp.types as types

from ModelRouter.ModelRouter import ModelRouter

# ── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("api")

# ── App Config ─────────────────────────────────────────────
APP_TITLE = "ModelRouter API Gateway"
APP_VERSION = "1.0.0"

# ── Universal Agent Identity (injected into every LLM call) ─
AGENT_SYSTEM_PROMPT = (
    "You are an assistant agent developed by DisesFgewu via OpenClaw. "
    "You are a helpful, harmless, and honest AI assistant. "
    "Always respond in the user's language unless explicitly instructed otherwise. "
    "If replying in Chinese, you MUST use Traditional Chinese (zh-TW) and concise style. "
    "If the user asks for code, provide complete runnable code first, then concise usage notes. "
    "For code tasks, enforce minimum output quality: include a compilable/executable main entry, at least 2 test cases, "
    "and complexity + boundary-condition notes. "
    "For research-backed answers, first align with the user's context in 1-2 short sentences, then provide a short visible work summary if tools/search were used, "
    "and then give the direct answer. Do not expose raw chain-of-thought or hidden reasoning. "
    "Answer format: start with one-line conclusion, then provide focused explanation and key points as needed "
    "(usually 3-6 bullets for complex topics)."
)

IDENTITY_QUESTION_PREFIX = (
    "You are an assistant agent developed by DisesFgewu via OpenClaw. "
    "Answer the user's question. [Question] : "
)


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _wrap_identity_question(question: str) -> str:
    cleaned = (question or "").strip()
    if cleaned.startswith(IDENTITY_QUESTION_PREFIX):
        return cleaned
    return f"{IDENTITY_QUESTION_PREFIX}{cleaned}"


def _prepend_identity_prefix_to_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    wrapped: List[Dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            wrapped.append(message)
            continue

        role = str(message.get("role", ""))
        if role != "user":
            wrapped.append(message)
            continue

        content = message.get("content", "")
        cloned = dict(message)

        if isinstance(content, str):
            cloned["content"] = _wrap_identity_question(content)
            wrapped.append(cloned)
            continue

        if isinstance(content, list):
            # Keep multimodal parts unchanged and add a leading text instruction part.
            wrapped_parts = list(content)
            if wrapped_parts:
                first = wrapped_parts[0]
                if isinstance(first, dict) and first.get("type") in {"text", "input_text"}:
                    original_text = str(first.get("text") or first.get("input_text") or "")
                    first_copy = dict(first)
                    first_copy["type"] = "text"
                    first_copy["text"] = _wrap_identity_question(original_text)
                    wrapped_parts[0] = first_copy
                else:
                    wrapped_parts.insert(0, {"type": "text", "text": _wrap_identity_question("")})
            else:
                wrapped_parts = [{"type": "text", "text": _wrap_identity_question("")}]

            cloned["content"] = wrapped_parts
            wrapped.append(cloned)
            continue

        cloned["content"] = _wrap_identity_question(str(content))
        wrapped.append(cloned)

    return wrapped


def _ensure_user_message_for_generation(messages: List[Dict[str, Any]], latest_user_text: str) -> List[Dict[str, Any]]:
    """Google OpenAI-compatible endpoint requires at least one non-empty user content item."""
    prepared = list(messages or [])
    has_user_payload = any(
        isinstance(m, dict)
        and m.get("role") == "user"
        and str(m.get("content", "")).strip()
        for m in prepared
    )
    if has_user_payload:
        return prepared

    fallback_query = _wrap_identity_question((latest_user_text or "").strip() or "請根據上方內容回答。")
    prepared.append({"role": "user", "content": fallback_query})
    return prepared


def _to_zh_tw_if_needed(user_text: str, content: str) -> str:
    if not content:
        return content
    if _opencc_converter is None:
        return content
    if _contains_cjk(user_text) or _contains_cjk(content):
        try:
            return _opencc_converter.convert(content)
        except Exception:
            return content
    return content


def _is_code_generation_request(user_text: str) -> bool:
    text = (user_text or "").lower()
    if not text:
        return False
    code_markers = [
        "幫我寫",
        "寫一份",
        "程式碼",
        "代碼",
        "實作",
        "implement",
        "implementation",
        "write code",
        "code",
        "c++",
        "python",
        "java",
        "javascript",
        "golang",
        "rust",
    ]
    return any(marker in text for marker in code_markers)


def _looks_like_code_output(content: str) -> bool:
    text = (content or "")
    if "```" in text:
        return True
    if re.search(r"(?m)^\s*#include\s*<", text):
        return True
    if re.search(r"(?m)^\s*(class|struct|def|function)\s+", text):
        return True
    if re.search(r"(?m)^\s*for\s*\(|^\s*while\s*\(|^\s*if\s*\(", text):
        return True
    return False


def _compress_answer_if_needed(user_text: str, content: str) -> str:
    text = (content or "").strip()
    if not text:
        return text

    # Never compress code-heavy outputs; preserve full implementation blocks.
    if _is_code_generation_request(user_text) or _looks_like_code_output(text):
        return text

    if len(text) <= 1400:
        return text

    segments = [seg.strip() for seg in re.split(r"(?<=[。！？\n])", text) if seg.strip()]
    if not segments:
        return text

    conclusion = segments[0]
    if len(conclusion) > 90:
        conclusion = conclusion[:90].rstrip("，,。!！?？ ") + "。"

    key_points: List[str] = []
    for seg in segments[1:]:
        if len(key_points) >= 6:
            break
        if re.search(r"(°|℃|\d+|建議|注意|降雨|風|濕度|來源|參考)", seg):
            normalized = re.sub(r"^[\-\*\d\.\)\s]+", "", seg)
            key_points.append(normalized)

    if not key_points and len(segments) > 1:
        key_points = [re.sub(r"^[\-\*\d\.\)\s]+", "", segments[1])]

    if conclusion.startswith("結論："):
        compact_lines = [conclusion]
    else:
        compact_lines = [f"結論：{conclusion}"]
    for item in key_points[:6]:
        compact_lines.append(f"- {item}")
    return "\n".join(compact_lines)


def _cleanup_noisy_boilerplate(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return text

    cleaned_lines: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            cleaned_lines.append(line)
            continue
        if "[Assistant requested tool:" in s:
            continue
        # Remove low-value citation disclaimer artifacts that often appear in non-factual Q&A.
        if "目前無具體來源 URL" in s:
            continue
        if "僅憑技術能力回答" in s:
            continue
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned or text


def _postprocess_user_response(user_text: str, content: str) -> str:
    compact = _cleanup_noisy_boilerplate(content)
    compact = _compress_answer_if_needed(user_text, compact)
    return _to_zh_tw_if_needed(user_text, compact)


def _sanitize_messages_for_model(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop tool-call transcript artifacts before sending context to the generation model."""
    sanitized: List[Dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue

        role = str(message.get("role", "user"))
        content = message.get("content", "")
        if not isinstance(content, str):
            sanitized.append(message)
            continue

        if "[Assistant requested tool:" in content:
            kept_lines = [
                ln for ln in content.splitlines()
                if "[Assistant requested tool:" not in ln
            ]
            cleaned = "\n".join(kept_lines).strip()
            if not cleaned:
                # Pure tool-request transcript is not useful generation context.
                continue
            cloned = dict(message)
            cloned["content"] = cleaned
            sanitized.append(cloned)
            continue

        sanitized.append(message)

    return sanitized


def _append_code_output_requirements(messages: List[Dict[str, Any]], user_text: str) -> List[Dict[str, Any]]:
    if not _is_code_generation_request(user_text):
        return messages

    requirements = (
        "程式碼輸出最低要求："
        "1) 必須提供可直接編譯/執行的完整程式，且包含 main() 入口。"
        "2) 必須提供至少 2 組測資（可用輸入/輸出示例或程式內測試）。"
        "3) 必須列出時間複雜度、空間複雜度與主要邊界條件。"
        "若使用者需求有歧義，請做最小必要假設並在註解或說明中明確寫出。"
    )
    return list(messages) + [{"role": "system", "content": requirements}]


def _research_answer_style_instruction() -> str:
    return (
        "若本題有使用搜尋、工具或外部資料，回答格式請遵守："
        "先用 1 至 2 句簡短對齊使用者情境與你採用的做法，例如『收到，我直接對著這個 repo 與相關公開資料核對』。"
        "之後直接給答案、步驟、設定片段與注意事項。"
        "若真的有幫助，才可加入很短的工作摘要；不可為了湊格式而輸出空泛或奇怪的條列。"
        "禁止輸出『正在思考』、『我先想一下』這類內部推理字樣；"
        "若要描述流程，請改寫成已完成的工作摘要，且每一點都必須具體。"
    )


def _inject_agent_system_prompt(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure AGENT_SYSTEM_PROMPT is the first system message in every LLM call."""
    if not messages:
        return [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
    if messages[0].get("role") == "system":
        existing = str(messages[0].get("content", ""))
        merged = list(messages)
        merged[0] = {"role": "system", "content": f"{AGENT_SYSTEM_PROMPT}\n\n{existing}".strip()}
        return merged
    return [{"role": "system", "content": AGENT_SYSTEM_PROMPT}] + list(messages)

# ── MCP Server Setup ───────────────────────────────────────
mcp_server = mcp.server.Server("modelrouter-mcp")
sse_transport: Optional[SseServerTransport] = None

@mcp_server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """列出 MCP 伺服器提供的工具"""
    return [
        types.Tool(
            name="search_web",
            description="使用 DuckDuckGo 搜尋網路上的即時資訊 (Search the web for current information)",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要搜尋的關鍵字或問題 (The search query)"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最多回傳幾筆結果（預設 5 筆） (Max results to return, default 5)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        )
    ]

@mcp_server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """處理 OpenClaw / MCP client 呼叫工具的請求"""
    if name != "search_web":
        raise ValueError(f"Unknown tool: {name}")

    if not arguments or "query" not in arguments:
        raise ValueError("Missing required argument: query")

    raw_query = str(arguments["query"])
    query = _sanitize_search_query(raw_query)
    max_results = arguments.get("max_results", 5)

    if not query:
        raise ValueError("Empty search query after sanitization")
    
    logger.info(f"[MCP DDGS] 搜尋關鍵字: '{query}', max_results={max_results}")
    if query != raw_query:
        logger.info(f"[MCP DDGS] 原始查詢: '{raw_query}' -> 正規化查詢: '{query}'")
    
    try:
        results = ""
        search_results = []
        low_quality_fallback_results: List[Dict[str, str]] = []
        low_quality_fallback_query = ""
        candidate_queries = _generate_search_query_variants(query)

        for index, candidate_query in enumerate(candidate_queries, 1):
            if index > 1:
                logger.info(f"[MCP DDGS] 無結果或低品質，使用 fallback 查詢: '{candidate_query}'")
            search_results = _ddgs_text_search(candidate_query, max_results=max_results)
            if search_results:
                results_preview = " ".join(
                    r.get("title", "") + " " + r.get("body", "") for r in search_results
                )
                if _looks_low_quality(results_preview, candidate_query):
                    logger.info("[MCP DDGS] 結果品質不足 (low overlap), 先保留並嘗試下一個查詢")
                    if not low_quality_fallback_results:
                        low_quality_fallback_results = search_results
                        low_quality_fallback_query = candidate_query
                    search_results = []
                    continue
                if candidate_query != query:
                    logger.info(f"[MCP DDGS] fallback 命中查詢: '{candidate_query}'")
                break

        if not search_results and low_quality_fallback_results:
            logger.info("[MCP DDGS] 使用保留的低品質結果（query='%s'）", low_quality_fallback_query)
            search_results = low_quality_fallback_results

        if search_results and _needs_source_enrichment(query):
            logger.info("[MCP DDGS] Data-heavy query detected; enriching top sources")
            search_results = _enrich_search_results(search_results, query)

        if not search_results:
            logger.info("[MCP DDGS] DDGS 無解析結果，改用 Bing HTML fallback")
            for candidate_query in candidate_queries:
                try:
                    search_results = _bing_html_search(candidate_query, max_results=max_results)
                    if search_results:
                        logger.info(f"[MCP DDGS] Bing HTML fallback 命中查詢: '{candidate_query}'")
                        break
                except Exception as fallback_error:
                    logger.warning(f"[MCP DDGS] Bing HTML fallback 失敗 ({candidate_query}): {fallback_error}")
            
        if not search_results:
            results = "找不到相關結果。 (No results found.)"
        else:
            for i, r in enumerate(search_results, 1):
                title = r.get("title", "No Title")
                href = r.get("href", r.get("url", "No URL"))
                body = r.get("body", r.get("snippet", "No Snippet"))
                detail = r.get("detail", "")
                results += f"[{i}] {title}\nURL: {href}\nSnippet: {body}\n"
                if detail:
                    results += f"Detail: {detail}\n"
                results += "\n"
                    
        return [types.TextContent(type="text", text=results.strip())]
    except Exception as e:
        logger.error(f"[MCP DDGS] 搜尋發生錯誤: {e}")
        return [types.TextContent(type="text", text=f"搜尋發生錯誤 (Search error): {str(e)}")]

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan：啟動與關閉時的管理"""
    global sse_transport
    sse_transport = SseServerTransport("/mcp/messages")
    logger.info("🔧 初始化自動定時任務...")
    
    google_api_key = os.getenv("GOOGLE_API_KEY")
    if google_api_key and genai is not None:
        genai.configure(api_key=google_api_key)  # type: ignore[attr-defined]
        logger.info("✅ Google Generative AI 已初始化")
    elif google_api_key and genai is None:
        logger.warning("⚠️  google-generativeai 套件未安裝，Google 文件上傳功能將無法使用")
    else:
        logger.warning("⚠️  GOOGLE_API_KEY 未設定，文件上傳功能將無法使用")
    
    scheduler.add_job(
        reset_quotas_job, CronTrigger(hour=0, minute=0), id="reset_quotas_daily",
        name="每日重置 RPD 配額", replace_existing=True
    )
    logger.info("📅 已設置：每日 0:00 自動重置 RPD 配額")
    
    scheduler.add_job(
        refresh_rpm_job, CronTrigger(minute="*/30"), id="refresh_rpm_30min",
        name="每30分鐘重置優先順序", replace_existing=True
    )
    logger.info("⏰ 已設置：每 30 分鐘自動重置優先順序指標")
    
    scheduler.start()
    logger.info("✅ 自動定時任務已啟動")
    logger.info("🔌 MCP Server 啟動 (Endpoints: /mcp/sse, /mcp/messages)")

    # ── Auth DB ─────────────────────────────────────────────
    auth_init_db()
    logger.info("🔐 Auth DB 已初始化")

    yield

    purge_expired_sessions()
    logger.info("🛑 停止自動定時任務...")
    scheduler.shutdown()
    logger.info("✅ 自動定時任務已停止")

# ── FastAPI App ────────────────────────────────────────────
app = FastAPI(title=APP_TITLE, version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth Middleware ────────────────────────────────────────

# Paths that require NO authentication at all
_AUTH_PUBLIC = {"/", "/health", "/auth/register", "/auth/login"}
# Path prefixes that are accessible with session token only (no API key)
_SESSION_ONLY_PREFIXES = ("/auth/", "/admin/")
# Paths that proxy to MCP — keep as-is (internal tool use)
_MCP_PREFIXES = ("/mcp/",)


def _is_localhost_client(request: Request) -> bool:
    if not request.client or not request.client.host:
        return False
    host = request.client.host
    return host in {"127.0.0.1", "::1", "localhost"}


def _path_to_scope(path: str) -> Optional[str]:
    """Map request path to its endpoint scope name."""
    if "/chat/" in path:
        return "chat"
    if path.endswith("/completions") or "/completions" in path:
        return "completions"
    if "/direct_query" in path:
        return "direct_query"
    if "/images/" in path:
        return "images"
    if "/file/" in path:
        return "file"
    if "/models" in path:
        return "models"
    return None


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # OPTIONS preflight — let CORS handle it
    if request.method == "OPTIONS":
        return await call_next(request)

    # Fully public (/health, /, /auth/login, /auth/register)
    if path in _AUTH_PUBLIC:
        return await call_next(request)

    # MCP endpoints pass through without auth enforcement
    if any(path.startswith(p) for p in _MCP_PREFIXES):
        return await call_next(request)

    # Single-device hardening: only allow local access to auth/admin by default.
    local_admin_only = os.getenv("AUTH_LOCAL_ADMIN_ONLY", "1").lower() not in {"0", "false", "no"}
    if local_admin_only and any(path.startswith(p) for p in _SESSION_ONLY_PREFIXES):
        if not _is_localhost_client(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "此端點僅允許本機存取"},
            )

    # ── Attempt session-token auth ──────────────────────────
    session_token = request.headers.get("X-Session-Token", "").strip()
    if session_token:
        acct = validate_session(session_token)
        if acct:
            if path.startswith("/admin/") and not acct["is_admin"]:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "需要管理員權限"},
                )
            request.state.auth = acct
            return await call_next(request)
        # Invalid/expired session — do not fall through to API key
        # (prevents session-fixation escalation)
        return JSONResponse(
            status_code=401,
            content={"detail": "Session token 無效或已過期，請重新登入"},
        )

    # ── Attempt API-key auth (Bearer token) ─────────────────
    # Admin paths cannot be accessed with API keys
    if any(path.startswith(p) for p in _SESSION_ONLY_PREFIXES):
        return JSONResponse(
            status_code=401,
            content={"detail": "此端點需要登入 Session（X-Session-Token header）"},
        )

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        raw_key = auth_header[7:].strip()
        client_ip: Optional[str] = request.client.host if request.client else None
        endpoint_scope = _path_to_scope(path)
        acct = validate_api_key(raw_key, client_ip=client_ip, endpoint_scope=endpoint_scope)
        if acct:
            request.state.auth = acct
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "API key 無效、已過期、超過速率限制或無此端點的存取權限"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    return JSONResponse(
        status_code=401,
        content={
            "detail": (
                "需要認證。"
                "外部應用程式請使用 Authorization: Bearer <api_key>，"
                "前端請使用 X-Session-Token: <session_token>"
            )
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── Router Instance (Singleton) ────────────────────────────
router_instance: Optional[ModelRouter] = None


def get_router() -> ModelRouter:
    global router_instance
    if router_instance is None:
        router_instance = ModelRouter()
    return router_instance


def _run_image_generation_with_router(
    router: ModelRouter,
    *,
    prompt: str,
    model: str,
    n: int,
    size: str,
    response_format: str,
    source_image_bytes: Optional[bytes] = None,
) -> Dict[str, Any]:
    provider_name = ""
    model_limit = 25
    for candidate_provider, models_dict in router._config_limits.get("ImageGeneration", {}).items():
        if model in models_dict:
            provider_name = candidate_provider
            model_limit = models_dict.get(model, 25)
            break

    if not provider_name:
        raise HTTPException(status_code=400, detail=f"unknown image generation model: {model}")

    model_candidates = [model]
    if provider_name == "HuggingFace":
        available_hf_models = list(router._config_limits.get("ImageGeneration", {}).get("HuggingFace", {}).keys())
        model_candidates.extend([candidate for candidate in available_hf_models if candidate != model])

    accounts = router._get_provider_accounts(provider_name)
    if not accounts:
        raise HTTPException(status_code=503, detail=f"{provider_name} provider has no available accounts")

    response_format_raw = (response_format or "b64_json").lower()
    if response_format_raw not in {"b64_json", "url"}:
        raise HTTPException(status_code=400, detail="response_format must be 'b64_json' or 'url'")
    response_format_value = cast(Literal["b64_json", "url"], response_format_raw)

    size_raw = size or "1024x1024"
    allowed_sizes = {"auto", "1024x1024", "1536x1024", "1024x1536", "256x256", "512x512", "1792x1024", "1024x1792"}
    if size_raw not in allowed_sizes:
        raise HTTPException(status_code=400, detail=f"unsupported size: {size_raw}")
    size_value = cast(
        Literal["auto", "1024x1024", "1536x1024", "1024x1536", "256x256", "512x512", "1792x1024", "1024x1792"],
        size_raw,
    )

    count = int(n or 1)
    if count < 1:
        count = 1
    if count > 4:
        count = 4

    last_error = None
    for candidate_model in model_candidates:
        candidate_limit = router._config_limits.get("ImageGeneration", {}).get(provider_name, {}).get(candidate_model, model_limit)
        for account in accounts:
            account_id = account.get("id", "default")
            usage_key = router.get_usage_key(provider_name, candidate_model, account_id)
            if router._get_remaining_quota(usage_key) == 0:
                continue

            account_info = router.get_provider_account_info(provider_name, account_id)
            try:
                items: List[Dict[str, Any]] = []

                if provider_name == "HuggingFace":
                    client = InferenceClient(
                        provider="hf-inference",
                        api_key=account_info.get("api_key") or None,
                    )
                    width, height = _parse_image_size_for_hf(size_value)
                    hf_prompt = _build_hf_prompt(prompt, bool(source_image_bytes))
                    generation_kwargs: Dict[str, Any] = {
                        "model": candidate_model,
                        "negative_prompt": (
                            "off-topic, irrelevant subject, blurry, low quality, distorted anatomy, "
                            "extra limbs, watermark, logo, text overlay"
                        ),
                    }
                    if width and height:
                        generation_kwargs["width"] = width
                        generation_kwargs["height"] = height

                    # Keep defaults model-aware to improve prompt adherence.
                    if "FLUX.1-schnell" in candidate_model:
                        generation_kwargs["num_inference_steps"] = 8
                    elif "stable-diffusion-xl" in candidate_model:
                        generation_kwargs["num_inference_steps"] = 30
                        generation_kwargs["guidance_scale"] = 7.0

                    for _ in range(count):
                        image = None
                        if source_image_bytes and hasattr(client, "image_to_image"):
                            try:
                                image = client.image_to_image(
                                    image=source_image_bytes,
                                    prompt=hf_prompt,
                                    **generation_kwargs,
                                )
                                logger.info(
                                    "[ImageGeneration] HF image_to_image used (model=%s, account=%s)",
                                    candidate_model,
                                    account_id,
                                )
                            except Exception as img2img_error:
                                logger.warning(
                                    "[ImageGeneration] HF image_to_image failed, fallback to text_to_image: %s",
                                    img2img_error,
                                )

                        if image is None:
                            try:
                                image = client.text_to_image(hf_prompt, **generation_kwargs)
                            except TypeError:
                                image = client.text_to_image(hf_prompt, model=candidate_model)

                        buffer = io.BytesIO()
                        if hasattr(image, "save"):
                            image.save(buffer, format="PNG")
                            image_bytes = buffer.getvalue()
                        elif isinstance(image, (bytes, bytearray)):
                            image_bytes = bytes(image)
                        else:
                            raise RuntimeError(f"unsupported HF image response type: {type(image)!r}")

                        encoded = base64.b64encode(image_bytes).decode("ascii")
                        row: Dict[str, Any] = {"b64_json": encoded}
                        if response_format_value == "url":
                            row["url"] = f"data:image/png;base64,{encoded}"
                        items.append(row)
                else:
                    client = router._get_client(provider_name, account_id)
                    resp = client.images.generate(
                        model=candidate_model,
                        prompt=prompt,
                        n=count,
                        size=size_value,
                        response_format=response_format_value,
                    )
                    for item in getattr(resp, "data", []) or []:
                        row = {}
                        if hasattr(item, "b64_json") and getattr(item, "b64_json"):
                            row["b64_json"] = getattr(item, "b64_json")
                        if hasattr(item, "url") and getattr(item, "url"):
                            row["url"] = getattr(item, "url")
                        if hasattr(item, "revised_prompt") and getattr(item, "revised_prompt"):
                            row["revised_prompt"] = getattr(item, "revised_prompt")
                        items.append(row)

                router._decrement_quota(usage_key)

                return {
                    "created": int(time.time()),
                    "data": items,
                    "model": candidate_model,
                    "provider": provider_name,
                    "account_id": account_id,
                    "usage": {
                        "prompt_tokens": len(prompt) // 4,
                        "completion_tokens": 0,
                        "total_tokens": len(prompt) // 4,
                    },
                    "quota": router.get_model_quota_summary(provider_name, candidate_model, candidate_limit),
                }
            except Exception as exc:
                last_error = exc
                message = str(exc).lower()
                if "rate" in message or "quota" in message:
                    router._mark_quota_exhausted(usage_key)
                logger.warning(
                    "[ImageGeneration] provider=%s account=%s model=%s failed: %s",
                    provider_name,
                    account_id,
                    candidate_model,
                    exc,
                )
                # Deprecated or unavailable HF model: keep trying the next configured model.
                if provider_name == "HuggingFace" and any(token in message for token in ["410", "deprecated", "not found", "404"]):
                    continue
                if provider_name == "HuggingFace" and candidate_model != model:
                    continue

    raise HTTPException(
        status_code=503,
        detail=f"image generation unavailable for model {model}: {last_error}",
    )


# ── Scheduler for Auto-Reset ───────────────────────────────
scheduler = BackgroundScheduler()


def reset_quotas_job():
    """定時任務：每日重置 RPD 配額"""
    try:
        router = get_router()
        router.reset_all_quotas()
        logger.info("✅ [Scheduled] RPD 配額已自動重置")
    except Exception as e:
        logger.error(f"❌ [Scheduled] RPD 配額重置失敗: {e}")


def refresh_rpm_job():
    """定時任務：每半小時重置優先順序指標"""
    try:
        router = get_router()
        router.refresh_rpm_limit()
        logger.info("✅ [Scheduled] 優先順序指標已自動重置")
    except Exception as e:
        logger.error(f"❌ [Scheduled] 優先順序指標重置失敗: {e}")


def _extract_first_data_image_from_messages(messages: List[Any]) -> Optional[bytes]:
    """Extract first base64 data-url image bytes from user messages, if any."""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue

        content = message.get("content", "")
        if not isinstance(content, list):
            continue

        for part in content:
            if not isinstance(part, dict):
                continue

            part_type = str(part.get("type") or "")
            if part_type not in {"image_url", "input_image"}:
                continue

            image_url_obj = part.get("image_url")
            url = ""
            if isinstance(image_url_obj, dict) and isinstance(image_url_obj.get("url"), str):
                url = image_url_obj.get("url") or ""
            elif isinstance(part.get("url"), str):
                url = part.get("url") or ""

            if not url.startswith("data:image") or "," not in url:
                continue

            try:
                encoded = url.split(",", 1)[1]
                return base64.b64decode(encoded)
            except Exception as decode_error:
                logger.warning("[ImageGeneration] Failed to decode input data image: %s", decode_error)
                continue

    return None


def _parse_image_size_for_hf(size: str) -> tuple[Optional[int], Optional[int]]:
    if not size or size == "auto":
        return None, None

    match = re.match(r"^(\d+)x(\d+)$", size)
    if not match:
        return None, None

    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0 or height <= 0:
        return None, None
    return width, height


def _build_hf_prompt(user_prompt: str, has_source_image: bool) -> str:
    cleaned = (user_prompt or "").strip()
    if not cleaned:
        cleaned = "Create a high-quality image"

    base = (
        "Create a high-quality, visually coherent image that strictly follows this request. "
        "Main subject and scene must stay on-topic and central. "
        "If style is not specified, use cinematic lighting, detailed textures, and clean composition."
    )

    if has_source_image:
        return (
            f"{base} Use the provided source image as reference for composition and visual identity, "
            f"while applying the requested transformation.\n"
            f"User request: {cleaned}"
        )

    return f"{base}\nUser request: {cleaned}"


def _is_data_backed_image_request(user_text: str) -> bool:
    text = (user_text or "").lower()
    if not text:
        return False
    markers = [
        "k線",
        "k 棒",
        "candlestick",
        "股價",
        "股票",
        "走勢圖",
        "chart",
        "plot",
        "graph",
        "dashboard",
        "infographic",
        "統計圖",
        "數據圖",
        "市場",
        "weather map",
        "地圖",
        "timeline",
        "比較圖",
        "趨勢圖",
    ]
    return any(marker in text for marker in markers)


def _compact_search_text_for_image_prompt(search_text: str, max_chars: int = 2200) -> str:
    text = (search_text or "").strip()
    if not text:
        return ""
    blocks = [block.strip() for block in re.split(r"\n(?=\[\d+\]\s)", text) if block.strip()]
    compact = "\n\n".join(blocks[:3]).strip()
    if len(compact) > max_chars:
        compact = compact[:max_chars].rstrip() + "\n...[truncated]"
    return compact


async def _collect_web_evidence_for_image_request(
    router: ModelRouter,
    raw_messages: List[Any],
    root_query: str,
) -> tuple[str, List[str], List[Dict[str, Any]]]:
    clean_query = _sanitize_search_query(root_query)
    if not clean_query:
        return "", [], []

    planned_tasks = _llm_plan_web_search_tasks(router, raw_messages, clean_query)
    if not planned_tasks:
        planned_tasks = [{"query": clean_query, "need": "核心資料", "priority": 1}]

    evidence_rows: List[str] = []
    citations: List[str] = []
    executed_tasks: List[Dict[str, Any]] = []

    for idx, task in enumerate(planned_tasks[:3], 1):
        task_need = str(task.get("need", "核心資料")).strip() or "核心資料"
        task_query = _sanitize_search_query(str(task.get("query", "")))
        if not task_query:
            continue

        task_result = await handle_call_tool("search_web", {"query": task_query, "max_results": 5})
        task_text_parts: List[str] = []
        for item in task_result:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                task_text_parts.append(text)
        task_search_content = "\n\n".join(task_text_parts).strip()
        if not task_search_content:
            continue

        compact = _compact_search_text_for_image_prompt(task_search_content)
        if not compact:
            continue

        executed_tasks.append({"need": task_need, "query": task_query})
        evidence_rows.append(f"[Task {idx}] need: {task_need}\nquery: {task_query}\n{compact}")
        citations.extend(_extract_citations_from_content(task_search_content))

    deduped: List[str] = []
    seen = set()
    for citation in citations:
        normalized = citation.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)

    return "\n\n".join(evidence_rows).strip(), deduped, executed_tasks


def _build_researched_image_prompt(user_prompt: str, evidence_text: str) -> str:
    base_prompt = (user_prompt or "").strip()
    if not evidence_text.strip():
        return base_prompt
    return (
        f"{base_prompt}\n\n"
        "This is a data-grounded image request. Use the retrieved evidence below to determine the entities, dates, labels, values, and trend direction. "
        "Do not invent unsupported numbers or events. If some values are missing, keep the visual faithful to available evidence and avoid fabricated annotations.\n\n"
        f"Retrieved evidence:\n{evidence_text}"
    )


async def _collect_search_evidence_for_queries(
    queries: List[str],
    *,
    max_queries: int = 3,
    max_results: int = 5,
    max_chars_per_result: int = 3500,
) -> tuple[str, List[str], List[str]]:
    evidence_rows: List[str] = []
    citations: List[str] = []
    executed_queries: List[str] = []
    seen_queries = set()

    for query in queries:
        normalized_query = _sanitize_search_query(query)
        if not normalized_query or normalized_query in seen_queries:
            continue
        seen_queries.add(normalized_query)
        if len(executed_queries) >= max_queries:
            break

        task_result = await handle_call_tool("search_web", {"query": normalized_query, "max_results": max_results})
        task_text_parts: List[str] = []
        for item in task_result:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                task_text_parts.append(text)

        task_search_content = "\n\n".join(task_text_parts).strip()
        if not task_search_content:
            continue

        executed_queries.append(normalized_query)
        citations.extend(_extract_citations_from_content(task_search_content))
        trimmed_content = task_search_content[:max_chars_per_result]
        evidence_rows.append(
            f"[Follow-up Query {len(executed_queries)}] {normalized_query}\nresult:\n{trimmed_content}"
        )

    deduped_citations: List[str] = []
    seen_citations = set()
    for citation in citations:
        normalized = citation.strip()
        if normalized and normalized not in seen_citations:
            seen_citations.add(normalized)
            deduped_citations.append(normalized)

    return "\n\n".join(evidence_rows).strip(), deduped_citations, executed_queries

# ── Helper Module Imports ──────────────────────────────────
from app.search import (
    _sanitize_search_query,
    _generate_search_query_variants,
    _ddgs_text_search,
    _bing_html_search,
    _needs_source_enrichment,
    _enrich_search_results,
    _looks_low_quality,
)
from app.messages import (
    normalize_content,
    normalize_messages,
    prune_messages,
    _estimate_messages_tokens,
)
from app.multimodal import prepare_multimodal_messages, inject_payload_attachments
from app.tools import (
    _is_openclaw_web_search,
    _extract_last_user_query,
    _extract_citations_from_content,
    _extract_citations_from_tool_result,
    _extract_query_from_tool_payload,
    _looks_like_search_results,
    _extract_search_content_from_tool_result,
    _last_message_is_tool_result,
    _assistant_requested_tool_since_last_user,
    _has_web_search_tool,
    _pick_web_search_tool_name,
    _tool_label,
    _should_search,
    _llm_decide_web_search,
    _llm_plan_web_search_tasks,
    _llm_review_answer_completeness,
)
from app.response import build_chat_response, build_completion_response
from app.schemas import (
    Message,
    ChatCompletionRequest,
    CompletionRequest,
    DirectQueryRequest,
    ChatCompletionResponse,
    FileContentRequest,
    ImageGenerationRequest,
)
from app.auth import (
    init_db as auth_init_db,
    login as auth_login,
    logout as auth_logout,
    validate_session,
    register_account,
    get_account_by_id,
    list_all_accounts,
    set_account_active,
    generate_full_key,
    generate_agent_key,
    list_api_keys,
    revoke_api_key,
    validate_api_key,
    add_ip_whitelist,
    list_ip_whitelist,
    delete_ip_whitelist,
    get_audit_log,
    ALLOWED_SCOPES,
    purge_expired_sessions,
)


# ── API Endpoints ──────────────────────────────────────────
@app.api_route("/", methods=["GET", "POST"])
async def root():
    """服務資訊"""
    router = get_router()
    return {
        "service": APP_TITLE,
        "version": APP_VERSION,
        "status": "running",
        "categories": list(router._config_limits.keys()),
        "endpoints": {
            "chat": "/v1/chat/completions",
            "completion": "/v1/completions",
            "images": "/v1/images/generations",
            "models": "/v1/models",
            "health": "/health",
            "direct_query": "/v1/direct_query",
            "file_generate": "/v1/file/generate_content",
            "admin_reset": "/admin/reset_quotas",
            "admin_refresh": "/admin/refresh_rpm",
            "admin_status": "/admin/status",
        },
    }


@app.api_route("/health", methods=["GET", "POST"])
async def health_check():
    """健康檢查（超輕量）"""
    return {"status": "healthy", "server": "up"}


@app.api_route("/v1/models", methods=["GET", "POST"])
async def list_models():
    """列出所有可用模型"""
    router = get_router()
    models = []
    
    for cat, providers in router._config_limits.items():
        for provider, models_dict in providers.items():
            for model_id, rpd in models_dict.items():
                quota_summary = router.get_model_quota_summary(provider, model_id, rpd)
                models.append({
                    "id": model_id,
                    "object": "model",
                    "created": 0,
                    "owned_by": provider,
                    "category": cat,
                    "rpd_limit": quota_summary["rpd_limit"],
                    "rpd_remaining": quota_summary["rpd_remaining"],
                    "provider_account_count": quota_summary["provider_account_count"],
                    "provider_accounts": quota_summary["accounts"],
                    "capabilities": router.get_model_capabilities(model_id),
                })
    
    # 加入 auto 模型
    models.insert(0, {
        "id": "auto",
        "object": "model",
        "created": 0,
        "owned_by": "ModelRouter",
        "category": "auto",
        "description": "自動選擇最佳可用模型",
    })
    
    return {"object": "list", "data": models}


# ── MCP Endpoints ──────────────────────────────────────────

@app.get("/mcp/sse")
async def handle_sse(request: Request):
    """OpenClaw MCP tool connection endpoint."""
    global sse_transport
    if sse_transport is None:
        raise HTTPException(status_code=500, detail="SSE transport not initialized")
    async with sse_transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
        await mcp_server.run(read_stream, write_stream, mcp_server.create_initialization_options())


@app.post("/mcp/messages")
async def handle_messages(request: Request):
    """OpenClaw MCP JSON-RPC messages endpoint."""
    global sse_transport
    if sse_transport is None:
        raise HTTPException(status_code=500, detail="SSE transport not initialized")
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)


# ── Completion Endpoints ───────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    OpenAI-compatible Chat Completions API
    Compatible with OpenClaw / LiteLLM / vLLM-style clients.
    """
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    logger.info("RAW /v1/chat/completions keys: %s", list(raw.keys()))

    model = raw.get("model", "auto")
    raw_messages = raw.get("messages", [])
    temperature = raw.get("temperature", 0.7)
    max_tokens = raw.get("max_tokens", raw.get("max_completion_tokens"))
    stream = raw.get("stream", False)

    # 這些欄位先收下，但目前先不真正實作 tool calling
    tools = raw.get("tools", [])
    tool_choice = raw.get("tool_choice")

    target_category = raw.get("target_category")
    enable_memory = raw.get("enable_memory", True)

    logger.info(
        "OpenAI-compatible request: model=%s, stream=%s, tools=%d, tool_choice=%s",
        model, stream, len(tools) if isinstance(tools, list) else 0, str(tool_choice)
    )

    router = get_router()

    if isinstance(model, str) and model != "auto":
        model_caps = router.get_model_capabilities(model)
        if not model_caps.get("chat_capable", True):
            task = model_caps.get("task", "chat")
            if task == "image_generation":
                raise HTTPException(
                    status_code=400,
                    detail=f"model {model} is image-generation only. Use /v1/images/generations",
                )
            raise HTTPException(
                status_code=400,
                detail=f"model {model} is not chat-capable (task={task})",
            )

    if not isinstance(raw_messages, list):
        raise HTTPException(status_code=400, detail="messages must be a list")

    raw_messages = inject_payload_attachments(raw_messages, raw)
    if not raw_messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    try:
        raw_messages, multimodal_profile = prepare_multimodal_messages(raw_messages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("[Multimodal] preprocess failed")
        raise HTTPException(status_code=400, detail=f"multimodal preprocess failed: {exc}")

    if multimodal_profile.get("has_multimodal_input"):
        enable_memory = False
        logger.info("[Multimodal] attachments detected; memory injection disabled")

    latest_user_text = str(multimodal_profile.get("latest_user_text", ""))
    has_input_attachments = bool(
        multimodal_profile.get("has_image_input") or multimodal_profile.get("has_file_input")
    )

    # ── Unified Gemma-3-27B Intent Classification ─────────
    # Single call replaces separate check_need_image_generation +
    # decide_multimodal_category + check_need_log_rag.
    intent_result: Dict[str, Any] = {"intent": "text_chat", "multimodal_format": None, "reason": ""}
    if target_category is None:
        intent_result = router.classify_intent(
            user_message=latest_user_text,
            has_image_input=bool(multimodal_profile.get("has_image_input")),
            has_file_input=bool(multimodal_profile.get("has_file_input")),
            file_kinds=list(multimodal_profile.get("file_kinds", [])),
        )
        logger.info(
            "[IntentRouter] intent=%s multimodal_format=%s reason=%s",
            intent_result["intent"], intent_result.get("multimodal_format"), intent_result.get("reason"),
        )
        if intent_result["intent"] == "multimodal":
            target_category = "MultiModal"
            logger.info("[IntentRouter] target_category → MultiModal")
        elif intent_result["intent"] == "memory_query":
            enable_memory = True
            logger.info("[IntentRouter] intent=memory_query; forcing enable_memory=True")

    # ── Image Generation ──────────────────────────────────
    enable_auto_image_generation = bool(raw.get("enable_auto_image_generation", True))
    if (
        enable_auto_image_generation
        and latest_user_text
        and intent_result.get("intent") == "image_generation"
    ):
            image_prompt = latest_user_text
            image_citations: List[str] = []
            image_research_tasks: List[Dict[str, Any]] = []

            if _is_data_backed_image_request(latest_user_text):
                image_root_query = _sanitize_search_query(_extract_last_user_query(raw_messages))
                if not image_root_query:
                    image_root_query = _sanitize_search_query(latest_user_text)

                llm_need_search, llm_image_query, _llm_image_alts = _llm_decide_web_search(
                    router,
                    raw_messages,
                    image_root_query,
                )
                should_research_image = bool(image_root_query) and (
                    llm_need_search is True or _is_data_backed_image_request(latest_user_text)
                )

                if should_research_image:
                    image_query = llm_image_query or image_root_query
                    try:
                        evidence_text, image_citations, image_research_tasks = await _collect_web_evidence_for_image_request(
                            router,
                            raw_messages,
                            image_query,
                        )
                        if evidence_text:
                            image_prompt = _build_researched_image_prompt(latest_user_text, evidence_text)
                            logger.info(
                                "[AutoImage] research pipeline used; tasks=%d citations=%d",
                                len(image_research_tasks),
                                len(image_citations),
                            )
                    except Exception as image_search_exc:
                        logger.warning("[AutoImage] research pipeline failed: %s", image_search_exc)

            source_image_bytes = _extract_first_data_image_from_messages(raw_messages) if has_input_attachments else None
            if source_image_bytes:
                logger.info("[AutoImage] input image detected, attempting HF image-to-image generation")
            else:
                logger.info("[AutoImage] no source image found, using text-to-image generation")

            image_model = str(raw.get("image_model") or "black-forest-labs/FLUX.1-schnell")
            image_n = int(raw.get("image_n", 1) or 1)
            image_size = str(raw.get("image_size") or "1024x1024")
            image_response_format = str(raw.get("image_response_format") or "url")

            image_result = _run_image_generation_with_router(
                router,
                prompt=image_prompt,
                model=image_model,
                n=image_n,
                size=image_size,
                response_format=image_response_format,
                source_image_bytes=source_image_bytes,
            )

            if image_research_tasks:
                lines = ["已先查找所需資料，再完成圖片生成："]
            else:
                lines = ["已自動判斷為圖片生成需求，已完成生成："]
            for idx, item in enumerate(image_result.get("data", []), 1):
                image_url = str(item.get("url") or "")
                if image_url and image_url.startswith("data:image"):
                    lines.append(f"[{idx}] 圖片已生成，內容請查看下方圖片預覽")
                elif item.get("url"):
                    lines.append(f"[{idx}] {item['url']}")
                elif item.get("b64_json"):
                    lines.append(f"[{idx}] 圖片已生成，內容請查看下方圖片預覽")
                else:
                    lines.append(f"[{idx}] image generated")

            content = "\n".join(lines)
            response_body = build_chat_response(
                model=image_result.get("model", image_model),
                content=content,
                prompt_tokens=len(latest_user_text) // 4,
                completion_tokens=len(content) // 4,
            )
            response_body["images"] = image_result.get("data", [])
            response_body["provider"] = image_result.get("provider")
            response_body["account_id"] = image_result.get("account_id")
            response_body["quota"] = image_result.get("quota")
            if image_citations:
                response_body["citations"] = image_citations
            if image_research_tasks:
                response_body["research_tasks"] = image_research_tasks
            return response_body

    # tool-calling shim：若宣告了 web_search tool，先回 tool_calls 讓 OpenClaw 觸發工具。
    raw_tools = raw.get("tools", [])
    has_web_tool = isinstance(raw_tools, list) and _has_web_search_tool(raw_tools)
    last_is_tool = _last_message_is_tool_result(raw_messages)
    already_requested_in_turn = _assistant_requested_tool_since_last_user(raw_messages)

    logger.info(
        "[ToolShim] precheck: tools_count=%s has_web_tool=%s last_is_tool=%s already_requested_in_turn=%s",
        len(raw_tools) if isinstance(raw_tools, list) else 0,
        has_web_tool,
        last_is_tool,
        already_requested_in_turn,
    )

    if isinstance(raw_tools, list) and not has_web_tool:
        logger.info(
            "[ToolShim] web-search tool not found in tools; tool_labels=%s",
            [_tool_label(t) for t in raw_tools if isinstance(t, dict)],
        )

    if (
        isinstance(raw_tools, list)
        and has_web_tool
        and not last_is_tool
        and not already_requested_in_turn
    ):
        query = _sanitize_search_query(_extract_last_user_query(raw_messages))

        # Build a better seed query for attachment-centric requests.
        attachment_name_hints: List[str] = []
        for message in raw_messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content", "")
            text = normalize_content(content)
            for match in re.findall(r"(?im)^name:\s*(.+)$", text):
                hint = str(match).strip()
                if hint:
                    attachment_name_hints.append(hint)

        if attachment_name_hints:
            hint_text = " ".join(attachment_name_hints[:2])
            if not query or re.search(r"[0-9a-f]{8}-[0-9a-f-]{27,}\.pdf$", query, re.IGNORECASE):
                query = _sanitize_search_query(f"{latest_user_text} {hint_text}")

        llm_use_search, llm_query, llm_alternates = _llm_decide_web_search(router, raw_messages, query)
        should_search = llm_use_search if llm_use_search is not None else _should_search(query, tool_choice, raw_messages)

        # Force search for common paper-summary requests with file attachments.
        paper_request_re = re.compile(
            r"(論文|paper|arxiv|summary|summarize|摘要|demo\s*code|程式碼|實作)",
            re.IGNORECASE,
        )
        force_search = bool(
            multimodal_profile.get("has_file_input")
            and (
                "pdf" in list(multimodal_profile.get("file_kinds", []))
                or paper_request_re.search(latest_user_text or "")
            )
        )
        if force_search and not should_search:
            should_search = True
            logger.info("[ToolShim] force_search enabled for attachment-based paper request")

        if should_search and llm_query:
            query = llm_query
        if should_search and not query and latest_user_text:
            query = _sanitize_search_query(latest_user_text)

        tool_name = _pick_web_search_tool_name(raw_tools)
        logger.info(
            "[ToolShim] web-search tool detected; should_search=%s; llm_decision=%s; tool_choice=%s; query_preview=%s; selected_tool=%s; tool_labels=%s",
            should_search,
            str(llm_use_search),
            str(tool_choice),
            query[:80],
            tool_name,
            [_tool_label(t) for t in raw_tools if isinstance(t, dict)],
        )
        if should_search and not query:
            logger.warning("[ToolShim] should_search=True but extracted query is empty; skip emitting tool_calls")
        if should_search:
            if not query:
                # Avoid emitting an invalid tool call with empty query.
                # Fall through to normal model response path.
                pass
            else:
                planned_tasks = _llm_plan_web_search_tasks(router, raw_messages, query)
                if not planned_tasks:
                    planned_tasks = [{"query": query, "need": "核心資訊", "priority": 1}]

                model_used = str(raw.get("model", "auto"))
                request_id = f"chatcmpl-{int(time.time())}"
                created_ts = int(time.time())
                tool_calls = []
                stream_tool_calls = []
                for idx, task in enumerate(planned_tasks, 1):
                    task_query = _sanitize_search_query(str(task.get("query", "")))
                    if not task_query:
                        continue
                    tool_call = {
                        "id": f"call_web_search_{idx}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps({"query": task_query, "count": 5}),
                        },
                    }
                    tool_calls.append(tool_call)
                    stream_tool_calls.append({"index": idx - 1, **tool_call})

                if not tool_calls:
                    tool_calls = [{
                        "id": "call_web_search_1",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps({"query": query, "count": 5}),
                        },
                    }]
                    stream_tool_calls = [{"index": 0, **tool_calls[0]}]

                logger.info("[ToolShim] emitting %d planned web_search tool calls", len(tool_calls))

                if stream:
                    def sse_tool_call_generator():
                        chunk_tool = {
                            "id": request_id,
                            "object": "chat.completion.chunk",
                            "created": created_ts,
                            "model": model_used,
                            "choices": [{
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "tool_calls": stream_tool_calls,
                                },
                                "finish_reason": None,
                            }],
                        }
                        yield f"data: {json.dumps(chunk_tool)}\n\n"

                        chunk_done = {
                            "id": request_id,
                            "object": "chat.completion.chunk",
                            "created": created_ts,
                            "model": model_used,
                            "choices": [{
                                "index": 0,
                                "delta": {},
                                "finish_reason": "tool_calls",
                            }],
                        }
                        yield f"data: {json.dumps(chunk_done)}\n\n"
                        yield "data: [DONE]\n\n"

                    logger.info("[ToolShim] Streaming SSE tool_calls response (tool=%s)", tool_name)
                    return StreamingResponse(sse_tool_call_generator(), media_type="text/event-stream")

                return {
                    "id": request_id,
                    "object": "chat.completion",
                    "created": created_ts,
                    "model": model_used,
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": tool_calls,
                        },
                        "finish_reason": "tool_calls",
                    }],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }

    # OpenClaw web_search 執行回合：由本地 MCP search_web 處理。
    # 注意：若已進入 post-tool 回合（最後一則是 tool result），必須走下方 LLM 合成流程，
    # 不能在這裡 short-circuit，否則會跳過「使用者問題 + 搜尋結果 -> LLM 潤飾/分析」。
    if _is_openclaw_web_search(raw, request) and not _last_message_is_tool_result(raw_messages):
        query = _extract_last_user_query(raw_messages)
        if not query:
            raise HTTPException(status_code=400, detail="missing search query")

        logger.info("[OpenClaw web_search] query=%s", query)

        try:
            tool_result = await handle_call_tool("search_web", {"query": query, "max_results": 5})
        except Exception as e:
            logger.exception("[OpenClaw web_search] search_web failed")
            raise HTTPException(status_code=502, detail=f"search_web failed: {str(e)}")

        text_parts: List[str] = []
        for item in tool_result:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                text_parts.append(text)

        content = "\n\n".join(text_parts).strip() or "No search results"
        user_hint = _extract_last_user_query(raw_messages)
        content = _postprocess_user_response(user_hint, content)
        citations = _extract_citations_from_content(content)
        model_used = str(raw.get("model", "model_router/DDGS"))

        return {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_used,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "citations": citations,
        }

    # 有些 client 在第二輪只回傳 tool args（例如 {"query":"..."}），
    # 這裡補跑一次本地 search_web，避免模型看到空洞 tool 訊息而回答「無法上網」。
    if _last_message_is_tool_result(raw_messages):
        last_msg = raw_messages[-1]
        if isinstance(last_msg, dict):
            raw_tool_content = last_msg.get("content", "")
            normalized_tool_text = normalize_content(raw_tool_content)
            if not _looks_like_search_results(normalized_tool_text):
                fallback_query = _extract_query_from_tool_payload(raw_tool_content)
                if fallback_query:
                    logger.info(
                        "[ToolShim] Tool message contains args only; execute local search_web fallback, query=%s",
                        fallback_query,
                    )
                    try:
                        fallback_result = await handle_call_tool(
                            "search_web", {"query": fallback_query, "max_results": 5}
                        )
                        text_parts: List[str] = []
                        for item in fallback_result:
                            text = getattr(item, "text", None)
                            if isinstance(text, str) and text.strip():
                                text_parts.append(text)
                        merged_content = "\n\n".join(text_parts).strip()
                        if merged_content:
                            last_msg["content"] = merged_content
                            last_msg["name"] = "web_search"
                    except Exception:
                        logger.exception("[ToolShim] Local fallback search_web failed")

    # post-tool 回合：清理工具輸出格式，讓 LLM 能順利基於結果作答。
    post_tool_citations: List[str] = []
    post_tool_evidence_summary = ""
    if _last_message_is_tool_result(raw_messages):
        user_query_hint = _sanitize_search_query(_extract_last_user_query(raw_messages))
        last_msg = raw_messages[-1]
        if isinstance(last_msg, dict):
            raw_tool_content = normalize_content(last_msg.get("content", ""))
            post_tool_citations = _extract_citations_from_tool_result(raw_tool_content)
            # Extract clean search text from OpenClaw's JSON-wrapped format
            clean_content = _extract_search_content_from_tool_result(raw_tool_content)
            if clean_content and clean_content != raw_tool_content:
                logger.info("[ToolShim] Extracted clean tool content (%d chars)", len(clean_content))
                last_msg["content"] = clean_content
            post_tool_evidence_summary = clean_content[:9000]
            logger.info("[ToolShim] tool_text_preview=%s", clean_content[:200])

        # 強制約束 LLM 必須基於工具輸出作答（泛用，不限天氣）
        references_text = "\n".join(
            f"[{i}] {url}" for i, url in enumerate(post_tool_citations[:8], 1)
        )
        references_rule = (
            "在答案最後必須加上『參考來源』段落，逐條列出來源 URL。"
            "若有可用來源，內文關鍵句請以 [1]、[2] 這種格式標註。"
        ) if post_tool_citations else (
            "若當前工具輸出沒有可用 URL，請直接給出精簡結論，不要加入『無來源 URL』類型聲明。"
        )
        code_rule = (
            "此題為程式碼需求：請直接提供完整、可執行的程式碼實作（不要只給片段或概念摘要）。"
            "先給完整程式碼區塊，再用精簡條列說明設計重點與複雜度。"
            "若需求含多個功能（例如加減乘除），需在同一份程式中完整實作。"
            "必須包含 main()；必須附至少 2 組測資；必須列出時間複雜度與邊界條件。"
        ) if _is_code_generation_request(latest_user_text) else ""
        raw_messages.append({
            "role": "system",
            "content": (
                "你必須根據上方工具回傳的搜尋結果作答，整合資訊後給出清楚的中文回覆。"
                "若使用中文，必須使用繁體中文（zh-TW），禁止簡體中文。"
                f"{_research_answer_style_instruction()}"
                "回覆需先給結論，再補上必要重點（通常 3-6 點）；可略詳細，但避免無關資訊。"
                "禁止說你無法上網、無法查詢即時資訊或要求使用者自行查詢。"
                "若搜尋結果與問題無關，請誠實說明並建議使用者換個關鍵字重試。"
                "若同名詞對應多個實體，且使用者未要求比較，先回答最可能的單一目標，不要平均分配篇幅。"
                "意圖判斷優先順序：與使用者語言地區一致 > 與問題措辭一致 > 來源排序較前且一致性較高。"
                "當使用者語句為繁體中文時，優先採台灣語境實體；其他同名實體僅可在最後以一句『可能混淆』補充。"
                "不得捏造來源未提供的細節；每個關鍵事實必須可由來源支持。"
                "若仍無法回答精確數值，必須明確指出『缺少的欄位是什麼』（例如：缺昨日收盤欄位/缺結算價欄位），"
                "並指出最接近的已取得數據及其來源，不可只給泛泛道歉。"
                f"{code_rule}"
                f"{references_rule}"
                f"\n使用者問題：{user_query_hint or '(unknown)'}"
                f"\n可用來源如下：\n{references_text if references_text else '(目前無可用來源 URL，請明確說明)'}"
            ),
        })
        logger.info("[ToolShim] post-tool: cleaned content + system constraint added; routing to LLM for synthesis")

    # ── Proactive Auto Web Search ─────────────────────────
    # Multi-step pipeline:
    # 1) decide whether search is needed,
    # 2) plan information requirements as a task table,
    # 3) execute web_search task-by-task,
    # 4) inject gathered evidence for final synthesis.
    auto_search_citations: List[str] = []
    auto_search_evidence_summary = ""
    if (
        not has_web_tool
        and not last_is_tool
        and not _last_message_is_tool_result(raw_messages)
        and intent_result.get("intent") not in {"image_generation", "multimodal"}
        and not _is_openclaw_web_search(raw, request)
        and latest_user_text
    ):
        auto_query = _sanitize_search_query(_extract_last_user_query(raw_messages))
        if auto_query:
            llm_need_search, llm_auto_query, _llm_alts = _llm_decide_web_search(router, raw_messages, auto_query)
            if llm_need_search is True:
                final_auto_query = llm_auto_query or auto_query
                logger.info("[AutoSearch] proactive search triggered; root_query=%s", final_auto_query)
                try:
                    search_tasks = _llm_plan_web_search_tasks(router, raw_messages, final_auto_query)
                    if not search_tasks:
                        search_tasks = [{
                            "need": "回答問題所需的核心外部資訊",
                            "query": final_auto_query,
                            "why": "fallback",
                            "priority": 1,
                        }]

                    gathered_rows: List[str] = []
                    information_rows: List[str] = []
                    for idx, task in enumerate(search_tasks, 1):
                        task_need = str(task.get("need", "未命名資訊需求"))
                        task_query = _sanitize_search_query(str(task.get("query", "")))
                        task_why = str(task.get("why", ""))
                        if not task_query:
                            continue

                        information_rows.append(
                            f"{idx}. need={task_need} | query={task_query} | why={task_why or 'n/a'}"
                        )
                        logger.info("[AutoSearch] task %d/%d query=%s", idx, len(search_tasks), task_query)

                        task_result = await handle_call_tool(
                            "search_web", {"query": task_query, "max_results": 5}
                        )
                        task_text_parts: List[str] = []
                        for item in task_result:
                            text = getattr(item, "text", None)
                            if isinstance(text, str) and text.strip():
                                task_text_parts.append(text)
                        task_search_content = "\n\n".join(task_text_parts).strip()
                        if not task_search_content:
                            continue

                        auto_search_citations.extend(_extract_citations_from_content(task_search_content))
                        gathered_rows.append(
                            f"[Task {idx}] need: {task_need}\n"
                            f"query: {task_query}\n"
                            f"result:\n{task_search_content[:4500]}"
                        )

                    # Deduplicate citations while preserving order.
                    deduped_citations: List[str] = []
                    seen_citations = set()
                    for c in auto_search_citations:
                        normalized = c.strip()
                        if not normalized or normalized in seen_citations:
                            continue
                        seen_citations.add(normalized)
                        deduped_citations.append(normalized)
                    auto_search_citations = deduped_citations

                    references_text = "\n".join(
                        f"[{i}] {url}" for i, url in enumerate(auto_search_citations[:12], 1)
                    )
                    gathered_text = "\n\n".join(gathered_rows).strip()
                    if gathered_text:
                        auto_search_evidence_summary = gathered_text[:12000]
                        information_table = "\n".join(information_rows) if information_rows else "(無)"
                        raw_messages.append({
                            "role": "system",
                            "content": (
                                "以下是系統自動搜尋管線的輸出，請根據這些資料回答使用者問題。\n"
                                "流程已完成：需求拆解 -> 逐項搜尋 -> 資料彙整。\n"
                                f"{_research_answer_style_instruction()}\n"
                                f"原始問題：{auto_query}\n\n"
                                f"資訊需求表：\n{information_table}\n\n"
                                f"資料列表：\n{gathered_text}\n\n"
                                f"可用來源：\n{references_text if references_text else '(無)'}\n\n"
                                "不得捏造來源未提供的細節；每個關鍵事實必須可由來源支持。\n"
                                "在答案最後必須加上『參考來源』段落，逐條列出來源 URL。\n"
                                "若有可用來源，內文關鍵句請以 [1]、[2] 格式標註。\n"
                            ),
                        })
                        logger.info(
                            "[AutoSearch] injected planned evidence: tasks=%d gathered_chars=%d",
                            len(search_tasks),
                            len(gathered_text),
                        )
                except Exception as auto_exc:
                    logger.warning("[AutoSearch] search_web failed: %s", auto_exc)

    # ── has_tools 判斷 ──────────────────────────────────────
    has_tools = isinstance(tools, list) and len(tools) > 0
    if has_tools:
        enable_memory = False  # tool request 不注入 log memory
        logger.info("[Tools] has_tools=True, memory disabled, pruning aggressively")

    preserve_multimodal = bool(
        target_category == "MultiModal" and
        multimodal_profile.get("has_image_input")
    )
    messages = normalize_messages(raw_messages, preserve_multimodal=preserve_multimodal)

    if not messages:
        raise HTTPException(status_code=400, detail="no usable messages after normalization")

    # ── 裁切 messages ─────────────────────────────────────
    if has_tools:
        messages = prune_messages(
            messages,
            max_input_tokens=2800,
            keep_last=8,
            max_chars_per_message=2000,
        )
    else:
        messages = prune_messages(
            messages,
            max_input_tokens=6000,
            keep_last=10,
            max_chars_per_message=4000,
        )

    est_tokens = _estimate_messages_tokens(messages)
    logger.info("[Prune] has_tools=%s, messages=%d, est_tokens=%d", has_tools, len(messages), est_tokens)

    # Safety guard for Google OpenAI-compatible endpoint: keep at least one user content turn.
    messages = _ensure_user_message_for_generation(messages, latest_user_text)
    messages = _sanitize_messages_for_model(messages)
    messages = _ensure_user_message_for_generation(messages, latest_user_text)

    # ── 決定 target_category ─────────────────────────────
    model_name = model.lower() if isinstance(model, str) else "auto"

    if has_tools:
        # tool request：不走全輪詢，固定走 TextOnlyHigh（gpt-4o 系列）
        target_category = "TextOnlyHigh"
        logger.info("[Tools] Routing to TextOnlyHigh only (skip full broadcast)")
    elif model_name in ["textonlyhigh", "high"]:
        target_category = "TextOnlyHigh"
    elif model_name in ["chatonly", "chat", "reasoning"]:
        target_category = "ChatOnly"
    elif model_name in ["multimodal", "vision", "ocr"]:
        target_category = "MultiModal"
    elif model_name in ["textonlylow", "low"]:
        target_category = "TextOnlyLow"
    elif model_name != "auto":
        for cat, providers in router._config_limits.items():
            for provider, models_dict in providers.items():
                if model in models_dict:
                    target_category = cat
                    break

    try:
        kwargs = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        original_user_message = ""
        if messages and messages[-1]["role"] == "user":
            original_user_message = messages[-1]["content"]

            if enable_memory:
                need_log = router.check_need_log_rag(original_user_message)
                if need_log:
                    logger.info("[Memory] 檢測到需要查詢 log，正在載入...")
                    log_data = router.read_app_log(max_lines=100)
                    enhanced_message = f"""這是過去的 log 資訊，請依據 log 的內容回答問題：

{log_data}

[問題]: {original_user_message}"""
                    messages[-1]["content"] = enhanced_message
                    logger.info("[Memory] 已將 log 資訊注入到 prompt 中")
            else:
                logger.info("[Memory] 記憶功能已停用，使用原始方法")

        # ── Per-prompt identity prefix + system identity ──
        messages = _prepend_identity_prefix_to_messages(messages)
        messages = _inject_agent_system_prompt(messages)
        messages = _append_code_output_requirements(messages, latest_user_text)

        # 目前先不真正把 tools 傳下去，只做兼容吸收
        response = router.chat(
            messages=messages,
            target_category=target_category,
            include_chat_only=True,
            **kwargs
        )

        content = ""
        model_used = model
        final_generation_messages = list(messages)
        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content or ""
        if hasattr(response, "model"):
            model_used = response.model

        review_evidence_summary = auto_search_evidence_summary or post_tool_evidence_summary
        if latest_user_text and review_evidence_summary and intent_result.get("intent") == "text_chat":
            max_review_iterations = int(raw.get("max_review_iterations", 3) or 3)
            max_review_iterations = max(1, min(max_review_iterations, 6))

            for review_round in range(1, max_review_iterations + 1):
                review_result = _llm_review_answer_completeness(
                    router,
                    latest_user_text,
                    content,
                    review_evidence_summary,
                )
                if review_result.get("is_complete", True):
                    logger.info("[AnswerReview] round=%d passed", review_round)
                    break

                next_queries = [q for q in review_result.get("next_queries", []) if isinstance(q, str) and q.strip()]
                if not next_queries:
                    logger.info("[AnswerReview] round=%d incomplete but no next_queries; stop", review_round)
                    break

                logger.info(
                    "[AnswerReview] round=%d incomplete; reason=%s; next_queries=%s",
                    review_round,
                    review_result.get("reason", ""),
                    next_queries,
                )

                try:
                    followup_evidence, followup_citations, executed_queries = await _collect_search_evidence_for_queries(next_queries)
                    if not followup_evidence:
                        logger.info("[AnswerReview] round=%d follow-up evidence empty; stop", review_round)
                        break

                    followup_references = "\n".join(
                        f"[{i}] {url}" for i, url in enumerate(followup_citations[:8], 1)
                    )
                    review_feedback = (
                        f"審核不通過原因：{review_result.get('reason', '未提供')}\n"
                        f"缺漏重點：{'; '.join(review_result.get('missing', [])[:4]) or '請補足遺漏資訊'}\n"
                        f"補充查詢：{', '.join(executed_queries)}"
                    )

                    # Re-run intent classification with review feedback as additional context.
                    feedback_intent_input = f"{latest_user_text}\n\n[REVIEW_FEEDBACK]\n{review_feedback}"
                    retry_intent = router.classify_intent(
                        user_message=feedback_intent_input,
                        has_image_input=bool(multimodal_profile.get("has_image_input")),
                        has_file_input=bool(multimodal_profile.get("has_file_input")),
                        file_kinds=list(multimodal_profile.get("file_kinds", [])),
                    )

                    retry_target_category = target_category
                    if retry_intent.get("intent") == "multimodal":
                        retry_target_category = "MultiModal"
                    elif retry_intent.get("intent") == "text_chat":
                        retry_target_category = target_category or "TextOnlyHigh"

                    retry_messages = list(messages) + [{
                        "role": "system",
                        "content": (
                            f"第 {review_round} 輪審核未通過，請根據以下資訊重寫答案。\n"
                            f"{review_feedback}\n\n"
                            f"補充證據：\n{followup_evidence}\n\n"
                            f"補充來源：\n{followup_references if followup_references else '(無)'}\n"
                            "請輸出更完整且直接可用的最終答案，避免重複或空泛描述。"
                        ),
                    }]

                    retry_response = router.chat(
                        messages=retry_messages,
                        target_category=retry_target_category,
                        include_chat_only=True,
                        **kwargs
                    )
                    if retry_response.choices and retry_response.choices[0].message:
                        content = retry_response.choices[0].message.content or content
                    if hasattr(retry_response, "model"):
                        model_used = retry_response.model

                    final_generation_messages = retry_messages
                    review_evidence_summary = f"{review_evidence_summary}\n\n{followup_evidence}"[:12000]
                    for citation in followup_citations:
                        if citation not in auto_search_citations:
                            auto_search_citations.append(citation)

                    logger.info(
                        "[AnswerReview] round=%d regenerated; retry_intent=%s retry_target=%s",
                        review_round,
                        retry_intent.get("intent"),
                        retry_target_category,
                    )
                except Exception as review_exc:
                    logger.warning("[AnswerReview] round=%d failed: %s", review_round, review_exc)
                    break

        content = _postprocess_user_response(latest_user_text, content)

        if enable_memory and original_user_message and content:
            router.add_to_history(original_user_message, content)
            logger.info("[Memory] 已將對話添加到歷史記錄")

        prompt_tokens = sum(len(m["content"]) // 4 for m in final_generation_messages)
        completion_tokens = len(content) // 4

        if stream:
            # SSE streaming：先送 role chunk，再送 content chunk，最後 [DONE]
            request_id = f"chatcmpl-{int(time.time())}"
            created_ts = int(time.time())

            def sse_generator():
                # chunk 1: role
                chunk_role = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_used,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk_role)}\n\n"

                # chunk 2: full content in one shot（非 token-by-token，夠用）
                chunk_content = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_used,
                    "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk_content)}\n\n"

                # chunk 3: finish
                chunk_done = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_used,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(chunk_done)}\n\n"
                yield "data: [DONE]\n\n"

            logger.info("[Stream] Sending SSE response (model=%s, len=%d)", model_used, len(content))
            return StreamingResponse(sse_generator(), media_type="text/event-stream")

        response_body = build_chat_response(
            model=model_used,
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        if post_tool_citations:
            response_body["citations"] = post_tool_citations
        if auto_search_citations and not post_tool_citations:
            response_body["citations"] = auto_search_citations
        return response_body

    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Chat error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/completions")
async def completions(request: CompletionRequest):
    """
    OpenAI Completions API (legacy)
    
    auto 模式：Low → High（反轉順序，不包含 ChatOnly 推理模型）
    適用於批量處理、數據清洗等複雜任務
    """
    if request.stream:
        raise HTTPException(status_code=400, detail="stream=True not supported yet")
    
    if not request.prompt:
        raise HTTPException(status_code=400, detail="prompt must not be empty")
    
    router = get_router()
    
    # 轉換為 chat 格式（每個 prompt 前加身份前綴）
    messages = [{"role": "user", "content": _wrap_identity_question(request.prompt)}]
    messages = _inject_agent_system_prompt(messages)
    messages = _append_code_output_requirements(messages, request.prompt)
    
    try:
        kwargs = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        
        # Legacy completions: Low → High 顺序，不包含 ChatOnly
        response = router.chat(
            messages=messages, 
            reverse_order=True,  # 反轉順序：Low → High
            **kwargs
        )
        
        content = ""
        model_used = request.model
        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content or ""
        if hasattr(response, 'model'):
            model_used = response.model
        
        prompt_tokens = len(request.prompt) // 4
        completion_tokens = len(content) // 4
        
        return build_completion_response(
            model=model_used,
            text=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Completion error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Admin Endpoints ────────────────────────────────────────
@app.post("/admin/reset_quotas")
async def admin_reset_quotas():
    """重置所有 RPD 配額（每日執行）"""
    router = get_router()
    router.reset_all_quotas()
    return {"status": "ok", "message": "所有配額已重置"}


@app.post("/admin/refresh_rpm")
async def admin_refresh_rpm():
    """重置優先順序指標（每半小時執行）"""
    router = get_router()
    router.refresh_rpm_limit()
    return {"status": "ok", "message": "優先順序指標已重置"}


@app.get("/admin/status")
async def admin_status():
    """查看配額狀態"""
    router = get_router()
    
    status = {
        "priority_flags": router.priority_flags,
        "quotas": {},
        "internal_usage": router.get_internal_usage_stats(),
    }
    
    for cat, providers in router._config_limits.items():
        status["quotas"][cat] = {}
        for provider, models_dict in providers.items():
            status["quotas"][cat][provider] = {}
            for model_id, rpd_limit in models_dict.items():
                quota_summary = router.get_model_quota_summary(provider, model_id, rpd_limit)
                status["quotas"][cat][provider][model_id] = {
                    "limit": quota_summary["rpd_limit"],
                    "remaining": quota_summary["rpd_remaining"],
                    "used": (
                        0
                        if quota_summary["rpd_limit"] == -1 or quota_summary["rpd_remaining"] == -1
                        else max(quota_summary["rpd_limit"] - quota_summary["rpd_remaining"], 0)
                    ),
                    "accounts": quota_summary["accounts"],
                }
    
    return status


@app.get("/admin/logs")
async def admin_logs():
    """獲取最新的 100 行日誌"""
    import subprocess
    try:
        # 優先檢查 app/app.log
        log_files = [
            "app/app.log",
            "modelrouter.log",
            "api.log",
            "/var/log/modelrouter.log",
            "/tmp/modelrouter.log"
        ]
        
        for log_file in log_files:
            try:
                if os.path.exists(log_file):
                    result = subprocess.run(
                        ["tail", "-n", "100", log_file],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0 and result.stdout:
                        return {
                            "logs": result.stdout,
                            "source": log_file,
                            "timestamp": time.time()
                        }
            except Exception as e:
                logger.warning(f"Failed to read {log_file}: {e}")
                continue
        
        # 如果找不到日誌文件，嘗試 systemd
        try:
            result = subprocess.run(
                ["journalctl", "-u", "modelrouter", "-n", "100", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout:
                return {
                    "logs": result.stdout,
                    "source": "systemd",
                    "timestamp": time.time()
                }
        except:
            pass
        
        # 沒有找到任何日誌
        return {
            "logs": "沒有找到日誌文件\n\n提示：可以將日誌輸出重定向到文件：\n  python api.py > app/app.log 2>&1",
            "source": "none",
            "timestamp": time.time()
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch logs: {e}")
        raise HTTPException(status_code=500, detail=f"無法獲取日誌: {str(e)}")


@app.post("/v1/direct_query")
async def direct_query(request: DirectQueryRequest):
    """
    直接查詢指定的 model 和 provider
    
    Args:
        model_name: 模型名稱 (例如: "gemma-3-7b-it", "gpt-4o")
        provider: 提供商 ("GitHub", "Google", or "Ollama")
        prompt: 提示詞
        temperature: 溫度參數
        max_tokens: 最大生成 token 數
    
    Returns:
        模型的回答
        
    Raises:
        HTTPException 500: 模型不存在、沒有額度、或調用失敗
    """
    router = get_router()
    
    # 驗證 provider
    provider_lower = request.provider.lower()
    if provider_lower not in ["github", "google", "ollama", "huggingface"]:
        raise HTTPException(
            status_code=400, 
            detail=f"不支持的 provider: {request.provider}。支持的 provider: GitHub, Google, Ollama, HuggingFace"
        )
    
    # 轉換為 chat 格式（每個 prompt 前加身份前綴）
    messages = [{"role": "user", "content": _wrap_identity_question(request.prompt)}]
    messages = _inject_agent_system_prompt(messages)
    messages = _append_code_output_requirements(messages, request.prompt)
    
    try:
        # 準備參數
        kwargs = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        
        # 根據模型調整參數
        prepared_kwargs = router._prepare_kwargs(request.model_name, kwargs)
        
        if provider_lower == "github":
            provider_name = "GitHub"
        elif provider_lower == "huggingface":
            provider_name = "HuggingFace"
        else:
            provider_name = request.provider.capitalize()
        accounts = router._get_provider_accounts(provider_name)
        if not accounts:
            raise HTTPException(status_code=500, detail=f"{request.provider} 沒有可用帳戶")

        response = None
        selected_account = "default"
        for account in accounts:
            account_id = account.get("id", "default")
            usage_key = router.get_usage_key(provider_name, request.model_name, account_id)
            remaining = router._get_remaining_quota(usage_key)
            if remaining == 0:
                continue

            client = router._get_client(provider_name, account_id)

            logger.info(
                "[Direct Query] Provider: %s | Account: %s | Model: %s | Prompt: %s...",
                request.provider,
                account_id,
                request.model_name,
                request.prompt[:50],
            )

            try:
                response = router._call_with_retry(
                    client=client,
                    model_id=request.model_name,
                    messages=messages,
                    **prepared_kwargs
                )
                router._decrement_quota(usage_key)
                selected_account = account_id
                break
            except Exception as exc:
                if "rate" in str(exc).lower() or "quota" in str(exc).lower():
                    router._mark_quota_exhausted(usage_key)
                logger.warning(
                    "[Direct Query] Provider: %s | Account: %s failed: %s",
                    request.provider,
                    account_id,
                    exc,
                )
                continue

        if response is None:
            raise HTTPException(status_code=503, detail=f"{request.provider} 所有帳戶目前都不可用或配額已滿")
        
        # 提取回答
        content = ""
        if response.choices and len(response.choices) > 0:
            choice = response.choices[0]
            if hasattr(choice, 'message') and choice.message:
                content = choice.message.content or ""
        
        if not content:
            logger.warning(f"[Direct Query] {request.model_name} 返回空答案")
            raise HTTPException(
                status_code=500,
                detail=f"模型 {request.model_name} 返回空答案，可能是配額不足或參數問題"
            )
        
        # 估算 tokens
        prompt_tokens = len(request.prompt) // 4
        completion_tokens = len(content) // 4
        
        logger.info(f"[Direct Query Success] Model: {request.model_name} | Answer: {content[:100]}...")
        
        response_body = build_chat_response(
            model=request.model_name,
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        response_body["provider"] = provider_name
        response_body["account_id"] = selected_account
        return response_body
        
    except HTTPException:
        # 重新拋出 HTTP 異常
        raise
    except Exception as e:
        # 處理各種錯誤
        error_msg = str(e)
        logger.error(f"[Direct Query Error] Provider: {request.provider} | Model: {request.model_name} | Error: {type(e).__name__}: {error_msg}")
        
        # 根據錯誤類型返回不同的錯誤信息
        if "rate" in error_msg.lower() or "quota" in error_msg.lower():
            detail_msg = f"模型 {request.model_name} 配額不足或達到速率限制"
        elif "not found" in error_msg.lower() or "does not exist" in error_msg.lower():
            detail_msg = f"模型 {request.model_name} 不存在或在 {request.provider} 上不可用"
        elif "authentication" in error_msg.lower() or "api key" in error_msg.lower():
            detail_msg = f"{request.provider} API 密鑰無效或未設置"
        else:
            detail_msg = f"調用模型 {request.model_name} 失敗: {error_msg}"
        
        raise HTTPException(status_code=500, detail=detail_msg)


@app.post("/v1/images/generations")
async def image_generations(request: ImageGenerationRequest):
    """OpenAI-compatible image generation endpoint backed by Google Imagen models."""
    router = get_router()

    prompt = (request.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    model = request.model or "black-forest-labs/FLUX.1-schnell"
    caps = router.get_model_capabilities(model)
    if caps.get("task") != "image_generation":
        raise HTTPException(status_code=400, detail=f"model {model} is not an image generation model")

    return _run_image_generation_with_router(
        router,
        prompt=prompt,
        model=model,
        n=int(request.n or 1),
        size=str(request.size or "1024x1024"),
        response_format=str(request.response_format or "b64_json"),
    )


@app.post("/v1/file/generate_content")
async def file_generate_content(
    file: UploadFile = File(...),
    prompt: str = Form(...),
    temperature: Optional[float] = Form(0.7),
    max_tokens: Optional[int] = Form(None)
):
    """
    上傳文件（圖片）並生成內容
    
    使用 Google Gemini gemma-3-12b-it 模型處理圖片和文件
    
    Args:
        file: 上傳的文件（支持圖片格式：jpg, png, gif, webp 等）
        prompt: 提示詞（例如：請描述這張圖片內容）
        temperature: 溫度參數 (0-1)
        max_tokens: 最大生成 token 數
    
    Returns:
        生成的內容
        
    Example:
        curl -X POST http://localhost:8000/v1/file/generate_content \\
            -F "file=@image.jpg" \\
            -F "prompt=請描述這張圖片內容"
    """
    # 檢查 Google API Key 是否配置
    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        raise HTTPException(
            status_code=503,
            detail="GOOGLE_API_KEY 未設定，無法使用文件上傳功能"
        )
    if genai is None:
        raise HTTPException(
            status_code=503,
            detail="google-generativeai 套件未安裝，無法使用文件上傳功能"
        )
    
    # 固定使用 gemma-3-12b-it 模型
    model_name = "gemma-3-12b-it"
    
    # 檢查文件名
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="文件名無效"
        )
    
    temp_file_path = None
    try:
        # 保存上傳的文件到臨時文件
        file_extension = os.path.splitext(file.filename)[1] or '.jpg'
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
            logger.info(f"[File Upload] 文件已保存到: {temp_file_path}, 大小: {len(content)} bytes")
        
        # 上傳文件到 Google Generative AI
        logger.info(f"[File Upload] 上傳文件到 Google Generative AI...")
        uploaded_file = genai.upload_file(temp_file_path)  # type: ignore[attr-defined]
        logger.info(f"[File Upload] 文件上傳成功，URI: {uploaded_file.uri}")
        
        # 創建模型實例
        model = genai.GenerativeModel(model_name)  # type: ignore[attr-defined]
        
        # 準備生成配置
        generation_config = {
            "temperature": temperature,
        }
        if max_tokens:
            generation_config["max_output_tokens"] = max_tokens
        
        # 生成內容
        logger.info(f"[File Generate] 使用模型 {model_name} 生成內容...")
        logger.info(f"[File Generate] Prompt: {prompt[:100]}...")
        
        wrapped_prompt = _wrap_identity_question(prompt)
        response = model.generate_content(
            [uploaded_file, wrapped_prompt],
            generation_config=generation_config  # type: ignore[arg-type]
        )
        
        # 提取生成的內容
        content_text = response.text if hasattr(response, 'text') else ""
        
        if not content_text:
            logger.warning(f"[File Generate] 模型返回空答案")
            raise HTTPException(
                status_code=500,
                detail="模型返回空答案，請檢查文件格式或提示詞"
            )
        
        logger.info(f"[File Generate Success] 生成內容長度: {len(content_text)} 字符")
        logger.info(f"[File Generate Success] 內容預覽: {content_text[:200]}...")
        
        # 估算 tokens
        prompt_tokens = len(prompt) // 4 + 100  # 文件本身也算 tokens
        completion_tokens = len(content_text) // 4
        
        # 清理上傳的文件（可選）
        try:
            genai.delete_file(uploaded_file.name)  # type: ignore[attr-defined]
            logger.info(f"[File Cleanup] 已刪除遠程文件: {uploaded_file.name}")
        except Exception as e:
            logger.warning(f"[File Cleanup] 刪除遠程文件失敗: {e}")
        
        return build_chat_response(
            model=model_name,
            content=content_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"[File Generate Error] {type(e).__name__}: {error_msg}")
        
        if "api key" in error_msg.lower() or "authentication" in error_msg.lower():
            detail_msg = "Google API 密鑰無效或未設置"
        elif "quota" in error_msg.lower() or "rate" in error_msg.lower():
            detail_msg = "Google API 配額不足或達到速率限制"
        elif "file" in error_msg.lower() and "format" in error_msg.lower():
            detail_msg = "不支持的文件格式，請上傳圖片文件（jpg, png, gif, webp）"
        else:
            detail_msg = f"生成內容失敗: {error_msg}"
        
        raise HTTPException(status_code=500, detail=detail_msg)
    
    finally:
        # 清理本地臨時文件
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
                logger.info(f"[File Cleanup] 已刪除本地臨時文件: {temp_file_path}")
            except Exception as e:
                logger.warning(f"[File Cleanup] 刪除本地臨時文件失敗: {e}")


# ── Auth Endpoints ─────────────────────────────────────────

@app.post("/auth/register")
async def auth_register(request: Request):
    """
    建立新帳號。
    第一個註冊的帳號自動成為管理員。
    """
    body = await request.json()
    username = str(body.get("username", "")).strip()
    email = str(body.get("email", "")).strip()
    password = str(body.get("password", ""))
    if not username or not email or not password:
        raise HTTPException(status_code=422, detail="username / email / password 不可為空")
    try:
        acct = register_account(username, email, password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return acct


@app.post("/auth/login")
async def auth_login_endpoint(request: Request):
    """
    登入並取得 session token。
    返回的 token 請存放在記憶體中（勿寫入 localStorage 或磁碟）。
    """
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    result = auth_login(username, password)
    if not result:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    return result


@app.post("/auth/logout")
async def auth_logout_endpoint(request: Request):
    """登出並撤銷 session token。"""
    token = request.headers.get("X-Session-Token", "").strip()
    if token:
        auth_logout(token)
    return {"status": "ok", "message": "已登出"}


@app.get("/auth/me")
async def auth_me(request: Request):
    """返回目前登入帳號資訊。"""
    acct = getattr(request.state, "auth", None)
    if not acct:
        raise HTTPException(status_code=401, detail="未登入")
    full = get_account_by_id(acct["account_id"])
    if not full:
        raise HTTPException(status_code=404, detail="帳號不存在")
    return full


# ── API Key management endpoints ───────────────────────────

@app.get("/auth/keys")
async def auth_list_keys(request: Request):
    """列出自己的所有 API key（不含 hash 或完整 key）。"""
    acct = getattr(request.state, "auth", None)
    if not acct:
        raise HTTPException(status_code=401, detail="未登入")
    return list_api_keys(acct["account_id"])


@app.post("/auth/keys/full")
async def auth_create_full_key(request: Request):
    """
    產生全存取 API key (mk_)。
    完整 key 只在這次回應中顯示一次，之後無法再取得。
    僅限管理員使用。
    """
    acct = getattr(request.state, "auth", None)
    if not acct:
        raise HTTPException(status_code=401, detail="未登入")
    if not acct.get("is_admin"):
        raise HTTPException(status_code=403, detail="僅管理員可以產生全存取 key")
    body = await request.json()
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="name 不可為空")
    try:
        full_key, record = generate_full_key(acct["account_id"], name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    record["full_key"] = full_key  # Only shown here, never again
    return record


@app.post("/auth/keys/agent")
async def auth_create_agent_key(request: Request):
    """
    產生受限 agent API key (ma_)。
    需要指定 scopes（限定可呼叫的端點）、過期時間、RPM 上限。
    完整 key 只在這次回應中顯示一次。
    """
    acct = getattr(request.state, "auth", None)
    if not acct:
        raise HTTPException(status_code=401, detail="未登入")
    body = await request.json()
    name = str(body.get("name", "")).strip()
    scopes = body.get("scopes", [])
    expires_hours = int(body.get("expires_hours", 24))
    rpm_limit = int(body.get("rpm_limit", 60))

    if not name:
        raise HTTPException(status_code=422, detail="name 不可為空")
    if not isinstance(scopes, list):
        raise HTTPException(status_code=422, detail="scopes 必須是陣列")
    try:
        full_key, record = generate_agent_key(
            acct["account_id"], name, scopes, expires_hours, rpm_limit
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    record["full_key"] = full_key  # Only shown here, never again
    return record


@app.delete("/auth/keys/{key_id}")
async def auth_revoke_key(key_id: int, request: Request):
    """撤銷指定 API key。"""
    acct = getattr(request.state, "auth", None)
    if not acct:
        raise HTTPException(status_code=401, detail="未登入")
    ok = revoke_api_key(key_id, acct["account_id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Key 不存在或不屬於此帳號")
    return {"status": "ok", "message": f"Key {key_id} 已撤銷"}


# ── IP Whitelist endpoints ──────────────────────────────────

@app.get("/auth/whitelist")
async def auth_list_whitelist(request: Request):
    """列出此帳號的 IP 白名單。"""
    acct = getattr(request.state, "auth", None)
    if not acct:
        raise HTTPException(status_code=401, detail="未登入")
    return list_ip_whitelist(acct["account_id"])


@app.post("/auth/whitelist")
async def auth_add_whitelist(request: Request):
    """新增 IP / CIDR 到白名單。空白名單代表允許所有 IP。"""
    acct = getattr(request.state, "auth", None)
    if not acct:
        raise HTTPException(status_code=401, detail="未登入")
    body = await request.json()
    ip_cidr = str(body.get("ip_cidr", "")).strip()
    description = str(body.get("description", "")).strip()
    if not ip_cidr:
        raise HTTPException(status_code=422, detail="ip_cidr 不可為空")
    try:
        entry = add_ip_whitelist(acct["account_id"], ip_cidr, description)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return entry


@app.delete("/auth/whitelist/{entry_id}")
async def auth_delete_whitelist(entry_id: int, request: Request):
    """從白名單移除一筆 IP 規則。"""
    acct = getattr(request.state, "auth", None)
    if not acct:
        raise HTTPException(status_code=401, detail="未登入")
    ok = delete_ip_whitelist(entry_id, acct["account_id"])
    if not ok:
        raise HTTPException(status_code=404, detail="白名單項目不存在")
    return {"status": "ok", "message": f"Entry {entry_id} 已移除"}


@app.get("/auth/audit")
async def auth_audit_log(request: Request):
    """查看此帳號的 API key 使用稽核紀錄（最新 100 筆）。"""
    acct = getattr(request.state, "auth", None)
    if not acct:
        raise HTTPException(status_code=401, detail="未登入")
    return get_audit_log(acct["account_id"])


@app.get("/auth/scopes")
async def auth_list_scopes():
    """列出 agent key 可用的 scope 清單。"""
    return {"scopes": sorted(ALLOWED_SCOPES)}


# ── Admin: Account management ───────────────────────────────

@app.get("/admin/accounts")
async def admin_list_accounts(request: Request):
    """（管理員）列出所有帳號。"""
    acct = getattr(request.state, "auth", None)
    if not acct or not acct.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理員權限")
    return list_all_accounts()


@app.post("/admin/accounts/{account_id}/activate")
async def admin_activate_account(account_id: int, request: Request):
    """（管理員）啟用帳號。"""
    acct = getattr(request.state, "auth", None)
    if not acct or not acct.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理員權限")
    ok = set_account_active(account_id, True)
    if not ok:
        raise HTTPException(status_code=404, detail="帳號不存在")
    return {"status": "ok", "message": f"帳號 {account_id} 已啟用"}


@app.post("/admin/accounts/{account_id}/deactivate")
async def admin_deactivate_account(account_id: int, request: Request):
    """（管理員）停用帳號（不可停用自己）。"""
    acct = getattr(request.state, "auth", None)
    if not acct or not acct.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理員權限")
    if account_id == acct["account_id"]:
        raise HTTPException(status_code=400, detail="不可停用自己的帳號")
    ok = set_account_active(account_id, False)
    if not ok:
        raise HTTPException(status_code=404, detail="帳號不存在")
    return {"status": "ok", "message": f"帳號 {account_id} 已停用"}


# ── Main ───────────────────────────────────────────────────
if __name__ == "__main__":
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8000"))
    
    logger.info(f"🚀 Starting {APP_TITLE} v{APP_VERSION}")
    logger.info(f"📍 Listening on http://{host}:{port}")
    logger.info(f"📚 API docs: http://{host}:{port}/docs")
    
    uvicorn.run(app, host=host, port=port)
