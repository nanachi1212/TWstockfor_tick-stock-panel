import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type DataHealthAction, type DataHealthStatus } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { useDataHealth } from '@/lib/useDataHealth'

// 「等待官方發布」「尚未執行」是資料正常的時間差，不是故障；只有「正常」計入健康數。
const statuses: Record<DataHealthStatus, string> = {
  current: '正常', partial: '部分可用', awaiting_publication: '等待官方發布', stale: '過期',
  provider_error: '外部服務失敗', not_run: '尚未執行', unavailable: '資料缺失', config_missing: '設定缺失',
  updating: '更新中', error: '錯誤',
}
const colors: Record<DataHealthStatus, string> = {
  current: 'text-emerald-600 dark:text-emerald-400', stale: 'text-warning', partial: 'text-warning',
  awaiting_publication: 'text-secondary', not_run: 'text-secondary', provider_error: 'text-danger',
  config_missing: 'text-warning', unavailable: 'text-muted', updating: 'text-accent', error: 'text-danger',
}
const actions: Record<DataHealthAction, string> = { update: '立即更新', validate: '重新驗證', retry: '重試' }
const jobStatuses = { queued: '排隊中', running: '執行中', completed: '已完成', partial: '部分完成', failed: '失敗' }

function localTime(value: string | null) {
  if (!value) return '未知'
  const stamp = new Date(value)
  return Number.isNaN(stamp.getTime()) ? '未知' : stamp.toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false })
}

