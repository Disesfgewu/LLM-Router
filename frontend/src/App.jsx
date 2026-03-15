import { useState, useEffect } from 'react'
import axios from 'axios'
import Dashboard from './components/Dashboard'
import ChatInterface from './components/ChatInterface'
import LogViewer from './components/LogViewer'

function App() {
  const [activeTab, setActiveTab] = useState('chat')
  const [status, setStatus] = useState(null)
  const [models, setModels] = useState([])
  const [loading, setLoading] = useState(false)
  const [backendOnline, setBackendOnline] = useState(true)
  const [backendMessage, setBackendMessage] = useState('')

  // 獲取配額狀態
  const fetchStatus = async () => {
    try {
      const response = await axios.get('/admin/status')
      setStatus(response.data)
    } catch (error) {
      console.error('Failed to fetch status:', error)
    }
  }

  // 獲取模型列表
  const fetchModels = async () => {
    try {
      const response = await axios.get('/v1/models')
      setModels(response.data.data || [])
    } catch (error) {
      console.error('Failed to fetch models:', error)
    }
  }

  const checkBackendHealth = async () => {
    try {
      await axios.get('/health')
      setBackendOnline(true)
      setBackendMessage('')
      return true
    } catch (error) {
      setBackendOnline(false)
      setBackendMessage('後端 API 目前無法連線（127.0.0.1:8000）。請先啟動 python api.py。')
      return false
    }
  }

  const fetchBackendData = async () => {
    const healthy = await checkBackendHealth()
    if (!healthy) return
    await Promise.all([fetchStatus(), fetchModels()])
  }

  // 初始載入
  useEffect(() => {
    fetchBackendData()
  }, [])

  // 自動刷新 - 每 20 秒（讓內部分類器配額變化可即時可見）
  useEffect(() => {
    const interval = setInterval(() => {
      fetchBackendData()
    }, 20 * 1000) // 20 秒

    return () => clearInterval(interval)
  }, [])

  // 手動刷新
  const handleRefresh = async () => {
    setLoading(true)
    await fetchBackendData()
    setLoading(false)
  }

  return (
    <div className="min-h-screen bg-gray-100">
      {/* Header */}
      <header className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center">
            <h1 className="text-2xl font-bold text-gray-900">
              🚀 ModelRouter Dashboard
            </h1>
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50"
            >
              {loading ? '刷新中...' : '🔄 刷新'}
            </button>
          </div>
        </div>
      </header>

      {/* Navigation Tabs */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 mt-4">
        <div className="border-b border-gray-200">
          <nav className="-mb-px flex space-x-8">
            <button
              onClick={() => setActiveTab('chat')}
              className={`${
                activeTab === 'chat'
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              } whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm`}
            >
              💬 對話
            </button>
            <button
              onClick={() => setActiveTab('dashboard')}
              className={`${
                activeTab === 'dashboard'
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              } whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm`}
            >
              📊 儀錶板
            </button>
            <button
              onClick={() => setActiveTab('logs')}
              className={`${
                activeTab === 'logs'
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              } whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm`}
            >
              📋 日誌
            </button>
          </nav>
        </div>
      </div>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {!backendOnline && (
          <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-amber-800">
            <div className="font-semibold">後端連線異常</div>
            <div className="text-sm mt-1">{backendMessage}</div>
          </div>
        )}
        {activeTab === 'chat' && <ChatInterface models={models} />}
        {activeTab === 'dashboard' && <Dashboard status={status} models={models} onRefresh={handleRefresh} />}
        {activeTab === 'logs' && <LogViewer />}
      </main>

      {/* Footer */}
      <footer className="mt-8 py-4 text-center text-gray-500 text-sm">
        <p>自動刷新：每 20 秒 | ModelRouter API Gateway v1.0.0</p>
      </footer>
    </div>
  )
}

export default App
