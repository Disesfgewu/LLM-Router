# -*- coding: utf-8 -*-
"""
Pydantic request/response schemas for all API endpoints.
"""

from typing import List, Optional, Dict, Any

from pydantic import BaseModel


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


class DirectQueryRequest(BaseModel):
    model_name: str
    provider: str  # "GitHub", "Google", or "Ollama"
    prompt: str
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Dict[str, Any]]
    usage: Dict[str, int]


class FileContentRequest(BaseModel):
    prompt: str
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
