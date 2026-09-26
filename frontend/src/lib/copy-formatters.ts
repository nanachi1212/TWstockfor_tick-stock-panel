/**
 * Deterministic formatters for "Export / Copy for External AI".
 *
 * Requirements:
 *   - Pure local computation — no AI API calls, no network requests.
 *   - Never include: API keys, tokens, secrets, filesystem paths, tracebacks, internal debug data.
 *   - Portfolio positions privacy:
 *       includePortfolio=false (default): never output shares, average_cost, invested_amount, pnl.
 *       includePortfolio=true: only included when user explicitly checks the privacy toggle.
 *   - Output: Structured Markdown / plain text (not raw JSON dump).
 *   - missing != 0: Missing fields must be explicitly marked as "不可用 (unavailable)" or "—", never 0.
 *   - Official events and news must be strictly separated.
 *   - Prompt Footer follows the 8 external AI analysis guidelines.
 */

export const EXTERNAL_AI_PROMPT_FOOTER = `
---
【請只根據以上提供的資料與你明確標示的推論進行分析】
1. 區分資料事實與推論。
2. 不得把缺失資料當成 0。
3. 不得捏造價格、財報、籌碼、事件或新聞。
4. 指出資料日期與可能過期的資料。
5. 分析基本面、籌碼面、技術面、事件與市場環境。
6. 列出主要正面因素、負面因素與不確定性。
7. 列出接下來值得追蹤的訊號。
8. 資料不足時明確指出，不自行補值。
`.trim()

// ─── Helpers ────────────────────────────────────────────────────────────────

export function sanitize(v: unknown): string {
  if (v === null || v === undefined) return '不可用 (unavailable)'
  const s = String(v).trim()
  if (!s) return '不可用 (unavailable)'
  // Mask anything that looks like a key, token or secret
  if (/^(sk-|ghp_|glpat-|ey[A-Za-z0-9_-]{10,}|token|bearer|secret|password)/i.test(s)) {
    return '[敏感資訊已隱藏]'
  }
  // Mask filesystem paths
  if (/[A-Za-z]:\\|\/(?:home|Users|var|etc|opt|tmp)\//i.test(s)) {
    return '[路徑已隱藏]'
  }
  return s
}

export function num(v: unknown, decimals = 2, fallback = '不可用 (unavailable)'): string {
  if (v === null || v === undefined || v === '') return fallback
  const n = Number(v)
  if (!Number.isFinite(n)) return fallback
  return n.toFixed(decimals)
}

export function pct(v: unknown, decimals = 2, fallback = '不可用 (unavailable)'): string {
  if (v === null || v === undefined || v === '') return fallback
  const n = Number(v)
  if (!Number.isFinite(n)) return fallback
  return `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`
}

// ─── 1. Stock Detail Formatter ───────────────────────────────────────────────

export interface StockDetailCopyData {
  symbol: string
  name: string
  exchange?: string | null
  instrument_type?: string | null
  sector?: string | null
  data_as_of?: string | null
  freshness?: string | null
  quote?: {
    close?: number | null
    change?: number | null
    change_pct?: number | null
    open?: number | null
    high?: number | null
    low?: number | null
    volume?: number | null
    turnover?: number | null
    quote_time?: string | null
  } | null
  quant?: {
    score?: number | null
    rank?: number | null
    reasons?: string[] | null
    factors?: Record<string, number | null> | null
  } | null
  valuation?: {
    pe?: number | null
    pb?: number | null
    dividend_yield?: number | null
  } | null
  fundamentals?: {
    revenue_yoy?: number | null
    revenue_mom?: number | null
    revenue_date?: string | null
    eps?: number | null
    eps_date?: string | null
    gross_margin?: number | null
    operating_margin?: number | null
    net_margin?: number | null
  } | null
  institutional_flows?: {
    foreign_buy_sell?: number | null
    trust_buy_sell?: number | null
    dealer_buy_sell?: number | null
    total_buy_sell?: number | null
    date?: string | null
  } | null
  foreign_shareholding?: {
    ratio?: number | null
    change_20d?: number | null
  } | null
  margin_lending?: {
    margin_balance?: number | null
    short_balance?: number | null
    lending_balance?: number | null
  } | null
  market_context?: {
    taiex_close?: number | null
    taiex_change_pct?: number | null
    sentiment?: string | null
  } | null
  technical_summary?: string | null
  official_events?: Array<{
    event_date: string
    event_type_label: string
    title: string
    summary?: string | null
    source?: string | null
  }> | null
  news?: Array<{
    published_at?: string | null
    title: string
    source?: string | null
    summary?: string | null
  }> | null
  portfolioPositions?: Array<{
    symbol: string
    shares: number
    avg_cost?: number | null
    invested_amount?: number | null
    unrealized_pnl?: number | null
    unrealized_pnl_pct?: number | null
  }> | null
  includePortfolio?: boolean
  aiReport?: string | null
}

