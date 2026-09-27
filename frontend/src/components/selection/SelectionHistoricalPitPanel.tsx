import { AlertTriangle, History } from 'lucide-react'
import type {
  HistoricalPitCountDistribution,
  HistoricalPitMetricBlock,
  TrendLiquidityV1HistoricalPitResponse,
} from '@/lib/api'

const BLOCKER_LABELS: Record<string, string> = {
  tpex_instrument_subtype_blocked: '上櫃歷史普通股身份無官方 PIT 來源',
  twse_instrument_subtype_unresolved: '上市歷史證券型別未驗證',
  regulatory_history_unavailable: '進場日前的處置／停牌／下市歷史紀錄不存在',
  corporate_action_coverage_unavailable: '公司行動覆蓋未涵蓋 20 日趨勢窗',
  corporate_action_unverified: '趨勢窗內有未驗證公司行動',
  trading_day_unverified: '趨勢窗內有未驗證交易日',
  entry_session_not_observed: '來源日之後尚無已驗證交易日',
}

const HORIZONS = [['1', '1D'], ['5', '5D'], ['20', '20D']] as const

function pct(value: number | null | undefined) {
  if (value === null || value === undefined) return '無樣本'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function ratio(value: number | null | undefined) {
  return value === null || value === undefined ? '資料不足' : `${(value * 100).toFixed(2)}%`
}

function Distribution({ value }: { value: HistoricalPitCountDistribution | null }) {
  if (!value) return <p className="text-muted-foreground">無可統計的 session</p>
  return (
    <dl className="grid grid-cols-2 gap-y-1 sm:grid-cols-4">
      <dt className="text-muted-foreground">Session 數</dt><dd>{value.sessions}</dd>
      <dt className="text-muted-foreground">最小／中位／最大</dt><dd>{value.min}／{value.median}／{value.max}</dd>
      <dt className="text-muted-foreground">少於 20 檔的 session</dt><dd>{value.sessions_below_batch_size}</dd>
      <dt className="text-muted-foreground">0 檔的 session</dt><dd>{value.sessions_with_zero}</dd>
    </dl>
  )
}

function MetricTable({ title, block }: { title: string; block: HistoricalPitMetricBlock }) {
  return (
    <section className="rounded-lg border border-border/60 p-3">
      <h4 className="mb-2 font-semibold">{title}</h4>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-right">
          <thead className="text-muted-foreground">
            <tr><th className="text-left">期間</th><th>N</th><th>命中率</th><th>平均</th><th>中位數</th><th>0050 平均</th><th>平均超額</th><th>中位超額</th><th>勝 0050</th></tr>
          </thead>
          <tbody>{HORIZONS.map(([key, label]) => {
            const m = block[key]
            return <tr key={key}><td className="text-left">{label}</td><td>{m.n}</td><td>{pct(m.hit_rate_pct)}</td><td>{pct(m.avg_return_pct)}</td><td>{pct(m.median_return_pct)}</td><td>{pct(m.avg_benchmark_return_pct)}</td><td>{pct(m.avg_excess_return_pct)}</td><td>{pct(m.median_excess_return_pct)}</td><td>{pct(m.beat_benchmark_rate_pct)}</td></tr>
          })}</tbody>
        </table>
      </div>
    </section>
  )
}

export function SelectionHistoricalPitPanel({ data }: { data: TrendLiquidityV1HistoricalPitResponse }) {
  const artifact = data.artifact
  if (data.status !== 'available' || !artifact) {
    return (
      <div role="status" className="rounded-xl border border-dashed border-border/70 bg-card/40 p-8 text-center text-sm">
        <History className="mx-auto mb-3 h-9 w-9 text-muted-foreground/50" />
        <h2 className="font-medium">尚未記錄歷史 PIT 驗證</h2>
        <p className="mt-2 text-xs text-muted-foreground">歷史驗證由維護腳本執行後寫入獨立紀錄；此頁不會即時計算或估算績效。</p>
      </div>
    )
  }
  const rep = artifact.reproducibility
  const strict = artifact.strict_result
  const range = artifact.evaluation_range
  const coverage = artifact.data_coverage
  const blockers = Object.entries(rep.blocker_session_counts).sort((a, b) => b[1] - a[1])
  const diag = artifact.degraded_diagnostics
  const strictAvailable = strict.status === 'available' && strict.claimable && strict.top10 && strict.full_batch

  return (
    <div className="space-y-4 text-xs">
      <header className="rounded-xl border border-border/60 bg-card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded bg-primary/10 px-2 py-0.5 font-semibold text-primary">Historical PIT</span>
          <span className="rounded bg-muted px-2 py-0.5 text-muted-foreground">與正式前瞻批次分開記錄</span>
          <span className="font-mono text-muted-foreground">{artifact.strategy_id}</span>
        </div>
        <p className="mt-2 text-muted-foreground">
          以當時可驗證的證據重播凍結的 v1 規則；歷史證據不足的 session 一律排除，不放寬任何規則。
        </p>
      </header>

      <section aria-label="嚴格可重現結果" className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <AlertTriangle className="h-4 w-4 text-amber-500" />
          嚴格可完整重現的 session：{rep.strict_fully_reproducible_sessions}／{rep.requested_sessions}
        </h3>
        {strictAvailable ? (
          <div className="mt-3 space-y-3">
            <MetricTable title={`Top10（${strict.strict_sessions} 個 session）`} block={strict.top10!} />
            <MetricTable title="完整批次（最多 20 檔）" block={strict.full_batch!} />
            {strict.rank_groups && Object.entries(strict.rank_groups).map(([group, block]) => (
              <MetricTable key={group} title={`排名 ${group}（描述性診斷）`} block={block} />
            ))}
            {strict.by_year && Object.entries(strict.by_year).map(([year, value]) => (
              <MetricTable key={year} title={`${year} 年 Top10（${value.sessions} 個 session）`} block={value.top10} />
            ))}
          </div>
        ) : (
          <div className="mt-2 space-y-1">
            <p className="font-medium">沒有產生可宣稱有效的 v1 歷史績效。</p>
            <p>原因是歷史證據不足，不是策略報酬為 0；因此不顯示命中率、報酬或超額報酬。</p>
          </div>
        )}
      </section>

      <section aria-label="排除原因" className="rounded-xl border border-border/60 bg-card p-4">
        <h3 className="mb-2 text-sm font-semibold">排除原因（依 session 計數，可重疊）</h3>
        <ul className="space-y-1">{blockers.map(([code, count]) => (
          <li key={code} className="flex justify-between gap-3">
            <span>{BLOCKER_LABELS[code] ?? code}<span className="ml-1 font-mono text-muted-foreground">{code}</span></span>
            <span className="shrink-0 font-medium">{count}</span>
          </li>
        ))}</ul>
      </section>

      <section aria-label="資料覆蓋" className="rounded-xl border border-border/60 bg-card p-4">
        <h3 className="mb-2 text-sm font-semibold">資料覆蓋</h3>
        <dl className="grid grid-cols-1 gap-y-1 sm:grid-cols-2">
          <dt className="text-muted-foreground">評估區間</dt><dd>{range.first_requested_session ?? '—'} ～ {range.last_requested_session ?? '—'}（{rep.requested_sessions} 個 session）</dd>
          <dt className="text-muted-foreground">已驗證上市交易日</dt><dd>{range.verified_sessions}（前 {range.warmup_sessions} 日為 20 日趨勢暖機）</dd>
          <dt className="text-muted-foreground">上市交易日覆蓋</dt><dd>{ratio(coverage.twse_census?.trading_coverage_ratio)}</dd>
          <dt className="text-muted-foreground">上市普通股型別覆蓋</dt><dd>{ratio(coverage.twse_classification?.primary_classification_ratio)}</dd>
          <dt className="text-muted-foreground">公司行動覆蓋</dt><dd>{coverage.corporate_actions?.status === 'verified' ? `${coverage.corporate_actions.start} ～ ${coverage.corporate_actions.end}` : '未驗證'}</dd>
          <dt className="text-muted-foreground">上櫃普通股型別</dt><dd>無官方歷史來源（blocked）</dd>
          <dt className="text-muted-foreground">監管事件歷史</dt><dd>無 PIT 歷史紀錄</dd>
        </dl>
      </section>

      <details className="rounded-xl border border-dashed border-border/70 bg-muted/30 p-4">
        <summary className="cursor-pointer text-sm font-semibold">降級覆蓋診斷（僅供診斷，不是 v1 結果）</summary>
        <p className="mt-2 text-muted-foreground">
          僅統計已驗證型別的上市股票在價格、流動性與趨勢條件下的候選檔數；未含上櫃、未套用監管排除，不含任何報酬。
        </p>
        <div className="mt-2 space-y-1">
          <p>計算 session：{diag.sessions_computed}；公司行動未驗證而略過：{diag.sessions_corporate_action_unverified}</p>
          <Distribution value={diag.candidate_count_distribution} />
        </div>
      </details>

      <section aria-label="限制與後續資料工作" className="rounded-xl border border-border/60 bg-card p-4">
        <h3 className="mb-2 text-sm font-semibold">主要限制與後續獨立資料工作</h3>
        <ul className="list-disc space-y-1 pl-4 text-muted-foreground">
          {[...artifact.limitations, ...artifact.follow_up_data_work].map(text => <li key={text}>{text}</li>)}
        </ul>
        <p className="mt-3 break-all font-mono text-[11px] text-muted-foreground">
          spec {artifact.spec_fingerprint.slice(0, 12)} · result {artifact.result_fingerprint.slice(0, 12)} · code {artifact.code_sha.slice(0, 8)} · {artifact.generated_at}
        </p>
      </section>
    </div>
  )
}
