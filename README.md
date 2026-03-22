# ModelRouter API Gateway

**v2.0.0** — OpenAI 相容的多模型 AI Gateway，支援 Chat、Embeddings、圖片生成、多模態輸入與 RAG 工作流程。

以 FastAPI 為核心，在 GitHub Models、Google Gemini、Ollama、HuggingFace 之間做智慧路由、配額追蹤與自動 failover。

---

## 功能總覽

| 類別 | 功能 |
|---|---|
| **Chat** | OpenAI 相容 chat completions，真實 token 串流 (SSE)，多輪對話 |
| **Embeddings** | Google Gemini Embedding 2（`gemini-embedding-2-preview`），OpenAI 相容格式，RAG 整合 |
| **圖片生成** | FLUX.1-schnell、Stable Diffusion XL (HuggingFace)、Imagen 4 系列 (Google) |
| **多模態** | 圖片、PDF、CSV、XLSX 文件附件，自動抽取與摘要 |
| **搜尋與研究** | 內建 web search，多步研究規劃，reviewer 回圈補救 |
| **記憶** | Pre-chat 記憶分析，自動注入歷史 log，對話記錄保留 |
| **Auth** | 雙層 API Key（全功能 `mk_` / 限定 `ma_`），Session 登入，IP 白名單，審計日誌 |
| **Docker** | Dockerfile + docker-compose，開箱即用部署 |
| **MCP** | Model Context Protocol SSE 入口，OpenClaw 相容 |

---

## 快速開始

### 1. 設定環境變數

複製 `.env.example` 為 `.env` 並填入 API Key：

```bash
cp .env.example .env
```

`.env` 最小設定（Google 必填，其他選填）：

```bash
# Google Gemini — chat、embedding、multimodal、Imagen 4
GOOGLE_API_KEY=your_google_api_key
GOOGLE_API_URL=https://generativelanguage.googleapis.com/v1beta/openai/

# GitHub Models — GPT-4o、Grok-3、DeepSeek-R1（需要 Copilot 訂閱）
GITHUB_MODELS_API_KEY=your_github_pat
GITHUB_MODELS_API_URL=https://models.github.ai/inference

# HuggingFace — FLUX.1-schnell 圖片生成（選填）
HUGGINGFACE_API_KEY=your_hf_token
HUGGINGFACE_API_URL=https://api-inference.huggingface.co

# Ollama — 本地模型（選填）
OLLAMA_API_URL=http://localhost:11434/v1

# 伺服器
API_HOST=0.0.0.0
API_PORT=8000

# 安全：設 0 允許遠端存取 /auth 與 /admin（預設 1 = 僅 localhost）
AUTH_LOCAL_ADMIN_ONLY=1
```

多帳號支援：在 key 後加 `_1`、`_2`... 可倍增配額，router 自動輪詢。

### 2. 安裝依賴

```bash
pip install -r requirements.txt
```

### 3. 啟動後端

```bash
python api.py
```

服務啟動位址：
- **API**：`http://localhost:8000`
- **Swagger UI**：`http://localhost:8000/docs`
- **健康檢查**：`http://localhost:8000/health`

### 4. 啟動前端（選填）

```bash
./start_frontend.sh
# → http://localhost:3000
```

### 5. Docker 部署

```bash
docker compose up --build
```

---

## 認證系統

所有 `/v1/*` 端點都需要認證。認證流程：

### 第一次使用：建立帳號

```bash
# 第一個帳號自動成為 Admin
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "email": "you@example.com", "password": "yourpassword"}'
```

### 登入取得 Session Token

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "yourpassword"}'
# → {"token": "...", "expires_at": "...", "is_admin": true}
```

### 建立 API Key（Session 驗證）

```bash
# 全功能 Key（mk_ 前綴，永久有效）
curl -X POST http://localhost:8000/auth/keys/full \
  -H "X-Session-Token: <session_token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-app"}'
# → {"key": "mk_xxxx..."}  ← 只顯示一次，請妥善保存

# 限定 Agent Key（ma_ 前綴，有效期 + scope 限制）
curl -X POST http://localhost:8000/auth/keys/agent \
  -H "X-Session-Token: <session_token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "rag-bot", "scopes": ["chat", "embeddings"], "expires_hours": 24, "rpm_limit": 20}'
```

### 使用 API Key 呼叫 API

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer mk_xxxx..." \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "Hello"}]}'
```

