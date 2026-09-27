import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { HistoricalPitMetricBlock, TrendLiquidityV1HistoricalPitResponse } from '@/lib/api'
import { SelectionHistoricalPitPanel } from './SelectionHistoricalPitPanel'

const empty = { n: 0, pending: 0, unavailable: 0, hit_rate_pct: null, avg_return_pct: null, median_return_pct: null, benchmark_n: 0, avg_benchmark_return_pct: null, excess_n: 0, avg_excess_return_pct: null, median_excess_return_pct: null, beat_benchmark_rate_pct: null }

function response(strictSessions = 0): TrendLiquidityV1HistoricalPitResponse {
  const block: HistoricalPitMetricBlock = {
    '1': { ...empty, n: 20, hit_rate_pct: 55, avg_return_pct: 0.4, median_return_pct: 0.1, benchmark_n: 20, avg_benchmark_return_pct: 0.2, excess_n: 20, avg_excess_return_pct: 0.2, median_excess_return_pct: 0.05, beat_benchmark_rate_pct: 52 },
    '5': empty,
    '20': empty,
  }
  return {
    status: 'available',
    record_scope: 'historical_pit',
    strategy_id: 'trend_liquidity_v1',
    spec_fingerprint: 'a'.repeat(64),
    artifact: {
      artifact_type: 'historical_pit_selection_evaluation',
      record_scope: 'historical_pit',
      strategy_id: 'trend_liquidity_v1',
      spec_version: 'trend-liquidity-v1-historical-pit-1',
      spec_fingerprint: 'a'.repeat(64),
      code_fingerprint: 'b'.repeat(64),
      code_sha: 'c'.repeat(40),
      dataset_identity: 'd'.repeat(64),
      result_fingerprint: 'e'.repeat(64),
      generated_at: '2026-09-27T21:00:00+08:00',
      run_id: 'run-1',
      evaluation_range: { verified_sessions: 2859, first_verified_session: '2015-01-05', last_verified_session: '2026-09-24', warmup_sessions: 19, first_requested_session: '2015-02-02', last_requested_session: '2026-09-24', earliest_strict_session: null },
      reproducibility: {
        requested_sessions: 2840,
        strict_fully_reproducible_sessions: strictSessions,
        excluded_sessions: 2840 - strictSessions,
        blocker_session_counts: { regulatory_history_unavailable: 2840, tpex_instrument_subtype_blocked: 2840 },
        blocker_combination_counts: {},
        blocker_descriptions: {},
        excluded_session_ranges: [],
      },
      strict_result: strictSessions ? {
        status: 'available', claimable: true, strict_sessions: strictSessions, picks: 20, message: null,
        candidate_count_distribution: null, top10: block, full_batch: block, rank_groups: null, by_year: null,
      } : {
        status: 'no_strict_sample', claimable: false, strict_sessions: 0, picks: 0,
        message: 'missing evidence, not a zero return', candidate_count_distribution: null,
        top10: null, full_batch: null, rank_groups: null, by_year: null,
      },
      degraded_diagnostics: {
        diagnostic_only: true, claimable: false, label: 'NOT trend_liquidity_v1', sessions_computed: 2800,
        sessions_corporate_action_unverified: 40,
        candidate_count_distribution: { sessions: 2800, min: 0, p25: 30, median: 60, p75: 90, max: 250, mean: 65, sessions_below_batch_size: 120, sessions_with_zero: 3 },
      },
      data_coverage: {
        twse_census: { trading_coverage_ratio: 1 },
        twse_classification: { primary_classification_ratio: 0.9983 },
        corporate_actions: { status: 'verified', start: '2015-01-05', end: '2026-09-25' },
      },
      limitations: ['TPEx historical ordinary-stock subtype is BLOCKED'],
      follow_up_data_work: ['historical disposition backfill'],
    },
  }
}

describe('SelectionHistoricalPitPanel', () => {
  it('states strict=0 and shows no performance numbers without a strict sample', () => {
    render(<SelectionHistoricalPitPanel data={response()} />)
    expect(screen.getByText('Historical PIT')).toBeInTheDocument()
    expect(screen.getByText(/嚴格可完整重現的 session：0／2840/)).toBeInTheDocument()
    expect(screen.getByText('沒有產生可宣稱有效的 v1 歷史績效。')).toBeInTheDocument()
    expect(screen.getByText(/不是策略報酬為 0/)).toBeInTheDocument()
    expect(screen.queryByText('命中率')).not.toBeInTheDocument()
    // Coverage ratios are unsigned; any signed percentage would be a return figure.
    expect(screen.queryAllByText(/^[+-]\d+\.\d{2}%$/)).toHaveLength(0)
    expect(screen.queryByText('無樣本')).not.toBeInTheDocument()
    expect(screen.getByText(/上櫃歷史普通股身份無官方 PIT 來源/)).toBeInTheDocument()
    expect(screen.getByText(/僅供診斷，不是 v1 結果/)).toBeInTheDocument()
  })

  it('renders strict metrics only for a claimable strict sample, with nulls as no sample', () => {
    render(<SelectionHistoricalPitPanel data={response(12)} />)
    expect(screen.getAllByText('命中率').length).toBeGreaterThan(0)
    expect(screen.getAllByText('+55.00%').length).toBe(2)
    expect(screen.getAllByText('無樣本').length).toBeGreaterThan(0)
    expect(screen.queryByText('沒有產生可宣稱有效的 v1 歷史績效。')).not.toBeInTheDocument()
  })

  it('explains that nothing is computed before a recorded run exists', () => {
    render(<SelectionHistoricalPitPanel data={{ ...response(), status: 'not_run', artifact: null }} />)
    expect(screen.getByRole('status')).toHaveTextContent('尚未記錄歷史 PIT 驗證')
  })
})
