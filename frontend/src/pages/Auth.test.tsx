import { afterEach, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { api } from '@/lib/api'
import { Auth } from './Auth'

vi.mock('@/lib/api', () => ({ api: { authStatus: vi.fn() } }))
afterEach(() => { vi.clearAllMocks() })

function renderAuth() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><Auth /></MemoryRouter>
    </QueryClientProvider>,
  )
}

it('keeps setup unavailable when authentication status cannot be read', async () => {
  vi.mocked(api.authStatus).mockRejectedValue(new Error('unavailable'))
  renderAuth()
  expect(await screen.findByRole('alert')).toHaveTextContent('無法確認認證狀態')
  expect(screen.queryByPlaceholderText('訪問密碼')).not.toBeInTheDocument()
  expect(screen.queryByText('設置並進入')).not.toBeInTheDocument()
})

it('shows first setup only after a successful unconfigured response', async () => {
  vi.mocked(api.authStatus).mockResolvedValue({ configured: false, authenticated: false })
  renderAuth()
  expect(await screen.findByText('設置並進入')).toBeInTheDocument()
})