export function formatStockDetailCopy(d: StockDetailCopyData): string {
  const lines: string[] = []
  lines.push(`# 個股資料：${sanitize(d.name)}（${sanitize(d.symbol)}）`)
  lines.push(`- 市場：${sanitize(d.exchange ?? 'TWSE/TPEX')} | 類別：${sanitize(d.instrument_type ?? '股票')} | 產業：${sanitize(d.sector)}`)
  lines.push(`- 資料日期 (as_of)：${sanitize(d.data_as_of)} | 資料狀態：${sanitize(d.freshness ?? 'fresh')}`)
  lines.push('')

  // 行情與技術面
  lines.push('## 行情資訊')
  if (d.quote && d.quote.close != null) {
    lines.push(`- 收盤價：${num(d.quote.close)} 元`)
    lines.push(`- 漲跌幅：${pct(d.quote.change_pct)}（漲跌：${num(d.quote.change)} 元）`)
    lines.push(`- 開高低：開盤 ${num(d.quote.open)} | 最高 ${num(d.quote.high)} | 最低 ${num(d.quote.low)}`)
    lines.push(`- 成交量：${num(d.quote.volume, 0)} 張 | 成交金額：${num(d.quote.turnover, 0)} 千元`)
    if (d.quote.quote_time) lines.push(`- 行情時間戳：${sanitize(d.quote.quote_time)}`)
  } else {
    lines.push('- 即時行情：不可用 (unavailable)')
  }
  if (d.technical_summary) {
    lines.push(`- 技術摘要：${sanitize(d.technical_summary)}`)
  }
  lines.push('')

  // 量化評分與因子
  lines.push('## 量化分析')
  if (d.quant && (d.quant.score != null || d.quant.rank != null)) {
    if (d.quant.score != null) lines.push(`- 量化總分：${num(d.quant.score)}`)
    if (d.quant.rank != null) lines.push(`- 市場排名：第 ${d.quant.rank} 名`)
    if (d.quant.reasons && d.quant.reasons.length > 0) {
      lines.push(`- 評分特徵：${d.quant.reasons.map(r => sanitize(r)).join('；')}`)
    }
    if (d.quant.factors && Object.keys(d.quant.factors).length > 0) {
      lines.push('- 因子明細：')
      for (const [k, v] of Object.entries(d.quant.factors)) {
        lines.push(`  - ${k}: ${num(v)}`)
      }
    }
  } else {
    lines.push('- 量化評分：不可用 (unavailable)')
  }
  lines.push('')

  // 估值與基本面
  lines.push('## 估值與基本面')
  if (d.valuation) {
    lines.push(`- 本益比 (P/E)：${num(d.valuation.pe)} | 股價淨值比 (P/B)：${num(d.valuation.pb)} | 現金殖利率：${pct(d.valuation.dividend_yield)}`)
  } else {
    lines.push('- 估值指標：不可用 (unavailable)')
  }
  if (d.fundamentals) {
    lines.push(`- 營收年增率 (YoY)：${pct(d.fundamentals.revenue_yoy)} | 月增率 (MoM)：${pct(d.fundamentals.revenue_mom)}${d.fundamentals.revenue_date ? ` (${d.fundamentals.revenue_date})` : ''}`)
    lines.push(`- 每股盈餘 (EPS)：${num(d.fundamentals.eps)} 元${d.fundamentals.eps_date ? ` (${d.fundamentals.eps_date})` : ''}`)
    if (d.fundamentals.gross_margin != null || d.fundamentals.operating_margin != null || d.fundamentals.net_margin != null) {
      lines.push(`- 獲利能力：毛利率 ${pct(d.fundamentals.gross_margin)} | 營業利益率 ${pct(d.fundamentals.operating_margin)} | 稅後淨利率 ${pct(d.fundamentals.net_margin)}`)
    }
  } else {
    lines.push('- 營收與獲利：不可用 (unavailable)')
  }
  lines.push('')

  // 籌碼面
  lines.push('## 籌碼面資訊')
  if (d.institutional_flows) {
    const f = d.institutional_flows
    lines.push(`- 三大法人買賣超${f.date ? ` (${f.date})` : ''}：`)
    lines.push(`  - 外資：${num(f.foreign_buy_sell, 0)} 張`)
    lines.push(`  - 投信：${num(f.trust_buy_sell, 0)} 張`)
    lines.push(`  - 自營商：${num(f.dealer_buy_sell, 0)} 張`)
    lines.push(`  - 合計買賣超：${num(f.total_buy_sell, 0)} 張`)
  } else {
    lines.push('- 法人買賣超：不可用 (unavailable)')
  }
  if (d.foreign_shareholding) {
    lines.push(`- 外資持股比例：${pct(d.foreign_shareholding.ratio)} | 外資20日持股變動：${pct(d.foreign_shareholding.change_20d)}`)
  }
  if (d.margin_lending) {
    lines.push(`- 信用與借券：融資餘額 ${num(d.margin_lending.margin_balance, 0)} 張 | 融券餘額 ${num(d.margin_lending.short_balance, 0)} 張 | 借券賣出餘額 ${num(d.margin_lending.lending_balance, 0)} 張`)
  }
  lines.push('')

  // 市場環境
  if (d.market_context) {
    lines.push('## 市場環境')
    lines.push(`- 加權指數收盤：${num(d.market_context.taiex_close)} (${pct(d.market_context.taiex_change_pct)}) | 市場情緒：${sanitize(d.market_context.sentiment)}`)
    lines.push('')
  }

  // 官方重大事件（與新聞嚴格分開）
  lines.push('## 官方事件 (Official Events)')
  if (d.official_events && d.official_events.length > 0) {
    for (const ev of d.official_events.slice(0, 10)) {
      lines.push(`- [${sanitize(ev.event_date)}] 【${sanitize(ev.event_type_label)}】${sanitize(ev.title)}`)
      if (ev.summary) lines.push(`  摘要：${sanitize(ev.summary)}`)
    }
  } else {
    lines.push('- 近期無重大官方處置或公告事件。')
  }
  lines.push('')

  // 新聞報導
  lines.push('## 相關新聞 (News)')
  if (d.news && d.news.length > 0) {
    for (const n of d.news.slice(0, 8)) {
      lines.push(`- [${sanitize(n.published_at || '近期')}] ${sanitize(n.title)}${n.source ? ` (${sanitize(n.source)})` : ''}`)
      if (n.summary) lines.push(`  ${sanitize(n.summary)}`)
    }
  } else {
    lines.push('- 近期無相關新聞或新聞資料未提供。')
  }
  lines.push('')

  // 個人持股資料（受隱私開關保護，預設嚴禁輸出）
  lines.push('## 個人持股狀況')
  if (d.includePortfolio) {
    const match = (d.portfolioPositions || []).filter(
      p => p.symbol.split('.')[0] === d.symbol.split('.')[0]
    )
    if (match.length > 0) {
      for (const p of match) {
        lines.push(`- 持股數量：${p.shares} 張`)
        lines.push(`- 平均成本：${p.avg_cost != null ? num(p.avg_cost) + ' 元' : '不可用'}`)
        if (p.invested_amount != null) lines.push(`- 投入金額：${num(p.invested_amount, 0)} 元`)
        if (p.unrealized_pnl != null) lines.push(`- 未實現損益：${num(p.unrealized_pnl, 0)} 元 (${pct(p.unrealized_pnl_pct)})`)
      }
    } else {
      lines.push('- 目前未持有此標的。')
    }
  } else {
    lines.push('- 個人持股資料：未包含（隱私保護已啟用）。')
  }
  lines.push('')

  if (d.aiReport) {
    lines.push('## 系統既有 AI 分析紀錄')
    lines.push(d.aiReport.slice(0, 3000))
    lines.push('')
  }

  return lines.join('\n')
}

