import { useState } from 'react'
import axios from 'axios'

function Dashboard({ status, models, onRefresh }) {
  const [resetting, setResetting] = useState(false)

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

      {/* Quotas by Category */}
      {Object.entries(status.quotas || {}).map(([category, providers]) => (
        <div key={category} className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold mb-4">📦 {category} 配額狀態</h2>
          
          {Object.entries(providers).map(([provider, models]) => (
            <div key={provider} className="mb-6 last:mb-0">
              <h3 className="text-lg font-medium mb-3 text-blue-600">{provider}</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {Object.entries(models).map(([modelId, quota]) => {
                  const usagePercent = quota.limit === -1 
                    ? 0 
                    : ((quota.used / quota.limit) * 100).toFixed(1)
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
                    {model.rpd_limit === -1 ? (
                      <span className="text-green-600">無限制</span>
                    ) : (
                      <span>
                        {model.rpd_remaining} / {model.rpd_limit}
                      </span>
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
