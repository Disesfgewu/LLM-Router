import { useState, useEffect } from 'react'
import axios from 'axios'
import Dashboard from './components/Dashboard'
import ChatInterface from './components/ChatInterface'
import LogViewer from './components/LogViewer'
import AccountManager from './components/AccountManager'

// ── Login / Register screen ────────────────────────────────

function AuthScreen({ onLogin }) {
  const [mode, setMode] = useState('login')  // 'login' | 'register'
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const handleLogin = async (e) => {
    e.preventDefault()
    setLoading(true)
    setErr('')
    try {
      const res = await axios.post('/auth/login', { username, password })
      // Store session token in axios default headers (memory only — not localStorage)
      axios.defaults.headers.common['X-Session-Token'] = res.data.token
      onLogin({ token: res.data.token, username: res.data.username, is_admin: res.data.is_admin })
    } catch (e) {
      setErr(e.response?.data?.detail || '登入失敗，請確認帳號密碼')
    }
    setLoading(false)
  }

  const handleRegister = async (e) => {
    e.preventDefault()
    setLoading(true)
    setErr('')
    try {
      await axios.post('/auth/register', { username, email, password })
      setMode('login')
      setErr('')
      setEmail('')
      setPassword('')
      setUsername('')
      alert('註冊成功，請登入')
    } catch (e) {
      setErr(e.response?.data?.detail || '註冊失敗')
    }
    setLoading(false)
  }

  return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center px-4">
      <div className="w-full max-w-sm bg-white rounded-xl shadow-lg p-8 space-y-6">
        <div className="text-center">
          <div className="text-3xl mb-2">🔐</div>
          <h1 className="text-xl font-bold text-gray-900">ModelRouter Dashboard</h1>
          <p className="text-sm text-gray-500 mt-1">
            {mode === 'login' ? '請登入以繼續' : '建立新帳號'}
          </p>
        </div>

        {err && (
          <div className="bg-red-50 border border-red-300 rounded px-4 py-3 text-sm text-red-800">
            {err}
          </div>
        )}

        <form onSubmit={mode === 'login' ? handleLogin : handleRegister} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">帳號名稱</label>
            <input
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </div>
          {mode === 'register' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">電子郵件</label>
              <input
                type="email"
                className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">密碼</label>
            <input
              type="password"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? '處理中...' : mode === 'login' ? '登入' : '註冊'}
          </button>
        </form>

        <div className="text-center text-sm text-gray-500">
          {mode === 'login' ? (
            <>
              還沒有帳號？{' '}
              <button onClick={() => { setMode('register'); setErr('') }} className="text-blue-600 hover:underline">
                立即註冊
              </button>
            </>
          ) : (
            <>
              已有帳號？{' '}
              <button onClick={() => { setMode('login'); setErr('') }} className="text-blue-600 hover:underline">
                返回登入
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Main App ───────────────────────────────────────────────

function App() {
  const [session, setSession] = useState(null)   // { token, username, is_admin }
  const [me, setMe] = useState(null)             // full account info from /auth/me
  const [activeTab, setActiveTab] = useState('chat')
  const [status, setStatus] = useState(null)
  const [models, setModels] = useState([])
  const [loading, setLoading] = useState(false)
  const [backendOnline, setBackendOnline] = useState(true)
  const [backendMessage, setBackendMessage] = useState('')

  // After login: set session and load full account info
  const handleLogin = async (sessionData) => {
    setSession(sessionData)
    try {
      const res = await axios.get('/auth/me')
      setMe(res.data)
    } catch {
      setMe(null)
    }
    fetchBackendData()
  }

  const handleLogout = async () => {
    try { await axios.post('/auth/logout') } catch { /* ignore */ }
    delete axios.defaults.headers.common['X-Session-Token']
    setSession(null)
    setMe(null)
    setStatus(null)
    setModels([])
  }

  const fetchStatus = async () => {
    try {
      const response = await axios.get('/admin/status')
      setStatus(response.data)
    } catch (error) {
      console.error('Failed to fetch status:', error)
    }
  }

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

  // Auto-refresh every 20 s only when logged in
  useEffect(() => {
    if (!session) return
    fetchBackendData()
    const interval = setInterval(fetchBackendData, 20 * 1000)
    return () => clearInterval(interval)
  }, [session])

  const handleRefresh = async () => {
    setLoading(true)
    await fetchBackendData()
    setLoading(false)
  }

  // Show login screen until authenticated
  if (!session) {
    return <AuthScreen onLogin={handleLogin} />
  }

  const tabs = [
    { key: 'chat', icon: '💬', label: '對話' },
    { key: 'dashboard', icon: '📊', label: '儀錶板' },
    { key: 'logs', icon: '📋', label: '日誌' },
    { key: 'account', icon: '🔐', label: '帳號與金鑰' },
  ]

  return (
    <div className="min-h-screen bg-gray-100">
      {/* Header */}
      <header className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center">
            <h1 className="text-2xl font-bold text-gray-900">
              🚀 ModelRouter Dashboard
            </h1>
            <div className="flex items-center gap-3">
              <span className="text-sm text-gray-500">
                {session.is_admin ? '👑' : '👤'} {session.username}
              </span>
              <button
                onClick={handleRefresh}
                disabled={loading}
                className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50 text-sm"
              >
                {loading ? '刷新中...' : '🔄 刷新'}
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Navigation Tabs */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 mt-4">
        <div className="border-b border-gray-200">
          <nav className="-mb-px flex space-x-8">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => setActiveTab(t.key)}
                className={`${
                  activeTab === t.key
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                } whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm`}
              >
                {t.icon} {t.label}
              </button>
            ))}
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
        {activeTab === 'account' && <AccountManager me={me} onLogout={handleLogout} />}
      </main>

      {/* Footer */}
      <footer className="mt-8 py-4 text-center text-gray-500 text-sm">
        <p>自動刷新：每 20 秒 | ModelRouter API Gateway v1.0.0</p>
      </footer>
    </div>
  )
}

export default App
