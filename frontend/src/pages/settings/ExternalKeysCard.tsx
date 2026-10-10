import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, AlertCircle, KeyRound, Save, Trash2 } from 'lucide-react'
import { api, type ExternalKeyName } from '@/lib/api'
import { toast } from '@/components/Toast'

const KEYS: { name: ExternalKeyName; title: string; desc: string; url?: string }[] = [
  {
    name: 'fred_api_key',
    title: 'FRED 全球總經（FRED_API_KEY）',
    desc: '美國利率、美元指數等總經背景。免費申請：註冊 FRED 帳號 → My Account → API Keys。',
    url: 'https://fredaccount.stlouisfed.org/apikeys',
  },
  {
    name: 'fugle_api_key',
    title: 'Fugle 盤中即時力道（FUGLE_API_KEY）',
    desc: '個股頁「買賣力道／內外盤」的即時資料。到 Fugle 開發者平台申請行情 API Key。',
    url: 'https://developer.fugle.tw/',
  },
  {
    name: 'finbridge_api_key',
    title: 'FinBridge 基本面交叉比對（FINBRIDGE_API_KEY）',
    desc: '第二來源比對月營收等基本面。選配，沒有也不影響核心功能。',
  },
]

export function ExternalKeysCard() {
  const qc = useQueryClient()
  const [inputs, setInputs] = useState<Partial<Record<ExternalKeyName, string>>>({})
  const { data } = useQuery({ queryKey: ['externalKeys'], queryFn: api.getExternalKeys })
  const mutation = useMutation({
    mutationFn: (p: { name: ExternalKeyName; key: string }) => api.updateExternalKey(p),
    onSuccess: (_, p) => {
      toast(p.key ? 'API Key 已儲存，下次載入即生效' : 'API Key 已清除', 'success')
      setInputs((s) => ({ ...s, [p.name]: '' }))
      qc.invalidateQueries({ queryKey: ['externalKeys'] })
    },
    onError: (err: Error) => toast(`儲存失敗: ${err.message}`, 'error'),
  })

  return (
    <section className="rounded-card border border-border bg-surface p-6 space-y-4">
      <div className="flex items-center gap-2">
        <KeyRound className="h-4 w-4 text-accent" />
        <h3 className="text-sm font-semibold text-foreground">選配資料 API Key（總經／即時力道／基本面比對）</h3>
      </div>
      <p className="text-xs text-secondary leading-relaxed">
        Key 只存在本機 data/user_data/secrets.json，不會上傳。匯率（Frankfurter）免 Key，不需要設定。
      </p>
      {KEYS.map((k) => {
        const st = data?.[k.name]
        const value = inputs[k.name] ?? ''
        return (
          <div key={k.name} className="rounded-lg border border-border/60 bg-elevated/40 p-4 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-foreground">{k.title}</span>
              {st?.has_key ? (
                <span className="inline-flex items-center gap-1 text-xs text-emerald-400">
                  <CheckCircle2 className="h-3.5 w-3.5" />已設定 {st.masked}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-xs text-amber-400">
                  <AlertCircle className="h-3.5 w-3.5" />未設定
                </span>
              )}
            </div>
            <p className="text-xs text-secondary">
              {k.desc}{' '}
              {k.url && (
                <a href={k.url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                  申請網址
                </a>
              )}
            </p>
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                if (value.trim()) mutation.mutate({ name: k.name, key: value.trim() })
              }}
            >
              <input
                type="password"
                autoComplete="off"
                placeholder="貼上 API Key"
                value={value}
                onChange={(e) => setInputs((s) => ({ ...s, [k.name]: e.target.value }))}
                className="flex-1 rounded-btn border border-border bg-surface px-3 py-1.5 text-xs text-foreground focus:border-accent focus:outline-none"
              />
              <button
                type="submit"
                disabled={mutation.isPending || !value.trim()}
                className="inline-flex items-center gap-1 px-3 py-1.5 rounded-btn bg-accent text-white text-xs font-medium disabled:opacity-50"
              >
                <Save className="h-3.5 w-3.5" />儲存
              </button>
              {st?.has_key && (
                <button
                  type="button"
                  onClick={() => mutation.mutate({ name: k.name, key: '' })}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-btn text-xs text-red-400 hover:bg-red-500/10"
                >
                  <Trash2 className="h-3.5 w-3.5" />清除
                </button>
              )}
            </form>
          </div>
        )
      })}
    </section>
  )
}
