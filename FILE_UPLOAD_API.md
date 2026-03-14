# 文件上傳和圖片內容生成 API

## 功能說明

這個 API 端點允許你上傳圖片文件，並使用 Google Gemini 模型生成內容描述或提取文字。

### 使用的模型

- **模型**: `gemini-1.5-flash`
- **提供商**: Google Generative AI
- **支持**: 圖片分析、OCR 文字識別、內容描述

## API 端點

### POST `/v1/file/generate_content`

上傳文件並生成內容

#### 請求參數

- **file** (File, 必需): 上傳的圖片文件
  - 支持格式: JPG, PNG, GIF, WebP 等
- **prompt** (string, 必需): 提示詞
  - 例如: "請描述這張圖片內容"
  - 例如: "請把圖片中的文字完整擷取出來"
- **temperature** (float, 可選): 溫度參數 (0-1)，默認 0.7
- **max_tokens** (int, 可選): 最大生成 token 數

#### 響應格式

```json
{
  "id": "chatcmpl-1234567890",
  "object": "chat.completion",
  "created": 1709856000,
  "model": "gemini-1.5-flash",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "這是一張..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 120,
    "completion_tokens": 150,
    "total_tokens": 270
  }
}
```

## 使用範例

### 命令行 (curl)

```bash
# 基本使用
curl -X POST http://localhost:8000/v1/file/generate_content \
  -F "file=@your_image.jpg" \
  -F "prompt=請描述這張圖片內容"

# 帶溫度參數
curl -X POST http://localhost:8000/v1/file/generate_content \
  -F "file=@your_image.jpg" \
  -F "prompt=請把圖片中的文字完整擷取出來" \
  -F "temperature=0.5"

# OCR 文字識別
curl -X POST http://localhost:8000/v1/file/generate_content \
  -F "file=@document.png" \
  -F "prompt=請完整擷取這張圖片中的所有文字內容"
```

### Python 測試腳本

使用提供的測試腳本：

```bash
# 基本使用
python test_file_upload.py your_image.jpg

# 自訂提示詞
python test_file_upload.py your_image.jpg "請分析這張圖片的內容並詳細描述"
```

### Python 代碼範例

```python
import requests

def upload_and_analyze_image(image_path: str, prompt: str):
    """上傳圖片並生成內容"""
    url = "http://localhost:8000/v1/file/generate_content"
    
    with open(image_path, 'rb') as f:
        files = {'file': f}
        data = {
            'prompt': prompt,
            'temperature': 0.7
        }
        response = requests.post(url, files=files, data=data)
    
    if response.status_code == 200:
        result = response.json()
        content = result['choices'][0]['message']['content']
        return content
    else:
        raise Exception(f"API 錯誤: {response.text}")

# 使用範例
content = upload_and_analyze_image(
    "screenshot.png",
    "請描述這張圖片內容，並把圖片中的文字完整擷取出來。"
)
print(content)
```

### JavaScript / Node.js

```javascript
const FormData = require('form-data');
const fs = require('fs');
const axios = require('axios');

async function uploadAndAnalyzeImage(imagePath, prompt) {
  const form = new FormData();
  form.append('file', fs.createReadStream(imagePath));
  form.append('prompt', prompt);
  form.append('temperature', '0.7');

  const response = await axios.post(
    'http://localhost:8000/v1/file/generate_content',
    form,
    { headers: form.getHeaders() }
  );

  return response.data.choices[0].message.content;
}

// 使用範例
uploadAndAnalyzeImage('image.jpg', '請描述這張圖片')
  .then(content => console.log(content))
  .catch(err => console.error(err));
```

## 常見使用場景

### 1. 圖片內容描述

```bash
curl -X POST http://localhost:8000/v1/file/generate_content \
  -F "file=@photo.jpg" \
  -F "prompt=請詳細描述這張圖片的內容、場景、物體和氛圍"
```

### 2. OCR 文字識別

```bash
curl -X POST http://localhost:8000/v1/file/generate_content \
  -F "file=@document.png" \
  -F "prompt=請把圖片中的所有文字完整擷取出來，保持原有格式"
```

### 3. 圖片分析

```bash
curl -X POST http://localhost:8000/v1/file/generate_content \
  -F "file=@chart.png" \
  -F "prompt=請分析這張圖表的數據趨勢和重點信息"
```

### 4. 多語言翻譯

```bash
curl -X POST http://localhost:8000/v1/file/generate_content \
  -F "file=@text_image.jpg" \
  -F "prompt=請識別圖片中的文字，並翻譯成英文"
```

## 環境配置

確保在 `.env` 文件中設置了 Google API Key：

```bash
GOOGLE_API_KEY=your_google_api_key_here
```

前往 https://aistudio.google.com/app/apikey 獲取 API Key。

## 錯誤處理

### 常見錯誤

- **503 Service Unavailable**: GOOGLE_API_KEY 未設定
- **500 Internal Server Error**: API 配額不足或文件格式不支持
- **400 Bad Request**: 缺少必需參數

### 錯誤響應範例

```json
{
  "detail": "GOOGLE_API_KEY 未設定，無法使用文件上傳功能"
}
```

## 支持的文件格式

- JPEG (.jpg, .jpeg)
- PNG (.png)
- GIF (.gif)
- WebP (.webp)
- BMP (.bmp)

## 性能考慮

- 文件大小建議不超過 10MB
- 處理時間取決於圖片大小和複雜度（通常 5-20 秒）
- 每個請求會自動清理臨時文件
- Google API 有配額限制，請注意使用頻率

## 安全注意事項

- 上傳的文件會臨時保存在本地，處理完成後自動刪除
- 文件也會上傳到 Google 服務器，處理完成後會嘗試刪除
- 不建議上傳包含敏感信息的圖片
- 建議在生產環境中添加文件大小和格式驗證

## 進階使用

### 批量處理

```python
import os
import requests

def batch_process_images(image_dir: str, prompt: str):
    """批量處理文件夾中的所有圖片"""
    results = []
    
    for filename in os.listdir(image_dir):
        if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
            image_path = os.path.join(image_dir, filename)
            
            with open(image_path, 'rb') as f:
                files = {'file': f}
                data = {'prompt': prompt}
                response = requests.post(
                    'http://localhost:8000/v1/file/generate_content',
                    files=files,
                    data=data
                )
            
            if response.status_code == 200:
                result = response.json()
                results.append({
                    'filename': filename,
                    'content': result['choices'][0]['message']['content']
                })
    
    return results

# 使用範例
results = batch_process_images('/path/to/images', '請描述這張圖片')
for r in results:
    print(f"{r['filename']}: {r['content'][:100]}...")
```

## 故障排除

### 1. API 端點無法訪問

確保 API 服務正在運行：

```bash
python api.py
```

### 2. Google API Key 無效

檢查 .env 文件中的 GOOGLE_API_KEY 是否正確設置。

### 3. 文件上傳失敗

- 檢查文件格式是否支持
- 檢查文件大小是否過大
- 檢查文件是否損壞

### 4. 返回空答案

- 檢查提示詞是否清晰明確
- 檢查圖片內容是否可識別
- 嘗試調整 temperature 參數

## 更新日誌

- **v1.0** - 初始版本，支持基本的圖片上傳和內容生成功能
