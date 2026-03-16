# ModelRouter API Gateway

多模型智慧路由 API 閘道，對外提供 OpenAI 相容介面，自動在 GitHub Models、Google Gemini、Ollama 之間做 failover 和配額管理。

## 功能特色

✅ **多提供者路由** - 自動在 GitHub Models、Google Gemini、Ollama 切換

✅ **智慧 Failover** - 一個模型失敗或額度滿，自動切換下一個

✅ **配額管理** - 本地追蹤每個模型的每日請求數 (RPD)

✅ **OpenAI 相容** - 完全相容 OpenAI API 格式

✅ **OpenClaw / MCP 整合** - 支援 MCP transport、tool registry 與本地 `search_web` 工具

✅ **Tool-Calling Shim** - 可判斷是否需要 web search，回傳 OpenAI 風格 `tool_calls`

✅ **搜尋後合成回答** - 工具結果會重新進 LLM 做整理、分析與引用

✅ **引用與來源回傳** - 搜尋型回答可附 `citations`，並要求輸出參考來源

✅ **多模態自動路由** - 同一個 `/v1/chat/completions` 可接圖像與最多 5 份文件，只有必要時才切到多模態模型

✅ **智慧記憶功能** - Pre-chat 分析，自動查詢歷史日誌（使用 gemma-3-27b-it）⭐ NEW

✅ **Gemma 統一意圖分類** - chat / multimodal / memory / image generation 先走 gemma-3-27b-it 分類，再決定後續路徑

✅ **主動式研究管線** - 複雜問題可自動拆成多個搜尋任務，逐步蒐集資料再合成答案

✅ **答案完整性審核回圈** - 第一版回答完成後，Gemma reviewer 可自動判斷缺漏並補查一次再重寫

✅ **程式碼輸出強制標準** - 程式碼任務預設要求完整可執行實作、`main()`、至少 2 組測資、複雜度與邊界條件

✅ **內部 Gemma 用量可視化** - Dashboard 可直接看到 classifier / planner / reviewer 等內部 helper 呼叫次數

✅ **Web UI** - React 前端儀錶板，實時查看配額和對話

✅ **自動文檔** - FastAPI 自動生成 API 文檔

## 今日功能強化

- 對話入口改為先做 Gemma 統一意圖分類，再決定文字聊天、多模態、記憶查詢或自動生圖
- 搜尋型問題新增 `需求拆解 -> 多次搜尋 -> 彙整 -> reviewer 檢查 -> 必要時補查` 的單次閉環
- 自動生圖新增資料型圖片管線，像股價 K 線圖這類需求會先查資料再生成
- 程式碼任務新增最低輸出標準，避免只回片段或概念摘要
- 回答風格新增研究型工作摘要模式，支援複雜任務但不暴露 raw chain-of-thought
- `/admin/status` 與前端 dashboard 會顯示內部 Gemma helper 用量與多 account 配額明細

## 📸 功能展示

### Web UI 介面預覽

#### 💬 對話介面
即時與 AI 對話。

![對話介面](demo-png/demo-chat.PNG)

#### 🤖 AI 回應展示
查看 AI 的詳細回應和對話內容。

![AI 回應](demo-png/demo-answer.PNG)

#### 📊 配額儀錶板
即時監控各模型的使用情況和配額狀態。

![配額儀錶板](demo-png/demo-dash.PNG)

#### 🔄 智慧切換
自動在不同 AI 提供者之間切換，確保服務持續可用。

![智慧切換](demo-png/demo-switch.PNG)

#### 📝 日誌檢視器
查看系統運行日誌和 API 呼叫記錄。

![日誌檢視器](demo-png/demo-log.PNG)

## 快速開始

### 1. 設定環境變數

創建 `.env` 檔案：

```bash
# Google Gemini API Key
GOOGLE_API_KEY=your_google_api_key
GOOGLE_API_KEY_1=your_google_api_key_account_1
GOOGLE_API_KEY_2=your_google_api_key_account_2

# GitHub Models API Key (可選)
GITHUB_MODELS_API_KEY=your_github_token
GITHUB_MODELS_API_KEY_1=your_github_token_account_1
GITHUB_MODELS_API_KEY_2=your_github_token_account_2

# API 服務配置
API_HOST=127.0.0.1
API_PORT=8000
```