export function formatStockDetailPrompt(d: StockDetailCopyData): string {
  const data = formatStockDetailCopy(d)
  return `以下是 ${sanitize(d.name)}（${sanitize(d.symbol)}）的台股完整資料：\n\n${data}\n\n${EXTERNAL_AI_PROMPT_FOOTER}`
}

// ─── 2. Daily Brief Formatter ────────────────────────────────────────────────

export interface DailyBriefCopyData {
  brief_date?: string | null
  evidence_date?: string | null
  freshness?: string | null
  market?: {
    taiex_close?: number | null
    change?: number | null
    change_pct?: number | null
    volume?: number | null
    turnover?: number | null
    advances?: number | null
    declines?: number | null
    unchanged?: number | null
    limit_up?: number | null
    limit_down?: number | null
    sentiment_label?: string | null
    foreign_net?: number | null
    trust_net?: number | null
    dealer_net?: number | null
  } | null
  sectors?: {
    strong?: Array<{ name: string; change_pct?: number | null }> | null
    weak?: Array<{ name: string; change_pct?: number | null }> | null
  } | null
  portfolio?: {
    holdings_count?: number
    items?: Array<{
      symbol: string
      name: string
      shares?: number
      avg_cost?: number
      close?: number | null
      change_pct?: number | null
    }>
  } | null
  includePortfolio?: boolean
  watchlist?: Array<{
    symbol: string
    name: string
    close?: number | null
    change_pct?: number | null
    quant_score?: number | null
  }> | null
  candidates?: Array<{
    symbol: string
    name: string
    score?: number | null
    rank?: number | null
    reason?: string | null
    close?: number | null
    change_pct?: number | null
  }> | null
  events?: Array<{
    symbol?: string
    name?: string
    date?: string
    type?: string
    title: string
  }> | null
  news?: Array<{
    title: string
    source?: string
    date?: string
  }> | null
  aiSummary?: string | null
}

