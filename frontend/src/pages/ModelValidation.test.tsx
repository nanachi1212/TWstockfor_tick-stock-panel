import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ModelValidation } from './ModelValidation'

vi.mock('@/lib/api', () => ({
  api: { taiwanModelValidationStatus: vi.fn() },
}))

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ModelValidation /></QueryClientProvider>)
}

const response = {
  current_model: {
    candidate_key: 'primary_v1_equal_momentum', model_identity: 'model-hash',
    factor_set: ['momentum_5d', 'momentum_20d', 'momentum_60d'],
    evaluation_status: 'historical_baseline_available',
  },
  primary_oos: {
    run_id: '2538dbd6-acc5-43ae-9061-a74149aeb9f1',
    period: { start: '2018-10-24', end: '2026-08-04' }, dataset_identity: 'dataset-hash',
    readiness: 'available', artifact_created_at: '2026-10-02T15:36:21+08:00',
    horizons: {
      '5': { mean_ic: -0.0324, median_ic: -0.029, positive_ic_ratio: 0.4116,
        top_bucket_return: 0.0053, universe_return: 0.0038, bottom_bucket_return: 0.0035,
        top_minus_universe: 0.0015, long_short: 0.0019, benchmark_symbol: '0050.TWSE',
        benchmark_return: 0.0056, top_minus_benchmark: -0.0002,
        valid_benchmark_dates: 1880, valid_date_count: 1890 },
      '20': { mean_ic: -0.0146, median_ic: -0.0007, positive_ic_ratio: 0.4963,
        top_bucket_return: 0.0214, universe_return: 0.0152, bottom_bucket_return: 0.012,
        top_minus_universe: 0.0062, long_short: 0.0094, benchmark_symbol: '0050.TWSE',
        benchmark_return: 0.0223, top_minus_benchmark: -0.0007,
        valid_benchmark_dates: 1865, valid_date_count: 1890 },
    },
  },
  diagnostics: { status: 'available', diagnostics_identity: 'diagnostic-hash', negative_ic: true,
    regime_instability: true, conclusion: '尚未證明穩定 Alpha' },
  v2: {
    preregistration_id: 'preregistration-hash', candidate_status: 'development_candidates',
    candidates: [
      { candidate_key: 'primary_v1_equal_momentum', model_identity: 'a', factor_set: ['momentum_5d', 'momentum_20d', 'momentum_60d'], factor_directions: {}, weights: {} },
      { candidate_key: 'primary_v2_momentum_60d', model_identity: 'b', factor_set: ['momentum_60d'], factor_directions: {}, weights: {} },
      { candidate_key: 'primary_v2_momentum_20d_60d', model_identity: 'c', factor_set: ['momentum_20d', 'momentum_60d'], factor_directions: {}, weights: {} },
    ],
    confirmatory_window: { start_session: '2026-08-05', required_decision_sessions: 63,
      observed_decision_sessions: 40, block_end_session: null, horizons: [5, 20],
      label_maturity: { '5': false, '20': false }, label_end_sessions: { '5': null, '20': null },
      status: 'waiting_for_sessions' },
  },
  lightgbm: { status: 'NOT_YET' },
} as const

describe('ModelValidation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows frozen OOS evidence and waiting confirmatory state without accuracy claims', async () => {
    vi.mocked(api.taiwanModelValidationStatus).mockResolvedValue(response as any)
    renderPage()

    expect(await screen.findByRole('heading', { name: '模型驗證' })).toBeInTheDocument()
    expect(screen.getByText('40 / 63')).toBeInTheDocument()
    expect(screen.getByText('尚未證明穩定 Alpha')).toBeInTheDocument()
    expect(screen.getByText('primary_v2_momentum_60d')).toBeInTheDocument()
    expect(screen.getByText('尚未啟用')).toBeInTheDocument()
    expect(screen.queryByText(/預測準確率/)).not.toBeInTheDocument()
  })

  it('keeps missing artifacts and diagnostics explicit', async () => {
    vi.mocked(api.taiwanModelValidationStatus).mockResolvedValue({
      ...response,
      current_model: { ...response.current_model, evaluation_status: 'artifact_unavailable' },
      primary_oos: { ...response.primary_oos, readiness: 'unavailable',
        horizons: { '5': {}, '20': {} } },
      diagnostics: { status: 'unavailable', diagnostics_identity: null, negative_ic: null,
        regime_instability: null, conclusion: 'diagnostics unavailable' },
    } as any)
    renderPage()

    expect(await screen.findByText('樣本外成績不可用')).toBeInTheDocument()
    expect(screen.getByText('正式成績不可用')).toBeInTheDocument()
    expect(screen.getByText(/健康檢查資料不可用/)).toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })
})
