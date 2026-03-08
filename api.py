#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ModelRouter API Gateway (OpenAI-compatible)

多模型智慧路由 API 閘道，對外提供 OpenAI 相容介面，
自動在 GitHub Models、Google Gemini、Ollama 之間做 failover 和配額管理。

Endpoints:
  POST /v1/chat/completions     OpenAI Chat Completions API
  POST /v1/completions          OpenAI Completions API (legacy)
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
from typing import List, Optional, Dict, Any

from dotenv import load_dotenv
load_dotenv()  # 載入 .env 檔案

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

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

# ── FastAPI App ────────────────────────────────────────────
app = FastAPI(title=APP_TITLE, version=APP_VERSION)
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


# ── Request / Response Models ──────────────────────────────
class Message(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "auto"
    messages: List[Message]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    # 額外參數：指定類別
    target_category: Optional[str] = None
    # 記憶功能控制：是否啟用記憶功能（default: True）
    enable_memory: Optional[bool] = True


class CompletionRequest(BaseModel):
    model: str = "auto"
    prompt: str
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Dict[str, Any]]
    usage: Dict[str, int]


# ── Helper Functions ───────────────────────────────────────
def build_chat_response(model: str, content: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> Dict[str, Any]:
    """Build OpenAI-compatible chat completion response."""
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def build_completion_response(model: str, text: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> Dict[str, Any]:
    """Build OpenAI-compatible completion response."""
    return {
        "id": f"cmpl-{int(time.time())}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "text": text,
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


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
                usage_key = f"{cat}|{model_id}"
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


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """
    OpenAI Chat Completions API
    
    model 可以是：
      - "auto": 自動選擇（先 TextOnlyHigh，再 TextOnlyLow）
      - "TextOnlyHigh": 只用高品質模型
      - "TextOnlyLow": 只用經濟型模型
      - 具體模型名稱（會自動找對應類別）
    """
    if request.stream:
        raise HTTPException(status_code=400, detail="stream=True not supported yet")
    
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")
    
    router = get_router()
    
    # 轉換 messages 格式
    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    
    # 決定 target_category
    target_category = request.target_category
    model_name = request.model.lower() if request.model else "auto"
    
    if model_name in ["textonlyhigh", "high"]:
        target_category = "TextOnlyHigh"
    elif model_name in ["textonlylow", "low"]:
        target_category = "TextOnlyLow"
    elif model_name != "auto":
        # 找模型對應的類別
        for cat, providers in router._config_limits.items():
            for provider, models_dict in providers.items():
                if request.model in models_dict:
                    target_category = cat
                    break
    
    try:
        # 準備 kwargs
        kwargs = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        
        # === Pre-chat: 檢查是否需要 RAG (記憶功能) ===
        original_user_message = ""
        if messages and messages[-1]["role"] == "user":
            original_user_message = messages[-1]["content"]
            
            # 只有在啟用記憶功能時才執行
            if request.enable_memory:
                # 使用 gemma-3-12b-it 判斷是否需要查詢 log
                need_log = router.check_need_log_rag(original_user_message)
                
                if need_log:
                    logger.info("[Memory] 檢測到需要查詢 log，正在載入...")
                    log_data = router.read_app_log(max_lines=100)
                    
                    # 修改 prompt，加入 log 資訊
                    enhanced_message = f"""這是過去的 log 資訊，請依據 log 的內容回答問題：

{log_data}

[問題]: {original_user_message}"""
                    
                    messages[-1]["content"] = enhanced_message
                    logger.info("[Memory] 已將 log 資訊注入到 prompt 中")
            else:
                logger.info("[Memory] 記憶功能已停用，使用原始方法")
        
        # 呼叫 router
        response = router.chat(
            messages=messages,
            target_category=target_category,
            **kwargs
        )
        
        # 取得回答
        content = ""
        model_used = request.model
        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content or ""
        if hasattr(response, 'model'):
            model_used = response.model
        
        # === 將對話添加到歷史記錄 ===
        if request.enable_memory and original_user_message and content:
            router.add_to_history(original_user_message, content)
            logger.info("[Memory] 已將對話添加到歷史記錄")
        
        # 估算 tokens（簡單估算）
        prompt_tokens = sum(len(m["content"]) // 4 for m in messages)
        completion_tokens = len(content) // 4
        
        return build_chat_response(
            model=model_used,
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Chat error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/completions")
async def completions(request: CompletionRequest):
    """OpenAI Completions API (legacy)"""
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
        
        response = router.chat(messages=messages, **kwargs)
        
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
                usage_key = f"{cat}|{model_id}"
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


# ── Main ───────────────────────────────────────────────────
if __name__ == "__main__":
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    
    logger.info(f"🚀 Starting {APP_TITLE} v{APP_VERSION}")
    logger.info(f"📍 Listening on http://{host}:{port}")
    logger.info(f"📚 API docs: http://{host}:{port}/docs")
    
    uvicorn.run(app, host=host, port=port)
