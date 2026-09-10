/**
 * 回測預熱期徽標 — 點擊彈出說明氣泡。
 *
 * 解釋「回測開頭幾個月沒有交易」這一高頻疑問: 技術指標需要歷史數據預熱,
 * 系統會自動在回測起點之前多取約 120 天 (≈4 個月) 數據; 若本地數據恰好從
 * 起點才開始, 開頭幾個月指標算不出、信號不觸發, 屬正常現象。
 *
 * 實現要點:
 *   - 點擊觸發 (非 hover), 移動端友好
 *   - 用 createPortal 渲染到 body, 繞開父容器 overflow 裁剪 (回測配置面板有 overflow-y-auto)
 *   - 全屏透明遮罩點擊關閉 + ESC 關閉
 *   - 氣泡位置 = 錨點 rect 實時計算, 自動判斷向左/向右展開避免溢出屏幕
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Info } from 'lucide-react'

interface Pos { top: number; left: number }

export function WarmupBadge() {
  const [open, setOpen] = useState(false)
  const anchorRef = useRef<HTMLButtonElement>(null)
  const [pos, setPos] = useState<Pos>({ top: 0, left: 0 })

  // 打開時根據錨點 rect 計算氣泡位置 (向下方彈出)
  useLayoutEffect(() => {
    if (!open || !anchorRef.current) return
    const rect = anchorRef.current.getBoundingClientRect()
    const POPUP_W = 272
    const GAP = 8
    // 優先左對齊錨點; 右側不夠則右對齊; 兜底貼左邊
    let left = rect.left
    if (left + POPUP_W > window.innerWidth - 8) {
      left = rect.right - POPUP_W
    }
    left = Math.max(8, left)
    setPos({ top: rect.bottom + GAP, left })
  }, [open])

  // ESC 關閉
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        onClick={() => setOpen(o => !o)}
        className="inline-flex items-center gap-0.5 rounded-full px-1.5 text-[10px] text-amber-500/70 transition-colors hover:bg-amber-400/10 hover:text-amber-500"
        title="為什麼開頭可能沒交易?"
      >
        <Info className="h-3 w-3" strokeWidth={1.5} />
        預熱 ≥120 天
      </button>

      {createPortal(
        <AnimatePresence>
          {open && (
            <>
              {/* 全屏透明遮罩: 點擊關閉 */}
              <div
                className="fixed inset-0 z-[60]"
                onClick={() => setOpen(false)}
              />
              {/* 氣泡: 絕對定位到 body, 繞開 overflow 裁剪 */}
              <motion.div
                initial={{ opacity: 0, y: -4, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -4, scale: 0.96 }}
                transition={{ duration: 0.15 }}
                style={{ position: 'fixed', top: pos.top, left: pos.left, width: 272 }}
                className="z-[70] rounded-btn border border-border bg-surface p-3 text-[11px] leading-relaxed text-secondary shadow-2xl"
                onClick={e => e.stopPropagation()}
              >
                <div className="mb-1.5 font-medium text-foreground">為什麼開頭幾個月可能沒有交易?</div>
                <p className="text-muted">
                  技術指標 (MA / MACD / RSI 等) 需要歷史數據才能算出。系統會自動在回測起點之前多取約
                  <span className="font-medium text-amber-300"> 120 天 (≈4 個月)</span> 數據做預熱。
                </p>
                <p className="mt-1.5 text-muted">
                  若本地數據恰好從回測起點才開始, 開頭幾個月指標算不出、信號不觸發,
                  <span className="text-secondary"> 屬正常現象, 不是 bug</span>。等數據攢夠後自然開始產生交易。
                </p>
                <div className="mt-2 border-t border-border/60 pt-2 text-muted">
                  <span className="text-secondary">解決:</span> 把歷史數據補到回測起點之前至少半年, 或把起點往後挪。
                </div>
                {/* 小箭頭指向錨點 */}
                <div
                  className="absolute -top-1 h-2 w-2 rotate-45 border-l border-t border-border bg-surface"
                  style={{ left: 12 }}
                />
              </motion.div>
            </>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  )
}
