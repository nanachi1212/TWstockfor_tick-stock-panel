import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { RouteError } from './RouteError'

function Broken(): never {
  throw new TypeError('Failed to fetch dynamically imported module: http://127.0.0.1:3018/assets/SocialSentiment-old.js')
}

describe('RouteError', () => {
  it('explains a stale build and always offers reload and a way back', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const router = createMemoryRouter([{ path: '/', element: <Broken />, errorElement: <RouteError /> }])
    render(<RouterProvider router={router} />)
    expect(screen.getByText('看板已更新，請重新整理')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新整理' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '回看板' })).toBeInTheDocument()
  })
})
