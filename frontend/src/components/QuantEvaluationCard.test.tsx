import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { QuantEvaluationCard } from './QuantEvaluationCard'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({ api: { taiwanQuantEvaluation: vi.fn() } }))

function buildStatus(overrides: Record<string, any> = {}) {
  return {
    status: 'processing',
    generated_at: '2026-09-24T15:00:00+08:00',
    evaluation_status: 'waiting_for_data_health',
    evaluation_timestamp: null,
    available_horizons: [5, 20],
    data_health: { status: 'blocked', primary_oos_ready: false, highest_level: 'ready_for_training', blocked_reasons: {} },
    a2b: { completed: 194, pending: 284, failed: 0, total: 478, worker_status: 'running' },
    evaluation: null,
    blocking_reasons: ['A2b classification is processing (194/478 complete)'],
    ranking_modes: { live_current: '/api/taiwan/quant/live/models', historical_oos: '/api/taiwan/quant/evaluation' },
    ...overrides,
  }
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <QuantEvaluationCard />
    </QueryClientProvider>,
  )
}

afterEach(() => { vi.clearAllMocks() })

describe('QuantEvaluationCard', () => {
  it('shows A2b progress without presenting OOS metrics while processing', async () => {
    vi.mocked(api.taiwanQuantEvaluation).mockResolvedValue(buildStatus() as any)
    renderCard()

    expect(await screen.findByText('194/478 完成，284 待處理，0 失敗')).toBeInTheDocument()
    expect(screen.getByText('背景整理中')).toBeInTheDocument()
    expect(screen.getByText('Primary OOS 尚未提供，需等資料健康門檻通過。')).toBeInTheDocument()
    expect(screen.queryByText('5D 指標 IC')).not.toBeInTheDocument()
  })

  it('shows blocked data reasons and does not expose metrics', async () => {
    vi.mocked(api.taiwanQuantEvaluation).mockResolvedValue(buildStatus({
      status: 'blocked',
      a2b: { completed: 478, pending: 0, failed: 0, total: 478, worker_status: 'idle' },
      blocking_reasons: ['TWSE classification coverage 70% < 99% required for a Primary OOS claim'],
    }) as any)
    renderCard()

    expect(await screen.findByText('尚未達到驗證條件')).toBeInTheDocument()
    expect(screen.getByText(/TWSE classification coverage 70%/)).toBeInTheDocument()
    expect(screen.queryByText('5D 指標 IC')).not.toBeInTheDocument()
  })

  it('renders verified evaluation metrics once the API exposes an available report', async () => {
    vi.mocked(api.taiwanQuantEvaluation).mockResolvedValue(buildStatus({
      status: 'ready',
      evaluation_status: 'available',
      data_health: { status: 'ready', primary_oos_ready: true, highest_level: 'ready_for_primary_oos', blocked_reasons: {} },
      a2b: { completed: 478, pending: 0, failed: 0, total: 478, worker_status: 'idle' },
      evaluation: {
        horizons: [5, 20],
        factor_ic: {
          momentum_5d: { '5': { ic_mean: 0.11, ic_std: 0.02, ic_positive_ratio: 0.7, n_dates: 80 } },
          momentum_20d: { '20': { ic_mean: 0.08, ic_std: 0.02, ic_positive_ratio: 0.65, n_dates: 60 } },
        },
        composite_score_ic: {
          '5': { ic_mean: 0.12, ic_std: 0.03, ic_positive_ratio: 0.7, n_dates: 80 },
          '20': { ic_mean: 0.09, ic_std: 0.02, ic_positive_ratio: 0.65, n_dates: 60 },
        },
        composite_score_buckets: {
          '20': { top_bucket_future_return: 0.08, bottom_bucket_future_return: -0.02, long_short_spread: 0.1, n_dates: 60 },
        },
        walk_forward: { folds: [{ index: 0, test_start: '2025-01-01', test_end: '2025-06-01' }], oos: {} },
      },
      blocking_reasons: [],
    }) as any)
    renderCard()

    expect(await screen.findByText('資料條件已符合')).toBeInTheDocument()
    expect(screen.getByText('5D 指標 IC')).toBeInTheDocument()
    expect(screen.getByText('20D 指標 IC')).toBeInTheDocument()
    expect(screen.getByText('Composite IC (20D)')).toBeInTheDocument()
    expect(screen.getByText('0.090')).toBeInTheDocument()
    expect(screen.getByText('10.00%')).toBeInTheDocument()
  })

  it('shows an API error and retry action without crashing', async () => {
    vi.mocked(api.taiwanQuantEvaluation).mockRejectedValue(new Error('network failure'))
    renderCard()

    expect(await screen.findByRole('alert')).toHaveTextContent('目前無法讀取 Quant Evaluation 狀態')
    expect(screen.getByRole('button', { name: '重試' })).toBeInTheDocument()
  })
})
