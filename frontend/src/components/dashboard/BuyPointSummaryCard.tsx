import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { BellRing } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { SectionTitle } from './SectionTitle'

export function BuyPointSummaryCard() {
  const summary = useQuery({
    queryKey: QK.buyPointSummary,
    queryFn: () => api.buyPointSummary(),
    enabled: typeof api.buyPointSummary === 'function',
    staleTime: 60_000,
  })
  const counts = summary.data?.counts
  return (
    <section className="rounded-card border border-border bg-surface/80 p-2 shadow-[0_1px_2px_hsl(var(--border)/0.4)]">
      <div className="flex items-center justify-between"><SectionTitle icon={BellRing} title="今日買點提醒" hint="研究提醒" /><Link to="/buy-points" className="text-[10px] text-accent hover:underline">查看買點策略</Link></div>
      <div className="grid grid-cols-3 gap-1.5 text-center text-[10px]"><div className="rounded bg-bull/10 p-1.5 text-bull">已觸發<br /><span className="font-mono text-sm">{counts?.triggered ?? '—'}</span></div><div className="rounded bg-warning/10 p-1.5 text-warning">接近<br /><span className="font-mono text-sm">{counts?.approaching ?? '—'}</span></div><div className="rounded bg-danger/10 p-1.5 text-danger">風險阻擋<br /><span className="font-mono text-sm">{counts?.blocked ?? '—'}</span></div></div>
    </section>
  )
}

// 看板監控中心小組件 — 顯示前 10 條觸發記錄 + 更多按鈕