### 2. 安裝依賴

```bash
pip install -r requirements.txt
```

### 3. 啟動後端 API

```bash
python api.py
```

服務將在以下地址啟動：
- **API 服務**: http://127.0.0.1:8000
- **API 文檔**: http://localhost:8000/docs
- **健康檢查**: http://localhost:8000/health

### 4. 啟動前端 (可選)

```bash
./start_frontend.sh
```

前端將在 http://localhost:3000 啟動

詳細說明請參考 [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md)

## 文件導覽

| 文件 | 說明 |
|---|---|
| [OPENCLAW_API_REPORT.md](OPENCLAW_API_REPORT.md) | 最新 API、物件、OpenClaw 與 MCP/tool 能力總覽 |
| [API_USAGE_GUIDE.md](API_USAGE_GUIDE.md) | 一般 API 呼叫指南 |
| [DIRECT_QUERY_EXAMPLES.md](DIRECT_QUERY_EXAMPLES.md) | `/v1/direct_query` 詳細範例 |
| [DIRECT_QUERY_SUMMARY.md](DIRECT_QUERY_SUMMARY.md) | direct query 功能摘要 |
| [FILE_UPLOAD_API.md](FILE_UPLOAD_API.md) | `/v1/file/generate_content` 使用方式 |

## API 端點

### 核心接口

| 端點 | 方法 | 說明 |
|------|------|------|
| `/` | `GET`/`POST` | 服務資訊與端點摘要 |
| `/health` | `GET`/`POST` | 健康檢查 |
| `/v1/chat/completions` | POST | OpenAI Chat Completions API |
| `/v1/completions` | POST | OpenAI Completions API (legacy) |
| `/v1/images/generations` | POST | OpenAI Images API (HuggingFace / open-source image models) |
| `/v1/direct_query` | POST | 直接查詢指定 provider/model |
| `/v1/file/generate_content` | POST | 上傳圖片或文件並生成內容 |
| `/v1/models` | GET/POST | 列出所有可用模型 |

`/v1/models` 會額外回傳 `capabilities`，用來描述模型是否支援 image/document input 與特殊任務類型。

### 管理接口

| 端點 | 方法 | 說明 |
|------|------|------|
| `/admin/status` | GET | 查看配額狀態 |
| `/admin/logs` | GET | 讀取最新日誌 |
| `/admin/reset_quotas` | POST | 重置所有配額 (每日) |
| `/admin/refresh_rpm` | POST | 重置優先順序指標 |

### OpenClaw / MCP 接口

| 端點 | 方法 | 說明 |
|------|------|------|
| `/mcp/sse` | GET | OpenClaw MCP SSE transport |
| `/mcp/messages` | POST | OpenClaw MCP JSON-RPC message channel |

## OpenClaw 支援重點

### 相容能力

- 支援 OpenAI-style `tools` 與 `tool_choice` 請求欄位
- 可辨識 web-search-like tool 宣告並輸出 `tool_calls`
- 支援 OpenClaw 內建 `web_search` 導向本地搜尋工具
- 支援 post-tool round 將搜尋結果重新送回 LLM 合成答案
- 支援在搜尋回應中附加 `citations`
- 支援 chat completions 串流與串流 tool-call 輸出

### Tool-Calling 流程

1. 使用者訊息送到 `/v1/chat/completions`
2. 如果宣告了 web search 工具，gateway 先判斷是否真的需要搜尋
3. 若需要，會先規劃搜尋任務，再回 `tool_calls`
4. client 執行工具或由 OpenClaw 走 MCP round trip
5. 工具結果回到 `/v1/chat/completions`
6. gateway 萃取來源、清理工具輸出，再交給模型做最終回答
7. 若 reviewer 判定答案仍不完整，會再補做一次 follow-up 搜尋並重寫答案

### 研究型回答流程