export function DataHealth() {
  const health = useDataHealth()
  const qc = useQueryClient()
  const [filter, setFilter] = useState<DataHealthStatus | ''>('')
  const [search, setSearch] = useState('')
  const jobs = useQuery({
    queryKey: QK.dataHealthJobs, queryFn: api.dataHealthJobs,
    refetchInterval: query => query.state.data?.some(j => ['queued', 'running'].includes(j.status)) ? 1500 : 10_000,
  })
  const inFlight = useRef(false)
  const action = useMutation({
    mutationFn: ({ id, kind }: { id: string; kind: DataHealthAction }) => api.dataHealthAction(id, kind),
    onSuccess: job => {
      qc.setQueryData(QK.dataHealthJobs, (old: typeof jobs.data) => [job, ...(old ?? []).filter(j => j.job_id !== job.job_id)])
      void qc.invalidateQueries({ queryKey: QK.dataHealth })
      void qc.invalidateQueries({ queryKey: QK.dataHealthJobs })
    },
  })
  const rows = (health.data?.datasets ?? []).filter(row => (!filter || row.status === filter) &&
    `${row.name} ${row.source ?? ''} ${row.reason}`.toLowerCase().includes(search.toLowerCase()))
  const busy = new Set((jobs.data ?? []).filter(j => ['queued', 'running'].includes(j.status)).flatMap(j => j.affected_datasets))
  // 一鍵: 每列挑最強的安全操作 (立即更新 > 重試 > 重新驗證)。日資料/社群同組只會起一個背景任務。
  const [updatingAll, setUpdatingAll] = useState(false)
  const updateAll = async () => {
    const targets = (health.data?.datasets ?? []).filter(row => row.actions.length && !busy.has(row.id) && row.status !== 'updating')
    setUpdatingAll(true)
    for (const row of targets) {
      const kind: DataHealthAction = row.actions.includes('update') ? 'update' : row.actions.includes('retry') ? 'retry' : 'validate'
      await action.mutateAsync({ id: row.id, kind }).catch(() => {})
    }
    setUpdatingAll(false)
  }

  return (
    <main className="p-4 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">資料健康</h1>
        <span className="text-sm text-muted">{health.data ? `${health.data.current_count} / ${health.data.total_count} 正常` : '讀取中…'}</span>
      </div>
      <p className="text-sm text-muted">聚合既有來源 metadata。未記錄的日期與時間顯示「未知」。日資料更新會一起更新日 K、法人、融資融券；社群更新沿用既有 PTT、Dcard 與 AI 流程。</p>
      <div className="flex flex-wrap gap-3">
        <input aria-label="搜尋資料集" value={search} onChange={e => setSearch(e.target.value)} placeholder="搜尋資料集或原因" className="min-w-0 w-full sm:w-auto rounded border border-border bg-surface px-3 py-2 text-sm" />
        <select aria-label="狀態篩選" value={filter} onChange={e => setFilter(e.target.value as DataHealthStatus | '')} className="min-w-0 w-full sm:w-auto rounded border border-border bg-surface px-3 py-2 text-sm">
          <option value="">所有狀態</option>
          {Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label} ({value})</option>)}
        </select>
        <button type="button" disabled={updatingAll || !health.data || jobs.isLoading || jobs.isError} onClick={() => void updateAll()} className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">{updatingAll ? '正在送出更新…' : '一鍵全部更新'}</button>
        <button type="button" disabled={health.isFetching} onClick={() => void health.refetch()} className="text-sm text-accent disabled:opacity-50">刷新健康狀態</button>
        <Link to="/settings?tab=ai" className="text-sm text-accent self-center">AI 設定</Link>
      </div>
      {health.isLoading && <p role="status">正在讀取資料健康…</p>}
      {health.isError && <p role="alert" className="text-danger">資料健康讀取失敗，請刷新健康狀態。</p>}
      {action.isError && <p role="alert" className="text-danger">操作未啟動：{action.error.message}</p>}
      {jobs.isError && <p role="alert" className="text-danger">無法讀取背景任務，暫停操作以避免重複更新。</p>}
      {health.data && <div className="overflow-x-auto rounded-card border border-border">
        <table className="w-full min-w-[950px] text-sm text-left">
          <thead className="bg-elevated"><tr>{['資料集', '狀態', '來源 / 資料日期', '新鮮度 / 原因', '最後嘗試', '最後成功', '安全操作'].map(label => <th key={label} className="p-3 font-medium">{label}</th>)}</tr></thead>
          <tbody>{rows.map(row => <tr key={row.id} className="border-t border-border align-top bg-surface">
            <th scope="row" className="p-3 font-medium">
              <div>{row.name}</div>
              {row.provider && <div className="text-xs font-normal text-muted">{row.provider}</div>}
              {row.enabled != null && <div className="text-xs font-normal text-muted">{row.enabled ? 'enabled' : 'disabled'} · {row.auth_configured ? 'auth configured' : 'auth not configured'}</div>}
            </th>
            <td className={`p-3 ${colors[row.status]}`}><span>{statuses[row.status]}</span><div className="text-xs">{row.status}</div></td>
            <td className="p-3"><div>{row.source ?? '未知'}</div><div className="text-muted">{row.as_of ?? row.data_date ?? '未知'}</div></td>
            <td className="p-3 max-w-sm break-words"><div className="text-muted text-xs mb-1">{row.freshness}</div>{row.reason}{row.error && <div className="mt-1 text-xs text-danger">{row.error}</div>}</td>
            <td className="p-3 text-xs"><time dateTime={row.last_attempt ?? undefined}>{localTime(row.last_attempt)}</time></td>
            <td className="p-3 text-xs"><time dateTime={row.last_success ?? undefined}>{localTime(row.last_success)}</time></td>
            <td className="p-3"><div className="flex flex-wrap gap-2">{row.actions.map(kind => <button type="button" key={kind} aria-label={`${row.name} ${actions[kind]}`} disabled={action.isPending || busy.has(row.id) || row.status === 'updating' || jobs.isLoading || jobs.isError}
              onClick={() => {
                if (inFlight.current) return
                inFlight.current = true
                void action.mutateAsync({ id: row.id, kind }).catch(() => {}).finally(() => { inFlight.current = false })
              }} className="rounded border border-border px-2 py-1 text-accent disabled:opacity-40">{actions[kind]}</button>)}{!row.actions.length && <span className="text-xs text-muted">僅顯示原因</span>}</div></td>
          </tr>)}</tbody>
        </table>
        {!rows.length && <p className="p-4 text-muted">沒有符合篩選條件的資料集。</p>}
      </div>}
      {!!jobs.data?.length && <section className="rounded-card border border-border bg-surface p-3">
        <h2 className="font-medium mb-2">更新與驗證任務</h2>
        <p className="text-xs text-muted mb-2">背景任務記錄於服務重啟後清除；重新驗證成功表示完成檢查，資料仍可能不可用。</p>
        <ul className="space-y-2 text-sm">{jobs.data.slice(0, 10).map(job => <li key={job.job_id} className="break-words">
          {health.data?.datasets.find(r => r.id === job.dataset)?.name ?? job.dataset} · {actions[job.action]} · {jobStatuses[job.status]} ({job.status})
          {job.reason && <span className="ml-2 text-muted">{job.reason}</span>}
        </li>)}</ul>
      </section>}
    </main>
  )
}
