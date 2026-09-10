import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const read = (relativePath: string) => readFileSync(resolve(__dirname, relativePath), 'utf-8')

describe('Taiwan History Daily Data Bootstrap & UI Card', () => {
  it('renders history status, 31 MiB package info, date range, and action buttons in TaiwanHistoryDataCard', () => {
    const cardCode = read('TaiwanHistoryDataCard.tsx')
    expect(cardCode).toContain('台股歷史日 K 資料庫')
    expect(cardCode).toContain('資料已就緒')
    expect(cardCode).toContain('缺少歷史日 K')
    expect(cardCode).toContain('最早交易日')
    expect(cardCode).toContain('最新交易日')
    expect(cardCode).toContain('涵蓋交易天數')
    expect(cardCode).toContain('更新到最新')
    expect(cardCode).toContain('一鍵下載官方歷史日 K 資料包')
    expect(cardCode).toContain('2024-01-02 ~ 2026-09-10')
    expect(cardCode).toContain('taiwanHistoryStatus')
    expect(cardCode).toContain('taiwanBootstrapRun')
    expect(cardCode).toContain('taiwanUpdateLatest')
    expect(cardCode).toContain('taiwanBootstrapJob')
  })

  it('embeds TaiwanHistoryDataCard inside TaiwanOfficialDetail in DataSources.tsx', () => {
    const dataSourcesCode = read('DataSources.tsx')
    expect(dataSourcesCode).toContain("import { TaiwanHistoryDataCard } from './TaiwanHistoryDataCard'")
    expect(dataSourcesCode).toContain('<TaiwanHistoryDataCard />')
    expect(dataSourcesCode).toContain('台灣官方資料源 (TWSE/TPEx)')
  })

  it('exposes typed API methods and data contracts in api.ts and queryKeys.ts', () => {
    const apiCode = read('../../lib/api.ts')
    expect(apiCode).toContain('taiwanHistoryStatus:')
    expect(apiCode).toContain('taiwanBootstrapRun:')
    expect(apiCode).toContain('taiwanBootstrapJob:')
    expect(apiCode).toContain('taiwanUpdateLatest:')
    expect(apiCode).toContain('interface TaiwanHistoryStatus')
    expect(apiCode).toContain('interface TaiwanBootstrapJobState')

    const qkCode = read('../../lib/queryKeys.ts')
    expect(qkCode).toContain('taiwanHistoryStatus:')
  })
})
