import { useQuery } from '@tanstack/react-query'
import { Activity, Loader2, RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

function formatNumber(value: number | null | undefined, digits = 3) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'
}

function formatPct(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '—'
}

export function QuantEvaluationCard() {
  const query = useQuery({
    queryKey: QK.taiwanQuantEvaluation,
    queryFn: api.taiwanQuantEvaluation,
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  return (
    <section className="mb-1.5 rounded-card border border-border bg-surface/85 p-3.5">
      <div className="mb-2.5 flex items-center gap-1.5">
        <Activity className="h-3.5 w-3.5 text-accent" />
        <h2 className="text-xs font-semibold text-foreground">Quant Evaluation</h2>
        <span className="ml-auto text-[10px] text-muted">歷史驗證，與即時選股分開</span>
      </div>

      {query.isLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted" role="status">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          正在讀取歷史驗證狀態…
        </div>
      ) : query.isError ? (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted" role="alert">
          <span>目前無法讀取 Quant Evaluation 狀態，不影響即時選股。</span>
          <button
            type="button"
            onClick={() => void query.refetch()}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-secondary hover:bg-elevated hover:text-accent"
          >
            <RefreshCw className="h-3 w-3" /> 重試
          </button>
        </div>
      ) : query.data ? (
        <>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <span className="text-secondary">資料狀態</span>
            <span className="font-medium text-foreground">
              {query.data.status === 'processing' ? '背景整理中' :
                query.data.status === 'failed' ? '資料準備失敗' :
                  query.data.status === 'blocked' ? '尚未達到驗證條件' : '資料條件已符合'}
            </span>
            <span className="text-secondary">A2b 歷史分類</span>
            <span className="font-mono text-foreground">
              {query.data.a2b.completed}/{query.data.a2b.total} 完成，
              {query.data.a2b.pending} 待處理，{query.data.a2b.failed} 失敗
            </span>
          </div>

          {query.data.a2b.total > 0 && (
            <div
              className="mt-2 h-1.5 overflow-hidden rounded-full bg-elevated"
              role="progressbar"
              aria-label="A2b 歷史分類進度"
              aria-valuemin={0}
              aria-valuemax={query.data.a2b.total}
              aria-valuenow={query.data.a2b.completed}
            >
              <div
                className="h-full rounded-full bg-accent transition-[width]"
                style={{ width: `${Math.min(100, query.data.a2b.completed / query.data.a2b.total * 100)}%` }}
              />
            </div>
          )}

          {query.data.evaluation_status !== 'available' ? (
            <div className="mt-2 text-[11px] leading-relaxed text-muted">
              <p>
                {query.data.evaluation_status === 'waiting_for_data_health'
                  ? 'Primary OOS 尚未提供，需等資料健康門檻通過。'
                  : query.data.evaluation_status === 'ready_for_evaluation'
                    ? '歷史資料已準備完成，等待首次正式歷史驗證。'
                    : query.data.evaluation_status === 'evaluation_running'
                      ? '正式歷史驗證執行中，完成後才會顯示結果。'
                      : query.data.evaluation_status === 'failed'
                        ? '正式歷史驗證失敗，本次沒有發布指標。'
                        : '資料健康門檻已通過，歷史評估報告尚未產生。'}
              </p>
              {query.data.blocking_reasons.length > 0 && (
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {query.data.blocking_reasons.map(reason => <li key={reason}>{reason}</li>)}
                </ul>
              )}
              <p className="mt-1">即時排序與歷史 OOS 驗證使用不同資料流程。</p>
            </div>
          ) : query.data.evaluation ? (
            <div className="mt-3">
              {query.data.evaluation_provenance && (
                <p className="mb-2 text-[10px] text-muted">
                  正式 OOS，資料截至 {query.data.evaluation_provenance.latest_market_date}，
                  spec {query.data.evaluation_provenance.evaluation_spec_version}，
                  run {query.data.evaluation_provenance.run_id.slice(0, 8)}
                </p>
              )}
              <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                <Metric label="5D 指標 IC" value={formatNumber(query.data.evaluation.factor_ic.momentum_5d?.['5']?.ic_mean)} />
                <Metric label="20D 指標 IC" value={formatNumber(query.data.evaluation.factor_ic.momentum_20d?.['20']?.ic_mean)} />
                <Metric label="Composite IC (20D)" value={formatNumber(query.data.evaluation.composite_score_ic['20']?.ic_mean)} />
                <Metric label="IC 正向比例" value={formatPct(query.data.evaluation.composite_score_ic['20']?.ic_positive_ratio)} />
              </div>
              <p className="mt-2 text-[10px] text-muted">IC 表示選股分數與未來報酬排序的一致程度。</p>
              <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
                <Metric label="20D 高分組報酬" value={formatPct(query.data.evaluation.composite_score_buckets['20']?.top_bucket_future_return)} />
                <Metric label="20D 低分組報酬" value={formatPct(query.data.evaluation.composite_score_buckets['20']?.bottom_bucket_future_return)} />
                <Metric label="20D 高低分組差" value={formatPct(query.data.evaluation.composite_score_buckets['20']?.long_short_spread)} />
                <Metric label="Walk-forward folds" value={String(query.data.evaluation.walk_forward.folds.length)} />
              </div>
              <p className="mt-2 text-[10px] text-muted">高低分組差為兩組未來報酬之差，不代表可成交策略績效。</p>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border/70 bg-base/50 px-2.5 py-2">
      <div className="text-[10px] text-muted">{label}</div>
      <div className="mt-0.5 font-mono text-xs font-medium text-foreground">{value}</div>
    </div>
  )
}
