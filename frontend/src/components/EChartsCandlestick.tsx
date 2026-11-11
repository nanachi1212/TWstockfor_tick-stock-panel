import { useEffect, useRef, useCallback, useMemo, useState } from 'react'
import { chartTheme, getTheme, useTheme } from '@/lib/theme'
import { RADIX, RADIX_CHART_SERIES } from '@/lib/radix'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption, InsideDataZoomComponentOption, SliderDataZoomComponentOption } from 'echarts'

export interface OHLC {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
  ma5?: number | null
  ma10?: number | null
  ma20?: number | null
  ma60?: number | null
}

interface Props {
  data: OHLC[]
  height?: number
  showMA?: boolean
}

// 序列顏色 (雙主題通用); 畫布軸/網格/文字等主題相關色走 CT() 動態取
const THEME = {
  bull: RADIX_CHART_SERIES.bull,
  bear: RADIX_CHART_SERIES.bear,
  bullAlpha: RADIX_CHART_SERIES.bullAlpha,
  bearAlpha: RADIX_CHART_SERIES.bearAlpha,
  ma5: RADIX_CHART_SERIES.neutral,
  ma10: RADIX_CHART_SERIES.accent,
  ma20: RADIX.orange,
  ma60: RADIX_CHART_SERIES.brand,
  bg: 'transparent',
}

/** 當前主題的圖表調色板 (buildOption/資訊欄在渲染時呼叫; 主題切換由元件 effect 觸發重建)。 */
const CT = () => chartTheme(getTheme())

/** 預設可見蠟燭根數 */
const VISIBLE_BARS = 60

/** 主圖資訊欄高度 (px) */
const MAIN_INFO_BAR_H = 40

/** 與 index.css 的 .touch-visible 同一條件 */
const TOUCH_QUERY = '(hover: none)'
const TOUCH_SLIDER_H = 20
/** 觸控時圖表底部保留給平移 slider 的高度 (上下各留 4px) */
const TOUCH_SLIDER_SPACE = TOUCH_SLIDER_H + 8

/**
 * 觸控裝置: 單指拖曳讓給頁面捲動, 雙指仍縮放 K 線, 平移改由底部 slider 負責。
 * RoamController 拖曳中會對 touchmove 呼叫 preventDefault, 只關 moveOnMouseMove 頁面仍捲不動。
 */
export function dataZoomFor(
  touch: boolean,
  xAxisIndex: number[],
): (InsideDataZoomComponentOption | SliderDataZoomComponentOption)[] {
  const inside: InsideDataZoomComponentOption = {
    type: 'inside',
    xAxisIndex,
    start: 0,
    end: 100,
    moveOnMouseMove: true,
    zoomOnMouseWheel: true,
  }
  if (!touch) return [inside]
  return [
    { ...inside, moveOnMouseMove: false, preventDefaultMouseMove: false },
    {
      type: 'slider',
      xAxisIndex,
      start: 0,
      end: 100,
      height: TOUCH_SLIDER_H,
      bottom: 4,
      brushSelect: false,
      showDetail: false,
      borderColor: CT().border,
    },
  ]
}

