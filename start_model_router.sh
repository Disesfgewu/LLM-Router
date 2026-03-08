#!/bin/bash
# ModelRouter API Gateway 啟動腳本
# 多模型智能路由，自動在 GitHub/Google/Ollama 之間 failover

echo "╔════════════════════════════════════════════════════════════╗"
echo "║       ModelRouter API Gateway - 多模型智能路由             ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# ── 載入 .env 檔案 ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "📄 載入 .env 檔案..."
    set -a  # 自動 export 所有變數
    source "$SCRIPT_DIR/.env"
    set +a
    echo "✅ .env 已載入"
fi

# ── 檢查環境變數 ──
check_env() {
    local missing=0
    
    if [ -z "$GOOGLE_API_KEY" ]; then
        echo "⚠️  GOOGLE_API_KEY 未設定 (Google 後端將無法使用)"
        missing=1
    else
        echo "✅ GOOGLE_API_KEY: 已設定"
    fi
    
    if [ -z "$GITHUB_MODELS_API_KEY" ]; then
        echo "⚠️  GITHUB_MODELS_API_KEY 未設定 (GitHub 後端將無法使用)"
        missing=1
    else
        echo "✅ GITHUB_MODELS_API_KEY: 已設定"
    fi
    
    if [ $missing -eq 1 ]; then
        echo ""
        echo "💡 設定方式："
        echo "   export GOOGLE_API_KEY='your-key'"
        echo "   export GITHUB_MODELS_API_KEY='your-key'"
        echo ""
        read -p "是否繼續啟動? (y/n): " -n 1 -r
        echo ""
        [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
    fi
}

# ── 檢查記憶體 ──
check_memory() {
    AVAILABLE_MEM=$(free -m | grep Mem | awk '{print $7}')
    echo ""
    echo "💾 可用記憶體: ${AVAILABLE_MEM}MB"
    
    if [ "$AVAILABLE_MEM" -lt 500 ]; then
        echo "⚠️  記憶體不足 500MB，建議清理後再啟動"
        read -p "是否繼續? (y/n): " -n 1 -r
        echo ""
        [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
    fi
}

# ── 檢查端口 ──
check_port() {
    PORT=${API_PORT:-8000}
    if lsof -i:$PORT > /dev/null 2>&1; then
        echo ""
        echo "⚠️  端口 $PORT 已被佔用"
        PID=$(lsof -t -i:$PORT)
        echo "   PID: $PID"
        read -p "是否終止該進程並繼續? (y/n): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            kill $PID 2>/dev/null
            sleep 1
            echo "✅ 已終止進程 $PID"
        else
            exit 1
        fi
    fi
}

# ── 主流程 ──
main() {
    check_env
    check_memory
    check_port
    
    echo ""
    echo "📍 API 端點:"
    echo "   http://0.0.0.0:${API_PORT:-8000}/v1/chat/completions"
    echo "   http://0.0.0.0:${API_PORT:-8000}/v1/models"
    echo "   http://0.0.0.0:${API_PORT:-8000}/admin/status"
    echo ""
    echo "📚 API 文檔: http://localhost:${API_PORT:-8000}/docs"
    echo ""
    echo "💡 測試命令:"
    echo "   python test_api.py --quick    # 快速測試"
    echo "   python test_api.py --auto     # 測試自動路由"
    echo ""
    echo "按 Ctrl+C 停止服務"
    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo ""
    
    # 啟動 API
    cd /home/martin/Desktop/llm-api
    /home/martin/.venv/bin/python api.py
}

main
