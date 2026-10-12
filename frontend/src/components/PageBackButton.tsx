import { ArrowLeft } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import { useSafeBack } from '@/lib/useSafeBack'

// 這些頁面已有自己的返回鍵，不重複顯示。
const OWN_BACK = [/^\/stocks\//, /^\/picks\/compare/]

/** 桌面版沒有瀏覽器的上一頁鍵：除了看板首頁，每頁頂端都有「上一頁」（沒有上一頁時回看板）。 */
export function PageBackButton() {
  const { pathname } = useLocation()
  const goBack = useSafeBack('/')
  if (pathname === '/' || OWN_BACK.some(re => re.test(pathname))) return null
  return (
    <div className="px-3 pt-2 sm:px-4">
      <button type="button" onClick={goBack}
        className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted hover:border-accent/50 hover:text-accent">
        <ArrowLeft className="h-3.5 w-3.5" /> 上一頁
      </button>
    </div>
  )
}
