import { useEffect, useRef, type ReactNode } from 'react'

/**
 * 共享模態對話框原語 — 統一處理可訪問性:
 * - role="dialog" + aria-modal + aria-labelledby / aria-label
 * - ESC 關閉
 * - 打開時把焦點移入對話框 (initialFocusRef 或首個可聚焦元素)
 * - Tab / Shift+Tab 焦點陷阱 (焦點不會跑出對話框)
 * - 關閉時把焦點還給打開前的元素
 * - 點擊遮罩關閉 (可用 closeOnBackdrop 關閉)
 *
 * 視覺: 提供居中遮罩 + 面板容器, 面板樣式由 panelClassName 定製。
 */
export interface ModalProps {
  onClose: () => void
  children: ReactNode
  /** 對話框標題元素 id (用於 aria-labelledby) */
  labelledBy?: string
  /** 無可見標題時的無障礙名稱 */
  ariaLabel?: string
  /** 面板 className (尺寸/背景/圓角等) */
  panelClassName?: string
  /** 遮罩 className (覆蓋默認居中/背景) */
  overlayClassName?: string
  /** 打開時聚焦的元素; 不傳則聚焦面板內首個可聚焦元素 */
  initialFocusRef?: React.RefObject<HTMLElement>
  /** 點擊遮罩是否關閉 (默認 true) */
  closeOnBackdrop?: boolean
}

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function Modal({
  onClose,
  children,
  labelledBy,
  ariaLabel,
  panelClassName = 'w-[92vw] max-w-lg bg-surface border border-border rounded-card shadow-xl',
  overlayClassName = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm',
  initialFocusRef,
  closeOnBackdrop = true,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  // 記錄鼠標按下時是否落在遮罩(而非面板)上。
  // 僅當 mousedown 和 mouseup 都在遮罩時才視為"點擊遮罩關閉",
  // 避免在面板內拖選文本時鼠標移出面板邊緣導致誤關 (拖拽穿透)。
  const mouseDownOnBackdrop = useRef(false)
  // onClose 存 ref: 焦點陷阱/ESC effect 只在掛載時裝一次。否則父級每次重渲染 (或未 memo 的
  // onClose) 都讓 effect 重跑, requestAnimationFrame(focusFirst) 會在每次輸入後把焦點搶回
  // 面板首個元素, 導致對話框內文本框無法輸入。
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    // 記住打開前的焦點, 關閉時還原
    const prevActive = document.activeElement as HTMLElement | null

    // 初始聚焦
    const focusFirst = () => {
      if (initialFocusRef?.current) {
        initialFocusRef.current.focus()
        return
      }
      const panel = panelRef.current
      if (!panel) return
      const first = panel.querySelector<HTMLElement>(FOCUSABLE)
      ;(first ?? panel).focus()
    }
    // 等一幀確保內容已掛載
    const raf = requestAnimationFrame(focusFirst)

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCloseRef.current()
        return
      }
      if (e.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
        .filter(el => el.offsetParent !== null || el === document.activeElement)
      if (nodes.length === 0) {
        e.preventDefault()
        panel.focus()
        return
      }
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      const active = document.activeElement as HTMLElement | null
      if (e.shiftKey) {
        if (active === first || !panel.contains(active)) {
          e.preventDefault()
          last.focus()
        }
      } else {
        if (active === last || !panel.contains(active)) {
          e.preventDefault()
          first.focus()
        }
      }
    }

    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('keydown', onKeyDown, true)
      // 還原焦點
      prevActive?.focus?.()
    }
    // 只在掛載時裝一次: onClose 走 ref, initialFocusRef 為穩定 ref 對象, 無需進依賴。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div
      className={overlayClassName}
      onMouseDown={(e) => {
        // 僅記錄"按下時確實在遮罩上"; 在面板內按下時記 false。
        mouseDownOnBackdrop.current = e.target === e.currentTarget
      }}
      onClick={closeOnBackdrop ? (e) => {
        // 只有按下和鬆開都在遮罩上才關閉, 避免拖選文本誤關。
        if (mouseDownOnBackdrop.current && e.target === e.currentTarget) onClose()
      } : undefined}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : ariaLabel}
        tabIndex={-1}
        className={`outline-none ${panelClassName}`}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  )
}
