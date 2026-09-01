// Phase 8B-3 — 品牌/HTML metadata 回歸測試
// 涵蓋：frontend/index.html 不再標示 zh-CN 或 A 股/簡體品牌文案。
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const indexHtml = readFileSync(resolve(__dirname, '../../index.html'), 'utf-8')

describe('index.html metadata — Taiwan-first branding (Phase 8B-3)', () => {
  it('declares zh-TW, not zh-CN', () => {
    expect(indexHtml).toContain('lang="zh-TW"')
    expect(indexHtml).not.toContain('zh-CN')
  })

  it('title does not carry A-share or mainland positioning', () => {
    expect(indexHtml).toMatch(/<title>[^<]*TickFlow[^<]*<\/title>/)
    expect(indexHtml).not.toMatch(/A-SHARE/i)
  })
})
