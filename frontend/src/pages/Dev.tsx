import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Search, AlertTriangle, CheckCircle2, XCircle, FlaskConical, Activity, Bell } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { QK } from '@/lib/queryKeys'
import { resetBadge } from '@/lib/monitorBadge'

// ── 分鐘K探測 (遷移自 MinuteDataProbe) ─────────────────
interface ProbeResult {
  date: string
  rows: number
  source: string
  ok: boolean
}

function MinuteProbePanel() {
  const [symbol, setSymbol] = useState('603261.SH')
  const [days, setDays] = useState(10)
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<ProbeResult[]>([])
  const [error, setError] = useState<string | null>(null)

  const runProbe = async () => {
    const sym = symbol.trim().toUpperCase()
    if (!sym) return
    setLoading(true)
    setError(null)
    setResults([])

    const dates: string[] = []
    const today = new Date()
    for (let i = 0; i < days; i++) {
      const d = new Date(today)
      d.setDate(d.getDate() - i)
      dates.push(d.toISOString().slice(0, 10))
    }

    const out: ProbeResult[] = []
    try {
      for (const date of dates) {
        const r = await api.klineMinute(sym, date)
        const rows = r.rows?.length ?? 0
        out.push({
          date,
          rows,
          source: r.source ?? (rows > 0 ? 'local' : 'none'),
          ok: rows > 0,
        })
        setResults([...out])
      }
    } catch (e: any) {
      setError(e?.message ?? String(e))
    } finally {
      setLoading(false)
    }
  }

  const total = results.length
  const hasData = results.filter((r) => r.ok).length
  const missing = results.filter((r) => !r.ok)

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-foreground">分鐘K數據探測</h2>
        <p className="mt-1 text-xs text-muted">
          逐日調用 <code className="px-1 rounded bg-elevated text-secondary">/api/kline/minute</code> 接口，
          檢測每隻股票最近若干天的分鐘K數據是否齊全。本地無數據時會自動走內建數據源實時拉取。
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-btn bg-elevated p-4">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted">股票代碼</label>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="603261.SH"
            className="w-44 rounded-btn border border-border bg-base px-3 py-1.5 text-sm text-foreground outline-none focus:border-accent"
            onKeyDown={(e) => e.key === 'Enter' && !loading && runProbe()}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted">回溯天數</label>
          <input
            type="number"
            min={1}
            max={30}
            value={days}
            onChange={(e) => setDays(Math.max(1, Math.min(30, Number(e.target.value) || 1)))}
            className="w-24 rounded-btn border border-border bg-base px-3 py-1.5 text-sm text-foreground outline-none focus:border-accent"
          />
        </div>
        <button
          onClick={runProbe}
          disabled={loading || !symbol.trim()}
          className="flex items-center gap-1.5 rounded-btn bg-accent px-4 py-1.5 text-sm font-medium text-base hover:bg-accent/90 disabled:opacity-50 cursor-pointer"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          {loading ? '探測中…' : '開始探測'}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-btn border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {total > 0 && (
        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-btn bg-elevated p-3">
            <div className="text-xs text-muted">檢測天數</div>
            <div className="mt-1 text-lg font-semibold text-foreground">{total}</div>
          </div>
          <div className="rounded-btn bg-elevated p-3">
            <div className="text-xs text-muted">有數據</div>
            <div className="mt-1 text-lg font-semibold text-emerald-400">{hasData}</div>
          </div>
          <div className="rounded-btn bg-elevated p-3">
            <div className="text-xs text-muted">缺失</div>
            <div className="mt-1 text-lg font-semibold text-danger">{missing.length}</div>
          </div>
        </div>
      )}

      {results.length > 0 && (
        <div className="overflow-hidden rounded-btn border border-border">
          <table className="w-full text-sm">
            <thead className="bg-elevated text-xs text-muted">
              <tr>
                <th className="px-4 py-2 text-left font-medium">日期</th>
                <th className="px-4 py-2 text-right font-medium">分鐘K條數</th>
                <th className="px-4 py-2 text-left font-medium">數據來源</th>
                <th className="px-4 py-2 text-center font-medium">狀態</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.date} className="border-t border-border/60">
                  <td className="px-4 py-2 text-foreground">{r.date}</td>
                  <td className="px-4 py-2 text-right tabular-nums text-foreground">{r.rows}</td>
                  <td className="px-4 py-2 text-secondary">
                    <span className="rounded bg-elevated px-1.5 py-0.5 text-xs">{r.source}</span>
                  </td>
                  <td className="px-4 py-2 text-center">
                    {r.ok ? (
                      <span className="inline-flex items-center gap-1 text-emerald-400">
                        <CheckCircle2 className="h-4 w-4" /> 有
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-danger">
                        <XCircle className="h-4 w-4" /> 缺失
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {missing.length > 0 && (
        <div className="rounded-btn border border-warning/40 bg-warning/10 p-3 text-xs text-foreground">
          <div className="mb-1 flex items-center gap-1.5 font-medium text-warning">
            <AlertTriangle className="h-4 w-4" /> 缺失日期的診斷
          </div>
          <p className="leading-relaxed text-secondary">
            缺失日期若為<span className="text-foreground">週末/節假日</span>屬正常；
            若為<span className="text-foreground">停牌日</span>（成交量為 0）也屬正常；
            若為<span className="text-foreground">正常交易日</span>（日K有成交量）卻缺失分鐘K，
            則是內建數據源未提供該日分鐘數據。
          </p>
        </div>
      )}
    </div>
  )
}

// ── 演示數據生成 ──────────────────────────────────────
function SeedPanel() {
  const qc = useQueryClient()
  const [count, setCount] = useState(12)
  const [recent, setRecent] = useState(true)
  const [msg, setMsg] = useState('')

  const seedMut = useMutation({
    mutationFn: () => api.alertSeed(count, recent),
    onSuccess: (data) => {
      setMsg(`已生成 ${data.generated} 條觸發記錄`)
      qc.invalidateQueries({ queryKey: ['alerts'] })
      qc.invalidateQueries({ queryKey: ['alerts-total'] })
      setTimeout(() => setMsg(''), 4000)
    },
    onError: () => {
      setMsg('生成失敗')
      setTimeout(() => setMsg(''), 4000)
    },
  })

  const clearMut = useMutation({
    mutationFn: () => api.alertsClear(),
    onSuccess: (data) => {
      setMsg(`已清空 ${data.cleared} 條觸發記錄`)
      qc.invalidateQueries({ queryKey: ['alerts'] })
      qc.invalidateQueries({ queryKey: ['alerts-total'] })
      resetBadge()
      setTimeout(() => setMsg(''), 4000)
    },
  })

  const ruleSeedMut = useMutation({
    mutationFn: () => api.monitorRuleSeed(),
    onSuccess: (data) => {
      setMsg(`已生成 ${data.generated} 條監控規則`)
      qc.invalidateQueries({ queryKey: QK.monitorRules })
      setTimeout(() => setMsg(''), 4000)
    },
    onError: () => {
      setMsg('規則生成失敗')
      setTimeout(() => setMsg(''), 4000)
    },
  })

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-foreground">監控觸發記錄演示數據</h2>
        <p className="mt-1 text-xs text-muted">
          生成模擬的觸發記錄,用於測試監控中心頁面的展示效果、未讀徽標、新增閃爍等功能。生成的數據可隨時清空。
        </p>
      </div>

      <div className="space-y-3 rounded-btn bg-elevated p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted">生成條數</label>
            <input
              type="number"
              min={1}
              max={50}
              value={count}
              onChange={(e) => setCount(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
              className="w-24 rounded-btn border border-border bg-base px-3 py-1.5 text-sm text-foreground outline-none focus:border-accent"
            />
          </div>
          <label className="flex items-center gap-1.5 pb-1.5">
            <input
              type="checkbox"
              checked={recent}
              onChange={(e) => setRecent(e.target.checked)}
              className="h-3.5 w-3.5 accent-accent"
            />
            <span className="text-xs text-secondary">時間戳設為"剛剛"(測試閃爍效果)</span>
          </label>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => seedMut.mutate()}
            disabled={seedMut.isPending}
            className="flex items-center gap-1.5 rounded-btn bg-accent px-4 py-1.5 text-sm font-medium text-base hover:bg-accent/90 disabled:opacity-50 cursor-pointer"
          >
            {seedMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
            生成演示數據
          </button>
          <button
            onClick={() => clearMut.mutate()}
            disabled={clearMut.isPending}
            className="flex items-center gap-1.5 rounded-btn border border-danger/40 bg-danger/10 px-4 py-1.5 text-sm font-medium text-danger hover:bg-danger/20 disabled:opacity-50 cursor-pointer"
          >
            {clearMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <XCircle className="h-4 w-4" />}
            清空全部
          </button>
        </div>
      </div>

      {msg && (
        <div className="rounded-btn border border-accent/40 bg-accent/10 p-3 text-sm text-accent">{msg}</div>
      )}

      {/* 監控規則生成 */}
      <div className="space-y-3 rounded-btn bg-elevated p-4">
        <div>
          <h3 className="text-sm font-medium text-foreground">監控規則</h3>
          <p className="mt-0.5 text-xs text-muted">
            生成多種類型的演示監控規則 (個股信號/價格/市場異動/策略變更),用於測試監控中心規則列表展示。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => ruleSeedMut.mutate()}
            disabled={ruleSeedMut.isPending}
            className="flex items-center gap-1.5 rounded-btn bg-accent px-4 py-1.5 text-sm font-medium text-base hover:bg-accent/90 disabled:opacity-50 cursor-pointer"
          >
            {ruleSeedMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
            生成演示規則
          </button>
        </div>
      </div>

      <div className="rounded-btn border border-border/40 bg-surface/40 p-4 text-xs leading-relaxed text-muted">
        <div className="mb-1 font-medium text-secondary">使用說明</div>
        <ul className="list-disc space-y-0.5 pl-4">
          <li>勾選「時間戳設為剛剛」後,切到其他頁面再回監控中心,新記錄會閃爍高亮</li>
          <li>生成後菜單「監控中心」會出現紅色未讀徽標</li>
          <li>數據覆蓋策略/信號/價格/市場異動四種來源</li>
          <li>清空操作不可撤銷</li>
        </ul>
      </div>
    </div>
  )
}

// ── 封單監控模擬觸發 ──────────────────────────────────
function LadderTestPanel() {
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.monitorRuleTestLadder>> | null>(null)
  const [error, setError] = useState('')
  const [pushMsg, setPushMsg] = useState('')

  const testMut = useMutation({
    mutationFn: () => api.monitorRuleTestLadder(),
    onSuccess: (data) => { setResult(data); setError('') },
    onError: (e: any) => { setError(e?.message ?? String(e)); setResult(null) },
  })

  const triggerMut = useMutation({
    mutationFn: () => api.monitorRuleTriggerLadder(),
    onSuccess: (data) => {
      setPushMsg(`✅ 已真實觸發 ${data.triggered} 條預警 (落盤 + 外部推播 + SSE)`)
      setTimeout(() => setPushMsg(''), 6000)
    },
    onError: (e: any) => {
      setPushMsg(`❌ 觸發失敗: ${e?.message ?? String(e)}`)
      setTimeout(() => setPushMsg(''), 6000)
    },
  })

  const fmtVal = (v: number | null | undefined, metric: string) => {
    if (v == null) return '—'
    if (metric === 'sealed_amount') return `${(v / 1e8).toFixed(4)} 億`
    return `${v.toLocaleString()} 手`
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-foreground">封單監控模擬觸發</h2>
        <p className="mt-1 text-xs text-muted">
          用當前 depth 封單數據 + 最新日 enriched, 評估所有 ladder 規則。
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3 rounded-btn bg-elevated p-4">
        <button
          onClick={() => testMut.mutate()}
          disabled={testMut.isPending}
          className="flex items-center gap-1.5 rounded-btn bg-accent px-4 py-1.5 text-sm font-medium text-base hover:bg-accent/90 disabled:opacity-50 cursor-pointer"
        >
          {testMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
          模擬觸發
        </button>
        <button
          onClick={() => {
            if (confirm('將真實送出外部推播 + 寫入監控中心 + 觸發 SSE 通知。確認?')) {
              triggerMut.mutate()
            }
          }}
          disabled={triggerMut.isPending}
          className="flex items-center gap-1.5 rounded-btn border border-amber-400/40 bg-amber-400/10 px-4 py-1.5 text-sm font-medium text-amber-400 hover:bg-amber-400/20 disabled:opacity-50 cursor-pointer"
        >
          {triggerMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
          真實觸發預警
        </button>
        {result && (
          <span className="text-xs text-muted">
            日期 {result.as_of} · 封單股 {result.sealed_count} · 觸發 {result.triggered.length} · 未觸發 {result.not_triggered.length}
          </span>
        )}
      </div>

      <div className="rounded-btn border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-muted">
        <span className="font-medium text-amber-400">模擬觸發</span>:純條件判斷,不落盤不推送。
        <span className="font-medium text-amber-400 ml-2">真實觸發</span>:走完整鏈路(落盤 alerts.jsonl + 外部推播 + SSE 通知),會在監控中心和已設定的渠道看到預警。
      </div>

      {pushMsg && (
        <div className="rounded-btn border border-accent/40 bg-accent/10 p-3 text-sm text-accent">{pushMsg}</div>
      )}

      {error && (
        <div className="flex items-center gap-2 rounded-btn border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {result && result.triggered.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-emerald-400">✅ 會觸發 ({result.triggered.length})</h3>
          {result.triggered.map((ev) => (
            <div key={ev.rule_id} className="rounded-btn border border-emerald-400/30 bg-emerald-400/5 p-3 text-sm">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-medium text-foreground">{ev.symbol}</span>
                {ev.name && <span className="text-secondary">{ev.name}</span>}
                <span className="ml-auto rounded bg-emerald-400/15 px-1.5 py-0.5 text-xs text-emerald-400">{ev.type}</span>
              </div>
              <div className="text-xs text-secondary">{ev.message}</div>
              <div className="mt-1 text-xs text-muted tabular-nums">
                當前封單: {fmtVal(ev.sealed_metric === 'sealed_amount' ? ev.current_sealed_amount : ev.current_sealed_vol, ev.sealed_metric)}
              </div>
            </div>
          ))}
        </div>
      )}

      {result && result.not_triggered.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-muted">⚪ 未觸發 ({result.not_triggered.length})</h3>
          {result.not_triggered.map((r) => (
            <div key={r.rule_id} className="rounded-btn border border-border bg-surface/40 p-3 text-sm">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-medium text-foreground">{r.symbol}</span>
                <span className="text-secondary text-xs">{r.rule_name}</span>
              </div>
              <div className="text-xs text-muted tabular-nums">
                閾值 {fmtVal(r.threshold, r.metric)} · 當前 {fmtVal(r.current_value, r.metric)} · {r.reason}
              </div>
            </div>
          ))}
        </div>
      )}

      {result && result.triggered.length === 0 && result.not_triggered.length === 0 && (
        <div className="rounded-btn border border-border bg-surface/40 p-4 text-center text-sm text-muted">
          無 ladder 監控規則,請先在連板梯隊頁設置封單監控
        </div>
      )}
    </div>
  )
}

// ── Dev 主頁面 ────────────────────────────────────────
export function Dev() {
  const [tab, setTab] = useState<'minute' | 'seed' | 'ladder'>('seed')

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="系統工具"
        subtitle="數據診斷與預警調試"
        right={
          <div className="flex items-center gap-1 rounded-btn bg-elevated p-0.5">
            <button
              onClick={() => setTab('seed')}
              className={cn(
                'inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer',
                tab === 'seed' ? 'bg-surface text-foreground shadow-sm' : 'text-muted hover:text-secondary',
              )}
            >
              <FlaskConical className="h-3.5 w-3.5" />演示數據
            </button>
            <button
              onClick={() => setTab('ladder')}
              className={cn(
                'inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer',
                tab === 'ladder' ? 'bg-surface text-foreground shadow-sm' : 'text-muted hover:text-secondary',
              )}
            >
              <Bell className="h-3.5 w-3.5" />封單監控
            </button>
            <button
              onClick={() => setTab('minute')}
              className={cn(
                'inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer',
                tab === 'minute' ? 'bg-surface text-foreground shadow-sm' : 'text-muted hover:text-secondary',
              )}
            >
              <Activity className="h-3.5 w-3.5" />分鐘K探測
            </button>
          </div>
        }
      />
      <div className="flex-1 overflow-auto px-5 py-4">
        <div className="mx-auto max-w-3xl space-y-4">
          {tab === 'minute' ? <MinuteProbePanel /> : tab === 'ladder' ? <LadderTestPanel /> : <SeedPanel />}
        </div>
      </div>
    </div>
  )
}
