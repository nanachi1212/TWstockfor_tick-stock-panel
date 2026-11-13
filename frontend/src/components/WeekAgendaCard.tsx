import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { AgendaEarnings, AgendaMacro } from '@/lib/api'

const WEEKDAY = ['日', '一', '二', '三', '四', '五', '六']
const dayLabel = (d: string) => `${d.slice(5).replace('-', '/')}（${WEEKDAY[new Date(`${d}T00:00:00`).getDay()]}）`

function byDay<T extends { date: string }>(items: T[]): [string, T[]][] {
  const groups = new Map<string, T[]>()
  for (const item of items) groups.set(item.date, [...(groups.get(item.date) ?? []), item])
  return [...groups.entries()]
}

export function WeekAgendaCard() {
  // Refetch while mounted: picks up the Sunday-noon week switch and, after a source error,
  // the backend's 5-minute retry (the app disables refetch-on-focus).
  const query = useQuery({
    queryKey: ['week-agenda'], queryFn: api.taiwanWeekAgenda, staleTime: 5 * 60_000,
    refetchInterval: q => (q.state.data?.errors.length ? 5 : 30) * 60_000,
  })
  const data = query.data
  return (
    <section aria-label="本週行事曆" className="rounded-card border border-border bg-surface p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium">本週行事曆{data && <span className="ml-2 text-xs text-muted">{dayLabel(data.start)}～{dayLabel(data.end)}</span>}</h2>
        <span className="text-xs text-muted">Forex Factory / Nasdaq</span>
      </div>
      {query.isLoading && <p className="mt-2 text-xs text-muted">讀取本週經濟數據與財報…</p>}
      {query.isError && <p role="alert" className="mt-2 text-xs text-warning">本週行事曆讀取失敗<button className="ml-2 underline" onClick={() => void query.refetch()}>重試</button></p>}
      {data && <>
        {data.errors.map(e => <p key={e} className="mt-2 text-xs text-warning">{e}</p>)}
        <div className="mt-3 grid gap-4 md:grid-cols-2">
          <div>
            <h3 className="text-xs font-semibold text-secondary">經濟數據（高、中影響；台北時間）</h3>
            {data.macro.length === 0 ? <p className="mt-1 text-xs text-muted">本週沒有高、中影響的數據。</p> : byDay(data.macro).map(([day, items]) => <div key={day} className="mt-2">
              <div className="text-xs text-muted">{dayLabel(day)}</div>
              <ul className="mt-1 space-y-1">{items.map((e: AgendaMacro) => <li key={`${e.time}-${e.title_en}-${e.country}`} className="flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className="tabular-nums text-xs text-muted">{e.holiday ? '全日' : e.time.slice(11, 16)}</span>
                <span className="text-xs text-muted">{e.country_name}</span>
                <span className={e.impact === 'High' ? 'font-medium text-warning' : ''} title={e.title_en}>{e.title}</span>
                {(e.forecast || e.previous) && <span className="text-xs text-muted tabular-nums">預估 {e.forecast || '—'}／前值 {e.previous || '—'}</span>}
              </li>)}</ul>
            </div>)}
          </div>
          <div>
            <h3 className="text-xs font-semibold text-secondary">美股財報（科技股與市值千億美元以上）</h3>
            {data.earnings.length === 0 ? <p className="mt-1 text-xs text-muted">本週沒有符合條件的財報。</p> : byDay(data.earnings).map(([day, items]) => <div key={day} className="mt-2">
              <div className="text-xs text-muted">{dayLabel(day)}（美東日期）</div>
              <ul className="mt-1 space-y-1">{items.map((e: AgendaEarnings) => <li key={`${day}-${e.symbol}`} className="flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className="text-xs text-muted">{e.timing}</span>
                <span className={e.tech ? 'font-medium' : ''}>{e.zh ?? e.name}</span>
                <span className="text-xs text-muted">{e.symbol}</span>
                {e.eps_forecast && <span className="text-xs text-muted tabular-nums">EPS 預估 {e.eps_forecast}</span>}
              </li>)}</ul>
            </div>)}
          </div>
        </div>
        <p className="mt-3 text-xs text-muted">「本週」為台北時間週一到週日，週日中午後改看下一週。資料每 3 小時更新一次，僅供行程參考。</p>
      </>}
    </section>
  )
}
