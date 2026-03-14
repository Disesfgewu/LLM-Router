# ModelRouter API / OpenClaw Support Report

## Summary

This report describes the current backend capability set implemented in the gateway.
It reflects the actual behavior in `api.py` and the extracted helper modules under `app/`.

Current highlights:

- OpenAI-compatible chat and legacy completions endpoints
- Direct query API for calling a specific provider/model pair
- File upload content generation API
- Admin endpoints for quota, logs, and scheduling state
- MCP endpoints for OpenClaw tool connectivity
- Built-in `web_search` tool shim for OpenClaw-compatible tool-calling flows
- Post-tool synthesis flow with citations and source-aware answer constraints
- Streaming support for chat completions and tool-call emission

## Backend Endpoint Matrix

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | `GET`, `POST` | Service metadata and endpoint summary |
| `/health` | `GET`, `POST` | Lightweight health check |
| `/v1/models` | `GET`, `POST` | List routed models and remaining RPD |
| `/v1/chat/completions` | `POST` | OpenAI-compatible chat completions |
| `/v1/completions` | `POST` | Legacy completions endpoint |
| `/v1/direct_query` | `POST` | Query a specific provider/model directly |
| `/v1/file/generate_content` | `POST` | Upload a file and generate image/file-based content |
| `/admin/status` | `GET` | Inspect priority flags and quota state |
| `/admin/logs` | `GET` | Return recent logs from file or systemd |
| `/admin/reset_quotas` | `POST` | Reset RPD counters |
| `/admin/refresh_rpm` | `POST` | Refresh routing priority flags |
| `/mcp/sse` | `GET` | OpenClaw MCP SSE transport endpoint |
| `/mcp/messages` | `POST` | OpenClaw MCP JSON-RPC message endpoint |

## OpenClaw-Compatible Capability

### 1. MCP Transport Support

OpenClaw can connect to the local MCP server through:

- `GET /mcp/sse`
- `POST /mcp/messages`

These endpoints expose the server-side tool registry and execution channel used by OpenClaw-compatible MCP clients.

### 2. Tool-Calling Shim in `/v1/chat/completions`

When the request contains a declared web-search-like tool, the gateway can:

1. Inspect the latest user query
2. Ask an LLM whether search is necessary
3. Emit an OpenAI-style `tool_calls` response
4. Let the client execute the tool round
5. Accept the tool result back in a follow-up chat request
6. Route the post-tool conversation through the model for final synthesis

This behavior is implemented for OpenClaw / LiteLLM / vLLM-style clients that send OpenAI-style `tools` payloads.

### 3. OpenClaw Built-In `web_search` Routing

Requests identified as OpenClaw built-in web search are intercepted and handled locally by the backend search tool instead of depending on an external search provider.

Detection currently uses:

- `x-title: OpenClaw Web Search`
- models prefixed with `perplexity/`

### 4. Post-Tool Synthesis

If the last message is a tool result, the backend:

- normalizes tool payloads
- recovers search text from JSON-wrapped tool results
- extracts citations from tool output
- appends a strict system instruction that requires grounded answering
- forces the final answer back through the model instead of short-circuiting

### 5. Citation-Aware Responses

Post-tool responses can include a top-level `citations` field when source URLs are available.

The synthesis stage also instructs the model to:

- cite key claims using `[1]`, `[2]` style markers when possible
- append a `參考來源` section
- avoid fabricating unsupported facts

## MCP Tool API List

Current registered tool list:

### `search_web`

Purpose:

- Search the web for current information

Input schema:

```json
{
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
```

Returned content shape:

- MCP text content list
- each result is flattened into plain text blocks like:

```text
[1] Result Title
URL: https://example.com
Snippet: ...
Detail: ...
```

Internal behavior:

- query sanitization
- DDGS multi-backend search
- Bing HTML fallback if needed
- CJK-aware quality checks
- optional source enrichment for data-heavy queries

## Request Object Support

The backend currently accepts or normalizes the following request objects.

### `Message`

```json
{
  "role": "user",
  "content": "Hello"
}
```

Supported normalized roles in chat payloads:

- `user`
- `assistant`
- `system`
- `developer` mapped to `system`
- `tool` mapped into a system transcript for model routing

Supported content shapes:

- string
- list of OpenAI-style content parts
- dicts containing `text` or `content`

### `ChatCompletionRequest`

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "Hello"}],
  "temperature": 0.7,
  "max_tokens": 512,
  "stream": false,
  "target_category": "TextOnlyHigh",
  "enable_memory": true,
  "tools": [],
  "tool_choice": "auto"
}
```

Supported fields in practice:

- `model`
- `messages`
- `temperature`
- `max_tokens` or `max_completion_tokens`
- `stream`
- `target_category`
- `enable_memory`
- `tools`
- `tool_choice`

### `CompletionRequest`

```json
{
  "model": "auto",
  "prompt": "Explain Docker",
  "temperature": 0.7,
  "max_tokens": 400,
  "stream": false
}
```

Note:

- `stream=true` is not supported for `/v1/completions`

### `DirectQueryRequest`

```json
{
  "model_name": "gpt-4o",
  "provider": "GitHub",
  "prompt": "Write a Python function",
  "temperature": 0.5,
  "max_tokens": 500
}
```

Supported providers:

- `GitHub`
- `Google`
- `Ollama`

### `FileContentRequest`

Multipart form request for `/v1/file/generate_content`:

- `file`
- `prompt`
- `temperature`
- `max_tokens`

## Response Object Support

### Standard Chat Completion Response

```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 20,
    "total_tokens": 30
  }
}
```

### Tool-Call Response

When the web search shim decides a search is required, `/v1/chat/completions` can return:

```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "auto",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "tool_calls": [
          {
            "id": "call_web_search_1",
            "type": "function",
            "function": {
              "name": "web_search",
              "arguments": "{\"query\":\"...\",\"count\":5}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

### Streaming Chat Response

`/v1/chat/completions` supports SSE chunks with:

- role delta
- content delta
- finish chunk
- `[DONE]`

It also supports streaming tool-call emission when a tool round is triggered.

### Search Response With Citations

OpenClaw-like search responses may also include:

```json
{
  "citations": [
    "https://example.com/1",
    "https://example.com/2"
  ]
}
```

## Behavioral Notes

- Memory injection is disabled automatically when a tool request is being handled.
- Tool payloads are absorbed for compatibility, but only the web-search flow has a dedicated shim today.
- Search quality checks are language-aware and handle CJK queries differently from whitespace-tokenized English queries.
- Search enrichment fetches top source pages only for data-heavy or time-sensitive queries.

## Recommended Reading Order

- `README.md` for project overview and endpoint index
- `OPENCLAW_API_REPORT.md` for current capability map
- `API_USAGE_GUIDE.md` for usage examples
- `DIRECT_QUERY_EXAMPLES.md` for direct provider/model calls
- `FILE_UPLOAD_API.md` for multipart upload examples