# API 使用指南

本文檔說明如何從其他程式碼中調用 ModelRouter API 的主要端點，並補充今天新增的研究型搜尋、生圖與程式碼輸出增強行為。

## 今日更新摘要

- `/v1/chat/completions` 會先經過 Gemma 統一意圖分類，再決定文字、多模態、記憶或生圖路徑
- 搜尋型問題支援多步規劃搜尋與 reviewer 補查回圈
- 程式碼任務會強制要求完整可執行程式、`main()`、至少 2 組測資、複雜度與邊界條件
- 資料驅動圖片需求可先查資料再進入 image generation
- `/admin/status` 現在也會回傳內部 Gemma helper 用量

## 目錄
- [0. `/v1/chat/completions` - 研究型與多模態聊天](#0-v1chatcompletions---研究型與多模態聊天)
- [1. `/v1/completions` - OpenAI Completions API (舊版)](#1-v1completions---openai-completions-api-舊版)
- [2. `/v1/direct_query` - 直接查詢指定模型](#2-v1direct_query---直接查詢指定模型)
- [3. `/v1/images/generations` - OpenAI Images API](#3-v1imagesgenerations---openai-images-api)

---

## 0. `/v1/chat/completions` - 研究型與多模態聊天

### 端點資訊
- **URL**: `http://localhost:8000/v1/chat/completions`
- **方法**: `POST`
- **Content-Type**: `application/json`

### 新增行為重點

- 所有請求會先走 Gemma 意圖分類
- 複雜問題可自動進入 `需求拆解 -> 多次搜尋 -> 彙整 -> reviewer 檢查 -> 補查` 流程
- 程式碼問題會偏向輸出完整實作，而不是只給概念摘要
- 同一個端點可直接接收圖片與文件附件
- 若判定為圖片生成需求，可自動改走生圖路徑

### 常用欄位

```json
{
    "model": "auto",
    "messages": [{"role": "user", "content": "幫我整理最近一週台積電股價走勢"}],
    "temperature": 0.7,
    "max_tokens": 2048,
    "stream": false,
    "tools": [],
    "tool_choice": "auto",
    "enable_memory": true,
    "input_files": [],
    "input_images": [],
    "enable_auto_image_generation": true,
    "image_model": "black-forest-labs/FLUX.1-schnell",
    "image_n": 1,
    "image_size": "1024x1024"
}
```

### 搜尋型回答補充

- 若回答使用了外部資料，可能回傳 `citations`
- 若 reviewer 判定第一版答案不完整，後端可能自動補做 follow-up 搜尋
- 若是資料型圖片需求，回應也可能帶有 `research_tasks`

### 程式碼任務補充

目前後端會盡量要求模型遵守以下最低標準：

- 提供完整可編譯/可執行程式
- 包含 `main()`
- 至少 2 組測資
- 說明時間複雜度、空間複雜度與邊界條件

---

## 1. `/v1/completions` - OpenAI Completions API (舊版)

### 端點資訊
- **URL**: `http://localhost:8000/v1/completions`
- **方法**: `POST`
- **Content-Type**: `application/json`

### 請求格式

```json
{
  "model": "auto",           // 可選，預設 "auto"
  "prompt": "你的提示詞",     // 必填
  "temperature": 0.7,        // 可選，預設 0.7，範圍 0.0-2.0
  "max_tokens": 1000,        // 可選，最大生成 token 數
  "stream": false            // 可選，預設 false（目前不支持 true）
}
```

### 參數說明
- `model`: 模型選擇（預設為 "auto"，自動選擇）
- `prompt`: 要發送給模型的提示詞文本（必填）
- `temperature`: 控制回答的隨機性，越高越隨機
- `max_tokens`: 限制回答的最大長度
- `stream`: 是否使用串流模式（目前不支持）

### 使用範例

#### Python (使用 requests)
```python
import requests

url = "http://localhost:8000/v1/completions"
# 假設您已從前端儀表板生成了 API key
api_key = "ma_... or mk_..." 

payload = {
    "model": "auto",
    "prompt": "請解釋什麼是機器學習",
    "temperature": 0.7,
    "max_tokens": 500
}

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}" # 每個 API 請求都需要認證
}

response = requests.post(url, json=payload, headers=headers)

if response.status_code == 200:
    result = response.json()
    print("回答:", result["choices"][0]["text"])
    print("使用模型:", result["model"])
elif response.status_code == 401:
    print("認證失敗：API key 無效或未提供。")
else:
    print("錯誤:", response.status_code, response.text)
```

#### Python (使用 httpx - async)
```python
import httpx
import asyncio

async def call_completions():
    url = "http://localhost:8000/v1/completions"
    
    payload = {
        "prompt": "Python 中如何處理異常？",
        "temperature": 0.5,
        "max_tokens": 300
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload)
        
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["text"]
        else:
            raise Exception(f"API Error: {response.status_code}")

# 執行
answer = asyncio.run(call_completions())
print(answer)
```

#### JavaScript (Node.js - fetch)
```javascript
const fetch = require('node-fetch');

async function callCompletions() {
    const url = 'http://localhost:8000/v1/completions';
    
    const payload = {
        model: 'auto',
        prompt: '介紹一下 FastAPI 框架',
        temperature: 0.7,
        max_tokens: 500
    };
    
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
        
        if (response.ok) {
            const result = await response.json();
            console.log('回答:', result.choices[0].text);
            console.log('使用模型:', result.model);
        } else {
            console.error('錯誤:', response.status, await response.text());
        }
    } catch (error) {
        console.error('請求失敗:', error);
    }
}

callCompletions();
```

#### JavaScript (瀏覽器 - axios)
```javascript
import axios from 'axios';

async function callCompletions() {
    const url = 'http://localhost:8000/v1/completions';
    
    try {
        const response = await axios.post(url, {
            prompt: '什麼是 REST API？',
            temperature: 0.6,
            max_tokens: 400
        });
        
        console.log('回答:', response.data.choices[0].text);
        console.log('Token 使用:', response.data.usage);
    } catch (error) {
        console.error('錯誤:', error.response?.data || error.message);
    }
}
```

#### cURL
```bash
curl -X POST "http://localhost:8000/v1/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "prompt": "解釋什麼是 Docker",
    "temperature": 0.7,
    "max_tokens": 500
  }'
```

### 響應格式
```json
{
  "id": "cmpl-1234567890",
  "object": "text_completion",
  "created": 1234567890,
  "model": "gemma-3-12b-it",
  "choices": [
    {
      "index": 0,
      "text": "模型的回答內容...",
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

---

## 2. `/v1/direct_query` - 直接查詢指定模型

### 端點資訊
- **URL**: `http://localhost:8000/v1/direct_query`
- **方法**: `POST`
- **Content-Type**: `application/json`

### 請求格式

```json
{
  "model_name": "gemma-3-7b-it",     // 必填：模型名稱
  "provider": "GitHub",               // 必填：提供商
  "prompt": "你的提示詞",              // 必填
  "temperature": 0.7,                 // 可選，預設 0.7
  "max_tokens": 1000                  // 可選
}
```

### 參數說明
- `model_name`: 模型名稱（必填），例如：
  - `"gemma-3-7b-it"`
  - `"gpt-4o"`
  - `"gemini-1.5-flash"`
- `provider`: 提供商（必填），支持：
  - `"GitHub"` - GitHub Models
  - `"Google"` - Google Gemini
  - `"Ollama"` - 本地 Ollama
    - `"HuggingFace"` - HuggingFace provider
- `prompt`: 提示詞文本（必填）
- `temperature`: 溫度參數（可選）
- `max_tokens`: 最大生成 token 數（可選）

---

## 3. `/v1/images/generations` - OpenAI Images API

### 端點資訊
- **URL**: `http://localhost:8000/v1/images/generations`
- **方法**: `POST`
- **Content-Type**: `application/json`

### 請求格式

```json
{
    "model": "black-forest-labs/FLUX.1-schnell",
    "prompt": "幫我生成一張台北夜景海報",
    "n": 1,
    "size": "1024x1024",
    "response_format": "b64_json"
}
```

### 補充說明

- chat 端點內也能自動判斷是否要切到 image generation
- 若屬於 K 線圖、趨勢圖、dashboard 之類資料驅動圖像，後端會先查資料再生成
- 支援的 `response_format`：`b64_json`、`url`
- 目前預設 image model 為 `black-forest-labs/FLUX.1-schnell`

### 使用範例

#### Python (使用 requests)
```python
import requests

url = "http://localhost:8000/v1/direct_query"

payload = {
    "model_name": "gemma-3-7b-it",
    "provider": "GitHub",
    "prompt": "寫一個 Python 排序函數",
    "temperature": 0.5,
    "max_tokens": 500
}

response = requests.post(url, json=payload)

if response.status_code == 200:
    result = response.json()
    print("回答:", result["choices"][0]["message"]["content"])
    print("使用模型:", result["model"])
else:
    print("錯誤:", response.status_code, response.json())
```

#### Python (完整錯誤處理)
```python
import requests
from typing import Optional

def direct_query(
    model_name: str,
    provider: str,
    prompt: str,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None
) -> dict:
    """
    直接查詢指定模型
    
    Args:
        model_name: 模型名稱
        provider: 提供商 (GitHub, Google, Ollama)
        prompt: 提示詞
        temperature: 溫度參數
        max_tokens: 最大 token 數
        
    Returns:
        API 響應 dict
        
    Raises:
        ValueError: 參數錯誤
        RuntimeError: API 調用失敗
    """
    # 驗證 provider
    valid_providers = ["GitHub", "Google", "Ollama"]
    if provider not in valid_providers:
        raise ValueError(f"provider 必須是 {valid_providers} 之一")
    
    url = "http://localhost:8000/v1/direct_query"
    
    payload = {
        "model_name": model_name,
        "provider": provider,
        "prompt": prompt,
        "temperature": temperature
    }
    
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        raise RuntimeError("請求超時")
    except requests.exceptions.HTTPError as e:
        error_detail = response.json().get("detail", str(e))
        raise RuntimeError(f"API 錯誤: {error_detail}")
    except Exception as e:
        raise RuntimeError(f"未知錯誤: {str(e)}")

# 使用範例
try:
    result = direct_query(
        model_name="gemma-3-12b-it",
        provider="GitHub",
        prompt="什麼是深度學習？",
        temperature=0.6,
        max_tokens=300
    )
    
    answer = result["choices"][0]["message"]["content"]
    print("回答:", answer)
    
except (ValueError, RuntimeError) as e:
    print(f"錯誤: {e}")
```

#### Python (批量調用多個模型)
```python
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

def query_models_parallel(prompt: str, models: list) -> dict:
    """
    並行查詢多個模型
    
    Args:
        prompt: 提示詞
        models: [(model_name, provider), ...] 列表
        
    Returns:
        {(model_name, provider): response, ...}
    """
    url = "http://localhost:8000/v1/direct_query"
    results = {}
    
    def query_single(model_info):
        model_name, provider = model_info
        payload = {
            "model_name": model_name,
            "provider": provider,
            "prompt": prompt,
            "temperature": 0.7
        }
        try:
            response = requests.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                return (model_info, response.json())
            else:
                return (model_info, {"error": response.text})
        except Exception as e:
            return (model_info, {"error": str(e)})
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(query_single, model): model for model in models}
        
        for future in as_completed(futures):
            model_info, result = future.result()
            results[model_info] = result
    
    return results

# 使用範例
models_to_test = [
    ("gemma-3-7b-it", "GitHub"),
    ("gemini-1.5-flash", "Google"),
    ("gpt-4o", "GitHub"),
]

results = query_models_parallel("介紹一下量子計算", models_to_test)

for (model, provider), response in results.items():
    print(f"\n=== {provider}/{model} ===")
    if "error" in response:
        print(f"錯誤: {response['error']}")
    else:
        answer = response["choices"][0]["message"]["content"]
        print(f"回答: {answer[:200]}...")
```

#### JavaScript (Node.js)
```javascript
const axios = require('axios');

async function directQuery(modelName, provider, prompt, options = {}) {
    const url = 'http://localhost:8000/v1/direct_query';
    
    const payload = {
        model_name: modelName,
        provider: provider,
        prompt: prompt,
        temperature: options.temperature || 0.7,
        ...(options.max_tokens && { max_tokens: options.max_tokens })
    };
    
    try {
        const response = await axios.post(url, payload);
        return response.data.choices[0].message.content;
    } catch (error) {
        if (error.response) {
            throw new Error(`API 錯誤: ${error.response.data.detail}`);
        } else {
            throw new Error(`請求失敗: ${error.message}`);
        }
    }
}

// 使用範例
(async () => {
    try {
        const answer = await directQuery(
            'gemma-3-7b-it',
            'GitHub',
            '什麼是機器學習？',
            { temperature: 0.6, max_tokens: 400 }
        );
        
        console.log('回答:', answer);
    } catch (error) {
        console.error('錯誤:', error.message);
    }
})();
```

#### cURL
```bash
curl -X POST "http://localhost:8000/v1/direct_query" \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "gemma-3-7b-it",
    "provider": "GitHub",
    "prompt": "介紹一下 Kubernetes",
    "temperature": 0.7,
    "max_tokens": 500
  }'
```

### 響應格式
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
    "prompt_tokens": 15,
    "completion_tokens": 100,
    "total_tokens": 115
  }
}
```

---

## 錯誤處理

### 常見錯誤碼

| 狀態碼 | 說明 | 處理方式 |
|--------|------|----------|
| 400 | 請求參數錯誤 | 檢查必填欄位和參數格式 |
| 500 | 模型調用失敗 | 查看 `detail` 字段的詳細錯誤信息 |
| 503 | 所有模型都不可用 | 稍後重試或手動重置配額 |

### 錯誤響應格式
```json
{
  "detail": "具体错误信息"
}
```

### Python 錯誤處理範例
```python
import requests

try:
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    
except requests.exceptions.Timeout:
    print("請求超時，請重試")
    
except requests.exceptions.HTTPError:
    error_info = response.json()
    status_code = response.status_code
    
    if status_code == 400:
        print(f"參數錯誤: {error_info['detail']}")
    elif status_code == 500:
        print(f"模型調用失敗: {error_info['detail']}")
    elif status_code == 503:
        print("服務暫時不可用，請稍後重試")
    else:
        print(f"未知錯誤 ({status_code}): {error_info['detail']}")
        
except Exception as e:
    print(f"發生異常: {str(e)}")
```

---

## 進階使用

### 重試機制
```python
import time
import requests

def call_with_retry(url, payload, max_retries=3, backoff=2):
    """帶指數退避的重試機制"""
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 503:
                # 服務不可用，重試
                if attempt < max_retries - 1:
                    wait_time = backoff ** attempt
                    print(f"服務不可用，{wait_time}秒後重試...")
                    time.sleep(wait_time)
                    continue
            else:
                # 其他錯誤不重試
                response.raise_for_status()
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                print(f"請求超時，重試...")
                time.sleep(backoff ** attempt)
                continue
            else:
                raise
    
    raise RuntimeError(f"重試 {max_retries} 次後仍然失敗")

# 使用
result = call_with_retry(
    "http://localhost:8000/v1/direct_query",
    {
        "model_name": "gemma-3-7b-it",
        "provider": "GitHub",
        "prompt": "測試問題"
    }
)
```

### 封裝為類
```python
import requests
from typing import Optional, Literal

class ModelRouterClient:
    """ModelRouter API 客戶端"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = requests.Session()
    
    def completions(
        self,
        prompt: str,
        model: str = "auto",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        """調用 completions API"""
        url = f"{self.base_url}/v1/completions"
        payload = {
            "model": model,
            "prompt": prompt,
            "temperature": temperature
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        response = self.session.post(url, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["text"]
    
    def direct_query(
        self,
        model_name: str,
        provider: Literal["GitHub", "Google", "Ollama"],
        prompt: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        """調用 direct_query API"""
        url = f"{self.base_url}/v1/direct_query"
        payload = {
            "model_name": model_name,
            "provider": provider,
            "prompt": prompt,
            "temperature": temperature
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        response = self.session.post(url, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    
    def close(self):
        """關閉 session"""
        self.session.close()

# 使用範例
client = ModelRouterClient()

try:
    # 使用 completions
    answer1 = client.completions("什麼是 AI？", temperature=0.5)
    print("Completions:", answer1)
    
    # 使用 direct_query
    answer2 = client.direct_query(
        "gemma-3-7b-it",
        "GitHub",
        "介紹深度學習"
    )
    print("Direct Query:", answer2)
    
finally:
    client.close()
```

---

## 注意事項

1. **端點選擇**：
   - 使用 `/v1/completions` 時，系統會自動選擇最佳可用模型（Low → High 順序）
   - 使用 `/v1/direct_query` 時，需要明確指定模型和提供商

2. **配額管理**：
   - 所有請求都受到 RPD（每日請求限制）管理
   - 如果配額用盡，會返回 503 錯誤
   - 配額每日自動重置（凌晨 0:00）

3. **超時設置**：
   - 建議設置 30 秒超時
   - 對於複雜問題，可能需要更長時間

4. **並發請求**：
   - 可以並發調用，但注意 RPM（每分鐘請求）限制
   - 建議使用連接池（如 requests.Session）

5. **Provider 大小寫**：
   - Provider 參數不區分大小寫
   - 建議使用首字母大寫：`"GitHub"`, `"Google"`, `"Ollama"`

---

## 相關端點

查看其他可用端點：
- `GET /v1/models` - 列出所有可用模型
- `POST /v1/chat/completions` - Chat completions API
- `GET /admin/status` - 查看配額狀態

完整 API 文檔：`http://localhost:8000/docs`
