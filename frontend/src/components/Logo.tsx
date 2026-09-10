// 原創 logo:方括號 [ ] 包裹一根帶 wick 的 K 線
//
// 概念:
//   - 外層 brackets:終端 / 代碼 / 引用邊界 — 賽博 + quant 氣質
//   - 中央 wick+body:一根標準 K 線 — 直接的金融指代
//   - body 偏上 + 下影長:bullish 站穩感 (上影短 / 下影長)
//
// 用 currentColor,繼承父級 color 設定,方便切換品牌色。
interface LogoProps {
  className?: string
  size?: number
  style?: React.CSSProperties
}

export function Logo({ className, size = 32, style }: LogoProps) {
  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      fill="none"
      className={className}
      style={style}
      role="img"
      aria-label="Nanachi 的台股監控看板"
    >
      {/* 左方括號 */}
      <path
        d="M10 4 L4 4 L4 28 L10 28"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="miter"
        strokeLinecap="butt"
      />
      {/* 右方括號 */}
      <path
        d="M22 4 L28 4 L28 28 L22 28"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="miter"
        strokeLinecap="butt"
      />
      {/* K 線 wick(上下影線,半透明) */}
      <line
        x1="16" y1="7" x2="16" y2="25"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeOpacity="0.6"
      />
      {/* K 線 body — 偏上,上影短/下影長, bullish 站穩感 */}
      <rect
        x="13" y="9" width="6" height="10"
        fill="currentColor"
        rx="0.5"
      />
    </svg>
  )
}
