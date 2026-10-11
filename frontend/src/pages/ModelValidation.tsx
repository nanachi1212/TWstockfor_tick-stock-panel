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
    waiting_for_sessions: '還在累積資料：要等滿 63 個交易日才能做正式考試',
    waiting_for_labels: '等最後一批選股的 20 日後成績出爐',
    ready: '資料已足夠，可以做一次正式考試（只能考一次）',
    evaluated: '正式考試已完成',
  }
  return labels[status] ?? status
}

function EvidenceValue({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border/70 bg-base/40 p-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-medium text-foreground">{value}</dd>
      {hint && <p className="mt-1 text-[11px] leading-4 text-muted">{hint}</p>}
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
          <p className="text-sm">目前無法讀取模型驗證狀態。缺少的成績不會用估計值代替。</p>
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
          <p className="text-xs font-medium tracking-wide text-muted">先看成績，再相信預測</p>
          <h1 className="mt-1 text-2xl font-semibold">模型驗證</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">
            白話：這頁是「選股模型的成績單」。模型用過去的資料學習，再拿它沒看過的期間考試（樣本外測試），看它挑的股票是不是真的比較會漲。
            還沒到期的成績不會拿來偷改模型，避免「考完再改答案」。
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
          <h2 id="current-model-title" className="text-lg font-semibold">目前使用的模型</h2>
        </div>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <EvidenceValue label="模型名稱" value={data.current_model.candidate_key} />
          <EvidenceValue label="模型編號" hint="用來確認是同一個版本" value={<span className="font-mono text-xs">{data.current_model.model_identity}</span>} />
          <EvidenceValue label="用了哪些指標" hint="模型挑股時參考的條件" value={data.current_model.factor_set.join(' + ')} />
          <EvidenceValue label="成績狀態" value={data.current_model.evaluation_status === 'historical_baseline_available' ? '已有歷史成績可參考' : '樣本外成績不可用'} />
        </dl>
      </section>

      <section aria-labelledby="primary-oos-title" className="rounded-card border border-border bg-surface p-4 sm:p-5">
        <div className="flex items-center gap-2">
          <Database className="h-5 w-5 text-accent" aria-hidden="true" />
          <h2 id="primary-oos-title" className="text-lg font-semibold">樣本外考試成績（模型沒看過的期間）</h2>
        </div>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <EvidenceValue label="考試編號" value={<span className="font-mono text-xs">{data.primary_oos.run_id}</span>} />
          <EvidenceValue label="考試期間" value={`${data.primary_oos.period.start} 至 ${data.primary_oos.period.end}`} />
          <EvidenceValue label="資料版本" value={<span className="font-mono text-xs">{data.primary_oos.dataset_identity}</span>} />
          <EvidenceValue label="成績是否可用" value={data.primary_oos.readiness === 'available' ? '正式成績已可用' : '正式成績不可用'} />
        </dl>
        <div className="mt-4 overflow-x-auto rounded-lg border border-border/70">
          <table className="w-full min-w-[860px] text-left text-sm">
            <thead className="bg-elevated text-xs text-muted">
              <tr>{['持有天數', '平均預測力', '中位預測力', '預測對的比例', '模型最看好組報酬', '全市場平均報酬', '模型最不看好組報酬', '好組減壞組', '最看好組比 0050', '有效天數'].map(label => <th key={label} className="p-3 font-medium">{label}</th>)}</tr>
            </thead>
            <tbody>
              {horizons.map(horizon => {
                const metric = data.primary_oos.horizons[horizon]
                return (
                  <tr key={horizon} className="border-t border-border/70">
                    <th scope="row" className="p-3 font-medium">{horizon} 日</th>
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
        <p className="mt-3 text-xs leading-5 text-muted">怎麼看：「預測力」（IC）大於 0 代表模型越看好的股票、之後真的越會漲；越接近 0 代表跟亂猜差不多，負的代表反向。「好組減壞組」越大越好；「最看好組比 0050」大於 0 代表打敗大盤。</p>
        {data.primary_oos.readiness === 'unavailable' && <p className="mt-3 text-sm text-muted">樣本外成績不可用。指標保持空白，不用估計值代替。</p>}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section aria-labelledby="diagnostics-title" className="rounded-card border border-border bg-surface p-4 sm:p-5">
          <h2 id="diagnostics-title" className="text-lg font-semibold">健康檢查</h2>
          {data.diagnostics.status === 'available' ? (
            <dl className="mt-4 space-y-3 text-sm">
              <div><dt className="text-muted">預測方向是否相反</dt><dd className="mt-1 font-medium">{data.diagnostics.negative_ic ? '是：5 日與 20 日的平均預測力都是負的（看好的反而跌）' : '否'}</dd></div>
              <div><dt className="text-muted">不同年份是否忽好忽壞</dt><dd className="mt-1 font-medium">{data.diagnostics.regime_instability ? '是：有些年份有效、有些年份反過來' : '沒有明顯忽好忽壞'}</dd></div>
              <div><dt className="text-muted">目前結論</dt><dd className="mt-1 text-base font-semibold text-foreground">{data.diagnostics.conclusion}</dd></div>
              <div><dt className="text-muted">檢查編號</dt><dd className="mt-1 break-all font-mono text-xs">{data.diagnostics.diagnostics_identity}</dd></div>
            </dl>
          ) : <p className="mt-4 text-sm text-muted">健康檢查資料不可用。</p>}
        </section>

        <section aria-labelledby="confirmatory-title" className="rounded-card border border-border bg-surface p-4 sm:p-5">
          <div className="flex items-center gap-2">
            <Clock3 className="h-5 w-5 text-accent" aria-hidden="true" />
            <h2 id="confirmatory-title" className="text-lg font-semibold">正式考試進度（未看過的新資料）</h2>
          </div>
          <p className="mt-3 text-sm font-medium">{readinessText(confirmatory.status)}</p>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <EvidenceValue label="開始日" value={confirmatory.start_session ?? '尚未開始'} />
            <EvidenceValue label="已累積交易日" value={`${confirmatory.observed_decision_sessions} / ${confirmatory.required_decision_sessions}`} />
            <EvidenceValue label="5 日成績" value={confirmatory.label_maturity['5'] ? '已出爐' : '等待中'} />
            <EvidenceValue label="20 日成績" value={confirmatory.label_maturity['20'] ? '已出爐' : '等待中'} />
          </dl>
          <p className="mt-3 text-xs leading-5 text-muted">只計算確認過的台股交易日。未滿 63 天或 20 日成績還沒出爐時，系統不會給結論，避免拿不完整的資料下判斷。</p>
        </section>
      </div>

      <section aria-labelledby="v2-title" className="rounded-card border border-border bg-surface p-4 sm:p-5">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-accent" aria-hidden="true" />
          <h2 id="v2-title" className="text-lg font-semibold">下一版候選模型（事先登記，避免事後挑最好看的）</h2>
        </div>
        <p className="mt-2 break-all font-mono text-xs text-muted">{data.v2.preregistration_id}</p>
        <ol className="mt-4 grid gap-3 md:grid-cols-3">
          {data.v2.candidates.map((candidate, index) => (
            <li key={candidate.candidate_key} className="rounded-lg border border-border/70 bg-base/40 p-4">
              <p className="text-xs font-medium text-muted">候選 {String.fromCharCode(65 + index)}</p>
              <h3 className="mt-1 break-words text-sm font-semibold">{candidate.candidate_key}</h3>
              <p className="mt-2 text-xs leading-5 text-muted">{candidate.factor_set.join(' + ')}</p>
              <p className="mt-3 break-all font-mono text-[11px] text-muted">{candidate.model_identity}</p>
            </li>
          ))}
        </ol>
      </section>

      <section aria-label="LightGBM 狀態" className="rounded-card border border-border bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><h2 className="font-semibold">進階機器學習模型（LightGBM）</h2><p className="mt-1 text-sm text-muted">要等簡單模型先在新資料上證明有效，才會考慮用更複雜的模型。</p></div>
          <span className="rounded-full border border-border px-3 py-1 text-sm font-semibold">{data.lightgbm.status === 'NOT_YET' ? '尚未啟用' : data.lightgbm.status}</span>
        </div>
      </section>
    </main>
  )
}
