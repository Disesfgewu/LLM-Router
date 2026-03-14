# Direct Query API 使用示例

## 端點說明

`POST /v1/direct_query`

直接查詢指定的模型和提供商，不經過自動路由邏輯。即使模型不在配置列表中也會嘗試訪問。

## 請求參數

```json
{
  "model_name": "gemma-3-7b-it",
  "provider": "Google",
  "prompt": "你好，請介紹一下你自己",
  "temperature": 0.7,      // 可選，默認 0.7
  "max_tokens": 1000       // 可選
}
```

### 參數說明

- `model_name` (必需): 模型名稱，例如 `"gemma-3-7b-it"`, `"gpt-4o"`, `"qwen3:4b-instruct"`
- `provider` (必需): 提供商名稱，支持:
  - `"GitHub"` - GitHub Models
  - `"Google"` - Google Gemini
  - `"Ollama"` - 本地 Ollama
- `prompt` (必需): 提示詞/問題
- `temperature` (可選): 溫度參數，控制輸出隨機性，默認 0.7
- `max_tokens` (可選): 最大生成 token 數

## 使用示例

### 1. 查詢 Google Gemini 模型

```bash
curl -X POST http://localhost:8000/v1/direct_query \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "gemma-3-7b-it",
    "provider": "Google",
    "prompt": "什麼是人工智能？"
  }'
```

### 2. 查詢 GitHub Models

```bash
curl -X POST http://localhost:8000/v1/direct_query \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "openai/gpt-4o",
    "provider": "GitHub",
    "prompt": "解釋量子計算的基本原理",
    "temperature": 0.5,
    "max_tokens": 500
  }'
```

### 3. 查詢本地 Ollama 模型

```bash
curl -X POST http://localhost:8000/v1/direct_query \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "qwen3:4b-instruct",
    "provider": "Ollama",
    "prompt": "寫一首關於春天的詩"
  }'
```

### 4. 嘗試不在配置列表中的模型

```bash
curl -X POST http://localhost:8000/v1/direct_query \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "gemini-2.0-pro",
    "provider": "Google",
    "prompt": "測試新模型的響應"
  }'
```

## 響應格式

成功響應 (HTTP 200):

```json
{
  "id": "chatcmpl-1234567890",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "gemma-3-7b-it",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "模型的回答內容..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 50,
    "total_tokens": 60
  }
}
```

## 錯誤響應

### 配額不足或速率限制 (HTTP 500)

```json
{
  "detail": "模型 gpt-4o 配額不足或達到速率限制"
}
```

### 模型不存在 (HTTP 500)

```json
{
  "detail": "模型 invalid-model 不存在或在 Google 上不可用"
}
```

### API 密鑰問題 (HTTP 500)

```json
{
  "detail": "Google API 密鑰無效或未設置"
}
```

### 無效的 provider (HTTP 400)

```json
{
  "detail": "不支持的 provider: InvalidProvider。支持的 provider: GitHub, Google, Ollama"
}
```

## Python 使用示例

```python
import requests

url = "http://localhost:8000/v1/direct_query"

# 準備請求數據
data = {
    "model_name": "gemma-3-7b-it",
    "provider": "Google",
    "prompt": "解釋什麼是深度學習？",
    "temperature": 0.7,
    "max_tokens": 1000
}

# 發送請求
response = requests.post(url, json=data)

# 檢查響應
if response.status_code == 200:
    result = response.json()
    answer = result["choices"][0]["message"]["content"]
    print(f"回答: {answer}")
else:
    print(f"錯誤: {response.json()['detail']}")
```

## JavaScript (Node.js) 使用示例

```javascript
const axios = require('axios');

async function directQuery() {
  try {
    const response = await axios.post('http://localhost:8000/v1/direct_query', {
      model_name: 'gemma-3-7b-it',
      provider: 'Google',
      prompt: '什麼是機器學習？',
      temperature: 0.7,
      max_tokens: 1000
    });
    
    const answer = response.data.choices[0].message.content;
    console.log('回答:', answer);
  } catch (error) {
    console.error('錯誤:', error.response?.data?.detail || error.message);
  }
}

directQuery();
```

## 注意事項

1. **繞過配額檢查**: 此 API 不會檢查或扣減本地配額追蹤，每次調用都會直接訪問上游 API
2. **錯誤處理**: 如果模型不存在、API 密鑰無效或配額不足，將返回 HTTP 500 錯誤
3. **嘗試任何模型**: 可以嘗試任何模型名稱，系統會嘗試訪問，成功與否取決於提供商是否支持該模型
4. **重試機制**: 內建網路錯誤重試機制，最多重試 2 次
5. **參數調整**: 系統會自動根據模型類型調整參數（例如推理模型的特殊參數處理）