### API Key 類型

| 類型 | 前綴 | 說明 |
|---|---|---|
| Full Key | `mk_` | 無 scope 限制、無過期。僅限 Admin 建立。 |
| Agent Key | `ma_` | 必須指定 scope，有 TTL（預設 1h，最長 24h），有 RPM 上限。 |

---

## API 端點總覽

### 核心 AI 端點

| 端點 | 方法 | Auth | 說明 |
|---|---|---|---|
| `/v1/chat/completions` | POST | API Key | OpenAI Chat Completions，支援串流 SSE |
| `/v1/completions` | POST | API Key | Legacy Completions API |
| `/v1/embeddings` | POST | API Key | Gemini Embedding 2，RAG 相容 |
| `/v1/images/generations` | POST | API Key | 圖片生成 (FLUX / Imagen 4) |
| `/v1/direct_query` | POST | API Key | 直接指定 provider/model 查詢 |
| `/v1/file/generate_content` | POST | API Key | 上傳文件並生成內容 |
| `/v1/models` | GET | API Key | 列出可用模型與配額狀態 |

### Auth 端點

| 端點 | 方法 | Auth | 說明 |
|---|---|---|---|
| `/auth/register` | POST | 公開 | 建立帳號（第一個自動為 Admin） |
| `/auth/login` | POST | 公開 | 登入，取得 Session Token |
| `/auth/logout` | POST | Session | 登出 |
| `/auth/me` | GET | Session | 查看目前帳號資訊 |
| `/auth/keys` | GET | Session | 列出我的 API Key |
| `/auth/keys/full` | POST | Session (Admin) | 建立全功能 Key |
| `/auth/keys/agent` | POST | Session | 建立限定 Agent Key |
| `/auth/keys/{id}` | DELETE | Session | 撤銷 API Key |
| `/auth/whitelist` | GET/POST | Session | 查看/新增 IP 白名單 |
| `/auth/whitelist/{id}` | DELETE | Session | 刪除 IP 白名單 |
| `/auth/audit` | GET | Session | 查看 API Key 使用審計日誌 |
| `/auth/scopes` | GET | 公開 | 查看可用 scope 清單 |

### Admin 端點（Session + localhost）

| 端點 | 方法 | 說明 |
|---|---|---|
| `/admin/status` | GET | 查看所有模型配額狀態 |
| `/admin/logs` | GET | 讀取最新日誌 |
| `/admin/reset_quotas` | POST | 重置所有配額 |
| `/admin/refresh_rpm` | POST | 重置 RPM 優先順序 |
| `/admin/accounts` | GET | 列出所有帳號（Admin）|
| `/admin/accounts/{id}/activate` | POST | 啟用帳號 |
| `/admin/accounts/{id}/deactivate` | POST | 停用帳號 |

### MCP 端點

| 端點 | 方法 | 說明 |
|---|---|---|
| `/mcp/sse` | GET | OpenClaw MCP SSE transport |
| `/mcp/messages` | POST | MCP JSON-RPC message channel |

### 公用端點

| 端點 | 方法 | 說明 |
|---|---|---|
| `/` | GET/POST | 服務資訊與所有端點摘要 |
| `/health` | GET/POST | 健康檢查 |

---

## Request/Response Schema

### Chat Completions — `POST /v1/chat/completions`

**Request：**

```json
{
  "model": "auto",
  "messages": [
    {"role": "system", "content": "你是一個助手"},
    {"role": "user", "content": "Hello"}
  ],
  "temperature": 0.7,
  "max_tokens": 2048,
  "stream": false,
  "target_category": null,
  "enable_memory": true,
  "tools": [],
  "tool_choice": "auto",
  "attachments": [],
  "input_files": [],
  "input_images": [],
  "enable_auto_image_generation": false
}
```

| 欄位 | 類型 | 說明 |
|---|---|---|
| `model` | string | `auto`、分類名或具體模型名 |
| `messages` | array | OpenAI-style message list |
| `temperature` | float | 生成溫度（0.0–2.0），預設 0.7 |
| `max_tokens` | integer | 最大 token 數 |
| `stream` | boolean | 啟用 SSE token 串流 |
| `target_category` | string | 指定路由類別（`TextOnlyHigh`/`TextOnlyLow`/`MultiModal`/...） |
| `enable_memory` | boolean | 啟用歷史記憶注入，預設 `true` |
| `tools` | array | OpenAI-style tool 定義 |
| `tool_choice` | string/object | 工具選擇策略 |
| `attachments` | array | 混合附件（text/image_url/input_file） |
| `input_files` | array | 文件附件（PDF/CSV/XLSX/TXT） |
| `input_images` | array | 圖片附件（base64 data URL）|
| `enable_auto_image_generation` | boolean | 自動判斷是否改走圖片生成 |

