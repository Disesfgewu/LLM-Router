# ModelRouter API / OpenClaw Support Report

## Summary

This report describes the current backend capability set implemented in the gateway.
It reflects the actual behavior in `api.py` and the extracted helper modules under `app/`.

Current highlights:

- OpenAI-compatible chat and legacy completions endpoints
- Direct query API for calling a specific provider/model pair
- OpenAI-compatible image generation endpoint
- File upload content generation API
- Automatic multimodal routing inside `/v1/chat/completions`
- Unified Gemma intent classification before routing
- Admin endpoints for quota, logs, and scheduling state
- MCP endpoints for OpenClaw tool connectivity
- Built-in `web_search` tool shim for OpenClaw-compatible tool-calling flows
- Post-tool synthesis flow with citations and source-aware answer constraints
- Proactive multi-step search planning and reviewer-driven follow-up search loop
- Data-backed image generation pipeline for charts / dashboards / trend visuals
- Streaming support for chat completions and tool-call emission

## Backend Endpoint Matrix

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | `GET`, `POST` | Service metadata and endpoint summary |
| `/health` | `GET`, `POST` | Lightweight health check |
| `/v1/models` | `GET`, `POST` | List routed models and remaining RPD |
| `/v1/chat/completions` | `POST` | OpenAI-compatible chat completions |
| `/v1/completions` | `POST` | Legacy completions endpoint |
| `/v1/images/generations` | `POST` | OpenAI-compatible image generation |
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
3. Plan multiple search tasks when needed
4. Emit an OpenAI-style `tool_calls` response
5. Let the client execute the tool round
6. Accept the tool result back in a follow-up chat request
7. Route the post-tool conversation through the model for final synthesis
8. Optionally run one follow-up search/regeneration pass if the reviewer detects missing information

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

### 6. Reviewer-Driven Research Loop

For research-backed text tasks, the gateway can now run a single reviewer loop:

1. generate the first answer draft from gathered evidence
2. ask Gemma whether the answer is complete
3. if incomplete, extract `next_queries`
4. run one more search pass
5. regenerate a better final answer

This improves complex tasks without exposing raw chain-of-thought.

### 7. Data-Backed Image Pipeline

For visual requests such as candlestick charts, dashboards, infographics, or trend graphics, the gateway can:

1. detect that the image requires factual data
2. run a search-planning pipeline first
3. gather evidence and citations
4. inject the evidence into the image prompt
5. generate the final image from an image model

Responses may include:

- `images`
- `citations`
- `research_tasks`

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
- `attachments`
- `input_files`
- `input_images`
- `enable_auto_image_generation`
- `image_model`
- `image_n`
- `image_size`

Multimodal content is also supported on the same endpoint through OpenAI-style content parts.

Supported content parts for chat:

- `text`
- `input_text`
- `image_url`
- `input_file`

Current file constraints:

- up to 5 attached files per request
- current file preprocessing support: `txt`, `csv`, `xlsx`, `pdf`
- images can remain as image parts for multimodal-capable chat models

Current routing behavior:

- the gateway preprocesses files before routing
- a cheap model is used once to decide whether expensive multimodal chat routing is necessary
- if multimodal is not necessary, the router keeps the request on cheaper text-capable models

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
- `HuggingFace`

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

### `/v1/models` Capability Metadata

`/v1/models` now returns per-model capability metadata under `capabilities`, for example:

```json
{
  "id": "gemini-2.5-flash",
  "category": "MultiModal",
  "capabilities": {
    "chat_capable": true,
    "image_input": true,
    "document_input": true,
    "task": "chat",
    "preferred_tasks": ["ocr", "vision", "multimodal_analysis"]
  }
}
```

`/v1/models` and `/admin/status` now expose provider account details when multiple keys are configured.
Quota accounting is tracked by `provider|account|model` and returned as:

- aggregated `rpd_limit` / `rpd_remaining`
- per-account details in `provider_accounts` (or `accounts` in admin status)

Example account detail snippet:

```json
{
  "provider_account_count": 3,
  "provider_accounts": [
    {"account_id": "default", "limit": 20, "remaining": 12, "used": 8},
    {"account_id": "1", "limit": 20, "remaining": 20, "used": 0},
    {"account_id": "2", "limit": 20, "remaining": 19, "used": 1}
  ]
}
```

### Chat Attachment Input Methods (Same Endpoint)

`/v1/chat/completions` keeps the same endpoint and supports two ways to pass multimodal data:

1. Standard OpenAI-style `messages[].content` parts (`image_url`, `input_file`, etc.)
2. Top-level helper fields (auto-injected into latest user message):
   - `attachments`
   - `input_files`
   - `input_images`

Example top-level payload:

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "請整理附件"}],
  "input_files": [
    {
      "file_name": "report.pdf",
      "mime_type": "application/pdf",
      "file_data": "base64..."
    }
  ],
  "input_images": ["data:image/png;base64,..."]
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