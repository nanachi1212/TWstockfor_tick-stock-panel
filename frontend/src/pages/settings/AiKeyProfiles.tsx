import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Key,
  Plus,
  Trash2,
  CheckCircle2,
  Loader2,
  Wifi,
  WifiOff,
  Star,
  Pencil,
  X,
} from 'lucide-react'
import { api, type AiKeyProfile } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'
import { cn } from '@/lib/cn'

const INPUT_CLS =
  'w-full h-9 px-2.5 rounded-lg bg-base border-0 ring-1 ring-border/30 text-xs font-mono text-foreground placeholder:text-muted/30 focus:outline-none focus:ring-2 focus:ring-accent/30 transition-shadow'

const PROVIDER_LABELS: Record<string, string> = {
  openai_compat: '自訂 OpenAI 相容',
  openai: 'OpenAI 官方',
  codex_cli: 'Codex 訂閱（ChatGPT）',
  claude_cli: 'Claude 訂閱（Claude Code）',
}

/** 訂閱類型直接呼叫本機已登入的 CLI，不需要 API Key 或 Base URL。 */
const SUBSCRIPTION_MODELS: Record<string, string[]> = {
  claude_cli: ['sonnet', 'opus', 'haiku', 'fable'],
  codex_cli: ['gpt-5.5', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna'],
}
const isSubscription = (provider: string) => provider in SUBSCRIPTION_MODELS

function SubscriptionNote({ provider }: { provider: string }) {
  const command = provider === 'claude_cli' ? 'claude' : 'codex login'
  return (
    <p className="rounded-lg border border-border/50 bg-base/40 px-2.5 py-2 text-[10px] text-muted">
      使用這台電腦上 <span className="font-mono">{command}</span> 的訂閱登入，不需要 API Key。
      呼叫會佔用你的訂閱額度；登入失效時請在終端機重新登入。Docker 版看不到本機登入，無法使用。
    </p>
  )
}

/** 原生下拉加自填：列出建議模型，也可以直接輸入其他名稱。 */
function ModelInput({ provider, value, onChange, ariaLabel, placeholder }: {
  provider: string
  value: string
  onChange: (value: string) => void
  ariaLabel?: string
  placeholder?: string
}) {
  const options = SUBSCRIPTION_MODELS[provider]
  const listId = options ? `ai-models-${provider}` : undefined
  return (
    <>
      <input
        type="text"
        aria-label={ariaLabel}
        list={listId}
        placeholder={options ? options[0] : placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
        className={INPUT_CLS}
      />
      {options && (
        <datalist id={listId}>
          {options.map(model => <option key={model} value={model} />)}
        </datalist>
      )}
    </>
  )
}

/**
 * AI Key Profiles panel.
 *
 * Usage clarifications (displayed inline):
 *   - LINE / Telegram are notification destinations, not AI providers
 *   - Stock alert rules are built in Monitor / TaiwanStockDetail
 *   - Real-time quote must be on for monitoring
 *   - These profiles are API keys for the AI assistant (Dashboard / Stock Detail / Daily Brief)
 *   - Rate limit => user message, not auto-rotate
 */
export function AiKeyProfilesPanel() {
  const qc = useQueryClient()

  const profilesQuery = useQuery({
    queryKey: QK.aiKeyProfiles,
    queryFn: () => (api.aiKeyProfiles ? api.aiKeyProfiles() : Promise.resolve({ profiles: [] })),
  })

  const profiles = profilesQuery.data?.profiles ?? []

  const [addOpen, setAddOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newProvider, setNewProvider] = useState('openai_compat')
  const [newKey, setNewKey] = useState('')
  const [newBaseUrl, setNewBaseUrl] = useState('')
  const [newModel, setNewModel] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; msg: string }>>({})
  const [editing, setEditing] = useState<AiKeyProfile | null>(null)
  const [editName, setEditName] = useState('')
  const [editProvider, setEditProvider] = useState('openai_compat')
  const [editBaseUrl, setEditBaseUrl] = useState('')
  const [editModel, setEditModel] = useState('')
  const [editKey, setEditKey] = useState('')
  const [showEditKey, setShowEditKey] = useState(false)

  const createMut = useMutation({
    mutationFn: () =>
      api.aiKeyProfileCreate({
        name: newName.trim(),
        provider: newProvider,
        api_key: newKey.trim(),
        base_url: newBaseUrl.trim(),
        model: newModel.trim(),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.aiKeyProfiles })
      qc.invalidateQueries({ queryKey: QK.settings })
      setAddOpen(false)
      setNewName('')
      setNewKey('')
      setNewBaseUrl('')
      setNewModel('')
      toast('已新增 AI Key Profile', 'success')
    },
    onError: (e: any) => toast(e.message || '新增失敗', 'error'),
  })

  const activateMut = useMutation({
    mutationFn: (id: string) => api.aiKeyProfileActivate(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.aiKeyProfiles })
      qc.invalidateQueries({ queryKey: QK.settings })
      toast('已切換使用中 Key', 'success')
    },
    onError: (e: any) => toast(e.message || '切換失敗', 'error'),
  })

  const deepMut = useMutation({
    mutationFn: (id: string | null) => api.aiKeyProfileSetDeep(id),
    onSuccess: (_res, id) => {
      qc.invalidateQueries({ queryKey: QK.aiKeyProfiles })
      toast(id ? '已設為深度模型' : '已取消深度模型', 'success')
    },
    onError: (e: any) => toast(e.message || '設定失敗', 'error'),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.aiKeyProfileDelete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.aiKeyProfiles })
      qc.invalidateQueries({ queryKey: QK.settings })
      toast('已刪除 Profile', 'success')
    },
    onError: (e: any) => toast(e.message || '刪除失敗', 'error'),
  })

  /** 訂閱 Profile 在列上直接換模型，不用開編輯視窗。 */
  const modelMut = useMutation({
    mutationFn: ({ p, model }: { p: AiKeyProfile; model: string }) =>
      api.aiKeyProfileUpdate(p.id, { name: p.name, provider: p.provider, base_url: p.base_url ?? '', model }),
    onSuccess: (_r, { model }) => {
      qc.invalidateQueries({ queryKey: QK.aiKeyProfiles })
      qc.invalidateQueries({ queryKey: QK.settings })
      toast(`已切換模型：${model}`, 'success')
    },
    onError: (e: any) => toast(e.message || '切換模型失敗', 'error'),
  })

  const updateMut = useMutation({
    mutationFn: () => {
      if (!editing) throw new Error('未選擇 Profile')
      return api.aiKeyProfileUpdate(editing.id, {
        name: editName.trim(),
        provider: editProvider,
        base_url: editBaseUrl.trim(),
        model: editModel.trim(),
        ...(editKey.trim() ? { api_key: editKey.trim() } : {}),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.aiKeyProfiles })
      qc.invalidateQueries({ queryKey: QK.settings })
      setEditing(null)
      setEditKey('')
      setShowEditKey(false)
      toast('已更新 AI Key Profile', 'success')
    },
    onError: (e: any) => toast(e.message || '更新失敗', 'error'),
  })

  function openEdit(profile: AiKeyProfile) {
    setEditing(profile)
    setEditName(profile.name)
    setEditProvider(profile.provider)
    setEditBaseUrl(profile.base_url)
    setEditModel(profile.model)
    setEditKey('')
    setShowEditKey(false)
  }

  async function handleTest(id: string) {
    setTestingId(id)
    try {
      const res = await api.aiKeyProfileTest(id)
      setTestResults(prev => ({
        ...prev,
        [id]: {
          ok: res.ok,
          msg: res.ok ? '連線成功' : (res.error || '連線失敗'),
        },
      }))
    } catch (e: any) {
      setTestResults(prev => ({ ...prev, [id]: { ok: false, msg: e.message || '連線失敗' } }))
    } finally {
      setTestingId(null)
    }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-bold text-foreground flex items-center gap-2">
            <Key className="h-4 w-4 text-accent" />
            AI API Key 多組管理
          </h3>
          <p className="text-xs text-muted mt-0.5">
            保存多組 API Key 或 Codex／Claude 訂閱，手動切換使用中的 Profile。Dashboard AI、個股 AI、每日摘要 AI
            全部使用同一個目前使用中的 Profile；「設為深度」的 Profile 只給辯論裁判與最終建議用。
          </p>
        </div>
        <button
          type="button"
          onClick={() => setAddOpen(!addOpen)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-accent/60 bg-accent/10 px-3 py-1.5 text-xs font-semibold text-accent hover:bg-accent/20 transition-colors cursor-pointer"
        >
          <Plus className="h-3.5 w-3.5" />
          新增
        </button>
      </div>

      {/* Rate limit notice */}
      <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-600 dark:text-amber-400">
        ⚠️ 若目前 Key 已達額度限制，系統<strong>不會</strong>自動輪替。
        請手動切換其他 Key，或稍後再試。
      </div>

      {/* Add form */}
      {addOpen && (
        <div className="rounded-xl border border-accent/30 bg-accent/5 p-4 space-y-3">
          <p className="text-[11px] font-semibold text-accent">新增 Key Profile</p>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-[10px] font-semibold text-muted mb-1">自訂名稱</label>
              <input
                type="text"
                placeholder="例如: free-key-1"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                className={INPUT_CLS}
              />
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-muted mb-1">Provider</label>
              <select
                value={newProvider}
                onChange={e => {
                  setNewProvider(e.target.value)
                  if (isSubscription(e.target.value)) setNewModel(SUBSCRIPTION_MODELS[e.target.value][0])
                }}
                className={INPUT_CLS + ' cursor-pointer'}
              >
                {Object.entries(PROVIDER_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </div>
          </div>
          {isSubscription(newProvider) ? (
            <>
              <SubscriptionNote provider={newProvider} />
              <div>
                <label className="block text-[10px] font-semibold text-muted mb-1">Model</label>
                <ModelInput provider={newProvider} value={newModel} onChange={setNewModel} ariaLabel="訂閱模型" />
              </div>
            </>
          ) : (<>
          <div>
            <label className="block text-[10px] font-semibold text-muted mb-1">API Key</label>
            <div className="relative">
              <input
                type={showKey ? 'text' : 'password'}
                placeholder="sk-..."
                value={newKey}
                onChange={e => setNewKey(e.target.value)}
                className={INPUT_CLS + ' pr-16'}
              />
              <button
                type="button"
                onClick={() => setShowKey(!showKey)}
                className="absolute right-2 top-1.5 text-[10px] text-muted hover:text-foreground cursor-pointer px-1"
              >
                {showKey ? '隱藏' : '顯示'}
              </button>
            </div>
            <p className="text-[10px] text-muted mt-1">
              Key 只存於本機後端，畫面只顯示遮罩值，不會寫入 log 或傳至第三方。
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-[10px] font-semibold text-muted mb-1">Base URL（選填）</label>
              <input
                type="text"
                placeholder="https://api.openai.com/v1"
                value={newBaseUrl}
                onChange={e => setNewBaseUrl(e.target.value)}
                className={INPUT_CLS}
              />
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-muted mb-1">Model（選填）</label>
              <input
                type="text"
                placeholder="gpt-4o-mini"
                value={newModel}
                onChange={e => setNewModel(e.target.value)}
                className={INPUT_CLS}
              />
            </div>
          </div>
          </>)}
          <div className="flex gap-2 justify-end">
            <button
              type="button"
              onClick={() => { setAddOpen(false); setNewName(''); setNewKey(''); setNewBaseUrl(''); setNewModel('') }}
              className="rounded-lg border border-border/80 px-3 py-1.5 text-xs text-muted hover:bg-elevated hover:text-foreground cursor-pointer"
            >
              取消
            </button>
            <button
              type="button"
              disabled={!newName.trim() || (!isSubscription(newProvider) && !newKey.trim()) || createMut.isPending}
              onClick={() => createMut.mutate()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-foreground hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
            >
              {createMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
              新增
            </button>
          </div>
        </div>
      )}

      {/* Profile list */}
      {profiles.length === 0 && !addOpen && (
        <div className="rounded-xl border border-dashed border-border/60 p-6 text-center">
          <Key className="h-6 w-6 text-muted mx-auto mb-2" />
          <p className="text-xs text-muted">尚未新增任何 AI Key Profile</p>
          <p className="text-[10px] text-muted mt-1">
            新增後可在此切換目前使用的 Key，不需重啟。
          </p>
        </div>
      )}

      <div className="space-y-2">
        {profiles.map((p: AiKeyProfile) => {
          const testResult = testResults[p.id]
          const isTesting = testingId === p.id
          return (
            <div
              key={p.id}
              className={cn(
                'rounded-xl border p-3 transition-all',
                p.active
                  ? 'border-accent/40 bg-accent/8'
                  : 'border-border/60 bg-surface',
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-xs font-semibold text-foreground truncate">{p.name}</span>
                    {p.active && (
                      <span className="inline-flex items-center gap-0.5 rounded bg-accent/20 px-1.5 py-0.5 text-[9px] font-bold text-accent">
                        <Star className="h-2.5 w-2.5" />
                        目前使用
                      </span>
                    )}
                    {p.deep && (
                      <span className="inline-flex items-center gap-0.5 rounded bg-violet-500/15 px-1.5 py-0.5 text-[9px] font-bold text-violet-500">
                        深度模型
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-muted flex-wrap">
                    <span>{PROVIDER_LABELS[p.provider] ?? p.provider}</span>
                    <span>·</span>
                    <span className="font-mono">{isSubscription(p.provider) ? '訂閱登入' : (p.key_masked || '(無 key)')}</span>
                    {isSubscription(p.provider)
                      ? <><span>·</span>
                        <select
                          aria-label={`${p.name} 模型`}
                          value={p.model}
                          disabled={modelMut.isPending}
                          onChange={e => modelMut.mutate({ p, model: e.target.value })}
                          className="h-6 rounded-md bg-base px-1.5 text-[10px] font-mono text-foreground ring-1 ring-border/30 focus:outline-none focus:ring-2 focus:ring-accent/30"
                        >
                          {[...new Set([...(p.model ? [p.model] : []), ...SUBSCRIPTION_MODELS[p.provider]])].map(m => <option key={m} value={m}>{m}</option>)}
                        </select></>
                      : p.model && <><span>·</span><span className="font-mono">{p.model}</span></>}
                    {p.base_url && <><span>·</span><span className="font-mono truncate max-w-[180px]" title={p.base_url}>{p.base_url}</span></>}
                  </div>
                  {testResult && (
                    <div className={cn(
                      'mt-1 inline-flex items-center gap-1 text-[10px] rounded px-1.5 py-0.5',
                      testResult.ok
                        ? 'text-emerald-600 bg-emerald-500/10'
                        : 'text-rose-500 bg-rose-500/10',
                    )}>
                      {testResult.ok
                        ? <><Wifi className="h-2.5 w-2.5" />{testResult.msg}</>
                        : <><WifiOff className="h-2.5 w-2.5" />{testResult.msg}</>}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {/* Test connection */}
                  <button
                    type="button"
                    disabled={isTesting}
                    onClick={() => handleTest(p.id)}
                    className="inline-flex items-center gap-1 rounded-lg border border-border/60 px-2 py-1 text-[10px] text-muted hover:border-accent/40 hover:text-accent transition-colors cursor-pointer disabled:opacity-50"
                  >
                    {isTesting ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <Wifi className="h-2.5 w-2.5" />}
                    測試
                  </button>
                  {/* Edit */}
                  <button
                    type="button"
                    onClick={() => openEdit(p)}
                    className="inline-flex items-center gap-1 rounded-lg border border-border/60 px-2 py-1 text-[10px] text-muted hover:border-accent/40 hover:text-accent transition-colors cursor-pointer"
                  >
                    <Pencil className="h-2.5 w-2.5" />
                    編輯
                  </button>
                  {/* Activate */}
                  {!p.active && (
                    <button
                      type="button"
                      disabled={activateMut.isPending}
                      onClick={() => activateMut.mutate(p.id)}
                      className="inline-flex items-center gap-1 rounded-lg border border-accent/40 bg-accent/10 px-2 py-1 text-[10px] font-semibold text-accent hover:bg-accent/20 transition-colors cursor-pointer disabled:opacity-50"
                    >
                      <CheckCircle2 className="h-2.5 w-2.5" />
                      設為使用
                    </button>
                  )}
                  {/* Deep model: debate judge + final Advice */}
                  <button
                    type="button"
                    disabled={deepMut.isPending}
                    onClick={() => deepMut.mutate(p.deep ? null : p.id)}
                    title="多空辯論的裁判與最終 Advice 改用這個 Profile；多方、空方與複核仍用目前使用的 Profile"
                    className="inline-flex items-center gap-1 rounded-lg border border-violet-500/30 px-2 py-1 text-[10px] text-violet-500 hover:bg-violet-500/10 transition-colors cursor-pointer disabled:opacity-50"
                  >
                    {p.deep ? '取消深度' : '設為深度'}
                  </button>
                  {/* Delete */}
                  <button
                    type="button"
                    disabled={deleteMut.isPending}
                    onClick={() => deleteMut.mutate(p.id)}
                    className="inline-flex items-center gap-1 rounded-lg border border-rose-500/30 px-2 py-1 text-[10px] text-rose-500 hover:bg-rose-500/10 transition-colors cursor-pointer disabled:opacity-50"
                  >
                    <Trash2 className="h-2.5 w-2.5" />
                    刪除
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setEditing(null)} />
          <div role="dialog" aria-modal="true" aria-labelledby="edit-ai-profile-title" className="relative w-[92vw] max-w-lg rounded-card border border-border bg-base p-5 shadow-2xl">
            <div className="mb-4 flex items-center justify-between">
              <h4 id="edit-ai-profile-title" className="text-sm font-semibold text-foreground">編輯 AI Key Profile</h4>
              <button type="button" aria-label="關閉" onClick={() => setEditing(null)} className="text-muted hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-[10px] font-semibold text-muted">Profile 名稱</label>
                  <input aria-label="Profile 名稱" value={editName} onChange={e => setEditName(e.target.value)} className={INPUT_CLS} />
                </div>
                <div>
                  <label className="mb-1 block text-[10px] font-semibold text-muted">Provider</label>
                  <select aria-label="Provider" value={editProvider} onChange={e => setEditProvider(e.target.value)} className={INPUT_CLS + ' cursor-pointer'}>
                    {Object.entries(PROVIDER_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </select>
                </div>
              </div>
              {isSubscription(editProvider) ? (
                <SubscriptionNote provider={editProvider} />
              ) : (
                <div>
                  <label className="mb-1 block text-[10px] font-semibold text-muted">Base URL</label>
                  <input aria-label="Base URL" value={editBaseUrl} onChange={e => setEditBaseUrl(e.target.value)} className={INPUT_CLS} />
                </div>
              )}
              <div>
                <label className="mb-1 block text-[10px] font-semibold text-muted">Model</label>
                <ModelInput provider={editProvider} value={editModel} onChange={setEditModel} ariaLabel="Model" />
              </div>
              {!isSubscription(editProvider) && <div>
                <label className="mb-1 block text-[10px] font-semibold text-muted">API Key</label>
                <div className="relative">
                  <input
                    aria-label="API Key"
                    type={showEditKey ? 'text' : 'password'}
                    value={editKey}
                    onChange={e => setEditKey(e.target.value)}
                    placeholder={`${editing.key_masked || '已儲存'} · 留空保留原 Key`}
                    className={INPUT_CLS + ' pr-16'}
                  />
                  <button type="button" onClick={() => setShowEditKey(value => !value)} className="absolute right-2 top-1.5 px-1 text-[10px] text-muted hover:text-foreground">
                    {showEditKey ? '隱藏' : '顯示'}
                  </button>
                </div>
                <p className="mt-1 text-[10px] text-muted">後端不會回傳原始 Key；留空即保留目前憑證。</p>
              </div>}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => setEditing(null)} className="rounded-lg border border-border/80 px-3 py-1.5 text-xs text-muted hover:text-foreground">取消</button>
              <button
                type="button"
                disabled={!editName.trim() || !editModel.trim() || (editProvider !== 'openai' && !isSubscription(editProvider) && !editBaseUrl.trim()) || updateMut.isPending}
                onClick={() => updateMut.mutate()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-foreground disabled:opacity-50"
              >
                {updateMut.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
                儲存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
