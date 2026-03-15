import { useState } from 'react'
import axios from 'axios'

function Dashboard({ status, models, onRefresh }) {
  const [resetting, setResetting] = useState(false)
  const imageModels = (models || []).filter((m) => m.category === 'ImageGeneration')

  const quotasByCategory = { ...(status?.quotas || {}) }
  if (!quotasByCategory.ImageGeneration && imageModels.length > 0) {
    const synthesizedProviders = {}
    imageModels.forEach((model) => {
      const provider = model.owned_by || 'Unknown'
      if (!synthesizedProviders[provider]) synthesizedProviders[provider] = {}

      const limit = typeof model.rpd_limit === 'number' ? model.rpd_limit : -1
      const remaining = typeof model.rpd_remaining === 'number' ? model.rpd_remaining : -1
      synthesizedProviders[provider][model.id] = {
        limit,
        remaining,
        used: limit === -1 || remaining === -1 ? 0 : Math.max(limit - remaining, 0),
        accounts: Array.isArray(model.provider_accounts) ? model.provider_accounts : [],
      }
    })
    quotasByCategory.ImageGeneration = synthesizedProviders
  }

  // 重置配額
  const handleResetQuotas = async () => {
    if (!confirm('確定要重置所有配額嗎？')) return
    setResetting(true)
    try {
      await axios.post('/admin/reset_quotas')
      alert('配額已重置')
      onRefresh()
    } catch (error) {
      alert('重置失敗: ' + error.message)
    }
    setResetting(false)
  }

  // 刷新 RPM
  const handleRefreshRPM = async () => {
    try {
      await axios.post('/admin/refresh_rpm')
      alert('優先順序指標已重置')
      onRefresh()
    } catch (error) {
      alert('重置失敗: ' + error.message)
    }
  }

  if (!status) {
    return (
      <div className="text-center py-12">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto"></div>
        <p className="mt-4 text-gray-600">載入中...</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Admin Actions */}
      {/* <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-xl font-semibold mb-4">⚙️ 管理操作</h2>
        <div className="flex gap-4">
          <button
            onClick={handleResetQuotas}
            disabled={resetting}
            className="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600 disabled:opacity-50"
          >
            {resetting ? '重置中...' : '🔄 重置所有配額'}
          </button>
          <button
            onClick={handleRefreshRPM}
            className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600"
          >
            ♻️ 刷新 RPM 指標
          </button>
        </div>
      </div> */}

      {/* Priority Flags */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-xl font-semibold mb-4">🎯 優先順序狀態</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {Object.entries(status.priority_flags || {}).map(([category, flag]) => (
            <div key={category} className="border rounded p-4">
              <div className="text-sm text-gray-600">{category}</div>
              <div className="text-2xl font-bold mt-2">
                {flag === 0 ? '✅ 正常' : '⚠️ 受限'}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Internal Gemma Usage */}
      {status?.internal_usage && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold mb-4">🧠 Internal Gemma 呼叫統計</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Object.entries(status.internal_usage).map(([name, value]) => (
              <div key={name} className="border rounded p-4">
                <div className="text-sm text-gray-600 break-all">{name}</div>
                <div className="text-2xl font-bold mt-2 text-indigo-700">{value}</div>
              </div>
            ))}
          </div>
          <p className="text-xs text-gray-500 mt-3">
            每次分類器/規劃器/審核器走 gemma-3-27b-it 都會累計，方便與配額消耗對照。
          </p>
        </div>
      )}

      {imageModels.length > 0 && (
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-4 text-sm text-indigo-800">
          <div className="font-semibold mb-1">ImageGeneration 配額提示</div>
          <div>
            偵測到 {imageModels.length} 個 image 模型。
            {status?.quotas?.ImageGeneration
              ? '目前已由 /admin/status 直接提供配額。'
              : '目前由 /v1/models 資料回填顯示，重啟 API 後會在 /admin/status 看到完整分類。'}
          </div>
        </div>
      )}

      {/* Quotas by Category */}
      {Object.entries(quotasByCategory).map(([category, providers]) => (
        <div key={category} className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold mb-4">📦 {category} 配額狀態</h2>
          
          {Object.entries(providers).map(([provider, models]) => (
            <div key={provider} className="mb-6 last:mb-0">
              <h3 className="text-lg font-medium mb-3 text-blue-600">{provider}</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {Object.entries(models).map(([modelId, quota]) => {
                  const usagePercent = quota.limit === -1
                    ? 0
                    : Number(((quota.used / quota.limit) * 100).toFixed(1))
                  const isLow = quota.limit !== -1 && usagePercent > 80
                  
                  return (
                    <div key={modelId} className={`border rounded p-4 ${isLow ? 'border-red-300 bg-red-50' : ''}`}>
                      <div className="text-sm font-medium text-gray-700 mb-2 truncate" title={modelId}>
                        {modelId}
                      </div>
                      
                      {quota.limit === -1 ? (
                        <div className="text-green-600 font-semibold">♾️ 無限制</div>
                      ) : (
                        <>
                          <div className="flex justify-between text-sm mb-1">
                            <span className="text-gray-600">已用 / 總量</span>
                            <span className="font-semibold">{quota.used} / {quota.limit}</span>
                          </div>
                          
                          {/* Progress Bar */}
                          <div className="w-full bg-gray-200 rounded-full h-2.5 mb-2">
                            <div
                              className={`h-2.5 rounded-full ${
                                usagePercent > 80 ? 'bg-red-500' : 
                                usagePercent > 50 ? 'bg-yellow-500' : 
                                'bg-green-500'
                              }`}
                              style={{ width: `${Math.min(usagePercent, 100)}%` }}
                            ></div>
                          </div>
                          
                          <div className="flex justify-between text-xs">
                            <span className="text-gray-500">剩餘: {quota.remaining}</span>
                            <span className={`font-semibold ${isLow ? 'text-red-600' : 'text-gray-700'}`}>
                              {usagePercent}%
                            </span>
                          </div>

                          {Array.isArray(quota.accounts) && quota.accounts.length > 0 && (
                            <div className="mt-3 border-t pt-2 space-y-1">
                              <div className="text-xs font-medium text-gray-600">帳戶明細</div>
                              {quota.accounts.map((account) => (
                                <div key={account.account_id} className="text-xs text-gray-600 flex justify-between gap-2">
                                  <span>#{account.account_id}</span>
                                  <span>
                                    {account.limit === -1 ? '♾️' : `${account.remaining}/${account.limit}`}
                                  </span>
                                </div>
                              ))}
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      ))}

      {/* Models List */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-xl font-semibold mb-4">🤖 可用模型列表</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  模型 ID
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  提供者
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  類別
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  配額狀態
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  能力
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  帳戶
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {models.map((model) => (
                <tr key={model.id}>
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                    {model.id}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {model.owned_by}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {model.category}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {model.id === 'auto' ? (
                      <span className="text-gray-500">自動路由</span>
                    ) : model.rpd_limit === -1 ? (
                      <span className="text-green-600">無限制</span>
                    ) : typeof model.rpd_limit === 'number' && typeof model.rpd_remaining === 'number' ? (
                      <span>
                        {model.rpd_remaining} / {model.rpd_limit}
                      </span>
                    ) : (
                      <span className="text-gray-400">-</span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500">
                    {model.capabilities ? (
                      <div className="flex flex-wrap gap-1">
                        {model.capabilities.chat_capable && (
                          <span className="px-2 py-0.5 rounded bg-blue-50 text-blue-700 text-xs">chat</span>
                        )}
                        {model.capabilities.image_input && (
                          <span className="px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 text-xs">image</span>
                        )}
                        {model.capabilities.document_input && (
                          <span className="px-2 py-0.5 rounded bg-amber-50 text-amber-700 text-xs">document</span>
                        )}
                        {model.capabilities.task && model.capabilities.task !== 'chat' && (
                          <span className="px-2 py-0.5 rounded bg-purple-50 text-purple-700 text-xs">{model.capabilities.task}</span>
                        )}
                      </div>
                    ) : (
                      <span className="text-gray-400">-</span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500">
                    {model.id === 'auto' ? (
                      <span className="text-gray-400">-</span>
                    ) : Array.isArray(model.provider_accounts) && model.provider_accounts.length > 0 ? (
                      <div className="space-y-1">
                        <div className="text-xs text-gray-600">{model.provider_account_count || model.provider_accounts.length} 個帳戶</div>
                        <div className="text-xs text-gray-500">
                          {model.provider_accounts
                            .map((account) => {
                              if (account.limit === -1) {
                                return `#${account.account_id}: ♾️`
                              }
                              return `#${account.account_id}: ${account.remaining}/${account.limit}`
                            })
                            .join(' | ')}
                        </div>
                      </div>
                    ) : (
                      <span>-</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

export default Dashboard
