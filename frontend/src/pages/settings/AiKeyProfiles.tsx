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
  const [showKey, setShowKey] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; msg: string }>>({})

  const createMut = useMutation({
    mutationFn: () =>
      api.aiKeyProfileCreate({ name: newName.trim(), provider: newProvider, api_key: newKey.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.aiKeyProfiles })
      qc.invalidateQueries({ queryKey: QK.settings })
      setAddOpen(false)
      setNewName('')
      setNewKey('')
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

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.aiKeyProfileDelete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.aiKeyProfiles })
      qc.invalidateQueries({ queryKey: QK.settings })
      toast('已刪除 Profile', 'success')
    },
    onError: (e: any) => toast(e.message || '刪除失敗', 'error'),
  })

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
            保存多組免費 API Key，手動切換使用中的 Key。Dashboard AI、個股 AI、每日摘要 AI
            全部使用同一個目前使用中的 Key。
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
                onChange={e => setNewProvider(e.target.value)}
                className={INPUT_CLS + ' cursor-pointer'}
              >
                {Object.entries(PROVIDER_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </div>
          </div>
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
          <div className="flex gap-2 justify-end">
            <button
              type="button"
              onClick={() => { setAddOpen(false); setNewName(''); setNewKey('') }}
              className="rounded-lg border border-border/80 px-3 py-1.5 text-xs text-muted hover:bg-elevated hover:text-foreground cursor-pointer"
            >
              取消
            </button>
            <button
              type="button"
              disabled={!newName.trim() || !newKey.trim() || createMut.isPending}
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
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-muted">
                    <span>{PROVIDER_LABELS[p.provider] ?? p.provider}</span>
                    <span>·</span>
                    <span className="font-mono">{p.key_masked || '(無 key)'}</span>
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
                  {/* Delete */}
                  <button
                    type="button"
                    disabled={deleteMut.isPending}
                    onClick={() => deleteMut.mutate(p.id)}
                    className="inline-flex items-center gap-1 rounded-lg border border-rose-500/30 px-2 py-1 text-[10px] text-rose-500 hover:bg-rose-500/10 transition-colors cursor-pointer disabled:opacity-50"
                  >
                    <Trash2 className="h-2.5 w-2.5" />
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
