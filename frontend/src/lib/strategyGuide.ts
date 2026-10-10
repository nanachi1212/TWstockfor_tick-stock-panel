// 每日候選策略的白話說明 — 規則本體在 backend/app/taiwan/selection_v2.py, 改規則要同步這裡。
export const STRATEGY_GUIDE: Record<string, { label: string; rule: string; plain: string }> = {
  trend_liquidity_v1: {
    label: '趨勢流動性',
    rule: '站上 20 日均線、近 5 日上漲、成交金額夠大',
    plain: '挑「正在漲、又很多人在交易（好買好賣）」的股票。最基本的順勢策略。',
  },
  institutional_momentum_v1: {
    label: '法人動能',
    rule: '外資與投信近 5 日都買超、買量占成交量 ≥ 1%、站上 20 日均線',
    plain: '大戶（外資＋投信）一起在買的股票。',
  },
  growth_trend_v1: {
    label: '成長趨勢',
    rule: '月營收年增率為正且越來越好、站上 20 日與 60 日均線、近 5/20 日上漲',
    plain: '公司生意越做越好，股價也跟著往上的股票。',
  },
  breakout_v1: {
    label: '突破轉強',
    rule: '收盤突破過去 20 或 60 天最高價、成交量比平常多 2 成以上、漲勢在加速',
    plain: '股價衝過前面的高點、有人追價，可能開始新一段漲勢；但也可能是假突破，要設停損。',
  },
  multi_factor_consensus_v1: {
    label: '多策略共識',
    rule: '法人動能、成長趨勢、突破轉強三個中至少同時符合兩個',
    plain: '多種理由同時看好，條件最嚴、檔數通常最少。',
  },
  pullback_support_v1: {
    label: '多頭回檔',
    rule: '20 日線在 60 日線之上、近 5 日回檔、收盤在 20 日均線上方 0～3%',
    plain: '好股票休息時，在支撐（20 日均線）附近等買點，不追高。',
  },
  foreign_trend_v1: {
    label: '外資跟買',
    rule: '外資近 5 日買超、站上 20 日均線、20 日線在 60 日線之上、近 20 日上漲',
    plain: '跟著外資買、而且趨勢已經走好的股票。',
  },
  oversold_rebound_v1: {
    label: '超跌反彈',
    rule: 'RSI14 ≤ 35（超賣）、當天收紅、收盤站回 5 日均線',
    plain: '跌深後開始反彈的股票。屬於逆勢短線，風險較高，部位要小。',
  },
}

export const STRATEGY_COMMON_NOTE = '所有策略都只看股票（不含 ETF）、成交金額至少 5,000 萬，並排除處置股、停牌與風險事件。'
