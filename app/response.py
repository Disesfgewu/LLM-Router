# -*- coding: utf-8 -*-
"""
Response builder helpers: OpenAI-compatible chat/completion response factories
and SSE streaming utility.
"""

import time
import json
from typing import Dict, Any

from fastapi.responses import StreamingResponse


def build_chat_response(
    model: str,
    content: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> Dict[str, Any]:
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


def build_completion_response(
    model: str,
    text: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> Dict[str, Any]:
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


def _stream_text_response(content: str, model: str) -> StreamingResponse:
    """Return OpenAI-compatible SSE chunks for a plain assistant text response."""
    request_id = f"chatcmpl-{int(time.time())}"
    created_ts = int(time.time())

    def sse_generator():
        chunk_role = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk_role)}\n\n"

        chunk_content = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk_content)}\n\n"

        chunk_done = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(chunk_done)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")
