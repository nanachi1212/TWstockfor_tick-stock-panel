import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type AiKeyProfile } from '@/lib/api'
import { AiKeyProfilesPanel } from './AiKeyProfiles'

const profile: AiKeyProfile = {
  id: 'aip_glm',
  name: 'GLM',
  provider: 'openai_compat',
  base_url: 'https://open.bigmodel.cn/api/paas/v4',
  model: 'glm-4.7',
  key_masked: 'glm-••••••1234',
  has_key: true,
  active: true,
  created_at: '2026-09-29T00:00:00Z',
}

vi.mock('@/lib/api', () => ({
  api: {
    aiKeyProfiles: vi.fn(),
    aiKeyProfileCreate: vi.fn(),
    aiKeyProfileUpdate: vi.fn(),
    aiKeyProfileActivate: vi.fn(),
    aiKeyProfileDelete: vi.fn(),
    aiKeyProfileTest: vi.fn(),
  },
}))

function renderPanel() {
  vi.mocked(api.aiKeyProfiles).mockResolvedValue({ profiles: [profile] })
  vi.mocked(api.aiKeyProfileUpdate).mockResolvedValue({ ok: true, profile })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AiKeyProfilesPanel />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('AiKeyProfilesPanel edit', () => {
  it('loads metadata, preserves a masked key, and keeps the active label', async () => {
    renderPanel()

    expect(await screen.findByText('目前使用')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '編輯' }))

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByLabelText('Profile 名稱')).toHaveValue('GLM')
    expect(screen.getByLabelText('Base URL')).toHaveValue(profile.base_url)
    expect(screen.getByLabelText('Model')).toHaveValue('glm-4.7')
    expect(screen.getByLabelText('API Key')).toHaveValue('')
    expect(screen.getByLabelText('API Key')).toHaveAttribute('placeholder', expect.stringContaining(profile.key_masked))

    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'glm-4.7-plus' } })
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(api.aiKeyProfileUpdate).toHaveBeenCalledWith('aip_glm', {
      name: 'GLM',
      provider: 'openai_compat',
      base_url: profile.base_url,
      model: 'glm-4.7-plus',
    }))
  })

  it('sends a replacement key only when the user enters one', async () => {
    renderPanel()
    await screen.findByText('目前使用')
    fireEvent.click(screen.getByRole('button', { name: '編輯' }))
    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'replacement-secret' } })
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(api.aiKeyProfileUpdate).toHaveBeenCalledWith('aip_glm', expect.objectContaining({
      api_key: 'replacement-secret',
    })))
  })
})

describe('AiKeyProfilesPanel subscription profiles', () => {
  it('creates a Claude subscription profile without an API key and offers model choices', async () => {
    renderPanel()
    vi.mocked(api.aiKeyProfileCreate).mockResolvedValue({ ok: true, profile: { ...profile, id: 'aip_claude', provider: 'claude_cli' } })
    await screen.findByText('目前使用')
    fireEvent.click(screen.getByRole('button', { name: '新增' }))

    fireEvent.change(screen.getByPlaceholderText('例如: free-key-1'), { target: { value: 'Claude Max' } })
    fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'claude_cli' } })

    expect(screen.queryByPlaceholderText('sk-...')).not.toBeInTheDocument()
    const model = screen.getByLabelText('訂閱模型')
    expect(model).toHaveValue('sonnet')
    expect(model).toHaveAttribute('list', 'ai-models-claude_cli')
    expect(Array.from(document.querySelectorAll('#ai-models-claude_cli option')).map(o => o.getAttribute('value')))
      .toEqual(['sonnet', 'opus', 'haiku', 'fable'])

    fireEvent.change(model, { target: { value: 'opus' } })
    fireEvent.click(screen.getAllByRole('button', { name: '新增' }).at(-1)!)
    await waitFor(() => expect(api.aiKeyProfileCreate).toHaveBeenCalledWith({
      name: 'Claude Max', provider: 'claude_cli', api_key: '', base_url: '', model: 'opus',
    }))
  })
})
