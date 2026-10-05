import { useQuery } from '@tanstack/react-query'
import { Activity, Clock3, Database, RefreshCw, ShieldCheck } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

function formatPct(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${(value * 100).toFixed(2)}%`
    : '—'
}

function readinessText(status: string) {
  const labels: Record<string, string> = {
    waiting_for_sessions: '等待 63 個已驗證交易日',
    waiting_for_labels: '等待最後一批 forward labels 成熟',
    ready: '已就緒，可執行一次 confirmatory evaluation',
    evaluated: '已完成 confirmatory evaluation',
  }
  return labels[status] ?? status
}

function EvidenceValue({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg border border-border/70 bg-base/40 p-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-medium text-foreground">{value}</dd>
    </div>
  )
}

export function ModelValidation() {
  const query = useQuery({
    queryKey: QK.taiwanModelValidationStatus,
    queryFn: api.taiwanModelValidationStatus,
    staleTime: 30_000,
  })

  if (query.isLoading) {
    return <main className="p-4"><p role="status" className="text-sm text-muted">正在讀取模型驗證證據…</p></main>
  }

  if (query.isError || !query.data) {
    return (
      <main className="space-y-4 p-4">
        <h1 className="text-xl font-semibold">模型驗證</h1>
        <section role="alert" className="rounded-card border border-border bg-surface p-4">
          <p className="text-sm">目前無法讀取模型驗證狀態。本頁不會用估計值代替缺少的 artifact。</p>
          <button type="button" onClick={() => void query.refetch()} className="mt-3 min-h-11 rounded-lg border border-border px-4 text-sm text-accent">
            重新讀取
          </button>
        </section>
      </main>
    )
  }

  const data = query.data
  const confirmatory = data.v2.confirmatory_window
  const horizons = ['5', '20']

  return (
    <main className="mx-auto max-w-7xl space-y-4 p-4 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted">Evidence before prediction</p>
          <h1 className="mt-1 text-2xl font-semibold">模型驗證</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">
            本頁只顯示已儲存的 Primary OOS 證據、預登記候選與 untouched confirmatory 進度。它不讀取未成熟結果來調參。
          </p>
        </div>
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching} aria-label="更新模型驗證狀態" className="flex min-h-11 items-center gap-2 rounded-lg border border-border px-4 text-sm text-accent disabled:opacity-50">
          <RefreshCw className={`h-4 w-4 ${query.isFetching ? 'animate-spin' : ''}`} aria-hidden="true" />
          更新狀態
        </button>
      </header>

      <section aria-labelledby="current-model-title" className="rounded-card border border-border bg-surface p-4 sm:p-5">
        <div className="flex items-center gap-2">
          <Activity className="h-5 w-5 text-accent" aria-hidden="true" />
          <h2 id="current-model-title" className="text-lg font-semibold">Current model</h2>
        </div>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <EvidenceValue label="Candidate" value={data.current_model.candidate_key} />
          <EvidenceValue label="Model identity" value={<span className="font-mono text-xs">{data.current_model.model_identity}</span>} />
          <EvidenceValue label="Factor set" value={data.current_model.factor_set.join(' + ')} />
          <EvidenceValue label="Evaluation status" value={data.current_model.evaluation_status === 'historical_baseline_available' ? '歷史 baseline 已可用' : 'OOS artifact 不可用'} />
        </dl>
      </section>

      <section aria-labelledby="primary-oos-title" className="rounded-card border border-border bg-surface p-4 sm:p-5">
        <div className="flex items-center gap-2">
          <Database className="h-5 w-5 text-accent" aria-hidden="true" />
          <h2 id="primary-oos-title" className="text-lg font-semibold">Primary OOS</h2>
        </div>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <EvidenceValue label="Run ID" value={<span className="font-mono text-xs">{data.primary_oos.run_id}</span>} />
          <EvidenceValue label="Period" value={`${data.primary_oos.period.start} 至 ${data.primary_oos.period.end}`} />
          <EvidenceValue label="Dataset identity" value={<span className="font-mono text-xs">{data.primary_oos.dataset_identity}</span>} />
          <EvidenceValue label="Readiness" value={data.primary_oos.readiness === 'available' ? '正式 artifact 已可用' : '正式 artifact 不可用'} />
        </dl>
        <div className="mt-4 overflow-x-auto rounded-lg border border-border/70">
          <table className="w-full min-w-[860px] text-left text-sm">
            <thead className="bg-elevated text-xs text-muted">
              <tr>{['Horizon', 'Mean IC', 'Median IC', 'Positive IC', 'Top', 'Universe', 'Bottom', 'Long-short', 'Top vs 0050', 'Valid dates'].map(label => <th key={label} className="p-3 font-medium">{label}</th>)}</tr>
            </thead>
            <tbody>
              {horizons.map(horizon => {
                const metric = data.primary_oos.horizons[horizon]
                return (
                  <tr key={horizon} className="border-t border-border/70">
                    <th scope="row" className="p-3 font-medium">{horizon}D</th>
                    <td className="p-3 tabular-nums">{formatPct(metric?.mean_ic)}</td>
                    <td className="p-3 tabular-nums">{formatPct(metric?.median_ic)}</td>
                    <td className="p-3 tabular-nums">{formatPct(metric?.positive_ic_ratio)}</td>
                    <td className="p-3 tabular-nums">{formatPct(metric?.top_bucket_return)}</td>
                    <td className="p-3 tabular-nums">{formatPct(metric?.universe_return)}</td>
                    <td className="p-3 tabular-nums">{formatPct(metric?.bottom_bucket_return)}</td>
                    <td className="p-3 tabular-nums">{formatPct(metric?.long_short)}</td>
                    <td className="p-3 tabular-nums">{formatPct(metric?.top_minus_benchmark)}</td>
                    <td className="p-3 tabular-nums">{metric?.valid_date_count ?? '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {data.primary_oos.readiness === 'unavailable' && <p className="mt-3 text-sm text-muted">OOS artifact 缺少。指標保持空值，不使用 diagnostics 或估計值取代。</p>}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section aria-labelledby="diagnostics-title" className="rounded-card border border-border bg-surface p-4 sm:p-5">
          <h2 id="diagnostics-title" className="text-lg font-semibold">Diagnostics</h2>
          {data.diagnostics.status === 'available' ? (
            <dl className="mt-4 space-y-3 text-sm">
              <div><dt className="text-muted">Negative IC</dt><dd className="mt-1 font-medium">{data.diagnostics.negative_ic ? '是，5D 與 20D 全期 mean IC 為負' : '否'}</dd></div>
              <div><dt className="text-muted">Regime instability</dt><dd className="mt-1 font-medium">{data.diagnostics.regime_instability ? '存在年度方向反轉' : '未偵測到方向反轉'}</dd></div>
              <div><dt className="text-muted">目前結論</dt><dd className="mt-1 text-base font-semibold text-foreground">{data.diagnostics.conclusion}</dd></div>
              <div><dt className="text-muted">Diagnostics identity</dt><dd className="mt-1 break-all font-mono text-xs">{data.diagnostics.diagnostics_identity}</dd></div>
            </dl>
          ) : <p className="mt-4 text-sm text-muted">Diagnostics artifact 不可用。本頁不用舊文字伯服器狀態。</p>}
        </section>

        <section aria-labelledby="confirmatory-title" className="rounded-card border border-border bg-surface p-4 sm:p-5">
          <div className="flex items-center gap-2">
            <Clock3 className="h-5 w-5 text-accent" aria-hidden="true" />
            <h2 id="confirmatory-title" className="text-lg font-semibold">Confirmatory window</h2>
          </div>
          <p className="mt-3 text-sm font-medium">{readinessText(confirmatory.status)}</p>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <EvidenceValue label="Start" value={confirmatory.start_session ?? '尚無已驗證 session'} />
            <EvidenceValue label="Decision sessions" value={`${confirmatory.observed_decision_sessions} / ${confirmatory.required_decision_sessions}`} />
            <EvidenceValue label="5D maturity" value={confirmatory.label_maturity['5'] ? '已成熟' : '待成熟'} />
            <EvidenceValue label="20D maturity" value={confirmatory.label_maturity['20'] ? '已成熟' : '待成熟'} />
          </dl>
          <p className="mt-3 text-xs leading-5 text-muted">Session 數只來自已驗證的 Taiwan trading calendar evidence。未滿 63 日或 20D label 未成熟時，evaluation 會 fail-closed。</p>
        </section>
      </div>

      <section aria-labelledby="v2-title" className="rounded-card border border-border bg-surface p-4 sm:p-5">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-accent" aria-hidden="true" />
          <h2 id="v2-title" className="text-lg font-semibold">V2 preregistration</h2>
        </div>
        <p className="mt-2 break-all font-mono text-xs text-muted">{data.v2.preregistration_id}</p>
        <ol className="mt-4 grid gap-3 md:grid-cols-3">
          {data.v2.candidates.map((candidate, index) => (
            <li key={candidate.candidate_key} className="rounded-lg border border-border/70 bg-base/40 p-4">
              <p className="text-xs font-medium text-muted">Candidate {String.fromCharCode(65 + index)}</p>
              <h3 className="mt-1 break-words text-sm font-semibold">{candidate.candidate_key}</h3>
              <p className="mt-2 text-xs leading-5 text-muted">{candidate.factor_set.join(' + ')}</p>
              <p className="mt-3 break-all font-mono text-[11px] text-muted">{candidate.model_identity}</p>
            </li>
          ))}
        </ol>
      </section>

      <section aria-label="LightGBM 狀態" className="rounded-card border border-border bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><h2 className="font-semibold">LightGBM</h2><p className="mt-1 text-sm text-muted">簡單候選尚未在 untouched block 建立穩定證據。</p></div>
          <span className="rounded-full border border-border px-3 py-1 text-sm font-semibold">{data.lightgbm.status}</span>
        </div>
      </section>
    </main>
  )
}