export function formatDailyBriefCopy(d: DailyBriefCopyData): string {
  const lines: string[] = []
  lines.push(`# 台股每日市場晨報 / 晚報`)
  lines.push(`- 晨報日期：${sanitize(d.brief_date)} | 數據基礎日 (evidence_date)：${sanitize(d.evidence_date || d.brief_date)}`)
  lines.push(`- 資料狀態：${sanitize(d.freshness ?? 'fresh')}`)
  lines.push('')

  // 市場概況與廣度
  lines.push('## 大盤概況與市場廣度')
  if (d.market) {
    const m = d.market
    lines.push(`- 加權指數：${num(m.taiex_close)} 點 | 漲跌：${num(m.change)} (${pct(m.change_pct)})`)
    lines.push(`- 成交金額：約 ${num(m.turnover, 0)} 億元 | 市場情緒：${sanitize(m.sentiment_label)}`)
    lines.push(`- 市場廣度：上漲 ${num(m.advances, 0, '—')} 家 (漲停 ${num(m.limit_up, 0, '0')}) | 下跌 ${num(m.declines, 0, '—')} 家 (跌停 ${num(m.limit_down, 0, '0')}) | 平盤 ${num(m.unchanged, 0, '—')} 家`)
    lines.push(`- 三大法人買賣超：外資 ${num(m.foreign_net, 0)} 億元 | 投信 ${num(m.trust_net, 0)} 億元 | 自營商 ${num(m.dealer_net, 0)} 億元`)
  } else {
    lines.push('- 市場行情資料：不可用 (unavailable)')
  }
  lines.push('')

  // 族群強弱
  if (d.sectors && ((d.sectors.strong && d.sectors.strong.length > 0) || (d.sectors.weak && d.sectors.weak.length > 0))) {
    lines.push('## 產業族群強弱')
    if (d.sectors.strong && d.sectors.strong.length > 0) {
      lines.push(`- 強勢族群：${d.sectors.strong.map(s => `${sanitize(s.name)} (${pct(s.change_pct)})`).join('、')}`)
    }
    if (d.sectors.weak && d.sectors.weak.length > 0) {
      lines.push(`- 弱勢族群：${d.sectors.weak.map(s => `${sanitize(s.name)} (${pct(s.change_pct)})`).join('、')}`)
    }
    lines.push('')
  }

  // 個人持股（隱私保護）
  lines.push('## 個人持股狀況')
  if (d.includePortfolio) {
    if (d.portfolio && d.portfolio.items && d.portfolio.items.length > 0) {
      lines.push(`- 持股檔數：${d.portfolio.holdings_count ?? d.portfolio.items.length} 檔`)
      for (const it of d.portfolio.items) {
        lines.push(`  - ${sanitize(it.name)}（${sanitize(it.symbol)}）：${it.shares ?? 0} 張 | 均成本 ${num(it.avg_cost)} 元 | 最新價 ${num(it.close)} (${pct(it.change_pct)})`)
      }
    } else {
      lines.push('- 目前投資組合無持股。')
    }
  } else {
    const count = d.portfolio?.holdings_count ?? d.portfolio?.items?.length ?? 0
    lines.push(`- 個人持股詳細資料：未包含（隱私保護已啟用，持有檔數：${count} 檔）。`)
  }
  lines.push('')

  // 自選股追蹤
  if (d.watchlist && d.watchlist.length > 0) {
    lines.push('## 自選股動態')
    for (const w of d.watchlist.slice(0, 15)) {
      lines.push(`- ${sanitize(w.name)}（${sanitize(w.symbol)}）：收盤 ${num(w.close)} (${pct(w.change_pct)})${w.quant_score != null ? ` | 量化分 ${num(w.quant_score)}` : ''}`)
    }
    lines.push('')
  }

  // 選股候選
  if (d.candidates && d.candidates.length > 0) {
    lines.push('## 今日量化選股候選標的')
    for (const c of d.candidates.slice(0, 20)) {
      const score = c.score != null ? ` | 量化分 ${num(c.score)}` : ''
      const reason = c.reason ? ` | 理由：${sanitize(c.reason)}` : ''
      lines.push(`- ${sanitize(c.name)}（${sanitize(c.symbol)}）：收盤 ${num(c.close)} (${pct(c.change_pct)})${score}${reason}`)
    }
    lines.push('')
  }

  // 重大事件
  if (d.events && d.events.length > 0) {
    lines.push('## 市場重點事件')
    for (const ev of d.events.slice(0, 10)) {
      lines.push(`- [${sanitize(ev.date || '近期')}] ${ev.type ? `【${sanitize(ev.type)}】` : ''}${ev.name ? `${sanitize(ev.name)}: ` : ''}${sanitize(ev.title)}`)
    }
    lines.push('')
  }

  // 重點新聞
  if (d.news && d.news.length > 0) {
    lines.push('## 今日重要新聞')
    for (const n of d.news.slice(0, 8)) {
      lines.push(`- [${sanitize(n.date || '今日')}] ${sanitize(n.title)}${n.source ? ` (${sanitize(n.source)})` : ''}`)
    }
    lines.push('')
  }

  if (d.aiSummary) {
    lines.push('## 系統既有 AI 摘要紀錄')
    lines.push(d.aiSummary.slice(0, 3000))
    lines.push('')
  }

  return lines.join('\n')
}

