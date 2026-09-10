/**
 * 台股即時報價「資料新鮮度 / 來源」共用顯示邏輯。
 *
 * Data Freshness & Source Labels batch (Post-8C follow-up)：Watchlist / Monitor /
 * StockDetail header 三處即時報價已共用同一個 TaiwanRealtimeService（見
 * backend/app/taiwan/realtime/service.py），但只有 Monitor
 * (components/monitor/TaiwanQuotePanel.tsx) 原本把 source_meta 完整顯示給使用者。
 * 這裡把「來源代碼 → 可讀顯示名稱」與「is_stale/freshness_class/status → 精簡徽章」
 * 的判斷邏輯抽成單一權威實作，讓三個頁面用同一套用語，不再各自維護一份規則。
 *
 * 判斷邏輯本身完全依賴 backend 已經算好的 canonical 欄位 (is_stale /
 * freshness_class / status)，不在前端重新用 Date.now() - quote_time 猜測是否過期。
 */
import { Activity } from 'lucide-react'
import { cn } from '@/lib/cn'

/** Watchlist 用的是 TaiwanSourceMeta；StockDetail header 用的是較精簡的
 * TaiwanSectionMeta（無 freshness_class / source_type / is_realtime）。兩者都
 * 可以套用同一套判斷，缺少的欄位視為未提供即可，不強迫兩邊改用同一個後端 schema
 * （那是架構變更，本批次明確排除）。 */
export interface TaiwanQuoteMetaLike {
  source?: string | null
  source_type?: string | null
  status?: string | null
  is_stale?: boolean | null
  is_realtime?: boolean | null
  freshness_class?: string | null
  fallback_reason?: string | null
  fetched_at?: string | null
  trade_date?: string | null
}

/** 已知 provider 識別碼 → 一般使用者看得懂的來源名稱 (deterministic mapping，
 * 未知來源一律回傳中性描述，不亂猜)。對應
 * backend/app/taiwan/realtime/{mis_provider,yahoo_provider}.py、
 * backend/app/taiwan/enrichment/quote.py、
 * backend/app/taiwan/realtime/service.py 目前實際會產生的 source 字串。 */
const SOURCE_DISPLAY_NAMES: Record<string, string> = {
  'twse:mis': '證交所即時報價',
  'yahoo:chart': 'Yahoo 延遲報價',
  'twse:stock_day_all': '證交所官方收盤快照',
  'twse:STOCK_DAY_ALL': '證交所官方收盤快照',
  'tpex:mainboard_quotes': '櫃買中心官方收盤快照',
  'daily_kline': '本地日線資料',
  'fallback:daily_kline': '本地日線資料',
}

/** 把 provider 內部識別碼轉成使用者看得懂的中文來源名稱；未知來源顯示中性描述，
 * 不把 "yahoo:chart" 這類內部字串原樣丟給使用者，也不猜測未知值的含意。 */
export function formatQuoteSource(source: string | null | undefined): string {
  if (!source) return '其他資料來源'
  return SOURCE_DISPLAY_NAMES[source] ?? '其他資料來源'
}

/** 格式化行情時間 (HH:mm:ss)；解析失敗時原樣回傳，不偽造時間。 */
export function formatQuoteTime(timeStr: string | null | undefined, isFallback = false): string {
  if (!timeStr) return isFallback ? '上一交易日 13:30' : '時間不可用'
  try {
    const d = new Date(timeStr)
    if (isNaN(d.getTime())) return timeStr
    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')
    const ss = String(d.getSeconds()).padStart(2, '0')
    return `${hh}:${mm}:${ss}`
  } catch {
    return timeStr
  }
}

export interface FreshnessLabel {
  label: string
  tone: 'realtime' | 'delayed' | 'snapshot' | 'stale' | 'unknown'
}

/** 依 backend 已提供的 canonical 欄位 (is_stale 優先、其次 freshness_class、
 * 再其次 status) 決定精簡徽章文案；不重新計算新鮮度，只做顯示層映射。
 * 涵蓋目前後端實際會出現的所有值 (見檔頭註解列出的來源檔案)，未知值一律
 * 顯示中性的「未知來源」，不假裝成即時。 */
export function freshnessLabel(meta: TaiwanQuoteMetaLike | null | undefined): FreshnessLabel | null {
  if (!meta) return null
  // 以下判斷順序與既有 Monitor DataQualityBadge 完全一致 (逐條移植，未變更任一條件)。
  if (meta.is_stale) return { label: '資料過期', tone: 'stale' }
  if (meta.status === 'daily_fallback' || meta.source_type === 'local_store') {
    return { label: '日線備援', tone: 'snapshot' }
  }
  if (meta.freshness_class === 'delayed_15m' || (meta.freshness_class ?? '').includes('delayed')) {
    return { label: '延遲 15m', tone: 'delayed' }
  }
  if (meta.freshness_class === 'eod_snapshot') return { label: '盤後快照', tone: 'snapshot' }
  if (meta.freshness_class === 'best_effort_near_realtime') return { label: '近即時', tone: 'realtime' }
  if (meta.is_realtime || meta.status === 'realtime') return { label: '即時', tone: 'realtime' }
  // 以下是新增的圖度分支：StockDetail header 用的 TaiwanSectionMeta 沒有
  // freshness_class/source_type/is_realtime 欄位，只有可信的 is_stale。當
  // is_stale 明確為 false 但以上欄位都缺席時 (SectionMeta 的既有情形)，仍應
  // 誠實顯示「非過期」而非落入「未知來源」；真正 SourceMeta 情形不會走到這裡，
  // 因為每個 provider 都會明確設定 freshness_class 為上述四個值之一。
  if (meta.is_stale === false) return { label: '近即時', tone: 'realtime' }
  return { label: '未知來源', tone: 'unknown' }
}

const TONE_CLASS: Record<FreshnessLabel['tone'], string> = {
  realtime: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30',
  delayed: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30',
  snapshot: 'bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/30',
  stale: 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30',
  unknown: 'bg-surface text-muted border-border',
}

/** 精簡「資料新鮮度 / 來源」徽章 — Watchlist / Monitor / StockDetail header 共用，
 * 唯一權威實作。沒有 meta 時不渲染 (不顯示假的新鮮度狀態)。 */
export function DataQualityBadge({
  meta,
  quoteTime,
  className,
}: {
  meta: TaiwanQuoteMetaLike | null | undefined
  /** 報價時間 (TaiwanRealtimeQuote.quote_time，與 source_meta 平行的欄位，非其
   * 子欄位) — 選填，提供時會附進 tooltip，方便使用者知道「資料大概幾點」。 */
  quoteTime?: string | null
  className?: string
}) {
  const info = freshnessLabel(meta)
  if (!info || !meta) return null

  const tooltip = [
    `來源: ${formatQuoteSource(meta.source)}`,
    meta.freshness_class ? `等級: ${meta.freshness_class}` : null,
    quoteTime !== undefined
      ? `報價時間: ${formatQuoteTime(quoteTime, meta.status === 'daily_fallback')}`
      : `抓取時間: ${meta.fetched_at || '--'}`,
    meta.fallback_reason ? `備援原因: ${meta.fallback_reason}` : null,
  ].filter(Boolean).join('\n')

  return (
    <span
      title={tooltip}
      className={cn(
        'cursor-help inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] border',
        TONE_CLASS[info.tone],
        className,
      )}
    >
      <Activity className="h-2.5 w-2.5" />
      {info.label}
    </span>
  )
}