- 適用於需要外部資料的複雜問題
- 先由 Gemma 決定是否需要搜尋
- 再由 Gemma 規劃多個資訊需求與 query
- 逐項執行搜尋並整理 evidence
- 產生第一版答案後，再由 Gemma reviewer 判斷是否仍有缺漏
- 若有缺漏，會再補查一次並重生最終答案

### 資料驅動圖片流程

- 適用於 K 線圖、趨勢圖、統計圖、dashboard、infographic 等資料型圖片需求
- 先判斷是否屬於 data-backed image request
- 若是，先走搜尋規劃與資料蒐集
- 將 evidence 注入 image prompt 後再交給 image model 生成
- 回應可包含 `images`、`citations`、`research_tasks`

## Tool API List

目前 MCP server 註冊的工具如下。

### `search_web`

用途：搜尋即時網路資訊，供 OpenClaw / MCP client 或 chat tool-calling round 使用。

輸入物件：

```json
{
  "query": "台指期 昨日收盤",
  "max_results": 5
}
```

輸入欄位：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `query` | string | 是 | 搜尋關鍵字 |
| `max_results` | integer | 否 | 最多回傳結果數，預設 `5` |

輸出內容：

- MCP text content list
- 每筆結果包含標題、URL、Snippet
- 部分資料型查詢會額外附 `Detail`

## 支援的主要請求物件

### Chat Completion Request

`/v1/chat/completions` 目前實際支援的主要欄位：

| 欄位 | 類型 | 說明 |
|---|---|---|
| `model` | string | `auto`、分類名或具體模型名 |
| `messages` | array | OpenAI-style message list |
| `temperature` | number | 生成溫度 |
| `max_tokens` | integer | 回答長度上限 |
| `max_completion_tokens` | integer | `max_tokens` 替代欄位 |
| `stream` | boolean | 支援 SSE 串流 |
| `target_category` | string | 指定路由類別 |
| `enable_memory` | boolean | 啟用或停用記憶注入 |
| `tools` | array | OpenAI-style tool definitions |
| `tool_choice` | string/object | 控制工具策略 |
| `attachments` | array | top-level 附件（自動注入成 content parts） |
| `input_files` | array | top-level 文件陣列（轉為 `input_file`） |
| `input_images` | array | top-level 圖像陣列（轉為 `image_url`） |
| `enable_auto_image_generation` | boolean | 對話中自動判斷是否要改走 image generation |
| `image_model` | string | 自動生圖時使用的 image model，預設為 HuggingFace FLUX |
| `image_n` | integer | 自動生圖張數（1-4） |
| `image_size` | string | 自動生圖尺寸 |

### 多模態輸入

`/v1/chat/completions` 保持同一個接口，但 `messages[].content` 可以是多段內容。

目前支援的 content part：

- `text`
- `input_text`
- `image_url`
- `input_file`

也支援直接從 request top-level 傳附件欄位（server 會自動注入到最後一則 `user` message）：

- `attachments`: 可混合 `text` / `image_url` / `input_file`
- `input_files`: 會轉成 `input_file` parts
- `input_images`: 會轉成 `image_url` parts

目前限制：

- 一次最多 5 份文件
- 已接上的文件預處理類型：`txt`、`csv`、`xlsx`、`pdf`

處理方式：

- 圖像會在需要時保留給多模態聊天模型
- 文件會先做 server-side 文字抽取或摘要，再合併進 prompt
- router 會先用較便宜的模型判斷是否真的需要切到 `MultiModal`
- 若只是文件摘要分析，通常仍優先走較便宜的文字模型

範例：

```json
{
  "model": "auto",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "請根據這些附件做摘要"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        {
          "type": "input_file",
          "file_name": "report.pdf",
          "mime_type": "application/pdf",
          "file_data": "base64..."
        }
      ]
    }
  ]
}
```

Top-level 附件欄位範例：

```json
{
  "model": "auto",
  "messages": [
    {
      "role": "user",
      "content": "請根據附件整理重點"
    }
  ],
  "input_files": [
    {
      "file_name": "sales.csv",
      "mime_type": "text/csv",
      "file_data": "base64..."
    }
  ],
  "input_images": [
    "data:image/png;base64,..."
  ]
}
```

