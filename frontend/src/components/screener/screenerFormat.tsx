// 台股選股結果表格的格式化工具 (台股慣例: 漲紅跌綠)。

export const formatChangePct = (val?: number | null) => {
  if (val === null || val === undefined) return '-'
  const pct = val * 100
  const text = `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`
  const color = pct > 0 ? 'text-red-500 font-medium' : pct < 0 ? 'text-emerald-500 font-medium' : 'text-zinc-400'
  return <span className={color}>{text}</span>
}

export const formatPrice = (val?: number | null) => {
  if (val === null || val === undefined) return '-'
  return val >= 1000 ? val.toFixed(0) : val.toFixed(2)
}

export const formatVolumeLots = (shares?: number | null) => {
  if (shares === null || shares === undefined) return '-'
  const lots = shares / 1000
  return `${lots.toLocaleString(undefined, { maximumFractionDigits: 0 })} 張`
}

export const formatAmount = (amt?: number | null) => {
  if (amt === null || amt === undefined) return '-'
  if (amt >= 100_000_000) {
    return `${(amt / 100_000_000).toFixed(2)} 億`
  }
  return `${(amt / 10_000).toFixed(0)} 萬`
}

// Institutional / Margin signed flow formatter (Taiwan color: >0 Red, <0 Green, null '—')
export const formatSignedSharesLots = (shares?: number | null) => {
  if (shares === null || shares === undefined) return <span className="text-zinc-500">—</span>
  const lots = Math.round(shares / 1000)
  const formatted = lots.toLocaleString(undefined)
  if (lots > 0) {
    return <span className="text-red-500 font-medium">+{formatted} 張</span>
  } else if (lots < 0) {
    return <span className="text-emerald-500 font-medium">{formatted} 張</span>
  }
  return <span className="text-zinc-400">0 張</span>
}

// Short balance formatter (neutral, null '—')
export const formatShortBalanceLots = (shares?: number | null) => {
  if (shares === null || shares === undefined) return <span className="text-zinc-500">—</span>
  const lots = Math.round(shares / 1000)
  return <span className="text-zinc-300 font-mono">{lots.toLocaleString(undefined)} 張</span>
}

// Short margin ratio formatter (10.0 = 10%, null '—')
export const formatShortMarginRatio = (ratio?: number | null) => {
  if (ratio === null || ratio === undefined) return <span className="text-zinc-500">—</span>
  return <span className="text-zinc-300 font-mono">{ratio.toFixed(2)}%</span>
}