export function formatDailyBriefPrompt(d: DailyBriefCopyData): string {
  const data = formatDailyBriefCopy(d)
  return `以下是台股每日市場摘要報告：\n\n${data}\n\n${EXTERNAL_AI_PROMPT_FOOTER}`
}

// ─── 3. Screener Formatter ───────────────────────────────────────────────────

export interface ScreenerCopyData {
  strategyName?: string | null
  as_of?: string | null
  condition_summary?: string | null
  coverage?: {
    total_universe?: number | null
    covered_count?: number | null
    coverage_pct?: number | null
    insufficient_reason?: string | null
  } | null
  missing_datasets?: string[] | null
  total_selected?: number | null
  results?: Array<{
    symbol: string
    name: string
    price?: number | null
    change_pct?: number | null
    score?: number | null
    rank?: number | null
    match_reasons?: string[] | null
    fundamental_summary?: string | null
    chips_summary?: string | null
    event_risk_summary?: string | null
  }> | null
}

export function formatScreenerCopy(d: ScreenerCopyData): string {
  const lines: string[] = []
  lines.push(`# 台股量化選股結果：${sanitize(d.strategyName) || '自訂篩選策略'}`)
  lines.push(`- 資料日期 (as_of)：${sanitize(d.as_of)}`)
  if (d.condition_summary) lines.push(`- 篩選條件：${sanitize(d.condition_summary)}`)
  lines.push('')

  // 覆蓋率檢查 (Coverage sufficiency)
  if (d.coverage) {
    const cov = d.coverage
    const covPct = cov.coverage_pct != null ? `${(cov.coverage_pct * 100).toFixed(1)}%` : '不可用'
    lines.push(`- 股票池覆蓋率：${covPct}（已涵蓋 ${num(cov.covered_count, 0, '—')} / 總市場 ${num(cov.total_universe, 0, '—')} 檔）`)
    if (cov.insufficient_reason) {
      lines.push(`- ⚠️ 覆蓋率不足警告：${sanitize(cov.insufficient_reason)}`)
    }
  }
  if (d.missing_datasets && d.missing_datasets.length > 0) {
    lines.push(`- ⚠️ 缺漏資料集：${d.missing_datasets.map(m => sanitize(m)).join('、')}（缺失項目未當作 0 計算）`)
  }
  lines.push('')

  if (d.results && d.results.length > 0) {
    const count = d.total_selected ?? d.results.length
    lines.push(`## 符合條件股票清單（共 ${count} 檔）`)
    lines.push('')
    for (const [idx, r] of d.results.slice(0, 50).entries()) {
      const rank = r.rank != null ? `排名 #${r.rank}` : `#${idx + 1}`
      const score = r.score != null ? ` | 量化分 ${num(r.score)}` : ''
      const price = r.price != null ? ` | 收盤 ${num(r.price)} 元 (${pct(r.change_pct)})` : ''
      lines.push(`### ${rank} ${sanitize(r.name)}（${sanitize(r.symbol)}）${price}${score}`)
      if (r.match_reasons && r.match_reasons.length > 0) {
        lines.push(`- 符合條件：${r.match_reasons.map(m => sanitize(m)).join('；')}`)
      }
      if (r.fundamental_summary) lines.push(`- 基本面：${sanitize(r.fundamental_summary)}`)
      if (r.chips_summary) lines.push(`- 籌碼面：${sanitize(r.chips_summary)}`)
      if (r.event_risk_summary) lines.push(`- 事件風險：${sanitize(r.event_risk_summary)}`)
      lines.push('')
    }
  } else {
    lines.push('## 選股結果')
    lines.push('本次篩選無符合條件的股票，或因資料不齊全未輸出。')
    lines.push('')
  }

  return lines.join('\n')
}

