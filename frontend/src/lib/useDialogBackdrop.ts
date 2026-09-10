import { useRef, useCallback } from 'react'

/**
 * 對話框遮罩"點擊外部關閉"的共享邏輯, 防止拖拽穿透。

 * 問題: 用戶在對話框內容內按下鼠標拖選文本, 鼠標移到遮罩上鬆開時,
 * 瀏覽器仍會觸發遮罩的 click 事件 (click 派發給 mousedown/mouseup 的共同祖先),
 * 導致對話框意外關閉。

 * 解法: 記錄 mousedown 時是否落在遮罩本身上; 僅當 mousedown 和 mouseup(click)
 * 都發生在遮罩上時才觸發關閉。

 * 用法:
 *   const backdrop = useDialogBackdrop(onClose)
 *   <div className="fixed inset-0 ..." {...backdrop}>
 *     <div onClick={e => e.stopPropagation()}>內容</div>
 *   </div>
 *
 * 對於有額外條件(如 isWorking 時禁止關閉)的場景, 傳 enabled 回調:
 *   const backdrop = useDialogBackdrop(onClose, () => !isWorking)
 */
export function useDialogBackdrop(
  onClose: () => void,
  enabled?: () => boolean,
) {
  const mouseDownOnBackdrop = useRef(false)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    mouseDownOnBackdrop.current = e.target === e.currentTarget
  }, [])

  const onClick = useCallback((e: React.MouseEvent) => {
    if (enabled && !enabled()) return
    if (mouseDownOnBackdrop.current && e.target === e.currentTarget) {
      onClose()
    }
  }, [onClose, enabled])

  return { onMouseDown, onClick }
}
