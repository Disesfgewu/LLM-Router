import { useState, useRef, useEffect } from 'react'
import axios from 'axios'

function ChatInterface({ models }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [settings, setSettings] = useState({
    model: 'auto',
    temperature: 0.7,
    max_tokens: 1024,
  })
  const [showSettings, setShowSettings] = useState(false)
  const messagesEndRef = useRef(null)

  // 自動滾動到底部
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  // 發送訊息
  const handleSend = async () => {
    if (!input.trim() || loading) return

    const userMessage = { role: 'user', content: input }
    setMessages((prev) => [...prev, userMessage])
    setInput('')
    setLoading(true)

    try {
      const response = await axios.post('/v1/chat/completions', {
        model: settings.model,
        messages: [...messages, userMessage].map((m) => ({
          role: m.role,
          content: m.content,
        })),
        temperature: settings.temperature,
        max_tokens: settings.max_tokens,
      })

      const assistantMessage = {
        role: 'assistant',
        content: response.data.choices[0].message.content,
        model: response.data.model,
      }
      setMessages((prev) => [...prev, assistantMessage])
    } catch (error) {
      const errorMessage = {
        role: 'error',
        content: `錯誤: ${error.response?.data?.detail || error.message}`,
      }
      setMessages((prev) => [...prev, errorMessage])
    }

    setLoading(false)
  }

  // 清空對話
  const handleClear = () => {
    if (confirm('確定要清空所有對話記錄嗎？')) {
      setMessages([])
    }
  }

  // Enter 鍵發送
  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="bg-white rounded-lg shadow">
      {/* Settings Panel */}
      <div className="border-b p-4">
        <div className="flex justify-between items-center mb-2">
          <h2 className="text-xl font-semibold">💬 對話介面</h2>
          <div className="flex gap-2">
            <button
              onClick={() => setShowSettings(!showSettings)}
              className="px-3 py-1 text-sm bg-gray-200 text-gray-700 rounded hover:bg-gray-300"
            >
              ⚙️ {showSettings ? '隱藏' : '顯示'}設定
            </button>
            <button
              onClick={handleClear}
              className="px-3 py-1 text-sm bg-red-500 text-white rounded hover:bg-red-600"
            >
              🗑️ 清空
            </button>
          </div>
        </div>

        {showSettings && (
          <div className="mt-4 p-4 bg-gray-50 rounded space-y-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                模型策略
              </label>
              
              {/* Quick Mode Selection Buttons */}
              <div className="grid grid-cols-3 gap-2 mb-3">
                <button
                  type="button"
                  onClick={() => setSettings({ ...settings, model: 'auto' })}
                  className={`px-4 py-3 rounded-lg font-medium transition-all ${
                    settings.model === 'auto'
                      ? 'bg-blue-500 text-white shadow-md'
                      : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  <div className="text-lg mb-1">🤖</div>
                  <div className="text-sm">Auto</div>
                  <div className="text-xs opacity-75">自動選擇</div>
                </button>
                
                <button
                  type="button"
                  onClick={() => setSettings({ ...settings, model: 'TextOnlyHigh' })}
                  className={`px-4 py-3 rounded-lg font-medium transition-all ${
                    settings.model === 'TextOnlyHigh'
                      ? 'bg-purple-500 text-white shadow-md'
                      : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  <div className="text-lg mb-1">⭐</div>
                  <div className="text-sm">High</div>
                  <div className="text-xs opacity-75">高品質</div>
                </button>
                
                <button
                  type="button"
                  onClick={() => setSettings({ ...settings, model: 'TextOnlyLow' })}
                  className={`px-4 py-3 rounded-lg font-medium transition-all ${
                    settings.model === 'TextOnlyLow'
                      ? 'bg-green-500 text-white shadow-md'
                      : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  <div className="text-lg mb-1">💰</div>
                  <div className="text-sm">Low</div>
                  <div className="text-xs opacity-75">經濟型</div>
                </button>
              </div>

              {/* Advanced: Specific Model Selection */}
              <details className="mt-2">
                <summary className="text-sm text-gray-600 cursor-pointer hover:text-gray-800">
                  進階：選擇具體模型
                </summary>
                <select
                  value={settings.model}
                  onChange={(e) => setSettings({ ...settings, model: e.target.value })}
                  className="mt-2 w-full px-3 py-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                >
                  <optgroup label="模型策略">
                    <option value="auto">🤖 Auto - 自動選擇</option>
                    <option value="TextOnlyHigh">⭐ High - 高品質模型</option>
                    <option value="TextOnlyLow">💰 Low - 經濟型模型</option>
                  </optgroup>
                  <optgroup label="具體模型">
                    {models
                      .filter((m) => m.id !== 'auto')
                      .map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.id} ({model.owned_by})
                        </option>
                      ))}
                  </optgroup>
                </select>
              </details>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Temperature: {settings.temperature}
              </label>
              <input
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={settings.temperature}
                onChange={(e) =>
                  setSettings({ ...settings, temperature: parseFloat(e.target.value) })
                }
                className="w-full"
              />
              <div className="flex justify-between text-xs text-gray-500">
                <span>精確 (0.0)</span>
                <span>平衡 (1.0)</span>
                <span>創意 (2.0)</span>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Tokens
              </label>
              <input
                type="number"
                min="1"
                max="4096"
                value={settings.max_tokens}
                onChange={(e) =>
                  setSettings({ ...settings, max_tokens: parseInt(e.target.value) })
                }
                className="w-full px-3 py-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
        )}
      </div>

      {/* Messages Area */}
      <div className="h-[500px] overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-gray-500 mt-20">
            <div className="text-6xl mb-4">💬</div>
            <p className="text-lg">開始對話吧！</p>
            <p className="text-sm mt-2">輸入訊息並按 Enter 發送</p>
          </div>
        )}

        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[70%] rounded-lg px-4 py-2 ${
                msg.role === 'user'
                  ? 'bg-blue-500 text-white'
                  : msg.role === 'error'
                  ? 'bg-red-100 text-red-700 border border-red-300'
                  : 'bg-gray-200 text-gray-800'
              }`}
            >
              {msg.role === 'assistant' && msg.model && (
                <div className="text-xs opacity-70 mb-1">🤖 {msg.model}</div>
              )}
              <div className="whitespace-pre-wrap break-words">{msg.content}</div>
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-200 text-gray-800 rounded-lg px-4 py-2">
              <div className="flex space-x-2">
                <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce"></div>
                <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce delay-100"></div>
                <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce delay-200"></div>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="border-t p-4">
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="輸入訊息... (Shift+Enter 換行)"
            rows="3"
            disabled={loading}
            className="flex-1 px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="px-6 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? '⏳' : '發送'}
          </button>
        </div>
        <div className="mt-2 flex items-center gap-4 text-xs text-gray-600">
          <span className="font-medium">
            {settings.model === 'auto' && '🤖 Auto (自動選擇)'}
            {settings.model === 'TextOnlyHigh' && '⭐ High (高品質)'}
            {settings.model === 'TextOnlyLow' && '💰 Low (經濟型)'}
            {!['auto', 'TextOnlyHigh', 'TextOnlyLow'].includes(settings.model) && 
              `🎯 ${settings.model}`}
          </span>
          <span>🌡️ Temp: {settings.temperature}</span>
          <span>📏 Tokens: {settings.max_tokens}</span>
        </div>
      </div>
    </div>
  )
}

export default ChatInterface
