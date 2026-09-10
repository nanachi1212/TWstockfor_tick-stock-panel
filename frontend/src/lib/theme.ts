// 主題管理 — 暗色(默認) / 亮色切換
//
// 機制:
//   - 狀態存 localStorage('tf-theme'), 默認 dark (保持老用戶體驗不變)
//   - 生效方式: html.dark class (index.css 的 CSS variables + Tailwind darkMode:class)
//   - index.html 裡有預渲染內聯腳本, 首屏前就設好 class, 避免閃爍 (FOUC)
//   - UI token (bg-surface/text-foreground 等) 自動跟隨;
//     圖表畫布不吃 CSS 變量, 統一走 useChartTheme() 取調色板
import { useEffect, useState } from 'react'

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
// 圖表調色板 — ECharts / lightweight-charts 畫布不吃 CSS 變量,
// 所有圖表組件統一從這裡取色, 主題切換時依賴 useTheme 重建 option。
// bull/bear/accent 等語義色雙主題一致, 不在此重複定義。
// ================================================================

export interface ChartTheme {
  /** 軸刻度/圖例等常規文字 */
  text: string
  /** 信息條/圖例裡的強調文字 */
  textStrong: string
  /** 網格線 */
  grid: string
  /** 軸線/邊框 */
  border: string
  /** 十字光標線 */
  crosshair: string
  /** 十字光標軸標籤背景 */
  crosshairLabelBg: string
  /** tooltip 背景 */
  tooltipBg: string
  /** tooltip 邊框 */
  tooltipBorder: string
  /** tooltip 文字 */
  tooltipText: string
  /** 半透明信息條背景 (K線圖左上角 OHLC 條) */
  infoBarBg: string
  /** dataZoom 滑塊填充 */
  zoomFill: string
  /** 分時圖均價線以外的弱填充 */
  fillSubtle: string
}

const DARK: ChartTheme = {
  text: '#A1A1AA',
  textStrong: '#E4E4E7',
  grid: 'rgba(255,255,255,0.06)',
  border: '#27272A',
  crosshair: 'rgba(255,255,255,0.25)',
  crosshairLabelBg: '#333',
  tooltipBg: 'rgba(24,24,27,0.95)',
  tooltipBorder: 'rgba(255,255,255,0.1)',
  tooltipText: '#E4E4E7',
  infoBarBg: 'rgba(39,39,42,0.6)',
  zoomFill: 'rgba(255,255,255,0.06)',
  fillSubtle: 'rgba(255,255,255,0.04)',
}

const LIGHT: ChartTheme = {
  text: '#71717A',
  textStrong: '#27272A',
  grid: 'rgba(0,0,0,0.06)',
  border: '#E4E4E7',
  crosshair: 'rgba(0,0,0,0.3)',
  crosshairLabelBg: '#52525B',
  tooltipBg: 'rgba(255,255,255,0.97)',
  tooltipBorder: 'rgba(0,0,0,0.1)',
  tooltipText: '#27272A',
  infoBarBg: 'rgba(244,244,245,0.85)',
  zoomFill: 'rgba(0,0,0,0.06)',
  fillSubtle: 'rgba(0,0,0,0.04)',
}

export function chartTheme(theme: Theme): ChartTheme {
  return theme === 'dark' ? DARK : LIGHT
}

/** hook: 當前主題的圖表調色板 (主題切換自動觸發重渲染)。 */
export function useChartTheme(): ChartTheme {
  return chartTheme(useTheme())
}
