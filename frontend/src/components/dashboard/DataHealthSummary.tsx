import { Link } from 'react-router-dom'
import { useDataHealth } from '@/lib/useDataHealth'

export function DataHealthSummary() {
  const health = useDataHealth()
  return (
    <Link to="/data-health" className="mb-2 block rounded-card border border-border bg-surface p-3 text-sm hover:border-accent">
      {health.data ? `資料健康 ${health.data.current_count} / ${health.data.total_count} 正常` :
        health.isError ? '資料健康暫時無法讀取，查看健康中心' : '資料健康讀取中…'}
    </Link>
  )
}