function buildOption(
  data: OHLC[],
  dates: string[],
  showMA: boolean,
  containerHeight: number,
  touch: boolean,
): EChartsOption {
  const candleData = data.map(d => [d.open, d.close, d.low, d.high])

  const hasMA = showMA && data.some(d => d.ma5 != null || d.ma10 != null || d.ma20 != null || d.ma60 != null)

  // ====== 佈局計算 ======
  const left = 60
  const right = 20
  const topPad = 8
  const candleBottomPad = 22

  const sliderSpace = touch ? TOUCH_SLIDER_SPACE : 0
  const candleAvail = Math.max(containerHeight - topPad - candleBottomPad - sliderSpace, 100)

  const series: any[] = []

  series.push({
    name: 'K', type: 'candlestick', data: candleData,
    animation: false,
    itemStyle: {
      color: THEME.bull, color0: THEME.bear,
      borderColor: THEME.bull, borderColor0: THEME.bear,
      cursor: 'pointer',
    },
  })

  if (hasMA) {
    const maLine = (key: keyof OHLC, color: string, name: string) => ({
      name, type: 'line',
      data: data.map(d => (d[key] != null ? Number(d[key]) : '-')),
      smooth: true, symbol: 'none', animation: false,
      silent: true,
      lineStyle: { width: 1, color }, itemStyle: { color },
    })
    series.push(maLine('ma5', THEME.ma5, 'MA5'))
    series.push(maLine('ma10', THEME.ma10, 'MA10'))
    series.push(maLine('ma20', THEME.ma20, 'MA20'))
    series.push(maLine('ma60', THEME.ma60, 'MA60'))
  }

  return {
    animation: false,
    backgroundColor: THEME.bg,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', crossStyle: { color: CT().crosshair } },
      backgroundColor: 'transparent',
      borderWidth: 0,
      textStyle: { fontSize: 0 },
      formatter: () => '',
    },
    axisPointer: {
      link: [{ xAxisIndex: 'all' }],
      label: {
        backgroundColor: CT().crosshairLabelBg,
        fontFamily: 'JetBrains Mono, monospace',
        fontSize: 12,
      },
    },
    grid: [{ left, right, top: topPad, height: candleAvail }],
    xAxis: [{
      type: 'category', data: dates, boundaryGap: true,
      axisLine: { lineStyle: { color: CT().border } },
      axisLabel: { color: CT().text, fontSize: 12, fontFamily: 'JetBrains Mono, monospace' },
      axisTick: { show: false },
      splitLine: { show: false },
    }],
    yAxis: [{
      scale: true,
      // 上下各留 3% 邊距: 防止最高/最低點的蠟燭貼邊
      boundaryGap: [0.03, 0.03],
      splitArea: { show: false },
      axisLine: { show: false }, axisTick: { show: false },
      splitLine: { lineStyle: { color: CT().grid } },
      axisLabel: { color: CT().text, fontSize: 12, fontFamily: 'JetBrains Mono, monospace' },
    }],
    dataZoom: dataZoomFor(touch, [0]),
    series,
  }
}


