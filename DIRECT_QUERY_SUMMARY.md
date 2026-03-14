# Direct Query API - 功能摘要

## 新增功能

已在 `api.py` 中新增 `/v1/direct_query` 端點，可以直接查詢指定的 model 和 provider。

## 主要特點

✅ **直接訪問**: 直接調用指定的 model，不經過自動路由邏輯  
✅ **靈活性**: 即使 model 不在配置列表中，也會嘗試訪問  
✅ **錯誤處理**: 如果沒有額度或模型不存在，返回 HTTP 500  
✅ **支持所有提供商**: GitHub, Google, Ollama  
✅ **參數自動調整**: 自動根據模型類型調整參數（例如推理模型）  
✅ **重試機制**: 內建網路錯誤重試機制  

## 快速使用

### 基本請求格式

```bash
curl -X POST http://localhost:8000/v1/direct_query \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "gemma-3-7b-it",
    "provider": "Google",
    "prompt": "你的問題"
  }'
```

### 參數說明

| 參數 | 必需 | 說明 | 範例 |
|------|------|------|------|
| `model_name` | ✅ | 模型名稱 | `"gemma-3-7b-it"`, `"openai/gpt-4o"` |
| `provider` | ✅ | 提供商 | `"GitHub"`, `"Google"`, `"Ollama"` |
| `prompt` | ✅ | 提示詞 | `"什麼是AI?"` |
| `temperature` | ❌ | 溫度 (默認 0.7) | `0.5` |
| `max_tokens` | ❌ | 最大 tokens | `1000` |

## 測試方法

### 方法 1: 使用測試腳本

```bash
python test_direct_query.py
```

### 方法 2: 手動測試

```bash
# 測試 Google 模型
curl -X POST http://localhost:8000/v1/direct_query \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "gemma-3-7b-it",
    "provider": "Google",
    "prompt": "解釋什麼是機器學習"
  }'
```

## 錯誤響應

| 錯誤情況 | HTTP 狀態碼 | 響應範例 |
|----------|------------|----------|
| 配額不足 | 500 | `{"detail": "模型 XXX 配額不足或達到速率限制"}` |
| 模型不存在 | 500 | `{"detail": "模型 XXX 不存在或在 YYY 上不可用"}` |
| API 密鑰問題 | 500 | `{"detail": "YYY API 密鑰無效或未設置"}` |
| 無效 provider | 400 | `{"detail": "不支持的 provider: ..."}` |

## 文件清單

### 新增檔案

1. **`DIRECT_QUERY_EXAMPLES.md`** - 詳細的使用示例和文檔
2. **`test_direct_query.py`** - 自動化測試腳本  
3. **`DIRECT_QUERY_SUMMARY.md`** - 本文件（功能摘要）

### 修改檔案

1. **`api.py`** - 新增以下內容：
   - `DirectQueryRequest` 類 (第 169 行)
   - `/v1/direct_query` 端點 (第 567 行)
   - 更新文檔字符串和根端點

## 與現有 API 的區別

| 特性 | `/v1/chat/completions` | `/v1/direct_query` |
|------|------------------------|---------------------|
| 路由方式 | 自動路由，多個模型 failover | 直接指定單個模型 |
| 配額檢查 | ✅ 檢查並扣減本地配額 | ❌ 不檢查本地配額 |
| 模型限制 | 僅配置列表中的模型 | 任何模型都可嘗試 |
| 錯誤處理 | 自動切換下一個模型 | 失敗返回 500 錯誤 |
| 適用場景 | 需要高可用性的自動化 | 測試特定模型 |

## 使用建議

### 適合使用 `/v1/direct_query` 的場景：

- 🧪 測試新模型是否可用
- 🎯 需要使用特定模型（即使不在列表中）
- 🔍 調試特定模型的行為
- ⚡ 繞過配額限制直接訪問 API

### 適合使用 `/v1/chat/completions` 的場景：

- 🚀 生產環境需要高可用性
- 📊 需要配額管理和追蹤
- 🔄 希望自動 failover 到其他模型
- 💰 需要控制 API 使用成本

## 下一步

1. 啟動 API 服務器（如果還沒運行）：
   ```bash
   python api.py
   ```

2. 運行測試腳本：
   ```bash
   python test_direct_query.py
   ```

3. 查看詳細文檔：
   ```bash
   cat DIRECT_QUERY_EXAMPLES.md
   ```

4. 開始使用 Direct Query API！

---

**注意事項**：
- 此 API 不會扣減本地配額，每次都會直接訪問上游 API
- 如果 API 密鑰未設置或無效，將返回錯誤
- Ollama 需要在本地運行才能訪問本地模型