多 key 行為：

- 可同時設定 `GOOGLE_API_KEY`, `GOOGLE_API_KEY_1`, `GOOGLE_API_KEY_2`...（GitHub 同規則）
- 生圖可另外設定 `HUGGINGFACE_API_KEY`, `HUGGINGFACE_API_KEY_1`, `HUGGINGFACE_API_KEY_2`...
- Router 會將同 provider 同 model 展開成多個 account 路由節點並輪詢
- 配額以 `provider|account|model` 追蹤，`/v1/models` 與 `/admin/status` 會回傳彙總與各 account 明細

### Message Object

支援的 message role 與正規化規則：

- `user`
- `assistant`
- `system`
- `developer` 會被正規化成 `system`
- `tool` 會被轉成 system transcript 再交給 router

### 內容型別

`content` 可接受：

- 純字串
- OpenAI-style part list
- 含 `text` 或 `content` 的 object

## 使用範例

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="dummy"  # ModelRouter 不需要驗證，可填任意值
)

response = client.chat.completions.create(
    model="auto",  # 自動選擇最佳模型
    messages=[
        {"role": "user", "content": "Hello!"}
    ]
)

print(response.choices[0].message.content)
```

### cURL

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### 查看配額狀態

```bash
curl http://localhost:8000/admin/status
```

### 直接 API 呼叫格式

您可以直接透過 HTTP POST 請求與 API 互動，無需安裝任何 SDK：

#### 基本請求格式

```bash
POST http://localhost:8000/v1/chat/completions
Content-Type: application/json

{
  "model": "auto",
  "messages": [
    {"role": "system", "content": "你是一個有幫助的助手"},
    {"role": "user", "content": "你好"}
  ]
}
```

#### 完整參數範例

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dummy" \
  -d '{
    "model": "auto",
    "messages": [
      {"role": "system", "content": "你是一個專業的程式設計助手"},
      {"role": "user", "content": "用 Python 寫一個 Hello World"}
    ],
    "temperature": 0.7,
    "max_tokens": 1000,
    "top_p": 1.0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0
  }'
```

#### 多輪對話範例

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "TextOnlyHigh",
    "messages": [
      {"role": "user", "content": "什麼是機器學習？"},
      {"role": "assistant", "content": "機器學習是人工智慧的一個分支..."},
      {"role": "user", "content": "可以舉例說明嗎？"}
    ]
  }'
```

#### 使用不同程式語言直接呼叫

**JavaScript/Node.js (Fetch API):**
```javascript
const response = await fetch('http://localhost:8000/v1/chat/completions', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    model: 'auto',
    messages: [
      { role: 'user', content: '你好，請介紹自己' }
    ],
    temperature: 0.7,
    max_tokens: 500
  })
});

const data = await response.json();
console.log(data.choices[0].message.content);
```

**Python (requests):**
```python
import requests

response = requests.post(
    'http://localhost:8000/v1/chat/completions',
    headers={'Content-Type': 'application/json'},
    json={
        'model': 'auto',
        'messages': [
            {'role': 'user', 'content': '用 Python 計算階乘'}
        ],
        'temperature': 0.7,
        'max_tokens': 1000
    }
)

result = response.json()
print(result['choices'][0]['message']['content'])
```

**PHP:**
```php
<?php
$data = [
    'model' => 'auto',
    'messages' => [
        ['role' => 'user', 'content' => 'Hello from PHP!']
    ]
];

$options = [
    'http' => [
        'method' => 'POST',
        'header' => 'Content-Type: application/json',
        'content' => json_encode($data)
    ]
];

$context = stream_context_create($options);
$response = file_get_contents('http://localhost:8000/v1/chat/completions', false, $context);
$result = json_decode($response, true);

echo $result['choices'][0]['message']['content'];
?>
```

**Java (HttpClient):**
```java
import java.net.http.*;
import java.net.URI;

HttpClient client = HttpClient.newHttpClient();

String json = """
{
  "model": "auto",
  "messages": [{"role": "user", "content": "Hello from Java!"}]
}
""";

