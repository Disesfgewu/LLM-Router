import { useState, useEffect } from 'react'
import axios from 'axios'

function LogViewer() {
  const [logs, setLogs] = useState('')
  const [source, setSource] = useState('')
  const [loading, setLoading] = useState(false)
  const [lastUpdate, setLastUpdate] = useState(null)
  const [autoRefresh, setAutoRefresh] = useState(true)

  // 獲取日誌
  const fetchLogs = async () => {
    setLoading(true)
    try {
      const response = await axios.get('/admin/logs')
      setLogs(response.data.logs)
      setSource(response.data.source)
      setLastUpdate(new Date(response.data.timestamp * 1000))
    } catch (error) {
      console.error('Failed to fetch logs:', error)
      setLogs(`錯誤：無法獲取日誌\n${error.message}`)
      setSource('error')
    }
    setLoading(false)
  }

  // 初始載入
  useEffect(() => {
    fetchLogs()
  }, [])

  // 自動刷新 - 每 3 分鐘
  useEffect(() => {
    if (!autoRefresh) return

    const interval = setInterval(() => {
      fetchLogs()
    }, 3 * 60 * 1000) // 3 分鐘

    return () => clearInterval(interval)
  }, [autoRefresh])

  // 手動刷新
  const handleRefresh = () => {
    fetchLogs()
  }

  // 格式化時間
  const formatTime = (date) => {
    if (!date) return '尚未更新'
    return date.toLocaleTimeString('zh-TW', { 
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    })
  }

  return (
    <div className="space-y-4">
      {/* 控制面板 */}
      <div className="bg-white rounded-lg shadow p-4">
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-4">
            <h2 className="text-xl font-semibold">📋 系統日誌</h2>
            <div className="text-sm text-gray-600">
              來源: <span className="font-mono bg-gray-100 px-2 py-1 rounded">{source || '載入中...'}</span>
            </div>
            <div className="text-sm text-gray-600">
              最後更新: <span className="font-mono">{formatTime(lastUpdate)}</span>
            </div>
          </div>
          
          <div className="flex items-center gap-3">
            {/* 自動刷新開關 */}
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="rounded"
              />
              <span>每 3 分鐘自動刷新</span>
            </label>
            
            {/* 手動刷新按鈕 */}
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50 flex items-center gap-2"
            >
              {loading ? (
                <>
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  <span>載入中...</span>
                </>
              ) : (
                <>
                  <span>🔄</span>
                  <span>刷新</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* 日誌顯示區域 */}
      <div className="bg-white rounded-lg shadow p-4">
        <div className="bg-gray-900 rounded p-4 overflow-x-auto">
          <pre className="text-gray-100 text-sm font-mono whitespace-pre-wrap break-words">
            {logs || '載入中...'}
          </pre>
        </div>
        
        {/* 提示訊息 */}
        <div className="mt-4 text-sm text-gray-600 border-t pt-4">
          <p>💡 提示：</p>
          <ul className="list-disc list-inside mt-2 space-y-1">
            <li>顯示最新 100 行日誌</li>
            <li>自動刷新間隔：3 分鐘（可關閉）</li>
            <li>若無日誌顯示，請檢查日誌配置或將輸出重定向到文件</li>
          </ul>
        </div>
      </div>
    </div>
  )
}

export default LogViewer