**Response（非串流）：**

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1742618400,
  "model": "openai/gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "你好！"},
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 8,
    "total_tokens": 20
  }
}
```

**Response（串流 SSE）：**

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"model":"openai/gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"你"},"finish_reason":null}]}

data: [DONE]
```

---

### Embeddings — `POST /v1/embeddings`

**Request：**

```json
{
  "model": "gemini-embedding-2-preview",
  "input": "Hello world",
  "encoding_format": "float"
}
```

`input` 可以是字串或字串陣列（批次 embedding）。

**Response：**

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "embedding": [0.012, -0.034, ...],
      "index": 0
    }
  ],
  "model": "gemini-embedding-2-preview",
  "usage": {
    "prompt_tokens": 2,
    "total_tokens": 2
  }
}
```

支援的 Embedding 模型：

| 模型 | 維度 | 配額 |
|---|---|---|
| `gemini-embedding-2-preview` | 3072 | 1000 RPD / 帳號 |
| `gemini-embedding-001` | 3072 | 1000 RPD / 帳號 |

---

### Image Generation — `POST /v1/images/generations`

**Request：**

```json
{
  "model": "black-forest-labs/FLUX.1-schnell",
  "prompt": "a futuristic city at sunset",
  "n": 1,
  "size": "1024x1024",
  "response_format": "b64_json"
}
```

**Response：**

```json
{
  "created": 1742618400,
  "data": [
    {"b64_json": "iVBORw0KGgo..."}
  ]
}
```

支援的圖片模型：

| 模型 | Provider |
|---|---|
| `black-forest-labs/FLUX.1-schnell` | HuggingFace |
| `stabilityai/stable-diffusion-xl-base-1.0` | HuggingFace |
| `imagen-4-generate` | Google |
| `imagen-4-ultra-generate` | Google |
| `imagen-4-fast-generate` | Google |

---

### Direct Query — `POST /v1/direct_query`

直接指定 provider 與 model，跳過 router 路由邏輯。

**Request：**

```json
{
  "model_name": "openai/gpt-4o",
  "provider": "GitHub",
  "prompt": "Hello",
  "temperature": 0.7,
  "max_tokens": 1000
}
```

---

### 標準錯誤格式

所有錯誤回應使用 OpenAI 相容格式：

```json
{
  "error": {
    "message": "Session token 無效或已過期，請重新登入",
    "type": "authentication_error",
    "code": 401,
    "param": null
  }
}
```

| HTTP 狀態碼 | type |
|---|---|
| 400 | `invalid_request_error` |
| 401 | `authentication_error` |
| 403 | `permission_error` |
| 404 | `not_found_error` |
| 429 | `rate_limit_error` |
| 5xx | `server_error` |

---

## Response Headers

每個回應都包含版本 header：

```
X-API-Version: 2.0.0
X-Powered-By: ModelRouter
```

---

## 模型路由系統

### 路由類別

| 類別 | 模型 | 用途 |
|---|---|---|
| `TextOnlyHigh` | GitHub: gpt-4o, gpt-5, gpt-5-mini, o1-preview, DeepSeek-R1, Grok-3, Grok-3-mini<br>Google: gemini-2.5-flash, gemini-2.5-flash-lite, gemini-3-flash | 高品質文字生成 |
| `TextOnlyLow` | GitHub: gpt-4o-mini<br>Google: gemma-3-12b-it, gemini-3.1-flash-lite<br>Ollama: qwen3:4b-instruct, deepseek-r1:1.5b | 輕量/本地模型 |
| `MultiModal` | Google: gemini-2.5-flash, gemma-3-27b-it, gemini-2.5-flash-tts | 圖片/文件理解 |
| `ChatOnly` | Ollama 本地模型 | 純文字對話，無外部依賴 |
| `ImageGeneration` | HuggingFace: FLUX.1-schnell, SD-XL | 圖片生成 |
| `Embedding` | Google: gemini-embedding-2-preview, gemini-embedding-001 | 向量 embedding |

### `model` 欄位支援值

```
auto             → TextOnlyHigh → TextOnlyLow failover
TextOnlyHigh     → 指定高品質類別
TextOnlyLow      → 指定輕量類別
MultiModal       → 指定多模態類別
openai/gpt-4o    → 具體模型名，直接路由
gemini-2.5-flash → 具體模型名，直接路由
```

### 配額管理

- 配額以 `provider|account|model` 為 key 追蹤
- 用完後自動 failover 到下一個 account/model
- 多帳號設定：`GOOGLE_API_KEY`, `GOOGLE_API_KEY_1`, `GOOGLE_API_KEY_2`...
- 可透過 `/admin/status` 查看所有配額，`/admin/reset_quotas` 重置

---

## 多模態輸入

`/v1/chat/completions` 支援圖片與文件附件：

```json
{
  "model": "auto",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "請分析這份報告"},
        {
          "type": "input_file",
          "file_name": "report.pdf",
          "mime_type": "application/pdf",
          "file_data": "<base64>"
        },
        {
          "type": "image_url",
          "image_url": {"url": "data:image/png;base64,<base64>"}
        }
      ]
    }
  ]
}
```

或使用 top-level 快捷欄位：

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "請整理附件重點"}],
  "input_files": [
    {"file_name": "sales.csv", "mime_type": "text/csv", "file_data": "<base64>"}
  ],
  "input_images": ["data:image/png;base64,<base64>"]
}
```

