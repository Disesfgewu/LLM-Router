# ModelRouter Dashboard Frontend

React + Vite 前端介面，用於管理和監控 ModelRouter API。

## 功能特色

- 💬 **對話介面**：即時與 AI 模型對話
- 📊 **儀錶板**：查看配額使用情況和模型狀態
- ⚙️ **設定調整**：溫度、max_tokens、模型選擇
- 🔄 **自動刷新**：每 5 分鐘自動更新配額資訊
- 🎨 **現代化 UI**：使用 Tailwind CSS

## 快速開始

### 1. 安裝依賴

```bash
cd frontend
npm install
```

### 2. 啟動開發伺服器

```bash
npm run dev
```

前端會在 http://localhost:3000 啟動

### 3. 確保後端運行

確保 ModelRouter API 在 http://localhost:8000 運行：

```bash
cd ..
python api.py
```

## 可用指令

- `npm run dev` - 啟動開發伺服器
- `npm run build` - 建置生產版本
- `npm run preview` - 預覽生產建置

## 架構說明

```
frontend/
├── src/
│   ├── components/
│   │   ├── Dashboard.jsx      # 儀錶板組件
│   │   └── ChatInterface.jsx  # 聊天介面組件
│   ├── App.jsx                # 主應用組件
│   ├── main.jsx               # 應用入口
│   └── index.css              # 全域樣式
├── index.html                 # HTML 模板
├── vite.config.js             # Vite 配置
├── tailwind.config.js         # Tailwind CSS 配置
└── package.json               # 專案配置
```

## API 代理

Vite 開發伺服器已配置代理，自動將 `/v1` 和 `/admin` 請求轉發到後端 API。

## 瀏覽器支援

支援所有現代瀏覽器（Chrome、Firefox、Safari、Edge 最新版本）。
