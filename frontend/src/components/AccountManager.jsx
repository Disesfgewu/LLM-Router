import { useState, useEffect } from 'react'
import axios from 'axios'

// ── Helpers ────────────────────────────────────────────────

function Badge({ label, color }) {
  const colors = {
    green: 'bg-green-100 text-green-800',
    red: 'bg-red-100 text-red-800',
    blue: 'bg-blue-100 text-blue-800',
    amber: 'bg-amber-100 text-amber-800',
    gray: 'bg-gray-100 text-gray-600',
  }
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${colors[color] || colors.gray}`}>
      {label}
    </span>
  )
}

function Alert({ msg, type }) {
  if (!msg) return null
  const cls =
    type === 'error'
      ? 'bg-red-50 border-red-300 text-red-800'
      : 'bg-green-50 border-green-300 text-green-800'
  return (
    <div className={`rounded border px-4 py-3 text-sm mb-4 ${cls}`}>{msg}</div>
  )
}

// ── Sub-panels ─────────────────────────────────────────────

function AccountInfo({ me }) {
  return (
    <div className="space-y-3">
      <h3 className="font-semibold text-gray-700">帳號資訊</h3>
      <div className="bg-gray-50 rounded-lg p-4 text-sm space-y-2">
        <div className="flex gap-2">
          <span className="w-24 text-gray-500">帳號名稱</span>
          <span className="font-mono">{me.username}</span>
        </div>
        <div className="flex gap-2">
          <span className="w-24 text-gray-500">電子郵件</span>
          <span>{me.email}</span>
        </div>
        <div className="flex gap-2">
          <span className="w-24 text-gray-500">角色</span>
          {me.is_admin ? (
            <Badge label="管理員" color="blue" />
          ) : (
            <Badge label="一般用戶" color="gray" />
          )}
        </div>
        <div className="flex gap-2">
          <span className="w-24 text-gray-500">建立時間</span>
          <span>{me.created_at}</span>
        </div>
      </div>
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-700">
        <strong>安全提示：</strong>Session token 僅存在記憶體中。瀏覽器重新整理後需重新登入。
        這是設計行為，可防止 agent 讀取儲存在磁碟的 token。
      </div>
    </div>
  )
}

// ── API Keys panel ─────────────────────────────────────────

const SCOPE_OPTIONS = ['chat', 'completions', 'direct_query', 'images', 'file', 'models']
const SCOPE_DESC = {
  chat: '/v1/chat/completions',
  completions: '/v1/completions',
  direct_query: '/v1/direct_query',
  images: '/v1/images/generations',
  file: '/v1/file/generate_content',
  models: '/v1/models (唯讀)',
}

function KeyRevealModal({ keyData, onClose }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(keyData.full_key)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 px-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-xl p-6 space-y-4">
        <div className="flex items-center gap-2">
          <span className="text-2xl">🔑</span>
          <h3 className="text-lg font-bold">API Key 產生成功</h3>
        </div>

        <div className="bg-red-50 border border-red-300 rounded-lg p-3 text-sm text-red-800">
          <strong>⚠️ 請立即儲存這把 key！</strong>
          <br />
          此完整 key 只顯示一次，後端不會儲存原始值。遺失後需重新產生。
        </div>

        <div>
          <div className="text-xs text-gray-500 mb-1">名稱：{keyData.name}</div>
          {keyData.key_type === 'agent' && (
            <div className="text-xs text-gray-500 mb-1">
              Scope：{JSON.parse(keyData.scope || '[]').join(', ')} ｜
              過期：{keyData.expires_at} ｜
              RPM：{keyData.rpm_limit || '無限制'}
            </div>
          )}
          <div className="font-mono text-sm bg-gray-100 rounded p-3 break-all select-all border border-gray-300">
            {keyData.full_key}
          </div>
        </div>

        <div className="flex gap-3">
          <button
            onClick={handleCopy}
            className="flex-1 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium"
          >
            {copied ? '✅ 已複製' : '📋 複製 Key'}
          </button>
          <button
            onClick={onClose}
            className="flex-1 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 text-sm font-medium"
          >
            關閉
          </button>
        </div>
      </div>
    </div>
  )
}

function CreateFullKeyForm({ onCreated, onCancel }) {
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setErr('')
    try {
      const res = await axios.post('/auth/keys/full', { name })
      onCreated(res.data)
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    }
    setLoading(false)
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3 bg-gray-50 rounded-lg p-4">
      <h4 className="font-medium text-gray-700">產生全存取 Key (mk_)</h4>
      <div className="text-xs text-amber-700 bg-amber-50 rounded p-2 border border-amber-200">
        全存取 key 無 scope 限制、預設永不過期。請只發給受信任的應用程式。
      </div>
      <Alert msg={err} type="error" />
      <input
        className="w-full border rounded px-3 py-2 text-sm"
        placeholder="Key 名稱（例如：MyApp Backend）"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
      />
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={loading}
          className="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? '產生中...' : '產生 Key'}
        </button>
        <button type="button" onClick={onCancel} className="px-4 py-2 text-gray-600 text-sm">取消</button>
      </div>
    </form>
  )
}

function CreateAgentKeyForm({ onCreated, onCancel }) {
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState([])
  const [expiresHours, setExpiresHours] = useState(24)
  const [rpmLimit, setRpmLimit] = useState(60)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const toggleScope = (s) =>
    setScopes((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]))

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (scopes.length === 0) { setErr('請至少選擇一個 scope'); return }
    setLoading(true)
    setErr('')
    try {
      const res = await axios.post('/auth/keys/agent', {
        name,
        scopes,
        expires_hours: expiresHours,
        rpm_limit: rpmLimit,
      })
      onCreated(res.data)
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    }
    setLoading(false)
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3 bg-gray-50 rounded-lg p-4">
      <h4 className="font-medium text-gray-700">產生 Agent Key (ma_)</h4>
      <div className="text-xs text-blue-700 bg-blue-50 rounded p-2 border border-blue-200">
        Agent key 有 scope 限制、RPM 上限和過期時間。安全地給自動化 agent 使用。
      </div>
      <Alert msg={err} type="error" />
      <input
        className="w-full border rounded px-3 py-2 text-sm"
        placeholder="Key 名稱（例如：Chat Bot Agent）"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
      />
      <div>
        <div className="text-xs text-gray-600 mb-1 font-medium">允許的 Scope（端點存取權限）</div>
        <div className="grid grid-cols-2 gap-1">
          {SCOPE_OPTIONS.map((s) => (
            <label key={s} className="flex items-start gap-2 cursor-pointer text-xs">
              <input
                type="checkbox"
                checked={scopes.includes(s)}
                onChange={() => toggleScope(s)}
                className="mt-0.5"
              />
              <span>
                <span className="font-mono font-medium">{s}</span>
                <span className="text-gray-500 block">{SCOPE_DESC[s]}</span>
              </span>
            </label>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-gray-600 mb-1 block">過期時間（小時）</label>
          <input
            type="number"
            min="1"
            max="8760"
            className="w-full border rounded px-3 py-2 text-sm"
            value={expiresHours}
            onChange={(e) => setExpiresHours(parseInt(e.target.value) || 24)}
          />
        </div>
        <div>
          <label className="text-xs text-gray-600 mb-1 block">RPM 上限（0=無限）</label>
          <input
            type="number"
            min="0"
            className="w-full border rounded px-3 py-2 text-sm"
            value={rpmLimit}
            onChange={(e) => setRpmLimit(parseInt(e.target.value) || 0)}
          />
        </div>
      </div>
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={loading}
          className="px-4 py-2 bg-green-600 text-white rounded text-sm hover:bg-green-700 disabled:opacity-50"
        >
          {loading ? '產生中...' : '產生 Agent Key'}
        </button>
        <button type="button" onClick={onCancel} className="px-4 py-2 text-gray-600 text-sm">取消</button>
      </div>
    </form>
  )
}

function KeysPanel({ isAdmin }) {
  const [keys, setKeys] = useState([])
  const [mode, setMode] = useState(null) // null | 'full' | 'agent'
  const [revealKey, setRevealKey] = useState(null)
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')

  const load = async () => {
    try {
      const res = await axios.get('/auth/keys')
      setKeys(res.data)
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    }
  }

  useEffect(() => { load() }, [])

  const handleRevoke = async (id) => {
    if (!confirm('確定要撤銷這把 key 嗎？撤銷後無法復原。')) return
    try {
      await axios.delete(`/auth/keys/${id}`)
      setOk(`Key ${id} 已撤銷`)
      load()
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    }
  }

  const handleCreated = (data) => {
    setMode(null)
    setRevealKey(data)
    load()
  }

  const scopeColor = (keyType) => (keyType === 'full' ? 'blue' : 'green')

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-gray-700">API Keys</h3>
        <div className="flex gap-2">
          {isAdmin && (
            <button
              onClick={() => setMode('full')}
              className="px-3 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700"
            >
              ＋ 全存取 Key
            </button>
          )}
          <button
            onClick={() => setMode('agent')}
            className="px-3 py-1.5 bg-green-600 text-white rounded text-sm hover:bg-green-700"
          >
            ＋ Agent Key
          </button>
        </div>
      </div>

      <Alert msg={err} type="error" />
      <Alert msg={ok} type="ok" />

      {mode === 'full' && (
        <CreateFullKeyForm onCreated={handleCreated} onCancel={() => setMode(null)} />
      )}
      {mode === 'agent' && (
        <CreateAgentKeyForm onCreated={handleCreated} onCancel={() => setMode(null)} />
      )}

      {revealKey && (
        <KeyRevealModal keyData={revealKey} onClose={() => setRevealKey(null)} />
      )}

      {keys.length === 0 ? (
        <div className="text-center py-8 text-gray-400 text-sm">尚無任何 API key</div>
      ) : (
        <div className="space-y-2">
          {keys.map((k) => (
            <div
              key={k.id}
              className={`rounded-lg border p-4 text-sm ${
                k.is_active ? 'bg-white' : 'bg-gray-50 opacity-60'
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="space-y-1 flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{k.name}</span>
                    <Badge label={k.key_type === 'full' ? '全存取 mk_' : 'Agent ma_'} color={scopeColor(k.key_type)} />
                    {!k.is_active && <Badge label="已撤銷" color="red" />}
                  </div>
                  <div className="font-mono text-xs text-gray-500">{k.prefix}…</div>
                  {k.scope && (
                    <div className="text-xs text-gray-600">
                      Scope: <span className="font-mono">{JSON.parse(k.scope).join(', ')}</span>
                    </div>
                  )}
                  <div className="flex flex-wrap gap-3 text-xs text-gray-400">
                    {k.expires_at && (
                      <span>過期：{new Date(k.expires_at).toLocaleString()}</span>
                    )}
                    {k.rpm_limit > 0 && <span>RPM：{k.rpm_limit}</span>}
                    {k.last_used_at && <span>上次使用：{k.last_used_at}</span>}
                    <span>建立：{k.created_at}</span>
                  </div>
                </div>
                {k.is_active && (
                  <button
                    onClick={() => handleRevoke(k.id)}
                    className="shrink-0 px-3 py-1 text-xs bg-red-100 text-red-700 rounded hover:bg-red-200"
                  >
                    撤銷
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Whitelist panel ─────────────────────────────────────────

function WhitelistPanel() {
  const [list, setList] = useState([])
  const [ipCidr, setIpCidr] = useState('')
  const [desc, setDesc] = useState('')
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')

  const load = async () => {
    try {
      const res = await axios.get('/auth/whitelist')
      setList(res.data)
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    }
  }

  useEffect(() => { load() }, [])

  const handleAdd = async (e) => {
    e.preventDefault()
    setErr('')
    setOk('')
    try {
      await axios.post('/auth/whitelist', { ip_cidr: ipCidr, description: desc })
      setOk(`已新增 ${ipCidr}`)
      setIpCidr('')
      setDesc('')
      load()
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    }
  }

  const handleDelete = async (id, cidr) => {
    if (!confirm(`確定移除 ${cidr}？`)) return
    try {
      await axios.delete(`/auth/whitelist/${id}`)
      setOk(`已移除 ${cidr}`)
      load()
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    }
  }

  return (
    <div className="space-y-4">
      <h3 className="font-semibold text-gray-700">IP 白名單</h3>
      <div className="text-xs text-gray-500 bg-gray-50 rounded p-3 border">
        白名單為空時，允許所有 IP。一旦新增任何條目，所有 API key 請求都必須來自白名單內的 IP / CIDR。
      </div>
      <Alert msg={err} type="error" />
      <Alert msg={ok} type="ok" />

      <form onSubmit={handleAdd} className="flex gap-2 flex-wrap">
        <input
          className="border rounded px-3 py-2 text-sm flex-1 min-w-32"
          placeholder="IP 或 CIDR（如 192.168.1.0/24）"
          value={ipCidr}
          onChange={(e) => setIpCidr(e.target.value)}
          required
        />
        <input
          className="border rounded px-3 py-2 text-sm w-48"
          placeholder="描述（選填）"
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
        />
        <button
          type="submit"
          className="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700"
        >
          新增
        </button>
      </form>

      {list.length === 0 ? (
        <div className="text-center py-6 text-gray-400 text-sm">白名單為空（允許所有 IP）</div>
      ) : (
        <div className="space-y-2">
          {list.map((e) => (
            <div key={e.id} className="flex items-center justify-between bg-white rounded-lg border p-3 text-sm">
              <div>
                <span className="font-mono font-medium">{e.ip_cidr}</span>
                {e.description && <span className="ml-2 text-gray-500">— {e.description}</span>}
                <div className="text-xs text-gray-400 mt-0.5">{e.created_at}</div>
              </div>
              <button
                onClick={() => handleDelete(e.id, e.ip_cidr)}
                className="px-3 py-1 text-xs bg-red-100 text-red-700 rounded hover:bg-red-200"
              >
                移除
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Audit log panel ─────────────────────────────────────────

function AuditPanel() {
  const [log, setLog] = useState([])
  const [err, setErr] = useState('')

  useEffect(() => {
    axios.get('/auth/audit')
      .then((r) => setLog(r.data))
      .catch((e) => setErr(e.response?.data?.detail || e.message))
  }, [])

  return (
    <div className="space-y-3">
      <h3 className="font-semibold text-gray-700">稽核紀錄（最新 100 筆）</h3>
      <Alert msg={err} type="error" />
      {log.length === 0 ? (
        <div className="text-center py-6 text-gray-400 text-sm">尚無紀錄</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="bg-gray-100 text-gray-600">
                <th className="text-left px-3 py-2 border-b">時間</th>
                <th className="text-left px-3 py-2 border-b">Key ID</th>
                <th className="text-left px-3 py-2 border-b">動作</th>
                <th className="text-left px-3 py-2 border-b">Scope</th>
                <th className="text-left px-3 py-2 border-b">來源 IP</th>
              </tr>
            </thead>
            <tbody>
              {log.map((r) => (
                <tr key={r.id} className="border-b hover:bg-gray-50">
                  <td className="px-3 py-2 font-mono">{r.created_at}</td>
                  <td className="px-3 py-2">{r.key_id ?? '-'}</td>
                  <td className="px-3 py-2">{r.action}</td>
                  <td className="px-3 py-2 font-mono">{r.endpoint ?? '-'}</td>
                  <td className="px-3 py-2 font-mono">{r.client_ip ?? '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Admin user management panel ─────────────────────────────

function AdminUsersPanel() {
  const [accounts, setAccounts] = useState([])
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')

  const load = async () => {
    try {
      const res = await axios.get('/admin/accounts')
      setAccounts(res.data)
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    }
  }

  useEffect(() => { load() }, [])

  const toggle = async (acct) => {
    const path = acct.is_active ? 'deactivate' : 'activate'
    try {
      await axios.post(`/admin/accounts/${acct.id}/${path}`)
      setOk(`帳號 ${acct.username} 已${acct.is_active ? '停用' : '啟用'}`)
      load()
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    }
  }

  return (
    <div className="space-y-3">
      <h3 className="font-semibold text-gray-700">帳號管理（管理員）</h3>
      <Alert msg={err} type="error" />
      <Alert msg={ok} type="ok" />
      <div className="space-y-2">
        {accounts.map((a) => (
          <div key={a.id} className="flex items-center justify-between bg-white rounded-lg border p-3 text-sm">
            <div className="space-y-0.5">
              <div className="flex items-center gap-2">
                <span className="font-medium">{a.username}</span>
                {a.is_admin && <Badge label="管理員" color="blue" />}
                <Badge label={a.is_active ? '啟用' : '停用'} color={a.is_active ? 'green' : 'red'} />
              </div>
              <div className="text-xs text-gray-400">{a.email} ｜ 建立：{a.created_at}</div>
            </div>
            {!a.is_admin && (
              <button
                onClick={() => toggle(a)}
                className={`px-3 py-1 text-xs rounded ${
                  a.is_active
                    ? 'bg-red-100 text-red-700 hover:bg-red-200'
                    : 'bg-green-100 text-green-700 hover:bg-green-200'
                }`}
              >
                {a.is_active ? '停用' : '啟用'}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main AccountManager component ──────────────────────────

export default function AccountManager({ me, onLogout }) {
  const tabs = [
    { key: 'account', label: '帳號資訊' },
    { key: 'keys', label: 'API Keys' },
    { key: 'whitelist', label: 'IP 白名單' },
    { key: 'audit', label: '使用紀錄' },
    ...(me?.is_admin ? [{ key: 'users', label: '帳號管理' }] : []),
  ]
  const [active, setActive] = useState('account')

  return (
    <div className="space-y-4">
      {/* Tabs */}
      <div className="border-b border-gray-200 flex gap-6">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setActive(t.key)}
            className={`pb-2 text-sm border-b-2 font-medium transition-colors ${
              active === t.key
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
        <div className="ml-auto pb-2">
          <button
            onClick={onLogout}
            className="text-sm text-red-600 hover:text-red-800"
          >
            登出
          </button>
        </div>
      </div>

      {/* Panel content */}
      {active === 'account' && <AccountInfo me={me} />}
      {active === 'keys' && <KeysPanel isAdmin={me?.is_admin} />}
      {active === 'whitelist' && <WhitelistPanel />}
      {active === 'audit' && <AuditPanel />}
      {active === 'users' && me?.is_admin && <AdminUsersPanel />}
    </div>
  )
}
