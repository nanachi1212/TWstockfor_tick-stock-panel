import { useState, useRef, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Calendar, ChevronLeft, ChevronRight } from 'lucide-react'

/** 彈層固定寬度(px) — 與下方 w-[260px] 保持一致,用於水平邊界裁剪計算 */
const POPUP_WIDTH = 260
/** 彈層預估高度(px) — 用於判斷是否需要向上翻轉 */
const POPUP_HEIGHT = 320
/** 彈層與觸發按鈕的間距(px) */
const POPUP_GAP = 6

interface DatePickerProps {
  value: string          // YYYY-MM-DD
  onChange: (v: string) => void
  min?: string
  max?: string
  placeholder?: string
  className?: string
  buttonClassName?: string
  align?: 'left' | 'right'
}

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']

function pad(n: number) { return String(n).padStart(2, '0') }
function toDateStr(y: number, m: number, d: number) {
  return `${y}-${pad(m + 1)}-${pad(d)}`
}
function todayStr() {
  const date = new Date()
  return toDateStr(date.getFullYear(), date.getMonth(), date.getDate())
}
function viewDate(value: string, min?: string, max?: string) {
  const source = value || max || min || todayStr()
  return {
    year: Number(source.slice(0, 4)),
    month: Number(source.slice(5, 7)) - 1,
  }
}

