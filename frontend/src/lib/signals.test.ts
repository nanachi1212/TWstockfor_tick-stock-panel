// TAIWAN_LOCALIZATION_POLISH — Signal Library 正體化回歸測試
//
// BUILTIN_SIGNAL_DEFINITIONS 是「訊號庫」設定頁 / SignalPicker (Screener) /
// RuleEditor (Monitor) 共用的唯一權威來源 (frontend/src/lib/signals.ts 頭部
// 註解: "權威展示來源, 兩頁統一")。本次只改 name/category/description 的顯示
// 文字, id 完全不變 (與 backend backtest/strategy.py 及已保存的 rule/策略
// identifier 對齊, 也是 Monitor rule 的 saved condition.field 值)。
import { describe, expect, it } from 'vitest'
import {
  BUILTIN_SIGNAL_DEFINITIONS,
  MONITOR_INTRADAY_SIGNAL_LABELS,
  SIGNAL_LABELS,
  SIGNAL_OPTIONS,
  cnSignal,
} from './signals'

// 這份原始 id 清單抄自 audit 報告記錄的「先前約 20 個內建訊號」，用來鎖定
// id 集合本身不因本次 wording 變更而增減/改名。
const EXPECTED_BUILTIN_IDS = [
  'signal_ma_golden_5_20',
  'signal_ma_dead_5_20',
  'signal_ma_golden_20_60',
  'signal_macd_golden',
  'signal_macd_dead',
  'signal_ma20_breakout',
  'signal_ma20_breakdown',
  'signal_ma5_breakout',
  'signal_ma5_breakdown',
  'signal_ma10_breakout',
  'signal_ma10_breakdown',
  'signal_n_day_high',
  'signal_n_day_low',
  'signal_boll_breakout_upper',
  'signal_boll_breakdown_lower',
  'signal_volume_surge',
  'signal_limit_up',
  'signal_limit_down',
  'signal_limit_down_recovery',
  'signal_broken_limit_up',
]

// 已知在 audit 中發現、且本批次應已處理掉的簡體字/A股慣用語彙（用「連續漲停」
// 取代裸「连板」是本批次刻意的語意修正，不是單純字元轉換）。
const KNOWN_SIMPLIFIED_OR_A_SHARE_TERMS = [
  '趋势', '强势', '强转弱', '信号', '市场', '资金', '连板', '动能', '确认',
  '开盘价', '收盘价', '涨跌幅', '涨跌额', '换手率', '成交额', '显著', '风险',
  '阶段', '价格', '布林上轨', '布林下轨', '短线', '中线', '长期', '止盈止损',
]

describe('BUILTIN_SIGNAL_DEFINITIONS — ids unchanged', () => {
  it('has exactly the same 20 built-in signal ids as before (backend contract / saved rule identifiers)', () => {
    expect(BUILTIN_SIGNAL_DEFINITIONS.map(s => s.id).sort()).toEqual([...EXPECTED_BUILTIN_IDS].sort())
  })

  it('SIGNAL_OPTIONS (consumed by SignalPicker/RuleEditor) still exposes exactly these ids', () => {
    expect(SIGNAL_OPTIONS.sort()).toEqual([...EXPECTED_BUILTIN_IDS].sort())
  })

  it('SIGNAL_LABELS keys still match the original ids (RuleEditor/SignalPicker lookups by id keep working)', () => {
    for (const id of EXPECTED_BUILTIN_IDS) {
      expect(SIGNAL_LABELS[id]).toBeDefined()
      expect(typeof SIGNAL_LABELS[id]).toBe('string')
    }
  })
})

describe('BUILTIN_SIGNAL_DEFINITIONS — Traditional Chinese wording', () => {
  it('no known simplified-Chinese / A-share-only term remains in any name/category/description', () => {
    for (const sig of BUILTIN_SIGNAL_DEFINITIONS) {
      for (const bad of KNOWN_SIMPLIFIED_OR_A_SHARE_TERMS) {
        expect(sig.name, `signal ${sig.id} name`).not.toContain(bad)
        expect(sig.category, `signal ${sig.id} category`).not.toContain(bad)
        expect(sig.description, `signal ${sig.id} description`).not.toContain(bad)
      }
    }
  })

  it('categories are Traditional Chinese (均線/趨勢/量價/漲跌停), not the old simplified set', () => {
    const categories = new Set(BUILTIN_SIGNAL_DEFINITIONS.map(s => s.category))
    expect(categories.has('均線')).toBe(true)
    expect(categories.has('趨勢')).toBe(true)
    expect(categories.has('量價')).toBe(true)
    expect(categories.has('漲跌停')).toBe(true)
    expect(categories.has('均线')).toBe(false)
    expect(categories.has('趋势')).toBe(false)
  })

  it('signal_limit_up description uses natural Taiwan wording and the corrected "連續漲停" term, without gaining investment-advice tone', () => {
    const sig = BUILTIN_SIGNAL_DEFINITIONS.find(s => s.id === 'signal_limit_up')!
    expect(sig.description).toContain('連續漲停')
    expect(sig.description).not.toMatch(/建議|買入|賣出|進場|出場/)
  })

  it('every signal still has a non-empty description (semantics preserved, not just deleted)', () => {
    for (const sig of BUILTIN_SIGNAL_DEFINITIONS) {
      expect(sig.description.length).toBeGreaterThan(0)
    }
  })
})

describe('MONITOR_INTRADAY_SIGNAL_LABELS and cnSignal — Traditional Chinese', () => {
  it('intraday signal labels are Traditional Chinese (分時, not 分时)', () => {
    for (const label of Object.values(MONITOR_INTRADAY_SIGNAL_LABELS)) {
      expect(label).toContain('分時')
      expect(label).not.toContain('分时')
    }
  })

  it('cnSignal resolves a built-in signal id to its Traditional Chinese name', () => {
    expect(cnSignal('signal_limit_up')).toBe('漲停')
  })

  it('cnSignal resolves a technical field id to Traditional Chinese (e.g. consecutive_limit_ups)', () => {
    expect(cnSignal('consecutive_limit_ups')).toBe('連續漲停')
    expect(cnSignal('consecutive_limit_downs')).toBe('連續跌停')
  })

  it('cnSignal falls back to the raw id for unknown names (no fabricated translation)', () => {
    expect(cnSignal('csg_some_custom_signal')).toBe('csg_some_custom_signal')
  })

  it('cnSignal prefers an explicitly supplied custom-name map over the built-in tables', () => {
    expect(cnSignal('csg_1', { csg_1: '自訂訊號A' })).toBe('自訂訊號A')
  })
})
