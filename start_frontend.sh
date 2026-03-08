#!/bin/bash
# 啟動前端開發伺服器

cd "$(dirname "$0")/frontend"

# 檢查 node_modules 是否存在
if [ ! -d "node_modules" ]; then
    echo "📦 首次啟動，正在安裝依賴..."
    npm install
fi

echo "🚀 啟動前端開發伺服器..."
echo "📍 前端: http://localhost:3000"
echo "📍 後端: http://localhost:8000"
echo ""
echo "請確保後端 API 已啟動！"
echo ""

npm run dev