export function DatePicker({
  value,
  onChange,
  min,
  max,
  placeholder = '選擇日期',
  className = '',
  buttonClassName = '',
  align = 'right',
}: DatePickerProps) {
  const [open, setOpen] = useState(false)
  const [showYearPicker, setShowYearPicker] = useState(false)
  // 觸發按鈕 ref (外部點擊檢測 + 計算彈層座標)
  const btnRef = useRef<HTMLButtonElement>(null)
  // 彈層 portal ref (外部點擊檢測)
  const popRef = useRef<HTMLDivElement>(null)
  // 彈層視口座標 + 展開方向(open 時計算一次,避免滾動時漂移)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)

  // 當前顯示的月份
  const [viewYear, setViewYear] = useState(() => viewDate(value, min, max).year)
  const [viewMonth, setViewMonth] = useState(() => viewDate(value, min, max).month)

  // 當 value 外部變化時同步 view
  useEffect(() => {
    const next = viewDate(value, min, max)
    setViewYear(next.year)
    setViewMonth(next.month)
  }, [value, min, max])

  // 打開彈層: 按鈕的視口座標計算 + 智能方向翻轉 + 水平邊界裁剪
  const handleOpen = () => {
    if (open) { setOpen(false); return }
    if (!btnRef.current) { setOpen(true); return }
    const r = btnRef.current.getBoundingClientRect()
    const spaceBelow = window.innerHeight - r.bottom
    // 下方空間不足 → 向上展開
    const dropUp = spaceBelow < POPUP_HEIGHT + POPUP_GAP && r.top > POPUP_HEIGHT + POPUP_GAP
    const top = dropUp
      ? Math.max(8, r.top - POPUP_HEIGHT - POPUP_GAP)
      : r.bottom + POPUP_GAP
    // 水平: 默認按 align 貼齊按鈕左/右邊緣, 再做右側溢出裁剪
    const rawLeft = align === 'left' ? r.left : r.right - POPUP_WIDTH
    const left = Math.max(8, Math.min(rawLeft, window.innerWidth - POPUP_WIDTH - 8))
    setPos({ top, left })
    setOpen(true)
  }

  // 點擊外部關閉 (Portal 下彈層不在 ref 樹內, 分別判斷按鈕與彈層)
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      const t = e.target as Node
      if (btnRef.current?.contains(t)) return
      if (popRef.current?.contains(t)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // 滾動 / resize 時關閉彈層 (fixed 定位不跟隨滾動,關閉比重算更可靠)
  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    // capture: true → 捕獲到任意祖先滾動容器的 scroll
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    return () => {
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
    }
  }, [open])

  const prevMonth = () => {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(viewYear - 1) }
    else setViewMonth(viewMonth - 1)
  }
  const nextMonth = () => {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(viewYear + 1) }
    else setViewMonth(viewMonth + 1)
  }

  // 構建日曆格子: 週一為第一天
  const firstDay = new Date(viewYear, viewMonth, 1).getDay()
  const offset = firstDay === 0 ? 6 : firstDay - 1          // 周一=0
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate()
  const prevMonthDays = new Date(viewYear, viewMonth, 0).getDate()

  const cells: { day: number; cur: boolean; dateStr: string; disabled: boolean }[] = []

  // 上月尾部
  for (let i = offset - 1; i >= 0; i--) {
    const d = prevMonthDays - i
    const m = viewMonth === 0 ? 11 : viewMonth - 1
    const y = viewMonth === 0 ? viewYear - 1 : viewYear
    const ds = toDateStr(y, m, d)
    cells.push({ day: d, cur: false, dateStr: ds, disabled: !!min && ds < min || !!max && ds > max })
  }
  // 當月
  for (let d = 1; d <= daysInMonth; d++) {
    const ds = toDateStr(viewYear, viewMonth, d)
    cells.push({ day: d, cur: true, dateStr: ds, disabled: !!min && ds < min || !!max && ds > max })
  }
  // 下月頭部 — 補齊到 6 行 × 7 = 42
  const remain = 42 - cells.length
  for (let d = 1; d <= remain; d++) {
    const m = viewMonth === 11 ? 0 : viewMonth + 1
    const y = viewMonth === 11 ? viewYear + 1 : viewYear
    const ds = toDateStr(y, m, d)
    cells.push({ day: d, cur: false, dateStr: ds, disabled: !!min && ds < min || !!max && ds > max })
  }

  const displayLabel = value || placeholder
  const today = todayStr()

  return (
    <div className={`relative inline-flex ${className}`}>
      {/* 觸發按鈕 */}
      <button
        ref={btnRef}
        type="button"
        onClick={handleOpen}
        className={`inline-flex items-center gap-1.5 h-7 px-2.5 rounded-input border border-border
          bg-elevated hover:border-accent/50 text-xs text-foreground num
          focus:outline-none focus:border-accent/60 transition-colors duration-150 cursor-pointer ${buttonClassName}`}
      >
        <Calendar className="h-3.5 w-3.5 text-accent" />
        <span className={value ? undefined : 'text-muted'}>{displayLabel}</span>
      </button>

      {/* 彈出日曆 — Portal 到 body, 逃逸祖先 overflow 裁剪與 framer-motion transform 包含塊 */}
      {createPortal(
        <AnimatePresence>
          {open && pos && (
            <motion.div
              ref={popRef}
              initial={{ opacity: 0, y: -4, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -4, scale: 0.97 }}
              transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
              style={{ position: 'fixed', top: pos.top, left: pos.left }}
              className="z-[9999] w-[260px] rounded-card border border-border
                bg-surface shadow-[0_8px_30px_rgba(0,0,0,0.4)] p-3"
            >
            {/* 月份導航 */}
            <div className="flex items-center justify-between mb-2">
              <button
                type="button"
                onClick={showYearPicker ? () => setViewYear(viewYear - 12) : prevMonth}
                className="p-1 rounded-btn hover:bg-elevated text-secondary hover:text-foreground transition-colors"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => setShowYearPicker(v => !v)}
                className="text-sm font-medium text-foreground num hover:text-accent transition-colors cursor-pointer"
              >
                {showYearPicker
                  ? `${viewYear - 5} - ${viewYear + 6}`
                  : `${viewYear} 年 ${viewMonth + 1} 月`
                }
              </button>
              <button
                type="button"
                onClick={showYearPicker ? () => setViewYear(viewYear + 12) : nextMonth}
                className="p-1 rounded-btn hover:bg-elevated text-secondary hover:text-foreground transition-colors"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>

            {showYearPicker ? (
              /* 年份選擇網格 */
              <div className="grid grid-cols-4 gap-1">
                {Array.from({ length: 12 }, (_, i) => viewYear - 5 + i).map(y => {
                  const isSelected = y === Number(value.slice(0, 4))
                  const isThisYear = y === new Date().getFullYear()
                  return (
                    <button
                      key={y}
                      type="button"
                      onClick={() => {
                        setViewYear(y)
                        setShowYearPicker(false)
                      }}
                      className={`h-8 text-xs rounded-btn transition-colors duration-100
                        ${isSelected ? 'bg-accent text-white font-bold' : ''}
                        ${isThisYear && !isSelected ? 'border border-accent/40' : ''}
                        ${!isSelected ? 'hover:bg-elevated cursor-pointer text-foreground' : ''}
                      `}
                    >
                      {y}
                    </button>
                  )
                })}
              </div>
            ) : (
              <>
                {/* 星期頭 */}
                <div className="grid grid-cols-7 text-center text-[10px] text-muted mb-1">
                  {WEEKDAYS.map((w) => (
                    <div key={w}>{w}</div>
                  ))}
                </div>

                {/* 日期格子 */}
                <div className="grid grid-cols-7 gap-px">
                  {cells.map((c, i) => {
                    const isSelected = c.dateStr === value
                    const isToday = c.dateStr === today
                    return (
                      <button
                        key={i}
                    type="button"
                    disabled={c.disabled}
                    onClick={() => {
                      if (!c.disabled) {
                        onChange(c.dateStr)
                        setOpen(false)
                      }
                    }}
                    className={`
                      h-7 w-full text-xs rounded-btn transition-colors duration-100
                      ${c.cur ? 'text-foreground' : 'text-muted/40'}
                      ${isSelected ? 'bg-accent text-white font-bold' : ''}
                      ${isToday && !isSelected ? 'border border-accent/40' : ''}
                      ${!isSelected && !c.disabled ? 'hover:bg-elevated' : ''}
                      ${c.disabled ? 'opacity-20 cursor-not-allowed' : 'cursor-pointer'}
                    `}
                  >
                    {c.day}
                  </button>
                )
              })}
            </div>
              </>
            )}
          </motion.div>
        )}
      </AnimatePresence>
      , document.body)}
    </div>
  )
}
