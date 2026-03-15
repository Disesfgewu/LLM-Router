import { useState, useRef, useEffect } from 'react'
import axios from 'axios'

function ChatInterface({ models }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [attachments, setAttachments] = useState([])
  const imageModels = models.filter((m) => m.capabilities?.task === 'image_generation')
  const [settings, setSettings] = useState({
    model: 'auto',
    temperature: 0.7,
    max_tokens: 1024,
  })
  const [imageSettings, setImageSettings] = useState({
    model: 'black-forest-labs/FLUX.1-schnell',
    size: '1024x1024',
    n: 1,
  })
  const [imagePrompt, setImagePrompt] = useState('')
  const [imageLoading, setImageLoading] = useState(false)
  const [generatedImages, setGeneratedImages] = useState([])
  const [showSettings, setShowSettings] = useState(false)
  const messagesEndRef = useRef(null)
  const fileInputRef = useRef(null)
  const imagePromptExamples = [
    '幫我生成一張台北夜景海報',
    '生成一張日系咖啡館插畫風封面',
    '畫一張未來感程式設計師工作桌面',
  ]

  const toImageSrc = (item) => {
    if (item?.url) return item.url
    if (item?.b64_json) return `data:image/png;base64,${item.b64_json}`
    return ''
  }

  const summarizeAttachmentNames = (items) => items.map((item) => item.name).join('、')

  const readFileAsDataUrl = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '')
    reader.onerror = () => reject(reader.error || new Error('讀取檔案失敗'))
    reader.readAsDataURL(file)
  })

  const buildAttachmentPayload = async (file) => {
    const dataUrl = await readFileAsDataUrl(file)
    const mimeType = file.type || 'application/octet-stream'
    const isImage = mimeType.startsWith('image/')

    return {
      id: `${file.name}-${file.size}-${file.lastModified}`,
      name: file.name,
      mimeType,
      size: file.size,
      kind: isImage ? 'image' : 'file',
      payload: isImage
        ? { url: dataUrl }
        : {
            file_name: file.name,
            mime_type: mimeType,
            file_data: dataUrl,
          },
      previewUrl: isImage ? dataUrl : '',
    }
  }

  // 自動滾動到底部
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const handlePickFiles = () => {
    fileInputRef.current?.click()
  }

  const handleFileChange = async (event) => {
    const pickedFiles = Array.from(event.target.files || [])
    if (pickedFiles.length === 0) return

    if (attachments.length + pickedFiles.length > 5) {
      setMessages((prev) => [
        ...prev,
        { role: 'error', content: '錯誤: 一次最多只能附加 5 份檔案或圖片' },
      ])
      event.target.value = ''
      return
    }

    try {
      const nextAttachments = await Promise.all(pickedFiles.map(buildAttachmentPayload))
      setAttachments((prev) => [...prev, ...nextAttachments])
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        { role: 'error', content: `附件讀取失敗: ${error.message || error}` },
      ])
    }

    event.target.value = ''
  }

  const handleRemoveAttachment = (attachmentId) => {
    setAttachments((prev) => prev.filter((item) => item.id !== attachmentId))
  }

  // 發送訊息
  const handleSend = async () => {
    if ((!input.trim() && attachments.length === 0) || loading) return

    const trimmedInput = input.trim()
    const userMessage = {
      role: 'user',
      content: trimmedInput || `請分析附件：${summarizeAttachmentNames(attachments)}`,
      attachments: attachments.map((item) => ({
        id: item.id,
        name: item.name,
        kind: item.kind,
        mimeType: item.mimeType,
        previewUrl: item.previewUrl,
      })),
    }
    setMessages((prev) => [...prev, userMessage])
    setInput('')
    setLoading(true)

    try {
      const inputFiles = attachments
        .filter((item) => item.kind === 'file')
        .map((item) => item.payload)
      const inputImages = attachments
        .filter((item) => item.kind === 'image')
        .map((item) => item.payload.url)

      const response = await axios.post('/v1/chat/completions', {
        model: settings.model,
        messages: [...messages, userMessage].map((m) => ({
          role: m.role,
          content: m.content,
        })),
        temperature: settings.temperature,
        max_tokens: settings.max_tokens,
        input_files: inputFiles,
        input_images: inputImages,
      })

      const assistantMessage = {
        role: 'assistant',
        content: response.data.choices[0].message.content,
        model: response.data.model,
        images: Array.isArray(response.data.images) ? response.data.images : [],
      }
      setMessages((prev) => [...prev, assistantMessage])
      setAttachments([])
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
      setAttachments([])
    }
  }

  // Enter 鍵發送
  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleGenerateImage = async () => {
    if (!imagePrompt.trim() || imageLoading) return
    setImageLoading(true)
    try {
      const response = await axios.post('/v1/images/generations', {
        model: imageSettings.model,
        prompt: imagePrompt,
        n: imageSettings.n,
        size: imageSettings.size,
        response_format: 'b64_json',
      })
      setGeneratedImages(response.data.data || [])
    } catch (error) {
      const errorMessage = {
        role: 'error',
        content: `生圖錯誤: ${error.response?.data?.detail || error.message}`,
      }
      setMessages((prev) => [...prev, errorMessage])
    }
    setImageLoading(false)
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
                      .filter((m) => m.id !== 'auto' && (m.capabilities?.chat_capable ?? true))
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

            <div className="border-t pt-3">
              <div className="text-sm font-semibold text-gray-700 mb-2">🖼️ Image Playground</div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-2">
                <select
                  value={imageSettings.model}
                  onChange={(e) => setImageSettings({ ...imageSettings, model: e.target.value })}
                  className="px-3 py-2 border rounded text-sm"
                >
                  {(imageModels.length > 0 ? imageModels : [{ id: 'black-forest-labs/FLUX.1-schnell', owned_by: 'HuggingFace' }]).map((m) => (
                    <option key={m.id} value={m.id}>{m.id} ({m.owned_by})</option>
                  ))}
                </select>
                <select
                  value={imageSettings.size}
                  onChange={(e) => setImageSettings({ ...imageSettings, size: e.target.value })}
                  className="px-3 py-2 border rounded text-sm"
                >
                  {['1024x1024', '1536x1024', '1024x1536', '512x512'].map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
                <input
                  type="number"
                  min="1"
                  max="4"
                  value={imageSettings.n}
                  onChange={(e) => setImageSettings({ ...imageSettings, n: parseInt(e.target.value || '1', 10) })}
                  className="px-3 py-2 border rounded text-sm"
                />
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={imagePrompt}
                  onChange={(e) => setImagePrompt(e.target.value)}
                  placeholder="輸入生圖 prompt..."
                  className="flex-1 px-3 py-2 border rounded text-sm"
                />
                <button
                  type="button"
                  onClick={handleGenerateImage}
                  disabled={imageLoading || !imagePrompt.trim()}
                  className="px-4 py-2 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700 disabled:opacity-50"
                >
                  {imageLoading ? '生成中...' : '生成圖片'}
                </button>
              </div>
              {generatedImages.length > 0 && (
                <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                  {generatedImages.map((item, index) => {
                    const src = toImageSrc(item)
                    if (!src) return null
                    return (
                      <img
                        key={`${index}-${src.slice(0, 24)}`}
                        src={src}
                        alt={`generated-${index + 1}`}
                        className="w-full rounded border"
                      />
                    )
                  })}
                </div>
              )}
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
            <p className="text-sm mt-2 text-indigo-600">直接輸入像「幫我生成一張台北夜景海報」會自動走生圖</p>
            <p className="text-sm mt-2 text-emerald-600">也可上傳圖片、txt、csv、pdf、xlsx 驗證多模態</p>
          </div>
        )}

        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`rounded-lg px-4 py-2 ${Array.isArray(msg.images) && msg.images.length > 0 ? 'max-w-[85%]' : 'max-w-[70%]'} ${
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
              {Array.isArray(msg.attachments) && msg.attachments.length > 0 && (
                <div className="mt-3 space-y-2">
                  <div className="text-xs opacity-80">附件</div>
                  <div className="flex flex-wrap gap-2">
                    {msg.attachments.map((item) => (
                      <div key={item.id} className="px-2 py-1 rounded border bg-white/70 text-xs text-gray-700">
                        {item.kind === 'image' ? '🖼️' : '📄'} {item.name}
                      </div>
                    ))}
                  </div>
                  {msg.attachments.some((item) => item.kind === 'image' && item.previewUrl) && (
                    <div className="grid grid-cols-2 gap-2">
                      {msg.attachments
                        .filter((item) => item.kind === 'image' && item.previewUrl)
                        .map((item) => (
                          <img
                            key={`${item.id}-preview`}
                            src={item.previewUrl}
                            alt={item.name}
                            className="w-full rounded border"
                          />
                        ))}
                    </div>
                  )}
                </div>
              )}
              {Array.isArray(msg.images) && msg.images.length > 0 && (
                <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2">
                  {msg.images.map((item, index) => {
                    const src = toImageSrc(item)
                    if (!src) return null
                    return (
                      <img
                        key={`${idx}-img-${index}`}
                        src={src}
                        alt={`assistant-image-${index + 1}`}
                        className="w-full rounded border"
                      />
                    )
                  })}
                </div>
              )}
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
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*,.txt,.csv,.pdf,.xlsx,.xls"
          onChange={handleFileChange}
          className="hidden"
        />
        {attachments.length > 0 && (
          <div className="mb-3 space-y-2">
            <div className="text-xs font-medium text-gray-600">待送出附件</div>
            <div className="flex flex-wrap gap-2">
              {attachments.map((item) => (
                <div key={item.id} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-100 border text-sm text-gray-700">
                  <span>{item.kind === 'image' ? '🖼️' : '📄'}</span>
                  <span className="max-w-[160px] truncate" title={item.name}>{item.name}</span>
                  <button
                    type="button"
                    onClick={() => handleRemoveAttachment(item.id)}
                    className="text-red-500 hover:text-red-700"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handlePickFiles}
            disabled={loading || attachments.length >= 5}
            className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg border hover:bg-gray-200 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            📎 上傳
          </button>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="輸入訊息... 例如：幫我生成一張台北夜景海報，或上傳附件後請我分析"
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
        <div className="mt-3 flex flex-wrap gap-2">
          {imagePromptExamples.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => setInput(example)}
              className="px-3 py-1.5 text-xs rounded-full bg-indigo-50 text-indigo-700 hover:bg-indigo-100"
            >
              {example}
            </button>
          ))}
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
          <span className="text-indigo-600">🖼️ Auto 生圖已啟用</span>
          <span className="text-emerald-600">📎 多模態上傳已啟用（最多 5 份）</span>
        </div>
      </div>
    </div>
  )
}

export default ChatInterface
