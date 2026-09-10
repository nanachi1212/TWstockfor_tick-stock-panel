// capability 內部名 → 用戶能理解的中文標籤
import { useNavigate } from 'react-router-dom'

export const CAP_LABELS: Record<string, { name: string; hint: string }> = {
  'quote.by_symbol':         { name: '自選股即時監控', hint: 'Free 可按標的查詢即時行情，用於少量自選股監控' },
  'quote.batch':             { name: '即時行情(批量)',   hint: '一次取得多檔股票的價格' },
  'quote.pool':              { name: '標的池查詢',        hint: '按標的池（如成分股清單）批量取得行情' },
  'kline.daily.by_symbol':   { name: '日 K(按標的)',    hint: '單一股票歷史日 K' },
  'kline.daily.batch':       { name: '日 K(批量)',      hint: '一次取得多檔股票的日 K — 選股 / 訊號掃描所需' },
  'kline.minute.by_symbol':  { name: '分鐘 K(按標的)',  hint: '單股 1m/5m/15m/30m/60m K 線' },
  'kline.minute.batch':      { name: '分鐘 K(批量)',    hint: '多股分鐘 K' },

  'depth5':                  { name: '五檔盤口',          hint: '買賣五檔報價' },
  'depth5.batch':            { name: '五檔盤口(批量)',   hint: '批量買賣五檔快照' },
  'websocket':               { name: '即時推送(WS)',    hint: '免輪詢的即時行情訂閱' },
  'financial':               { name: '財務數據',          hint: '損益表 / 資產負債表 / 現金流量表 / 關鍵指標' },
  'adj_factor':              { name: '除權因子',          hint: '讓 MA/MACD 等指標在除權息日不失真' },
}

// ===== 數據源無關的能力提示 (所有數據源共用一套標準) =====
// 功能門檻一律以能力鍵表達, 不再出現 TickFlow 檔位詞 (檔位僅出現在 TickFlow 專屬界面)。

/** 能力鍵 → 用戶可讀能力名 */
export function capName(capKey: string): string {
  return CAP_LABELS[capKey]?.name ?? capKey
}

/** 數據不可用標準徽章: 「分鐘 K(批量) · 不可用」, 通用狀態陳述, 默認點擊跳轉 設置→數據源 (to=null 關閉跳轉) */
export function MissingCapChip({ capKey, label, to = '/settings?tab=data-sources', className = '' }: {
  capKey?: string
  label?: string
  to?: string | null
  className?: string
}) {
  const navigate = useNavigate()
  const text = label ?? (capKey != null ? capName(capKey) : '')
  const content = (
    <>
      {text ? `${text} · 不可用` : '不可用'}
    </>
  )
  if (to == null) {
    return (
      <span className={`text-[10px] text-warning/90 bg-warning/8 rounded px-1.5 py-px font-medium ${className}`} title="該資料目前不可用">
        {content}
      </span>
    )
  }
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); navigate(to) }}
      className={`text-[10px] text-warning/90 bg-warning/8 rounded px-1.5 py-px font-medium hover:bg-warning/15 transition-colors ${className}`}
      title="前往 設定 → 資料來源"
    >
      {content}
    </button>
  )
}

// 套餐等級 —— 僅用於 TickFlow 專屬界面 (Key 配置 / 端點測速 / 引導頁 tickflow 分支)。
// 通用功能門檻一律用能力鍵 (capName/needCapText/MissingCapChip), 不用檔位詞。
// 基礎檔提取與後端 quote_service.py 一致:取 label 第一個詞("Pro +" → "pro")。
// none = None 檔(無 key / 無效 key),低於 free,僅歷史日K無實時行情。
export const TIER_RANK: Record<string, number> = { none: -1, free: 0, starter: 1, pro: 2, expert: 3 }
export const EXPERT_RANK = TIER_RANK.expert

export function tierRank(label: string): number {
  const base = (label.split(' ')[0] ?? '').split('+')[0].trim().toLowerCase()
  return TIER_RANK[base] ?? -1
}

export function isExpertOrAbove(label: string): boolean {
  return tierRank(label) >= EXPERT_RANK
}

/** 檔位完整樣式(tag 背景 + 圓點 + 文字漸變), 與左側菜單 TierBadge 一致 */
export interface TierStyle {
  tagBg: { background: string }
  dotStyle: { background: string }
  labelTextStyle: { color?: string; background?: string; WebkitBackgroundClip?: string; backgroundClip?: string }
  desc: string
}

const TIER_STYLE: Record<string, TierStyle> = {
  none: {
    desc: '未配置 Key · 僅歷史日K',
    tagBg: { background: 'rgba(113,113,122,0.15)' },
    dotStyle: { background: '#52525b' },
    labelTextStyle: { color: '#71717a' },
  },
  free: {
    desc: '歷史日K · 自選即時',
    tagBg: { background: 'rgba(113,113,122,0.3)' },
    dotStyle: { background: '#71717a' },
    labelTextStyle: { color: '#a1a1aa' },
  },
  starter: {
    desc: '除權因子 · 全市場即時',
    tagBg: { background: 'rgba(59,130,246,0.2)' },
    dotStyle: { background: '#3b82f6' },
    labelTextStyle: { color: '#60a5fa' },
  },
  pro: {
    desc: '分鐘K · 盤口',
    tagBg: { background: 'linear-gradient(135deg, rgba(168,85,247,0.2), rgba(124,58,237,0.15))' },
    dotStyle: { background: 'linear-gradient(135deg, #a855f7, #7c3aed)' },
    labelTextStyle: { background: 'linear-gradient(135deg, #c084fc, #a855f7)', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' },
  },
  expert: {
    desc: 'WebSocket · 財務數據',
    tagBg: { background: 'linear-gradient(135deg, rgba(59,130,246,0.2), rgba(168,85,247,0.2), rgba(245,158,11,0.2))' },
    dotStyle: { background: 'linear-gradient(135deg, #3b82f6, #a855f7, #f59e0b)' },
    labelTextStyle: { background: 'linear-gradient(135deg, #60a5fa, #c084fc, #fbbf24)', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' },
  },
}

/** 從檔位 label 提取基礎檔位名(小寫): "Expert +" → "expert" */
export function tierBaseName(label: string): string {
  return (label.split(' ')[0] ?? '').split('+')[0].trim().toLowerCase()
}

/** 返回檔位完整樣式 */
export function tierStyle(label: string): TierStyle {
  return TIER_STYLE[tierBaseName(label)] ?? TIER_STYLE.free
}

/** 所有檔位(有序, 供檔位列表渲染) */
export const ALL_TIERS = ['none', 'free', 'starter', 'pro', 'expert'] as const

/** 返回檔位標籤的漸變文字樣式(用於大字顯示, 如 Keys 頁檔位) */
export function tierTextStyle(label: string): { color?: string; background?: string; WebkitBackgroundClip?: string; backgroundClip?: string } {
  return tierStyle(label).labelTextStyle
}

/** 渲染檔位 tag(與左側菜單一致的膠囊樣式) */
export function TierTag({ label, className = '' }: { label: string; className?: string }) {
  const t = tierStyle(label)
  const base = tierBaseName(label)
  // none 檔顯示英文「None」,其餘檔顯示英文檔名
  const display = base === 'none' ? 'None' : base
  return (
    <span
      className={`inline-flex h-[18px] max-w-[80px] shrink-0 items-center overflow-hidden rounded px-1.5 text-[10px] font-bold font-mono leading-none ${className}`}
      style={t.tagBg}
    >
      <span className="truncate capitalize" style={t.labelTextStyle}>{display}</span>
    </span>
  )
}

