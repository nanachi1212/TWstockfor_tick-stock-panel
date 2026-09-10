/** 迷你分時折線圖（自選列表共享）。

用當日分鐘K的 close 畫一條折線 + 昨收水平基準線 + 分時均線。
風格仿同花順/東方財富分時圖：
- 價格折線：漲（收盤 ≥ 昨收）紅色，跌綠色 — 以昨收價(prevClose)為基準, 不是當日開盤價
- 昨收基準線：淺灰實線（從開到右），比虛線更明顯
- 分時均線：黃色細線（成交均價，這裡用 close 的累計均值近似）
- 價格線下方漸變填充: 頂部半透明實色 → 底部全透明(與個股對話框 EChartsIntraday 一致)
空數據返回等尺寸佔位 SVG，保證加載前後尺寸一致（同 MiniCandlestick 模式）。
*/
import { useId } from 'react'
import type { MinuteKlineRow } from '@/lib/api'

export function MiniIntraday({ rows, prevClose, changePct, width = 100, height = 56 }: {
  rows: MinuteKlineRow[]
  /** 昨收價 (前收), 用於基準線。無則用 close/changePct 反算 */
  prevClose?: number | null
  /** 漲跌幅 (小數, 如 -0.029 = -2.9%), 用於漲跌著色。優先級最高 */
  changePct?: number | null
  width?: number
  height?: number
}) {
  const rawId = useId()
  // 空數據：返回等尺寸佔位
  if (!rows || rows.length < 2) {
    return <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="block" aria-label="尚無分時" />
  }

  const gradId = rawId.replace(/:/g, '')

  const BULL = '#C74040'
  const BEAR = '#2D9B65'
  const LINE_PREV_CLOSE = '#7A7A85'   // 昨收基準線: 深灰實線
  const LINE_AVG = '#E0B84A'          // 均線: 暖黃

  const W = width
  const H = height
  const padY = 3
  const n = rows.length

  // 漲跌著色: 優先用 changePct (後端 enriched 字段, 最可靠);
  // 其次用 prevClose vs lastClose; 最後回退到第一根 open
  const lastClose = rows[n - 1].close
  const firstOpen = rows[0].open
  const isUp = changePct != null
    ? changePct >= 0
    : prevClose != null && prevClose > 0
      ? lastClose >= prevClose
      : lastClose >= firstOpen
  const color = isUp ? BULL : BEAR

  // 昨收基準線: 優先用 prevClose; 其次用 changePct 反算 (close/(1+changePct));
  // 最後回退到第一根 open
  const baseline = (prevClose != null && prevClose > 0)
    ? prevClose
    : (changePct != null && changePct !== 0)
      ? lastClose / (1 + changePct)
      : firstOpen

  // 價格區間: close + 昨收 + 均線 全部納入, 確保都在可視範圍
  let hi = -Infinity, lo = Infinity
  // 累計均價 (分時均線的近似: close 的累計平均)
  const avgLine: number[] = []
  let cumSum = 0
  for (let i = 0; i < n; i++) {
    const c = rows[i].close
    cumSum += c
    const avg = cumSum / (i + 1)
    avgLine.push(avg)
    if (c > hi) hi = c
    if (c < lo) lo = c
    if (avg > hi) hi = avg
    if (avg < lo) lo = avg
  }
  // 把昨收也納入區間
  hi = Math.max(hi, baseline)
  lo = Math.min(lo, baseline)
  const range = hi - lo || 1

  const yScale = (v: number) => padY + (1 - (v - lo) / range) * (H - padY * 2)
  const xScale = (i: number) => (i / (n - 1)) * W

  // 價格折線 points
  const pricePoints = rows.map((r, i) => `${xScale(i).toFixed(1)},${yScale(r.close).toFixed(1)}`).join(' ')
  // 均線 points
  const avgPoints = avgLine.map((v, i) => `${xScale(i).toFixed(1)},${yScale(v).toFixed(1)}`).join(' ')

  // 漸變填充多邊形 points: 價格折線 + 底部右下角 + 左下角, 閉合到畫布底邊
  const bottomY = H - padY
  const areaPoints = `${pricePoints} ${xScale(n - 1).toFixed(1)},${bottomY.toFixed(1)} ${xScale(0).toFixed(1)},${bottomY.toFixed(1)}`

  // 昨收參考線 y 座標
  const prevCloseY = yScale(baseline)

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block">
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity={0.4} />
          <stop offset="1" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      {/* 昨收基準線 (深灰實線, 比虛線更明顯) */}
      <line
        x1={0} y1={prevCloseY} x2={W} y2={prevCloseY}
        stroke={LINE_PREV_CLOSE} strokeWidth={0.6} opacity={0.7}
      />
      {/* 價格折線下方漸變填充 */}
      <polygon points={areaPoints} fill={`url(#${gradId})`} stroke="none" />
      {/* 分時均線 (暖黃細線) */}
      <polyline
        points={avgPoints}
        fill="none"
        stroke={LINE_AVG}
        strokeWidth={0.8}
        strokeLinejoin="round"
        strokeLinecap="round"
        opacity={0.85}
      />
      {/* 分時價格折線 */}
      <polyline
        points={pricePoints}
        fill="none"
        stroke={color}
        strokeWidth={1.1}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}
