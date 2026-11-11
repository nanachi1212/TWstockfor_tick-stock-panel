// 台股選股結果表格的格式化工具 (台股慣例: 漲紅跌綠)。數字格式一律走 @/lib/format。
import { fmtBigNum, fmtLots, fmtPct, fmtPrice, priceColorClass } from '@/lib/format'

export const formatChangePct = (val?: number | null) => {
  if (val == null) return '-'
  return <span className={`font-medium ${priceColorClass(val)}`}>{fmtPct(val)}</span>
}

export const formatPrice = (val?: number | null) => {
  if (val == null) return '-'
  return fmtPrice(val, val >= 1000 ? 0 : 2)
}

export const formatVolumeLots = (shares?: number | null) => {
  if (shares == null) return '-'
  return fmtLots(shares)
}

export const formatAmount = (amt?: number | null) => {
  if (amt == null) return '-'
  return fmtBigNum(amt)
}

// Institutional / Margin signed flow formatter (Taiwan color: >0 Red, <0 Green, null '—')
export const formatSignedSharesLots = (shares?: number | null) => {
  if (shares == null) return <span className="text-zinc-500">—</span>
  return <span className={`font-medium ${priceColorClass(shares)}`}>{fmtLots(shares, true)}</span>
}

// Short balance formatter (neutral, null '—')
export const formatShortBalanceLots = (shares?: number | null) => {
  if (shares == null) return <span className="text-zinc-500">—</span>
  return <span className="text-zinc-300 font-mono">{fmtLots(shares)}</span>
}

// Short margin ratio formatter (10.0 = 10%, null '—')
export const formatShortMarginRatio = (ratio?: number | null) => {
  if (ratio == null) return <span className="text-zinc-500">—</span>
  return <span className="text-zinc-300 font-mono">{ratio.toFixed(2)}%</span>
}