HttpRequest request = HttpRequest.newBuilder()
    .uri(URI.create("http://localhost:8000/v1/chat/completions"))
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString(json))
    .build();

HttpResponse<String> response = client.send(request, 
    HttpResponse.BodyHandlers.ofString());

System.out.println(response.body());
```

#### 回應格式

成功的回應格式 (OpenAI 相容):

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1709856000,
  "model": "gemini-2.5-flash",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你好！我是 AI 助手，很高興為您服務。"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 15,
    "total_tokens": 25
  }
}
```

錯誤回應格式:

```json
{
  "error": {
    "message": "所有模型都不可用或已達配額上限",
    "type": "unavailable_error",
    "code": 503
  }
}
```

#### 遠端伺服器呼叫

如果 API 部署在遠端伺服器，將 `localhost` 替換為伺服器 IP 或網域名稱：

```bash
# 使用 IP 位址
curl -X POST http://192.168.1.100:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "Hello"}]}'

# 使用網域名稱
curl -X POST https://api.yourdomain.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "Hello"}]}'
```

#### 支援的參數

| 參數 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `model` | string | 是 | 模型名稱 (`auto`, `TextOnlyHigh`, `TextOnlyLow` 或具體模型) |
| `messages` | array | 是 | 對話訊息陣列 |
| `temperature` | float | 否 | 控制隨機性 (0.0-2.0)，預設 1.0 |
| `max_tokens` | integer | 否 | 最大生成 tokens 數，預設模型限制 |
| `top_p` | float | 否 | Nucleus sampling (0.0-1.0)，預設 1.0 |
| `frequency_penalty` | float | 否 | 頻率懲罰 (-2.0-2.0)，預設 0.0 |
| `presence_penalty` | float | 否 | 存在懲罰 (-2.0-2.0)，預設 0.0 |
| `stop` | string/array | 否 | 停止序列 |
| `stream` | boolean | 否 | `/v1/chat/completions` 支援 SSE；`/v1/completions` 不支援 |

## 🎯 模型選擇

`model` 參數支援：

- `auto` - 自動選擇（先 TextOnlyHigh，再 TextOnlyLow）
- `TextOnlyHigh` - 只用高品質模型（GitHub gpt-4o, Gemini 2.5 flash）
- `TextOnlyLow` - 只用經濟型模型（GitHub gpt-4o-mini, Gemini 3.1 flash-lite, Ollama 本地模型）
- 具體模型名稱 - 如 `openai/gpt-4o`、`gemini-2.5-flash`

## 🧠 智慧記憶功能 ⭐ NEW

ModelRouter 現在支援智慧記憶功能，可以自動判斷是否需要查詢歷史日誌：

### 功能特點

- **Pre-chat 分析**：使用 gemma-3-27b-it 判斷用戶問題是否需要查詢歷史
- **自動 RAG**：檢測到記憶相關關鍵字時，自動讀取 app.log 並增強 prompt
- **配額追蹤**：gemma-3-27b-it 的使用會正確計入配額管理系統
- **對話歷史**：自動保存最近 10 輪對話

### 工作流程

```
用戶輸入
    ↓
檢查關鍵字
    ↓
Pre-chat 分析 (gemma-3-27b-it)
    ├─ 檢查配額是否足夠 ⭐
    ├─ 調用模型
    └─ 成功後扣減配額 ⭐
    ↓
需要查 log？
    ├─ 否 → 正常處理
    └─ 是 → 讀取 app.log
            ↓
         增強 prompt
            ↓
         發送到主模型
            ↓
         返回結果
            ↓
         保存到對話歷史
```

### 觸發關鍵字

當用戶問題包含以下關鍵字時，會觸發記憶查詢：

**中文**：記憶、查看過去、剛剛、之前、先前、上次、日誌、歷史、記錄

**英文**：memory、log、history

### 使用範例

```python
# 一般對話（不觸發記憶）
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "你好，介紹一下你自己"}]
)

# 查詢歷史（觸發記憶，會自動查詢 app.log）
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "請查看剛剛的記錄"}]
)
```

### 配額管理機制

Pre-chat 分析採用**兩層配額管理機制**：