支援文件類型：`pdf`、`csv`、`xlsx`、`txt`（每次最多 5 份）

---

## RAG / Embedding 整合

`/v1/embeddings` 相容 OpenAI Embeddings API，可直接用於 LangChain、LlamaIndex 等框架：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="mk_xxxx..."
)

# 單筆
resp = client.embeddings.create(
    model="gemini-embedding-2-preview",
    input="今天天氣很好"
)
vector = resp.data[0].embedding  # 3072-dim float list

# 批次
resp = client.embeddings.create(
    model="gemini-embedding-2-preview",
    input=["文件一", "文件二", "文件三"]
)
```

---

## 智慧搜尋與記憶功能

### 搜尋與研究流程（實驗性）

- 判斷用戶問題是否需要搜尋
- 規劃多個搜尋 query，逐項執行 web search
- 整理 evidence 後生成答案
- Reviewer 判斷是否有缺漏，必要時補查並重寫（最多 3 輪，上限 6 輪）
- 回應可附加 `citations`、`research_tasks`

### 記憶功能

- Pre-chat 分析：使用 Gemma 判斷是否需要查歷史 log
- 觸發關鍵字：記憶、剛剛、之前、上次、日誌、history、log、memory
- 自動讀取 `app/app.log` 並增強 prompt
- 對話歷史保留最近 10 輪
- 可在 request 中設 `"enable_memory": false` 停用

---

## 使用範例

### Python（OpenAI SDK）

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="mk_xxxx..."
)

# 普通對話
resp = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "你好！"}]
)
print(resp.choices[0].message.content)

# 串流
for chunk in client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "寫一首詩"}],
    stream=True
):
    print(chunk.choices[0].delta.content or "", end="", flush=True)

# Embedding
resp = client.embeddings.create(
    model="gemini-embedding-2-preview",
    input="Hello world"
)
print(resp.data[0].embedding[:5])
```

### cURL

```bash
# Chat
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer mk_xxxx..." \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Hello"}]}'

# Embedding
curl -X POST http://localhost:8000/v1/embeddings \
  -H "Authorization: Bearer mk_xxxx..." \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-embedding-2-preview","input":"Hello world"}'

# 查配額
curl http://localhost:8000/admin/status \
  -H "X-Session-Token: <session_token>"
```

---

## 專案結構

```
llm-api/
├── api.py                    # 主 FastAPI 應用，所有 endpoint
├── ModelRouter/
│   ├── ModelRouter.py        # 核心路由引擎，chat/embed/stream 方法
│   └── models.py             # 模型配置（類別、RPM/RPD 配額）
├── app/
│   ├── auth.py               # 認證：帳號、session、API key、IP 白名單
│   ├── schemas.py            # Pydantic request/response schema
│   ├── response.py           # 回應建構工具
│   ├── search.py             # Web search + research 流程
│   ├── messages.py           # Message 正規化工具
│   ├── multimodal.py         # 圖片/文件預處理
│   ├── tools.py              # Tool-calling 工具
│   └── app.log               # 執行期日誌
├── frontend/                 # React 前端
│   ├── src/
│   │   ├── components/       # Dashboard、Chat、Auth 等 UI 組件
│   │   └── App.jsx
│   └── package.json
├── Dockerfile                # Docker 映像
├── docker-compose.yml        # Docker Compose 部署
├── .env.example              # 環境變數範本
├── requirements.txt          # Python 依賴
├── auth.db                   # SQLite 認證資料庫（自動建立）
├── usage_tracker.json        # 配額追蹤（自動建立）
└── start_frontend.sh         # 前端啟動腳本
```