export function formatScreenerPrompt(d: ScreenerCopyData): string {
  const data = formatScreenerCopy(d)
  return `以下是台股量化選股結果資料：\n\n${data}\n\n${EXTERNAL_AI_PROMPT_FOOTER}`
}

// ─── 4. Selection Review Formatter ───────────────────────────────────────────

export interface SelectionReviewCopyData {
  strategy_id?: string | null
  strategy_name?: string | null
  snapshot_date?: string | null
  as_of_date?: string | null
  status?: string | null
  original_conditions?: Record<string, any> | string | null
  market_context?: string | null
  picks?: Array<{
    symbol: string
    name: string
    rank?: number | null
    quant_score?: number | null
    initial_price?: number | null
    h5d_status?: string | null
    h5d_return_pct?: number | null
    h20d_status?: string | null
    h20d_return_pct?: number | null
    match_reasons?: string[] | null
  }> | null
  strategy_stats?: {
    win_rate_5d?: number | null
    avg_return_5d?: number | null
    win_rate_20d?: number | null
    avg_return_20d?: number | null
    benchmark_5d?: number | null
    benchmark_20d?: number | null
    excess_return_5d?: number | null
    excess_return_20d?: number | null
  } | null
  condition_stats?: Array<{
    condition_name: string
    pass_count: number
    win_rate_5d?: number | null
  }> | null
}

