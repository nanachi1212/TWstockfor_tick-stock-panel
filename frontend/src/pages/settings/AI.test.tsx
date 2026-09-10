import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type SettingsState } from '@/lib/api'
import { useSettings } from '@/lib/useSharedQueries'
import { SettingsAIPanel } from './AI'

vi.mock('@/lib/api', () => ({
  api: {
    saveAiSettings: vi.fn().mockResolvedValue({ ok: true, ai_provider: 'openai_compat' }),
    clearAiSettings: vi.fn().mockResolvedValue({ ok: true }),
    strategyAiTest: vi.fn().mockResolvedValue({ ok: true }),
  },
}))

vi.mock('@/lib/useSharedQueries', () => ({
  useSettings: vi.fn(),
}))

const settings = (overrides: Partial<SettingsState> = {}): SettingsState => ({
  mode: 'none',
  tickflow_api_key_masked: '',
  has_tickflow_key: false,
  tier_label: '',
  current_endpoint: '',
  probe_log: [],
  missing_caps: [],
  extras_caps: [],
  onboarding_completed: true,
  ai_provider: 'openai_compat',
  ai_base_url: '',
  ai_api_key_masked: '',
  has_ai_key: false,
  ai_configured: false,
  ai_model: '',
  ai_user_agent: '',
  ...overrides,
})

function renderPanel(state: SettingsState) {
  vi.mocked(useSettings).mockReturnValue({ data: state } as ReturnType<typeof useSettings>)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingsAIPanel />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('SettingsAIPanel quick presets', () => {
  it('shows only Custom and OpenAI', () => {
    renderPanel(settings())

    const presetCard = screen.getByText('快速預設').closest('section')!
    expect(within(presetCard).getAllByRole('button').map(button => button.textContent)).toEqual(['自訂', 'OpenAI'])
  })

  it('loads a legacy provider-specific preset as Custom without rewriting persisted settings', async () => {
    renderPanel(settings({
      ai_base_url: 'https://api.deepseek.com',
      ai_model: 'deepseek-chat',
      ai_api_key_masked: 'sk-a......z',
      has_ai_key: true,
      ai_configured: true,
    }))

    await waitFor(() => {
      expect(screen.getByPlaceholderText('https://api.example.com/v1')).toHaveValue('https://api.deepseek.com')
      expect(screen.getByPlaceholderText('your-model-id')).toHaveValue('deepseek-chat')
    })
    expect(screen.getByRole('button', { name: '自訂' })).toHaveAttribute('aria-pressed', 'true')
    expect(api.saveAiSettings).not.toHaveBeenCalled()
    expect(api.clearAiSettings).not.toHaveBeenCalled()
  })

  it('filters a persisted Codex preset from the UI without deleting its stored configuration', async () => {
    renderPanel(settings({
      ai_provider: 'codex_cli',
      ai_base_url: 'https://saved-compatible.example/v1',
      ai_openai_model: 'saved-compatible-model',
      ai_configured: true,
      ai_codex_model: 'gpt-5.6-sol',
      ai_codex_command: 'codex',
      ai_codex_reasoning_effort: 'xhigh',
    }))

    await waitFor(() => expect(screen.getByRole('button', { name: '自訂' })).toHaveAttribute('aria-pressed', 'true'))
    expect(screen.queryByText(/Codex CLI/)).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText('https://api.example.com/v1')).toHaveValue('https://saved-compatible.example/v1')
    expect(screen.getByPlaceholderText('your-model-id')).toHaveValue('saved-compatible-model')
    expect(api.saveAiSettings).not.toHaveBeenCalled()
    expect(api.clearAiSettings).not.toHaveBeenCalled()
  })

  it('saves any OpenAI-compatible API through the generic Custom provider', async () => {
    renderPanel(settings())

    fireEvent.change(screen.getByPlaceholderText('https://api.example.com/v1'), {
      target: { value: 'https://integrate.api.nvidia.com/v1' },
    })
    fireEvent.change(screen.getByPlaceholderText('your-model-id'), {
      target: { value: 'nvidia/llama-3.1-nemotron-ultra-253b-v1' },
    })
    fireEvent.change(screen.getByPlaceholderText('sk-...'), {
      target: { value: 'test-key' },
    })
    fireEvent.click(screen.getByRole('button', { name: '儲存設定' }))

    await waitFor(() => expect(api.saveAiSettings).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(api.saveAiSettings).mock.calls[0][0]
    expect(payload).toMatchObject({
      provider: 'openai_compat',
      base_url: 'https://integrate.api.nvidia.com/v1',
      api_key: 'test-key',
      model: 'nvidia/llama-3.1-nemotron-ultra-253b-v1',
    })
    expect(payload).not.toHaveProperty('codex_command')
    expect(payload).not.toHaveProperty('codex_reasoning_effort')
  })
})