---

## Docker 部署

```bash
# 建立並啟動
docker compose up --build

# 背景執行
docker compose up -d

# 查看日誌
docker compose logs -f
```

`docker-compose.yml` 會自動掛載 `auth.db`、`usage_tracker.json`、`app/app.log` 到容器外部，資料不會在重啟時遺失。

---

## 環境變數說明

| 變數 | 說明 |
|---|---|
| `GOOGLE_API_KEY` | Google Gemini API Key（chat + embedding + Imagen 4） |
| `GOOGLE_API_URL` | Google API URL（預設 `generativelanguage.googleapis.com/v1beta/openai/`） |
| `GITHUB_MODELS_API_KEY` | GitHub PAT，需有 Copilot 訂閱 |
| `GITHUB_MODELS_API_URL` | GitHub Models URL（預設 `models.github.ai/inference`） |
| `HUGGINGFACE_API_KEY` | HuggingFace Token，用於 FLUX 圖片生成 |
| `OLLAMA_API_URL` | Ollama URL（預設 `http://localhost:11434/v1`） |
| `API_HOST` | 服務綁定地址（預設 `0.0.0.0`） |
| `API_PORT` | 服務端口（預設 `8000`） |
| `AUTH_LOCAL_ADMIN_ONLY` | `1` = `/auth` 與 `/admin` 僅限 localhost，`0` = 允許遠端 |
| `AUTH_DEFAULT_LOCALHOST_ONLY` | `1` = API Key 無白名單時僅允許 localhost |
| `AUTH_AGENT_MAX_HOURS` | Agent Key 最長有效期（預設 `24` 小時） |
| `AUTH_AGENT_MAX_RPM` | Agent Key 最大 RPM（預設 `20`） |

---

## 常見問題

### 無法登入 / 忘記密碼

找回帳號名稱：

```bash
python3 -c "
import sqlite3; conn = sqlite3.connect('auth.db')
rows = conn.execute('SELECT id, username, email, is_admin FROM accounts').fetchall()
print(rows)
"
```

### GitHub Models 403 錯誤

- 確認 PAT 有效且帳號有 Copilot 訂閱
- 部分模型（gpt-5、o1）需要 Copilot Pro/Enterprise
- 系統會自動 failover 到 Google Gemini

### 取得 Google Gemini API Key

前往 [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) 建立

### 使用 Ollama 本地模型

```bash
ollama pull qwen3:4b-instruct
ollama serve
```

ModelRouter 會在高優先級模型額度用完後自動切換 Ollama。

### Embedding 模型不可用

確認 `text-embedding-004` 已於 2026-01-14 下線，請改用 `gemini-embedding-2-preview` 或 `gemini-embedding-001`。

---

## 文件索引

| 文件 | 說明 |
|---|---|
| [API_USAGE_GUIDE.md](API_USAGE_GUIDE.md) | API 呼叫詳細指南 |
| [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) | 系統架構與資料流 |
| [OPENCLAW_API_REPORT.md](OPENCLAW_API_REPORT.md) | OpenClaw / MCP 能力總覽 |
| [DIRECT_QUERY_EXAMPLES.md](DIRECT_QUERY_EXAMPLES.md) | `/v1/direct_query` 範例 |
| [FILE_UPLOAD_API.md](FILE_UPLOAD_API.md) | 文件上傳使用方式 |

---

## 📸 功能展示

### 對話介面
![對話介面](demo-png/demo-chat.PNG)

### 配額儀錶板
![配額儀錶板](demo-png/demo-dash.PNG)

### AI 回應
![AI 回應](demo-png/demo-answer.PNG)

### 智慧切換
![智慧切換](demo-png/demo-switch.PNG)

### 日誌檢視器
![日誌檢視器](demo-png/demo-log.PNG)

---

## 授權

MIT License