export function EChartsCandlestick({
  data,
  height = 480,
  showMA = true,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)
  const dataRef = useRef(data)
  dataRef.current = data
  // 主題: buildOption/資訊欄內部通過 CT() 動態取調色板, 這裡只負責切換時觸發重建
  const theme = useTheme()
  const [touch] = useState(() => window.matchMedia(TOUCH_QUERY).matches)
  const sliderSpace = touch ? TOUCH_SLIDER_SPACE : 0

  // --- 全部用 ref，避免高頻交互觸發 React 重渲染 ---
  const infoIdxRef = useRef<number>(data.length - 1)
  const userZoomRef = useRef<{ start: number; end: number } | null>(null)
  const getInfoBarHTMLRef = useRef<() => string>(() => '')
  const infoBarRef = useRef<HTMLDivElement>(null)

  const minCandleH = 120

  const chartHeight = Math.max(height - MAIN_INFO_BAR_H, 8 + minCandleH + 14 + sliderSpace)

  const dates = useMemo(() => data.map(d => d.date), [data])

  // 計算 dataZoom 初始範圍
  const initialZoom = useMemo(() => ({
    start: Math.max(0, 100 - (VISIBLE_BARS / Math.max(data.length, 1)) * 100),
    end: 100,
  }), [data.length])

  // ===== 資訊欄 HTML 內容 (基於 infoIdxRef.current) =====
  const getInfoBarHTML = useCallback(() => {
    let idx = infoIdxRef.current
    let d = idx >= 0 && idx < data.length ? data[idx] : null
    // fallback: 如果當前 idx 無資料，取最後一根 K 線
    if (!d && data.length > 0) {
      idx = data.length - 1
      d = data[idx]
    }
    if (!d) return ''
    const prev = idx > 0 ? data[idx - 1] : null
    const chg = prev ? d.close - prev.close : 0
    const isUp = chg >= 0
    const clr = isUp ? THEME.bull : THEME.bear

    let html = `<div style="display:flex;align-items:center;gap:6px;padding:0 8px;font:11px 'JetBrains Mono',monospace;select:none;height:20px;flex-wrap:wrap">`
    html += `<span style="color:${CT().text}">${d.date}</span>`
    html += `<span style="color:${CT().text}">開</span>`
    html += `<span style="color:${d.open >= d.close ? THEME.bear : THEME.bull}">${d.open.toFixed(2)}</span>`
    html += `<span style="color:${CT().text}">高</span>`
    html += `<span style="color:${THEME.bull}">${d.high.toFixed(2)}</span>`
    html += `<span style="color:${CT().text}">低</span>`
    html += `<span style="color:${THEME.bear}">${d.low.toFixed(2)}</span>`
    html += `<span style="color:${CT().text}">收</span>`
    html += `<span style="color:${clr};font-weight:600">${d.close.toFixed(2)}</span>`
    // 漲跌幅 (收盤後; 和收間隔一些距離)
    if (prev) {
      const chgPct = (chg / prev.close * 100)
      html += `<span style="color:${clr};margin-left:8px">${isUp ? '+' : ''}${chgPct.toFixed(2)}%</span>`
    }
    html += `</div>`

    // 第二行: MA
    if (showMA) {
      html += `<div style="display:flex;align-items:center;gap:10px;padding:0 8px;font:11px 'JetBrains Mono',monospace;select:none;height:20px;flex-wrap:wrap">`
      if (d.ma5 != null) html += `<span style="color:${THEME.ma5}">MA5:${Number(d.ma5).toFixed(2)}</span>`
      if (d.ma10 != null) html += `<span style="color:${THEME.ma10}">MA10:${Number(d.ma10).toFixed(2)}</span>`
      if (d.ma20 != null) html += `<span style="color:${THEME.ma20}">MA20:${Number(d.ma20).toFixed(2)}</span>`
      if (d.ma60 != null) html += `<span style="color:${THEME.ma60}">MA60:${Number(d.ma60).toFixed(2)}</span>`
      html += `</div>`
    }

    return html
  }, [data, showMA])
  getInfoBarHTMLRef.current = getInfoBarHTML

  // data 變化時重置 infoIdx
  useEffect(() => {
    infoIdxRef.current = data.length - 1
    userZoomRef.current = null
  }, [data.length])

  // ===== 初始化 chart (只在 chartHeight 變化時重建) =====
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const chart = echarts.init(el, undefined, { renderer: 'canvas' })
    chartRef.current = chart

    // 滑鼠移動 → 只更新 ref + DOM，不觸發 React re-render
    // 設計原則: 找不到有效資料時保持上次顯示，永遠不清空資訊欄
    chart.on('updateAxisPointer', (event: any) => {
      const axesInfo = event.axesInfo
      if (!axesInfo) return // 滑鼠移出圖表區域，保持當前顯示
      for (const info of Object.values(axesInfo)) {
        const val = (info as any)?.value
        if (val == null) continue
        const d = dataRef.current
        const idx = typeof val === 'number' ? val : d.findIndex(x => x.date === val)
        if (idx >= 0 && idx < d.length) {
          if (infoIdxRef.current === idx) return
          infoIdxRef.current = idx

          // 直接更新資訊欄 DOM (通過 ref 讀取最新的生成函數)
          const infoEl = infoBarRef.current
          if (infoEl) {
            const html = getInfoBarHTMLRef.current()
            if (html) infoEl.innerHTML = html  // 只在有內容時更新
          }
          return
        }
      }
      // 沒有找到有效資料 — 不做任何操作，保持上次顯示
    })

    // dataZoom → 只更新 ref，不觸發 React re-render
    chart.on('dataZoom', () => {
      const opt = chart.getOption() as any
      const zoom = opt?.dataZoom?.[0]
      if (!zoom) return
      userZoomRef.current = { start: zoom.start, end: zoom.end }
    })

    const ro = new ResizeObserver(() => { chart.resize() })
    ro.observe(el)

    return () => {
      chart.off('updateAxisPointer')
      chart.off('dataZoom')
      ro.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [chartHeight])

  // ===== 核心: 僅在資料/設定變更時全量 setOption =====
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    const option = buildOption(data, dates, showMA, chartHeight, touch)

    chart.setOption(option, true)

    // 恢復使用者縮放位置
    const zoom = userZoomRef.current
    if (zoom) {
      chart.dispatchAction({ type: 'dataZoom', start: zoom.start, end: zoom.end })
    } else {
      chart.dispatchAction({ type: 'dataZoom', start: initialZoom.start, end: initialZoom.end })
    }

    // 初始資訊欄
    const infoEl = infoBarRef.current
    if (infoEl) {
      infoEl.innerHTML = getInfoBarHTML()
    }
  }, [data, showMA, chartHeight, dates, initialZoom, getInfoBarHTML, theme, touch])

  // 渲染資訊欄容器 (內容由 JS 直接寫入)
  const initialHTML = useMemo(() => {
    const idx = data.length - 1
    const d = idx >= 0 && idx < data.length ? data[idx] : null
    if (!d) return ''
    let html = `<div style="display:flex;align-items:center;gap:6px;padding:0 8px;font:11px 'JetBrains Mono',monospace;height:20px;flex-wrap:wrap">`
    html += `<span style="color:${CT().text}">${d.date}</span>`
    html += `<span style="color:${CT().text}">開</span>`
    html += `<span style="color:${d.open >= d.close ? THEME.bear : THEME.bull}">${d.open.toFixed(2)}</span>`
    html += `<span style="color:${CT().text}">高</span>`
    html += `<span style="color:${THEME.bull}">${d.high.toFixed(2)}</span>`
    html += `<span style="color:${CT().text}">低</span>`
    html += `<span style="color:${THEME.bear}">${d.low.toFixed(2)}</span>`
    html += `<span style="color:${CT().text}">收</span>`
    const prevClose0 = data[idx-1]?.close ?? d.close
    const clr0 = d.close >= prevClose0 ? THEME.bull : THEME.bear
    html += `<span style="color:${clr0};font-weight:600">${d.close.toFixed(2)}</span>`
    // 漲跌幅 (收盤後; 和收間隔一些距離)
    if (idx > 0) {
      const chgPct0 = ((d.close - prevClose0) / prevClose0 * 100)
      html += `<span style="color:${clr0};margin-left:8px">${chgPct0 >= 0 ? '+' : ''}${chgPct0.toFixed(2)}%</span>`
    }
    html += `</div>`
    if (showMA) {
      html += `<div style="display:flex;align-items:center;gap:10px;padding:0 8px;font:11px 'JetBrains Mono',monospace;height:20px;flex-wrap:wrap">`
      if (d.ma5 != null) html += `<span style="color:${THEME.ma5}">MA5:${Number(d.ma5).toFixed(2)}</span>`
      if (d.ma10 != null) html += `<span style="color:${THEME.ma10}">MA10:${Number(d.ma10).toFixed(2)}</span>`
      if (d.ma20 != null) html += `<span style="color:${THEME.ma20}">MA20:${Number(d.ma20).toFixed(2)}</span>`
      if (d.ma60 != null) html += `<span style="color:${THEME.ma60}">MA60:${Number(d.ma60).toFixed(2)}</span>`
      html += `</div>`
    }
    return html
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="w-full">
      {/* 主圖資訊欄 — 內容由 JS 直接操作 innerHTML */}
      <div ref={infoBarRef} style={{ backgroundColor: CT().infoBarBg }}
        dangerouslySetInnerHTML={{ __html: initialHTML }} />

      {/* ECharts canvas */}
      <div ref={containerRef} className="w-full" style={{ height: chartHeight }} />
    </div>
  )
}
