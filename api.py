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
from typing import List, Optional, Dict, Any

from dotenv import load_dotenv
load_dotenv()  # 載入 .env 檔案

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import google.generativeai as genai
import contextlib

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
    if google_api_key:
        genai.configure(api_key=google_api_key)  # type: ignore[attr-defined]
        logger.info("✅ Google Generative AI 已初始化")
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
    
    yield
    
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

# ── Router Instance (Singleton) ────────────────────────────
router_instance: Optional[ModelRouter] = None


def get_router() -> ModelRouter:
    global router_instance
    if router_instance is None:
        router_instance = ModelRouter()
    return router_instance


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
from app.multimodal import prepare_multimodal_messages
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
)
from app.response import build_chat_response, build_completion_response
from app.schemas import (
    Message,
    ChatCompletionRequest,
    CompletionRequest,
    DirectQueryRequest,
    ChatCompletionResponse,
    FileContentRequest,
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
                usage_key = router.get_usage_key(provider, model_id)
                remaining = router._local_remaining_rpd.get(usage_key, rpd)
                models.append({
                    "id": model_id,
                    "object": "model",
                    "created": 0,
                    "owned_by": provider,
                    "category": cat,
                    "rpd_limit": rpd,
                    "rpd_remaining": remaining,
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

    if not isinstance(raw_messages, list) or not raw_messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    router = get_router()

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

    if target_category is None:
        decided_category = router.decide_multimodal_category(raw_messages, multimodal_profile)
        if decided_category:
            target_category = decided_category
            logger.info("[Multimodal] target_category auto-selected: %s", decided_category)

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
        llm_use_search, llm_query, llm_alternates = _llm_decide_web_search(router, raw_messages, query)
        should_search = llm_use_search if llm_use_search is not None else _should_search(query, tool_choice, raw_messages)
        if should_search and llm_query:
            query = llm_query
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
                model_used = str(raw.get("model", "auto"))
                request_id = f"chatcmpl-{int(time.time())}"
                created_ts = int(time.time())
                tool_call = {
                    "id": "call_web_search_1",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps({"query": query, "count": 5}),
                    },
                }
                stream_tool_call = {
                    "index": 0,
                    **tool_call,
                }

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
                                    "tool_calls": [stream_tool_call],
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
                            "tool_calls": [tool_call],
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
        citations = _extract_citations_from_content(content)
        model_used = str(raw.get("model", "perplexity/sonar-pro"))

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
            logger.info("[ToolShim] tool_text_preview=%s", clean_content[:200])

        # 強制約束 LLM 必須基於工具輸出作答（泛用，不限天氣）
        references_text = "\n".join(
            f"[{i}] {url}" for i, url in enumerate(post_tool_citations[:8], 1)
        )
        raw_messages.append({
            "role": "system",
            "content": (
                "你必須根據上方工具回傳的搜尋結果作答，整合資訊後給出清楚的中文回覆。"
                "禁止說你無法上網、無法查詢即時資訊或要求使用者自行查詢。"
                "若搜尋結果與問題無關，請誠實說明並建議使用者換個關鍵字重試。"
                "若同名詞對應多個實體，且使用者未要求比較，先回答最可能的單一目標，不要平均分配篇幅。"
                "意圖判斷優先順序：與使用者語言地區一致 > 與問題措辭一致 > 來源排序較前且一致性較高。"
                "當使用者語句為繁體中文時，優先採台灣語境實體；其他同名實體僅可在最後以一句『可能混淆』補充。"
                "不得捏造來源未提供的細節；每個關鍵事實必須可由來源支持。"
                "若仍無法回答精確數值，必須明確指出『缺少的欄位是什麼』（例如：缺昨日收盤欄位/缺結算價欄位），"
                "並指出最接近的已取得數據及其來源，不可只給泛泛道歉。"
                "在答案最後必須加上『參考來源』段落，逐條列出來源 URL。"
                "若有可用來源，內文關鍵句請以 [1]、[2] 這種格式標註。"
                f"\n使用者問題：{user_query_hint or '(unknown)'}"
                f"\n可用來源如下：\n{references_text if references_text else '(目前無可用來源 URL，請明確說明)'}"
            ),
        })
        logger.info("[ToolShim] post-tool: cleaned content + system constraint added; routing to LLM for synthesis")

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
            keep_last=5,
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

        # 目前先不真正把 tools 傳下去，只做兼容吸收
        response = router.chat(
            messages=messages,
            target_category=target_category,
            include_chat_only=True,
            **kwargs
        )

        content = ""
        model_used = model
        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content or ""
        if hasattr(response, "model"):
            model_used = response.model

        if enable_memory and original_user_message and content:
            router.add_to_history(original_user_message, content)
            logger.info("[Memory] 已將對話添加到歷史記錄")

        prompt_tokens = sum(len(m["content"]) // 4 for m in messages)
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
    
    # 轉換為 chat 格式
    messages = [{"role": "user", "content": request.prompt}]
    
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
    }
    
    for cat, providers in router._config_limits.items():
        status["quotas"][cat] = {}
        for provider, models_dict in providers.items():
            status["quotas"][cat][provider] = {}
            for model_id, rpd_limit in models_dict.items():
                usage_key = router.get_usage_key(provider, model_id)
                remaining = router._local_remaining_rpd.get(usage_key, rpd_limit)
                status["quotas"][cat][provider][model_id] = {
                    "limit": rpd_limit,
                    "remaining": remaining,
                    "used": rpd_limit - remaining if rpd_limit != -1 else 0,
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
    if provider_lower not in ["github", "google", "ollama"]:
        raise HTTPException(
            status_code=400, 
            detail=f"不支持的 provider: {request.provider}。支持的 provider: GitHub, Google, Ollama"
        )
    
    # 轉換為 chat 格式
    messages = [{"role": "user", "content": request.prompt}]
    
    try:
        # 準備參數
        kwargs = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        
        # 根據模型調整參數
        prepared_kwargs = router._prepare_kwargs(request.model_name, kwargs)
        
        # 獲取對應的 client
        client = getattr(router, provider_lower)
        
        # 記錄調用
        logger.info(f"[Direct Query] Provider: {request.provider} | Model: {request.model_name} | Prompt: {request.prompt[:50]}...")
        
        # 直接調用模型（帶重試）
        response = router._call_with_retry(
            client=client,
            model_id=request.model_name,
            messages=messages,
            **prepared_kwargs
        )
        
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
        
        return build_chat_response(
            model=request.model_name,
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        
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
        
        response = model.generate_content(
            [uploaded_file, prompt],
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


# ── Main ───────────────────────────────────────────────────
if __name__ == "__main__":
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8000"))
    
    logger.info(f"🚀 Starting {APP_TITLE} v{APP_VERSION}")
    logger.info(f"📍 Listening on http://{host}:{port}")
    logger.info(f"📚 API docs: http://{host}:{port}/docs")
    
    uvicorn.run(app, host=host, port=port)