export function formatSelectionReviewCopy(d: SelectionReviewCopyData): string {
  const lines: string[] = []
  lines.push(`# 選股策略復盤回顧：${sanitize(d.strategy_name) || '選股快照'}`)
  lines.push(`- 策略識別碼 (ID)：${sanitize(d.strategy_id || 'custom')}`)
  lines.push(`- 快照建立日期：${sanitize(d.snapshot_date)} | 績效評估至：${sanitize(d.as_of_date || '最新交易日')}`)
  lines.push(`- 追蹤狀態：${sanitize(d.status || 'tracking')}`)
  if (d.market_context) lines.push(`- 市場背景：${sanitize(d.market_context)}`)
  lines.push('')

  // 統計與超額報酬
  if (d.strategy_stats) {
    const s = d.strategy_stats
    lines.push('## 策略統計與績效表現')
    lines.push(`- 5日表現：勝率 ${pct(s.win_rate_5d)} | 平均報酬 ${pct(s.avg_return_5d)} | 大盤基準 ${pct(s.benchmark_5d)} | 超額報酬 ${pct(s.excess_return_5d)}`)
    lines.push(`- 20日表現：勝率 ${pct(s.win_rate_20d)} | 平均報酬 ${pct(s.avg_return_20d)} | 大盤基準 ${pct(s.benchmark_20d)} | 超額報酬 ${pct(s.excess_return_20d)}`)
    lines.push('')
    lines.push('> ⚠️ 重要聲明：超額報酬 (excess return) 僅為歷史描述統計，不等於未來超額收益 (alpha)；本復盤為描述性分析 (descriptive analytics)，不代表任何因果推論 (causal evidence) 或投資績效保證。')
    lines.push('')
  }

  // 條件統計
  if (d.condition_stats && d.condition_stats.length > 0) {
    lines.push('## 條件命中與勝率表現')
    for (const c of d.condition_stats) {
      lines.push(`- 【${sanitize(c.condition_name)}】：命中 ${c.pass_count} 檔 | 5日勝率 ${pct(c.win_rate_5d)}`)
    }
    lines.push('')
  }

  // 標的明細
  if (d.picks && d.picks.length > 0) {
    lines.push('## 選股標的追蹤明細')
    for (const p of d.picks) {
      const score = p.quant_score != null ? ` | 量化分 ${num(p.quant_score)}` : ''
      const initP = p.initial_price != null ? ` | 快照價 ${num(p.initial_price)} 元` : ''
      const r5 = p.h5d_return_pct != null ? ` | 5D: ${pct(p.h5d_return_pct)} [${sanitize(p.h5d_status)}]` : ` | 5D: [${sanitize(p.h5d_status || 'pending')}]`
      const r20 = p.h20d_return_pct != null ? ` | 20D: ${pct(p.h20d_return_pct)} [${sanitize(p.h20d_status)}]` : ` | 20D: [${sanitize(p.h20d_status || 'pending')}]`
      lines.push(`- ${sanitize(p.name)}（${sanitize(p.symbol)}）${initP}${score}${r5}${r20}`)
      if (p.match_reasons && p.match_reasons.length > 0) {
        lines.push(`  - 當初選股條件：${p.match_reasons.map(m => sanitize(m)).join('；')}`)
      }
    }
    lines.push('')
  } else {
    lines.push('本快照尚無標的追蹤資料。')
    lines.push('')
  }

  return lines.join('\n')
}

export function formatSelectionReviewPrompt(d: SelectionReviewCopyData): string {
  const data = formatSelectionReviewCopy(d)
  return `以下是台股選股策略復盤回顧資料：\n\n${data}\n\n${EXTERNAL_AI_PROMPT_FOOTER}`
}

// ─── Clipboard Helper ────────────────────────────────────────────────────────

export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // fall through to document.execCommand
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.top = '0'
    ta.style.left = '0'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return Boolean(ok)
  } catch {
    return false
  }
}
