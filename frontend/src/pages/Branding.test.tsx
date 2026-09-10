// Phase 8B-3 — Branding 頁品牌文案回歸測試
// 涵蓋：視覺風格預覽頁不再出現 A-SHARE 定位 tagline 或 A 股範例代碼。
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Branding } from './Branding'

describe('Branding page — no A-share positioning (Phase 8B-3)', () => {
  it('does not render any A-SHARE tagline', () => {
    render(<Branding />)
    expect(screen.queryByText(/A-SHARE/i)).not.toBeInTheDocument()
  })

  it('does not show an A-share example symbol', () => {
    render(<Branding />)
    expect(screen.queryByText('600519.SH')).not.toBeInTheDocument()
  })
})
