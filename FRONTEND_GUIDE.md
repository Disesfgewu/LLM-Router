# ModelRouter Frontend 快速啟動指南

## 前端功能

✅ **對話介面** - 與 AI 模型即時對話
✅ **配額儀錶板** - 實時查看配額使用情況
✅ **設定調整** - Temperature、Max Tokens、模型選擇
✅ **自動刷新** - 每 5 分鐘自動更新配額資訊
✅ **現代化 UI** - 使用 React + Vite + Tailwind CSS

## 快速啟動

### 方法一：使用啟動腳本（推薦）

```bash
# 1. 啟動後端 API（在一個終端）
python api.py

# 2. 啟動前端（在另一個終端）
./start_frontend.sh
```

前端會自動：
- 安裝依賴（首次）
- 啟動在 http://localhost:3000

### 方法二：手動啟動

```bash
# 1. 進入前端目錄
cd frontend

# 2. 安裝依賴（首次啟動）
npm install

# 3. 啟動開發伺服器
npm run dev
```

## 使用說明

### 對話功能

1. 點擊 **「💬 對話」** 標籤
2. 在設定中選擇模型（auto / TextOnlyHigh / TextOnlyLow / 具體模型）
3. 調整 Temperature 和 Max Tokens
4. 輸入訊息並按 Enter 發送
5. Shift+Enter 可以換行

### 儀錶板功能

1. 點擊 **「📊 儀錶板」** 標籤
2. 查看配額使用情況
3. 查看可用模型列表
4. 使用管理功能：
   - **重置所有配額** - 重置每日配額
   - **刷新 RPM 指標** - 重置優先順序指標

### 自動刷新

- 儀錶板每 5 分鐘自動更新配額資訊
- 也可以點擊右上角 **「🔄 刷新」** 手動更新

## 生產部署

### 建置前端

```bash
cd frontend
npm run build
```

建置完成後，`dist` 目錄包含所有靜態檔案。

### 使用 FastAPI 提供前端

修改 `api.py`，添加靜態檔案服務：

```python
from fastapi.staticfiles import StaticFiles

# 在 app 定義後添加
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="static")
```

然後只需啟動 `python api.py`，前端和後端都在 8000 端口。

## 故障排除

### 前端無法連接到後端

確認：
1. 後端 API 在 http://localhost:8000 運行
2. CORS 已啟用（已在 `api.py` 配置）
3. 檢查瀏覽器控制台是否有錯誤

### 配額顯示不正確

1. 點擊 **「🔄 刷新」** 手動更新
2. 檢查後端 `/admin/status` 端點是否正常
3. 查看瀏覽器控制台網路請求

### npm install 失敗

```bash
# 清除 npm 快取
npm cache clean --force

# 刪除 node_modules 重新安裝
rm -rf node_modules package-lock.json
npm install
```

## 技術棧

- **React 18** - UI 框架
- **Vite** - 建置工具
- **Tailwind CSS** - 樣式框架
- **Axios** - HTTP 客戶端

## 開發版本要求

- Node.js >= 16
- npm >= 8
