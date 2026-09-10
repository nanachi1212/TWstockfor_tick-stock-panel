/** 迷你蠟燭圖（自選/策略列表共享）。 */
import type { KlineRow } from '@/lib/api'

export function MiniCandlestick({ rows, width = 100, height = 80 }: { rows: KlineRow[]; width?: number; height?: number }) {
  // 空數據：返回等尺寸佔位（不畫內容），保證 kline 加載前後單元格尺寸一致、不閃爍
  if (!rows || rows.length === 0) {
    return <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="block" aria-label="載入中" />
  }

  const BULL = '#C74040'
  const BEAR = '#2D9B65'
  const NEUTRAL = '#A1A1AA'

  const W = width
  const H = height
  const padY = 2
  const n = rows.length
  const barW = W / n
  const bodyW = Math.max(barW * 0.55, 2)

  let hi = -Infinity, lo = Infinity
  for (const r of rows) {
    if (r.high > hi) hi = r.high
    if (r.low < lo) lo = r.low
  }
  const range = hi - lo || 1

  const yScale = (v: number) => padY + (1 - (v - lo) / range) * (H - padY * 2)

  const rects: React.ReactNode[] = []
  const wicks: React.ReactNode[] = []

  for (let i = 0; i < n; i++) {
    const r = rows[i]
    const x = i * barW + barW / 2

    // 漲跌判斷: open !== close 用實體方向, 一字板用前日收盤計算漲跌
    let color: string
    if (r.close > r.open) {
      color = BULL
    } else if (r.close < r.open) {
      color = BEAR
    } else {
      // 一字板: open === close, 用前一日收盤價判斷漲跌方向
      const prevClose = i > 0 ? rows[i - 1].close : null
      if (prevClose && prevClose > 0 && r.close !== prevClose) {
        color = r.close > prevClose ? BULL : BEAR
      } else {
        color = NEUTRAL
      }
    }

    wicks.push(
      <line
        key={`w${i}`}
        x1={x} y1={yScale(r.high)} x2={x} y2={yScale(r.low)}
        stroke={color} strokeWidth={1}
      />
    )

    const top = yScale(Math.max(r.open, r.close))
    const bot = yScale(Math.min(r.open, r.close))
    const bodyH = Math.max(bot - top, 1)

    rects.push(
      <rect
        key={`c${i}`}
        x={x - bodyW / 2} y={top}
        width={bodyW} height={bodyH}
        fill={color}
      />
    )
  }

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block">
      {wicks}
      {rects}
    </svg>
  )
}
