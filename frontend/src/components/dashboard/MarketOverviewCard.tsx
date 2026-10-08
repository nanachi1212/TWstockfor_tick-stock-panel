import { Loader2, TrendingUp } from 'lucide-react'
import { type IndexSnapshot, type TaiwanMarketIntelligenceSnapshot } from '@/lib/api'
import { fmtBigNum } from '@/lib/format'
import { cn } from '@/lib/cn'
import { SectionTitle } from './SectionTitle'
import { pctClass, fmtStockPct } from './dashboardFormat'

export function MiniMetric({ label, value, cls = 'text-foreground' }: { label: string; value: string; cls?: string }) {
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

export function taiwanStrengthLabel(advanceRatio: number | null): '偏強' | '中性' | '偏弱' | null {
  // 門檻僅由既有 advance/decline/flat 家數計算, 非新研究結論: ≥55% 偏強,
  // ≤45% 偏弱, 其餘中性 —— 對稱、可重現的既有數值判讀。
  if (advanceRatio == null) return null
  if (advanceRatio >= 0.55) return '偏強'
  if (advanceRatio <= 0.45) return '偏弱'
  return '中性'
}

export function TaiwanBreadthBar({ advance, decline, flat }: { advance: number; decline: number; flat: number }) {
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

export type TaiwanDailyStatus = 'current' | 'stale' | 'unavailable'

export function MarketOverviewCard({ snapshot, loading, error, marketDailyStatus }: { snapshot?: TaiwanMarketIntelligenceSnapshot | null; loading: boolean; error: boolean; marketDailyStatus: TaiwanDailyStatus }) {
  const intel = { data: snapshot, isLoading: loading, isError: error }
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
  const dailyBreadthAvailable = Boolean(
    marketDailyStatus === 'current'
    &&
    intel.data?.data_quality.daily?.status === 'current'
    && (totals?.snapshot_row_count ?? 0) > 0,
  )
  const dailyDataIncomplete = intel.data ? !dailyBreadthAvailable : false
  const otherMarketDataIncomplete = Boolean(
    intel.data && intel.data.data_quality.overall_status !== 'complete' && dailyBreadthAvailable,
  )
  const label = dailyBreadthAvailable ? taiwanStrengthLabel(advanceRatio) : null
  const indexQuality = intel.data?.data_quality?.indexes
  const indexRows: Array<{ label: string; index: IndexSnapshot | null }> = intel.data ? [
    { label: '加權指數', index: intel.data.indexes.taiex },
    { label: '上櫃指數', index: intel.data.indexes.tpex_index },
  ] : []

  return (
    <section className="mb-1.5 rounded-card border border-accent/25 bg-surface/90 p-3 shadow-[0_1px_2px_hsl(var(--border)/0.4)] backdrop-blur-sm">
      <SectionTitle icon={TrendingUp} title="市場概況" hint={intel.data ? `資料交易日 ${intel.data.trade_date}` : undefined} />
      {intel.isLoading ? (
        <div className="flex items-center gap-2 py-4 text-xs text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />正在讀取市場強弱資料…
        </div>
      ) : intel.isError || !intel.data || !totals ? (
        <p className="py-4 text-xs text-muted">目前無法讀取市場強弱資料,不影響其他功能使用。</p>
      ) : (
        <>
          {dailyDataIncomplete && (
            <div className="mb-1.5 rounded border border-warning/40 bg-warning/10 px-2 py-1 text-[11px] text-warning">
              日行情尚未完整，漲跌與成交統計僅依目前可用資料顯示
              {intel.data.data_quality.target_trade_date > intel.data.trade_date && (
                <>；目標交易日 {intel.data.data_quality.target_trade_date} 尚未取得，以下為 {intel.data.trade_date} 資料</>
              )}
            </div>
          )}
          {marketDailyStatus !== 'current' && (
            <div className="mb-1.5 rounded border border-warning/40 bg-warning/10 px-2 py-1 text-[11px] text-warning">
              日行情狀態 {marketDailyStatus}；以下為最近可用資料，不代表今日市場強弱。
            </div>
          )}
          {otherMarketDataIncomplete && (
            <div className="mb-1.5 rounded border border-border bg-elevated/40 px-2 py-1 text-[10px] text-secondary">
              日行情覆蓋 {totals.snapshot_row_count.toLocaleString()} / {totals.supported_count.toLocaleString()} 檔；法人或融資融券資料尚未完整。
            </div>
          )}
          {intel.data.trade_date < new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei' }) && (
            <div className="mb-2 rounded border border-border bg-elevated/50 px-2 py-1 text-[11px] text-secondary">
              目前使用最近可用交易日資料（{intel.data.trade_date}）
            </div>
          )}
          <div className="mb-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {indexRows.map(({ label: indexLabel, index }) => (
              <div key={indexLabel} className="rounded-md border border-border/50 bg-elevated/35 px-2.5 py-2">
                <div className="text-[10px] text-muted">{indexLabel}</div>
                {index?.close != null && index.status !== 'unavailable' ? (
                  <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 font-mono text-xs">
                    <span className="font-semibold text-foreground">{index.close.toLocaleString('zh-TW')}</span>
                    <span className={pctClass(index.change_pct)}>{index.change == null ? '—' : `${index.change > 0 ? '+' : ''}${index.change.toLocaleString('zh-TW')}`} ({fmtStockPct(index.change_pct)})</span>
                    <span className="text-[9px] text-muted">{index.trade_date ?? '—'}</span>
                  </div>
                ) : <div className="mt-0.5 text-xs text-muted">目前無可靠指數資料</div>}
              </div>
            ))}
          </div>
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
          {totals.traded_count > 0 && label && (
            <p className="mt-2 text-[11px] text-secondary">
              {label === '偏強'
                ? `多數股票上漲（${totals.advance_count} 漲、${totals.decline_count} 跌），市場廣度偏強。`
                : label === '偏弱'
                  ? `下跌家數較多（${totals.advance_count} 漲、${totals.decline_count} 跌），市場廣度偏弱。`
                  : `漲跌家數接近（${totals.advance_count} 漲、${totals.decline_count} 跌），市場廣度中性。`}
            </p>
          )}
          {dailyBreadthAvailable && intel.data.data_quality.missing_symbols_count > 0 && (
            <p className="mt-1 text-[9px] text-muted">漲跌廣度只依當日有行情的標的計算，另有 {intel.data.data_quality.missing_symbols_count.toLocaleString()} 檔缺少行情。</p>
          )}
          <div className="mt-1 text-[9px] text-muted">
            指數資料：{indexQuality?.status ?? 'unavailable'}
            {indexQuality?.as_of ? ` · ${indexQuality.as_of}` : ''}
            {' · '}市場資料狀態：{intel.data.data_quality.overall_status}
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
