/**
 * 全局顏色別名 — 集中管理項目中重複使用的色彩組合。
 *
 * 不修改 Tailwind 配置和 CSS 變量，僅作為語義化引用。
 * 使用方式：
 *   import { color } from '@/lib/colors'
 *   className={`${color.select.bg} ${color.select.text}`}
 *   // → bg-sky-500 text-sky-400
 */

export const color = {
  /** 選中/激活態 — sky 色系 */
  select: {
    bg: 'bg-sky-500',
    text: 'text-sky-400',
    border: 'border-sky-400/40',
    bgLight: 'bg-sky-400/10',
    borderHover: 'hover:border-sky-400/30',
  },
  /** 選股條件區 section 標題色 */
  filterSection: 'text-sky-400',

  /** 評分權重正常指示 — emerald 色系 */
  ok: 'text-emerald-400',

  /** 評分權重異常警告 — amber 色系 */
  scoreWarn: 'text-amber-400',

  /** 交易參數區 section 標題色 */
  tradeSection: 'text-emerald-400',
} as const
