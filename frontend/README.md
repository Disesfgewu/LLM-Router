# ModelRouter API Gateway

多模型智慧路由 API 閘道，對外提供 OpenAI 相容介面，自動在 GitHub Models、Google Gemini、Ollama 之間做 failover 和配額管理。

## 功能特色

✅ **多提供者路由** - 自動在 GitHub Models、Google Gemini、Ollama 切換
✅ **智慧 Failover** - 一個模型失敗或額度滿，自動切換下一個
✅ **配額管理** - 本地追蹤每個模型的每日請求數 (RPD)
✅ **OpenAI 相容** - 完全相容 OpenAI API 格式
✅ **Web UI** - React 前端儀錶板，實時查看配額和對話
✅ **自動文檔** - FastAPI 自動生成 API 文檔

## 快速開始

### 1. 設定環境變數

創建 \`.env\` 檔案：

\`\`\`bash
# Google Gemini API Key
GOOGLE_API_KEY=your_google_api_key

# GitHub Models API Key (可選)
GITHUB_MODELS_API_KEY=your_github_token

# API 服務配置
API_HOST=0.0.0.0
API_PORT=8000
\`\`\`

### 2. 安裝依賴

\`\`\`bash
pip install fastapi uvicorn openai python-dotenv pydantic
\`\`\`

### 3. 啟動後端 API

\`\`\`bash
python api.py
\`\`\`

服務將在以下地址啟動：
- **API 服務**: http://0.0.0.0:8000
- **API 文檔**: http://localhost:8000/docs
- **健康檢查**: http://localhost:8000/health

### 4. 啟動前端 (可選)

\`\`\`bash
./start_frontend.sh
\`\`\`

前端將在 http://localhost:3000 啟動

詳細說明請參考 [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md)

## API 端點

### 核心接口

| 端點 | 方法 | 說明 |
|------|------|------|
| \`/v1/chat/completions\` | POST | OpenAI Chat Completions API |
| \`/v1/completions\` | POST | OpenAI Completions API (legacy) |
| \`/v1/models\` | GET/POST | 列出所有可用模型 |
| \`/health\` | GET/POST | 健康檢查 |
| \`/\` | GET/POST | 服務資訊 |

### 管理接口

| 端點 | 方法 | 說明 |
|------|------|------|
| \`/admin/status\` | GET | 查看配額狀態 |
| \`/admin/reset_quotas\` | POST | 重置所有配額 (每日) |
| \`/admin/refresh_rpm\` | POST | 重置優先順序指標 |

## 使用範例

### Python (OpenAI SDK)

\`\`\`python
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
\`\`\`

### cURL

\`\`\`bash
curl -X POST http://localhost:8000/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
\`\`\`

### 查看配額狀態

\`\`\`bash
curl http://localhost:8000/admin/status
\`\`\`

## 模型選擇

\`model\` 參數支援：

- \`auto\` - 自動選擇（先 TextOnlyHigh，再 TextOnlyLow）
- \`TextOnlyHigh\` - 只用高品質模型（GitHub gpt-4o, Gemini 2.5 flash）
- \`TextOnlyLow\` - 只用經濟型模型（GitHub gpt-4o-mini, Gemini 3.1 flash-lite, Ollama 本地模型）
- 具體模型名稱 - 如 \`openai/gpt-4o\`、\`gemini-2.5-flash\`

## 專案結構

\`\`\`
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
\`\`\`

## 配置說明

### 環境變數

在 \`.env\` 中配置：

\`\`\`bash
# Google Gemini API
GOOGLE_API_KEY=your_google_api_key
GOOGLE_API_URL=https://generativelanguage.googleapis.com/v1beta/openai/

# GitHub Models (可選)
GITHUB_MODELS_API_KEY=your_github_personal_access_token
GITHUB_MODELS_API_URL=https://models.github.ai/inference

# Ollama (本地，可選)
OLLAMA_API_KEY=ollama
OLLAMA_API_URL=http://localhost:11434/v1

# API 服務
API_HOST=0.0.0.0
API_PORT=8000
\`\`\`

### 模型優先順序

在 \`ModelRouter/ModelRouter.py\` 的 \`_config_limits\` 中配置：

- **TextOnlyHigh**: GitHub gpt-4o → Google gemini-2.5-flash
- **TextOnlyLow**: GitHub gpt-4o-mini → Google gemini-3.1-flash-lite → Ollama 本地模型

順序決定 failover 策略，額度滿或失敗會自動切換下一個。

## 常見問題

### 1. GitHub Models 403 錯誤

- 確認 \`GITHUB_MODELS_API_KEY\` 已設定（需要 GitHub PAT Token）
- 確認你的 GitHub 帳號有 Copilot 訂閱
- 部分模型需要 Copilot Pro/Enterprise

暫時解決：系統會自動 failover 到 Google Gemini

### 2. Google Gemini API Key

前往 https://aistudio.google.com/app/apikey 建立 API Key

### 3. 如何使用本地 Ollama 模型

\`\`\`bash
# 1. 安裝 Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 2. 下載模型
ollama pull qwen3:4b-instruct
ollama pull deepseek-r1:1.5b

# 3. 確保 Ollama 在背景運行
ollama serve
\`\`\`

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
