import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { PageBackButton } from './PageBackButton'

function at(paths: string[], index: number) {
  return (
    <MemoryRouter initialEntries={paths} initialIndex={index}>
      <Routes>
        {['/', '/selection-review', '/stocks/:symbol'].map(path => (
          <Route key={path} path={path} element={<><PageBackButton /><p>{`PAGE ${path}`}</p></>} />
        ))}
      </Routes>
    </MemoryRouter>
  )
}

describe('PageBackButton', () => {
  it('shows on inner pages and goes back; hidden on the dashboard and pages with their own back', () => {
    render(at(['/', '/selection-review'], 1))
    fireEvent.click(screen.getByRole('button', { name: '上一頁' }))
    expect(screen.getByText('PAGE /')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '上一頁' })).not.toBeInTheDocument()
  })

  it('falls back to the dashboard when there is no history', () => {
    render(at(['/selection-review'], 0))
    fireEvent.click(screen.getByRole('button', { name: '上一頁' }))
    expect(screen.getByText('PAGE /')).toBeInTheDocument()
  })

  it('does not duplicate the stock page back button', () => {
    render(at(['/stocks/2330.TWSE'], 0))
    expect(screen.queryByRole('button', { name: '上一頁' })).not.toBeInTheDocument()
  })
})
