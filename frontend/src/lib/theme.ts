// 主題管理 — 暗色(預設) / 亮色切換
//
// 機制:
//   - 狀態存 localStorage('tf-theme'), 預設 dark (保持老使用者體驗不變)
//   - 生效方式: html.dark class (index.css 的 CSS variables + Tailwind darkMode:class)
//   - index.html 裡有預渲染內聯腳本, 首屏前就設好 class, 避免閃爍 (FOUC)
//   - UI token (bg-surface/text-foreground 等) 自動跟隨;
//     圖表畫布不吃 CSS 變數, 統一走 useChartTheme() 取調色板
import { useEffect, useState } from 'react'
import { RADIX_CHART } from './radix'

const KEY = 'tf-theme'
const EVENT = 'tf-theme-change'

export type Theme = 'dark' | 'light'

export function getTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function setTheme(theme: Theme) {
  try { localStorage.setItem(KEY, theme) } catch { /* ignore */ }
  document.documentElement.classList.toggle('dark', theme === 'dark')
  window.dispatchEvent(new CustomEvent(EVENT, { detail: theme }))
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === 'dark' ? 'light' : 'dark'
  setTheme(next)
  return next
}

/** 訂閱當前主題 (本頁切換 + 其他標籤頁切換均同步)。 */
export function useTheme(): Theme {
  const [theme, set] = useState<Theme>(getTheme)
  useEffect(() => {
    const onChange = () => set(getTheme())
    window.addEventListener(EVENT, onChange)
    window.addEventListener('storage', onChange)  // 跨標籤頁同步
    return () => {
      window.removeEventListener(EVENT, onChange)
      window.removeEventListener('storage', onChange)
    }
  }, [])
  return theme
}

// ================================================================
// 字級縮放 — localStorage('tf-font-scale'), 以 html zoom 生效:
// 連 text-xs 這類固定 px 字與圖表一起放大。--ui-zoom 給 Layout 修正 100vh。
// ================================================================

const FONT_KEY = 'tf-font-scale'
export const FONT_SCALES = [1, 1.1, 1.25, 1.4] as const
// 手機預設不縮放: Android 已套系統字級, 再疊 html zoom 會讓版面超出螢幕且干擾雙指縮放。
function defaultFontScale(): number {
  return window.matchMedia?.('(max-width: 767px)').matches ? 1 : 1.1
}

export function getFontScale(): number {
  try {
    const v = Number(localStorage.getItem(FONT_KEY))
    return (FONT_SCALES as readonly number[]).includes(v) ? v : defaultFontScale()
  } catch {
    return defaultFontScale()
  }
}

export function applyFontScale(scale: number = getFontScale()) {
  const root = document.documentElement
  root.style.zoom = String(scale)
  root.style.setProperty('--ui-zoom', String(scale))
}

export function cycleFontScale(): number {
  const list = FONT_SCALES as readonly number[]
  const next = list[(list.indexOf(getFontScale()) + 1) % list.length]
  try { localStorage.setItem(FONT_KEY, String(next)) } catch { /* ignore */ }
  applyFontScale(next)
  return next
}

// ================================================================
// 圖表調色板 — ECharts 畫布不吃 CSS 變數,
// 所有圖表元件統一從這裡取色, 主題切換時依賴 useTheme 重建 option。
// bull/bear/accent 等語義色雙主題一致, 不在此重複定義。
// ================================================================

export interface ChartTheme {
  /** 軸刻度/圖例等常規文字 */
  text: string
  /** 資訊條/圖例裡的強調文字 */
  textStrong: string
  /** 網格線 */
  grid: string
  /** 軸線/邊框 */
  border: string
  /** 十字游標線 */
  crosshair: string
  /** 十字游標軸標籤背景 */
  crosshairLabelBg: string
  /** tooltip 背景 */
  tooltipBg: string
  /** tooltip 邊框 */
  tooltipBorder: string
  /** tooltip 文字 */
  tooltipText: string
  /** 半透明資訊條背景 (K線圖左上角 OHLC 條) */
  infoBarBg: string
  /** dataZoom 滑塊填充 */
  zoomFill: string
  /** 分時圖均價線以外的弱填充 */
  fillSubtle: string
}

const DARK: ChartTheme = RADIX_CHART.dark
const LIGHT: ChartTheme = RADIX_CHART.light

export function chartTheme(theme: Theme): ChartTheme {
  return theme === 'dark' ? DARK : LIGHT
}

/** hook: 當前主題的圖表調色板 (主題切換自動觸發重渲染)。 */
export function useChartTheme(): ChartTheme {
  return chartTheme(useTheme())
}
