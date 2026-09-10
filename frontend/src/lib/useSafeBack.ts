// DAILY_USE_CORE_UX_FIXES (P1-2) — 通用「返回上一頁」, 避免頁面固定寫死單一
// 來源(例如永遠顯示「返回即時監控」並導回 /monitor, 即使實際是從自選股/
// 台股選股/StockPreview 進入)。
//
// 判斷依據: react-router-dom(底層 history 套件)每次 push 都會在
// window.history.state 寫入遞增的 idx; idx > 0 代表目前分頁至少有一次站內
// 導覽可以往回, 可安全使用瀏覽器 history back(navigate(-1)), 回到使用者實際
// 進入前的那一頁。idx 缺席或為 0 時(直接網址列輸入、開新分頁、外部連結進入)
// 改用呼叫端提供的 fallbackPath, 避免「返回」把使用者帶出 app 或到空白頁。
//
// 不做完整 from/fromLabel 來源追蹤(那需要在每個導覽進入點都補 state, 影響
// 面大); 呼叫端搭配此 hook 使用通用「返回」文案即可覆蓋所有進入路徑。
import { useNavigate } from 'react-router-dom'

export function useSafeBack(fallbackPath: string) {
  const navigate = useNavigate()
  return () => {
    const idx = (window.history.state as { idx?: number } | null)?.idx
    if (typeof idx === 'number' && idx > 0) {
      navigate(-1)
    } else {
      navigate(fallbackPath)
    }
  }
}