**第一層：gemma-3-27b-it（主要）**
- 使用 gemma-3-27b-it 模型進行智能判斷
- 調用前檢查配額，確保配額足夠才調用
- 調用成功後自動扣減配額（計數跳動）⭐
- 配額設定：14400 RPD（每日請求數）

**第二層：關鍵字匹配（備用）**
- 如果 gemma-3-27b-it 配額用完，自動降級為關鍵字匹配
- 完全本地處理，不依賴外部 API
- 保證功能不會完全失效

### 查看配額狀態

```bash
# 查看所有模型配額（包含 gemma-3-27b-it）
curl http://localhost:8000/admin/status

# 重置配額（每日一次）
curl -X POST http://localhost:8000/admin/reset_quotas

# 查看即時日誌
tail -f app/app.log | grep -E '\[Pre-chat\]|\[Memory\]'
```

### 技術細節

ModelRouter 新增的方法：

- **`add_to_history(user_message, assistant_response)`** - 將對話添加到歷史記錄
- **`check_need_log_rag(user_message)`** - Pre-chat 分析，自動管理配額
- **`read_app_log(max_lines=100)`** - 讀取 app.log 的最後 N 行

配置參數：
- `max_history_size`: 最多保留的對話輪數（默認 10）
- `max_lines`: 讀取 log 的最大行數（默認 100）

## 專案結構

```
llm-api/
├── api.py                     # 主 API 服務
├── ModelRouter/               # 路由引擎
│   ├── ModelRouter.py         # 核心路由邏輯
│   └── models.py              # 模型配置
├── frontend/                  # React 前端
│   ├── src/
│   │   ├── components/        # UI 組件
│   │   └── App.jsx            # 主應用
│   └── package.json
├── .env                       # 環境變數配置
├── start_frontend.sh          # 前端啟動腳本
└── README.md                  # 本文件
```

## 配置說明

### 環境變數

在 `.env` 中配置：

```bash
# Google Gemini API
GOOGLE_API_KEY=your_google_api_key
GOOGLE_API_URL=https://generativelanguage.googleapis.com/v1beta/openai/

# 同一組 Google key 目前也用於多模態 chat / OCR 類請求

# GitHub Models (可選)
GITHUB_MODELS_API_KEY=your_github_personal_access_token
GITHUB_MODELS_API_URL=https://models.github.ai/inference

# Ollama (本地，可選)
OLLAMA_API_KEY=ollama
OLLAMA_API_URL=http://localhost:11434/v1

# API 服務
API_HOST=0.0.0.0
API_PORT=8000
```

### 模型優先順序

在 `ModelRouter/ModelRouter.py` 的 `_config_limits` 中配置：

- **TextOnlyHigh**: GitHub gpt-4o → Google gemini-2.5-flash
- **TextOnlyLow**: GitHub gpt-4o-mini → Google gemini-3.1-flash-lite → Ollama 本地模型

順序決定 failover 策略，額度滿或失敗會自動切換下一個。

## 常見問題

### 1. GitHub Models 403 錯誤

- 確認 `GITHUB_MODELS_API_KEY` 已設定（需要 GitHub PAT Token）
- 確認你的 GitHub 帳號有 Copilot 訂閱
- 部分模型需要 Copilot Pro/Enterprise

暫時解決：系統會自動 failover 到 Google Gemini

### 2. Google Gemini API Key

前往 https://aistudio.google.com/app/apikey 建立 API Key

### 3. 如何使用本地 Ollama 模型

```bash
# 1. 安裝 Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 2. 下載模型
ollama pull qwen3:4b-instruct
ollama pull deepseek-r1:1.5b

# 3. 確保 Ollama 在背景運行
ollama serve
```

ModelRouter 會在高優先級模型額度用完後自動切換到 Ollama。

## Web UI 功能

前端提供：
- 💬 **對話介面** - 即時與 AI 對話
- 📊 **配額儀錶板** - 查看各模型使用情況
- ⚙️ **設定調整** - Temperature、Max Tokens、模型選擇
- 🔄 **自動刷新** - 每 5 分鐘更新配額

詳細說明請參考 [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md)

## 授權

MIT License
