import { useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Activity, ArrowUpRight, BellRing, Database, Eye, Layers, Loader2, TrendingUp } from 'lucide-react'
import { api, type AlertEvent, type IndustryMetrics } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { fmtBigNum, fmtPct } from '@/lib/format'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { cn } from '@/lib/cn'
import { cnSignal } from '@/lib/signals'
import { strategyEventMeta, strategyName } from '@/lib/strategyMonitorEvents'
import { boardTag } from '@/components/stock-table/primitives'

function n(v: number | null | undefined) {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function fmtPrice(v: number | null | undefined, digits = 2) {
  const x = n(v)
  return x == null ? '—' : x.toFixed(digits)
}

function fmtStockPct(v: number | null | undefined) {
  const x = n(v)
  if (x == null) return '—'
  return `${x >= 0 ? '+' : ''}${(x * 100).toFixed(2)}%`
}

function pctClass(v: number | null | undefined) {
  const x = n(v)
  if (x == null || x === 0) return 'text-muted'
  return x > 0 ? 'text-bull' : 'text-bear'
}

function SectionTitle({ icon: Icon, title, hint }: { icon: typeof Activity; title: string; hint?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between gap-2">
      <div className="flex items-center gap-1.5">
        <span className="h-3 w-0.5 rounded-full bg-gradient-to-b from-accent to-accent/30" />
        <Icon className="h-3.5 w-3.5 text-accent" />
        <h2 className="text-xs font-semibold text-foreground">{title}</h2>
      </div>
      {hint && <span className="font-mono text-[10px] text-muted">{hint}</span>}
    </div>
  )
}

// 看板監控中心小組件 — 顯示前 10 條觸發記錄 + 更多按鈕
const _SOURCE_BADGE: Record<string, string> = {
  strategy: 'bg-amber-400/10 text-amber-400',
  signal: 'bg-accent/10 text-accent',
  price: 'bg-emerald-400/10 text-emerald-400',
  market: 'bg-purple-500/10 text-purple-400',
  sector: 'bg-cyan-500/10 text-cyan-700 dark:text-cyan-300',
}
const _SOURCE_LABEL: Record<string, string> = {
  strategy: '策略', signal: '訊號', price: '價格', market: '異動', sector: '板塊',
}
const _SEVERITY_BAR: Record<string, string> = {
  info: 'bg-accent/40', warn: 'bg-warning', critical: 'bg-danger',
}

function MonitorWidget({ onStockClick }: { onStockClick: (event: AlertEvent) => void }) {
  const navigate = useNavigate()
  const alerts = useQuery({
    queryKey: ['alerts', ''],
    queryFn: () => api.alertsList({ days: 7, limit: 10 }),
    refetchInterval: 10000,
  })
  const events: AlertEvent[] = alerts.data?.alerts ?? []

  if (events.length === 0) {
    return (
      <div className="mt-1 py-6 text-center text-[11px] text-muted">暫無觸發記錄</div>
    )
  }

  return (
    <>
      <div className="mt-1 space-y-1.5">
        {events.map((ev, i) => {
          const sev = _SEVERITY_BAR[ev.severity ?? 'info'] ?? _SEVERITY_BAR.info
          const pct = ev.change_pct ?? 0
          const isStrategy = ev.source === 'strategy'
          const isSector = ev.source === 'sector'
          const sname = isStrategy ? strategyName(ev.message ?? '') : ''
          const eventMeta = strategyEventMeta(ev.type)
          return (
            <motion.div
              key={`${ev.ts}-${i}`}
              initial={{ opacity: 0, y: -8, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ duration: 0.3, delay: Math.min(i * 0.03, 0.3) }}
              className="relative overflow-hidden rounded-md border border-border/40 bg-surface/60 pl-2.5 pr-2 py-1.5 hover:border-border hover:bg-surface transition-colors"
            >
              <div className={cn('absolute left-0 top-0 h-full w-0.5', sev)} />
              {/* 第一行: 代碼 + 名稱 + 價格 + 漲跌幅 (點擊代碼/名稱彈日K) */}
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => isSector ? navigate('/monitor') : ev.symbol && onStockClick(ev)}
                  title={isSector ? '在監控中心查看板塊告警' : ev.symbol ? `查看 ${ev.symbol} 日K` : undefined}
                  className={`inline-flex items-center gap-1 min-w-0 shrink-0 rounded hover:bg-elevated/60 transition-colors -mx-0.5 px-0.5 ${isSector || ev.symbol ? 'cursor-pointer' : 'cursor-default'}`}
                >
                  <span className="font-mono text-[10px] font-medium text-foreground/80 hover:text-accent">{ev.symbol?.replace(/\.(SH|SZ|BJ)$/, '')}</span>
                  {ev.symbol && (() => {
                    const board = boardTag(ev.symbol)
                    return board && (
                      <span className={`inline-flex items-center justify-center h-3 w-3 rounded text-[7px] font-bold leading-none border ${board.color}`}>
                        {board.label}
                      </span>
                    )
                  })()}
                  {ev.name && <span className="text-[10px] text-secondary truncate max-w-[5rem] hover:text-foreground">{ev.name}</span>}
                </button>
                <span className="flex-1" />
                {ev.price != null && (
                  <span className="text-[10px] font-mono text-foreground/60 shrink-0">{fmtPrice(ev.price)}</span>
                )}
                {ev.change_pct != null && (
                  <span className={cn('text-[10px] font-mono font-medium shrink-0 w-12 text-right', pct >= 0 ? 'text-danger' : 'text-bear')}>
                    {fmtPct(pct)}
                  </span>
                )}
              </div>
              {/* 第二行: 策略類型走新格式, 其他走舊格式 */}
              {isStrategy ? (
                <>
                  {ev.symbol ? (
                    <div className="mt-0.5 flex min-w-0 items-center gap-1.5">
                      <span className={cn('shrink-0 text-[9px] font-medium', eventMeta.className)}>
                        {eventMeta.action}
                      </span>
                      {sname
                        ? <span className="truncate text-[9px] font-medium text-amber-400">「{sname}」</span>
                        : ev.message && <span className="truncate text-[9px] text-muted">{ev.message}</span>}
                      <span className="flex-1" />
                      <span className="text-[8px] text-muted/50 shrink-0 font-mono">
                        {ev.ts ? new Date(ev.ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}
                      </span>
                    </div>
                  ) : (
                    <div className="mt-0.5 flex min-w-0 items-center gap-1.5">
                      <span className="truncate text-[9px] text-muted">{ev.message}</span>
                      <span className="flex-1" />
                      <span className="text-[8px] text-muted/50 shrink-0 font-mono">
                        {ev.ts ? new Date(ev.ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}
                      </span>
                    </div>
                  )}
                  {ev.signals && ev.signals.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {ev.signals.map(signal => (
                        <span key={signal} className="rounded bg-accent/8 px-1 py-px text-[8px] text-accent/80">{cnSignal(signal)}</span>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div className="mt-0.5 flex items-center gap-1.5">
                    <span className={cn('shrink-0 rounded px-1 py-px text-[8px] font-medium', _SOURCE_BADGE[ev.source] ?? 'bg-elevated text-muted')}>
                      {_SOURCE_LABEL[ev.source] ?? ev.source}
                    </span>
                    {ev.message && (
                      <span className="text-[9px] text-muted truncate flex-1">{ev.message}</span>
                    )}
                    <span className="text-[8px] text-muted/50 shrink-0 font-mono">
                      {ev.ts ? new Date(ev.ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}
                    </span>
                  </div>
                  {ev.signals && ev.signals.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {ev.signals.map((s, j) => (
                        <span key={j} className="rounded bg-accent/8 px-1 py-px text-[8px] text-accent/80">{cnSignal(s)}</span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </motion.div>
          )
        })}
      </div>
    </>
  )
}

function MiniMetric({ label, value, cls = 'text-foreground' }: { label: string; value: string; cls?: string }) {
  return (
    <div className="rounded-md bg-elevated/45 px-2 py-1.5 border border-border/40">
      <div className="text-[10px] text-muted">{label}</div>
      <div className={`mt-0.5 font-mono text-xs font-semibold ${cls}`}>{value}</div>
    </div>
  )
}

// ===== Phase 8C-B: 台股市場強弱摘要 — 首頁第一層資訊 =====
// 直接重用既有 /api/taiwan/market-intelligence (api.taiwanMarketIntelligence),
// 與 TaiwanScreener 同一份 API、同一 query key, 不新增 backend 邏輯/計算。
function taiwanStrengthLabel(advanceRatio: number | null): '偏強' | '中性' | '偏弱' | null {
  // 門檻僅由既有 advance/decline/flat 家數計算, 非新研究結論: ≥55% 偏強,
  // ≤45% 偏弱, 其餘中性 —— 對稱、可重現的既有數值判讀。
  if (advanceRatio == null) return null
  if (advanceRatio >= 0.55) return '偏強'
  if (advanceRatio <= 0.45) return '偏弱'
  return '中性'
}

function TaiwanBreadthBar({ advance, decline, flat }: { advance: number; decline: number; flat: number }) {
  const total = Math.max(advance + decline + flat, 1)
  const upW = advance / total * 100
  const downW = decline / total * 100
  const flatW = Math.max(0, 100 - upW - downW)
  return (
    <div className="flex h-2.5 overflow-hidden rounded-full bg-elevated" role="img" aria-label={`上漲 ${advance} 檔, 平盤 ${flat} 檔, 下跌 ${decline} 檔`}>
      <div className="bg-bull/85" style={{ width: `${upW}%` }} />
      <div className="bg-muted/45" style={{ width: `${flatW}%` }} />
      <div className="bg-bear/85" style={{ width: `${downW}%` }} />
    </div>
  )
}

function MarketStrengthCard() {
  const intel = useQuery({
    queryKey: ['taiwanMarketIntelligence'],
    queryFn: () => api.taiwanMarketIntelligence(),
    staleTime: 5 * 60 * 1000,
  })
  const totals = intel.data?.market_totals
  // 分母為 0 (全市場無漲跌平資料, 例如尚未下載當日資料) 時不可判讀強弱, ratio
  // 保持 null —— 避免把「無資料」誤判成「偏弱」而顯示假結論。
  const countedTotal = totals ? totals.advance_count + totals.decline_count + totals.flat_count : 0
  const advanceRatio = totals && countedTotal > 0 ? totals.advance_count / countedTotal : null
  // DAILY_USE_CORE_UX_FIXES (P1-1): 與 TaiwanScreener 同一份 data_quality 欄位
  // (overall_status !== 'complete' 時下方漲/平/跌/成交額可能全部是 0、也可能
  // 只是部分到位 —— 兩種情形都不該讓「偏強/偏弱/中性」這類 deterministic 結論
  // 顯示出來)。沿用 TaiwanScreener 既有判斷條件,不新增第二套完整性計算;只是
  // 把同一個既有 badge 樣式語意搬來這裡。
  const dataIncomplete = intel.data ? intel.data.data_quality?.overall_status !== 'complete' : false
  const label = dataIncomplete ? null : taiwanStrengthLabel(advanceRatio)

  return (
    <section className="mb-1.5 rounded-card border border-border bg-surface/80 p-2.5 shadow-[0_1px_2px_hsl(var(--border)/0.4)] backdrop-blur-sm">
      <SectionTitle icon={TrendingUp} title="今日市場強弱" hint={intel.data ? `交易日 ${intel.data.trade_date}` : undefined} />
      {intel.isLoading ? (
        <div className="flex items-center gap-2 py-4 text-xs text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />正在讀取市場強弱資料…
        </div>
      ) : intel.isError || !totals ? (
        <p className="py-4 text-xs text-muted">目前無法讀取市場強弱資料,不影響其他功能使用。</p>
      ) : (
        <>
          {dataIncomplete && (
            <div className="mb-1.5 rounded border border-warning/40 bg-warning/10 px-2 py-1 text-[11px] text-warning">
              今日市場資料尚未完整，以下統計僅供參考
              {intel.data?.data_quality?.previous_trade_date && (
                <>；前一交易日：{intel.data.data_quality.previous_trade_date}</>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span className="text-bull">漲 <span className="font-mono font-semibold">{totals.advance_count}</span></span>
            <span className="text-muted">平 <span className="font-mono">{totals.flat_count}</span></span>
            <span className="text-bear">跌 <span className="font-mono font-semibold">{totals.decline_count}</span></span>
            {label && (
              <span className={cn(
                'rounded-full border px-2 py-0.5 text-[10px] font-medium',
                label === '偏強' ? 'border-bull/40 bg-bull/10 text-bull' : label === '偏弱' ? 'border-bear/40 bg-bear/10 text-bear' : 'border-border bg-elevated text-muted',
              )}>
                {label}
              </span>
            )}
          </div>
          <div className="mt-2">
            <TaiwanBreadthBar advance={totals.advance_count} decline={totals.decline_count} flat={totals.flat_count} />
          </div>
          <div className="mt-2 grid grid-cols-3 gap-1.5">
            <MiniMetric
              label="漲停 / 跌停"
              value={`${totals.upper_limit_count} / ${totals.lower_limit_count}`}
              cls={totals.upper_limit_count >= totals.lower_limit_count ? 'text-bull' : 'text-bear'}
            />
            <MiniMetric label="成交額" value={fmtBigNum(totals.turnover)} />
            {intel.data && intel.data.institutional.foreign_net != null ? (
              <MiniMetric
                label="外資買賣超"
                value={`${intel.data.institutional.foreign_net > 0 ? '+' : ''}${(intel.data.institutional.foreign_net / 1000).toLocaleString()} 張`}
                cls={pctClass(intel.data.institutional.foreign_net)}
              />
            ) : (
              <MiniMetric label="外資買賣超" value="—" />
            )}
          </div>
        </>
      )}
    </section>
  )
}

// ===== Phase 8C-B: 台股產業強弱摘要 — 只顯示 Top/Bottom, 不做完整 34 檔表格 =====
// 直接重用既有 /api/taiwan/industry-intelligence (api.taiwanIndustryIntelligence),
// 與 TaiwanScreener 預設排序 (turnover/desc) 同一份 query key, 排名於前端依
// average_change_pct 對既有資料排序, 不新增 backend 計算。此 API 回傳的
// industry 欄位本身已是可讀產業名稱 (34 大類股中文名), 非數字代碼, 無需額外
// mapping(Phase 8C-C 已於 backend 修正 TPEx/TWSE 數字代碼正規化)。
function IndustryStrengthList({ title, rows, tone }: { title: string; rows: IndustryMetrics[]; tone: 'bull' | 'bear' }) {
  return (
    <div className="min-w-0 space-y-1">
      <div className={`text-[10px] font-medium ${tone === 'bull' ? 'text-bull' : 'text-bear'}`}>{title}</div>
      {rows.map(ind => (
        <div key={ind.industry} className="flex items-center justify-between gap-2 rounded-md bg-elevated/40 px-2 py-1 text-[11px]">
          <span className="truncate text-foreground" title={ind.industry}>{ind.industry}</span>
          <span className={`shrink-0 font-mono font-semibold ${pctClass(ind.average_change_pct)}`}>{fmtStockPct(ind.average_change_pct)}</span>
        </div>
      ))}
      {rows.length === 0 && <div className="rounded border border-dashed border-border py-3 text-center text-[11px] text-muted">暫無資料</div>}
    </div>
  )
}

function IndustryStrengthCard() {
  const ind = useQuery({
    queryKey: ['taiwanIndustryIntelligence', 'turnover', 'desc'],
    queryFn: () => api.taiwanIndustryIntelligence({ sort_by: 'turnover', order: 'desc' }),
    staleTime: 5 * 60 * 1000,
  })
  const comparable = (ind.data?.industries ?? []).filter(i => i.average_change_pct != null)
  const sorted = [...comparable].sort((a, b) => (b.average_change_pct ?? 0) - (a.average_change_pct ?? 0))
  const topCount = Math.min(5, sorted.length)
  const bottomCount = Math.min(5, Math.max(0, sorted.length - topCount))
  const top = sorted.slice(0, topCount)
  const bottom = sorted.slice(sorted.length - bottomCount).reverse()

  return (
    <section className="rounded-card border border-border bg-surface/80 p-2.5 shadow-[0_1px_2px_hsl(var(--border)/0.4)] backdrop-blur-sm">
      <SectionTitle icon={Layers} title="產業強弱" hint={ind.data ? `${ind.data.industries.length} 大類股` : undefined} />
      {ind.isLoading ? (
        <div className="flex items-center gap-2 py-4 text-xs text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />正在讀取產業資料…
        </div>
      ) : ind.isError || !ind.data ? (
        <p className="py-4 text-xs text-muted">目前無法讀取產業強弱資料,不影響其他功能使用。</p>
      ) : sorted.length === 0 ? (
        <p className="py-4 text-xs text-muted">目前尚無可比較的產業資料。</p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <IndustryStrengthList title="最強" rows={top} tone="bull" />
          <IndustryStrengthList title="最弱" rows={bottom} tone="bear" />
        </div>
      )}
    </section>
  )
}

// ===== Phase 8C-B: 自選股快覽 — 只顯示 3-5 檔值得注意的標的 =====
// 重用既有 /api/watchlist/enriched (api.watchlistEnriched) 與 Watchlist 頁面
// 相同的 query key 慣例 (無 ext columns 時皆為空字串), 未加自選時不顯示大空表,
// 改為簡短 empty state + CTA。點擊標的重用既有 StockPreviewDialog 動作流程
// (與 Phase 8C-A 一致), 不做第二套 stock action UI。
function WatchlistQuickGlance({ onStockClick }: { onStockClick: (symbol: string, name?: string) => void }) {
  const enriched = useQuery({
    queryKey: QK.watchlistEnriched(''),
    queryFn: () => api.watchlistEnriched(''),
    staleTime: 30_000,
  })
  const rows: any[] = enriched.data?.rows ?? []
  // 依既有資料的漲跌幅絕對值排序, 找出今天值得注意的標的; 資料缺漲跌幅時
  // (abs 視為 0) 排序穩定, 自然退回既有自選順序 —— 不發明新排名邏輯。
  const attention = [...rows]
    .sort((a, b) => Math.abs(b.change_pct ?? 0) - Math.abs(a.change_pct ?? 0))
    .slice(0, 5)

  return (
    <section className="rounded-card border border-border bg-surface/80 p-2.5 shadow-[0_1px_2px_hsl(var(--border)/0.4)] backdrop-blur-sm">
      <SectionTitle icon={Eye} title="自選股動態" hint={rows.length ? `${rows.length} 檔` : undefined} />
      {enriched.isLoading ? (
        <div className="flex items-center gap-2 py-4 text-xs text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />正在讀取自選股資料…
        </div>
      ) : enriched.isError ? (
        <p className="py-4 text-xs text-muted">目前無法讀取自選股資料,不影響其他功能使用。</p>
      ) : rows.length === 0 ? (
        <div className="py-4 text-center">
          <p className="text-xs text-secondary">尚未加入任何自選股</p>
          <Link to="/watchlist" className="mt-1.5 inline-block text-[11px] text-accent hover:text-accent/80 transition-colors">
            前往自選股 →
          </Link>
        </div>
      ) : (
        <div className="space-y-1">
          {attention.map(r => (
            <button
              key={r.symbol}
              type="button"
              onClick={() => onStockClick(r.symbol, r.name ?? undefined)}
              aria-label={`查看 ${r.name || r.symbol} 走勢`}
              className="flex w-full items-center justify-between gap-2 rounded-md bg-elevated/40 px-2 py-1.5 text-left hover:bg-elevated hover:brightness-110 transition-colors border border-transparent hover:border-border/60"
            >
              <div className="min-w-0">
                <div className="truncate text-[11px] text-foreground">{r.name || r.symbol}</div>
                <div className="font-mono text-[9px] text-muted">{r.symbol}</div>
              </div>
              <div className="text-right shrink-0">
                <div className="font-mono text-[11px] text-foreground">{fmtPrice(r.close)}</div>
                <div className={`font-mono text-[10px] font-semibold ${pctClass(r.change_pct)}`}>{fmtStockPct(r.change_pct)}</div>
              </div>
            </button>
          ))}
        </div>
      )}
    </section>
  )
}

// ===== Phase 8B-2: 台股資料狀態卡 =====
// 只讀既有 /api/taiwan/data-status(與 Onboarding 台股資料狀態步驟同一個 API,
// 不建立重複 backend 邏輯)。沒有資料時顯示清楚的繁體 empty state, 不假造台股
// 指數或任何資料。Phase 8C-B: 不再是首頁第一眼內容, 改列於監控中心之後的最下層
// (資料新鮮度)。Phase 8C-D: 中國 A 股 legacy Dashboard 區塊已隨產品介面整體
// 移除, 此卡不再有任何 legacy 開關依賴。
const TAIWAN_FRESHNESS_LABEL: Record<string, string> = {
  current: '最新',
  stale: '過期',
  unavailable: '尚無資料',
}

function TaiwanOverviewCard() {
  const status = useQuery({
    queryKey: QK.taiwanDataStatus,
    queryFn: api.taiwanDataStatus,
    staleTime: 60_000,
  })

  const rows = status.data
    ? [
        { label: '日K', asOf: status.data.daily_as_of, freshness: status.data.daily_status },
        { label: '三大法人', asOf: status.data.institutional_as_of, freshness: status.data.institutional_status },
        { label: '融資融券', asOf: status.data.margin_as_of, freshness: status.data.margin_status },
      ]
    : []
  const hasAnyData = rows.some(r => r.asOf)

  return (
    <section className="mb-3 rounded-card border border-border bg-surface/85 p-3.5">
      <div className="flex items-center justify-between gap-2 mb-2.5">
        <div className="flex items-center gap-1.5">
          <Database className="h-3.5 w-3.5 text-accent" />
          <h2 className="text-xs font-semibold text-foreground">台股資料狀態</h2>
        </div>
        <div className="flex items-center gap-3 text-[11px]">
          <Link to="/taiwan-screener" className="text-secondary hover:text-accent transition-colors">台股選股</Link>
          <Link to="/stocks/compare" className="text-secondary hover:text-accent transition-colors">多股比較</Link>
          <Link to="/watchlist" className="text-secondary hover:text-accent transition-colors">自選股</Link>
        </div>
      </div>

      {status.isLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          正在讀取台股資料狀態…
        </div>
      ) : status.isError ? (
        <p className="text-xs text-muted leading-relaxed">
          目前無法讀取台股資料狀態,不影響其他功能使用,請稍後再試。
        </p>
      ) : hasAnyData ? (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
          {rows.map(r => (
            <div key={r.label} className="flex items-center gap-1.5 text-xs">
              <span className="text-secondary">{r.label}</span>
              <span className="font-mono text-muted">{r.asOf ?? '—'}</span>
              <span className="text-[11px] font-medium text-muted">
                {TAIWAN_FRESHNESS_LABEL[r.freshness] ?? r.freshness}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-xs text-secondary leading-relaxed">
          目前尚未下載台股資料,仍可先使用「台股選股」「多股比較」等功能,資料將於每日排程自動更新。
        </p>
      )}
    </section>
  )
}

export function Dashboard() {
  const [previewStock, setPreviewStock] = useState<{symbol: string; name?: string; alert?: AlertEvent} | null>(null)

  return (
    <div className="min-h-full bg-base p-1.5">
      {/* Phase 8C-B — Dashboard Market Clarity: 首頁第一印象為「今日市場強弱」,
          其次為「產業強弱 + 自選股動態」, 監控事件摘要在下方, 台股資料狀態卡
          (資料新鮮度) 移到最下層。Phase 8C-D: 中國 A 股 legacy 大盤看板整段已
          移除產品介面, Dashboard 全站僅剩台股內容, 不再有任何 legacy 開關。 */}
      <MarketStrengthCard />

      <div className="mb-1.5 grid grid-cols-1 gap-1.5 lg:grid-cols-2">
        <IndustryStrengthCard />
        <WatchlistQuickGlance onStockClick={(symbol, name) => setPreviewStock({ symbol, name })} />
      </div>

      <section className="mb-1.5 rounded-card border border-border bg-surface/80 p-1.5 shadow-[0_1px_2px_hsl(var(--border)/0.4)] backdrop-blur-sm transition-shadow hover:shadow-[0_2px_8px_hsl(var(--border)/0.5)]">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <BellRing className="h-3.5 w-3.5 text-accent" />
            <h2 className="text-xs font-semibold text-foreground">監控中心</h2>
            <span className="font-mono text-[10px] text-muted">即時信號</span>
          </div>
          <Link to="/monitor" className="inline-flex items-center justify-center h-5 w-5 rounded text-muted hover:text-accent hover:bg-accent/10 transition-colors" title="進入監控中心">
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        </div>
        <MonitorWidget onStockClick={(event) => {
          if (event.symbol) setPreviewStock({ symbol: event.symbol, name: event.name ?? undefined, alert: event })
        }} />
      </section>

      {/* 台股資料狀態(資料新鮮度) — 最下層, 不再是首頁第一眼內容。 */}
      <TaiwanOverviewCard />

      <StockPreviewDialog
        symbol={previewStock?.symbol ?? null}
        name={previewStock?.name}
        triggerInfo={previewStock?.alert ? {
          price: previewStock.alert.price ?? null,
          changePct: previewStock.alert.change_pct ?? null,
          ts: previewStock.alert.ts,
          signals: previewStock.alert.signals,
          message: previewStock.alert.message,
        } : null}
        onClose={() => setPreviewStock(null)}
      />
    </div>
  )
}
