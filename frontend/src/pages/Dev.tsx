import { useState } from 'react'
import { Loader2, Search, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { api } from '@/lib/api'
import { taipeiDate } from '@/lib/format'

// ── 分鐘K探測 (遷移自 MinuteDataProbe) ─────────────────
interface ProbeResult {
  date: string
  rows: number
  source: string
  ok: boolean
}

function MinuteProbePanel() {
  const [symbol, setSymbol] = useState('2330.TWSE')
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
    const now = Date.now()
    for (let i = 0; i < days; i++) {
      dates.push(taipeiDate(now - i * 86_400_000)!)
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
        <h2 className="text-sm font-semibold text-foreground">分鐘K資料探測</h2>
        <p className="mt-1 text-xs text-muted">
          逐日呼叫 <code className="px-1 rounded bg-elevated text-secondary">/api/kline/minute</code> 介面，
          檢查台股最近若干天的分鐘K資料。歷史分鐘資料需先設定具備分鐘能力的自訂資料來源；官方公開報價不提供此歷史資料。
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-btn bg-elevated p-4">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted">股票代碼</label>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="2330.TWSE"
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
            <div className="text-xs text-muted">有資料</div>
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
                <th className="px-4 py-2 text-left font-medium">資料來源</th>
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
            缺失日期若為<span className="text-foreground">週末/國定假日</span>屬正常；
            若為<span className="text-foreground">暫停交易日</span>（成交量為 0）也屬正常；
            若為<span className="text-foreground">正常交易日</span>（日K有成交量）卻缺失分鐘K，
            請確認所選自訂來源是否提供該交易日的分鐘資料。
          </p>
        </div>
      )}
    </div>
  )
}

// ── Dev 主頁面 ────────────────────────────────────────
export function Dev() {
  return (
    <div className="flex flex-col h-full">
      <PageHeader title="系統工具" />
      <div className="flex-1 overflow-auto px-5 py-4">
        <div className="mx-auto max-w-3xl space-y-4">
          <MinuteProbePanel />
        </div>
      </div>
    </div>
  )
}
