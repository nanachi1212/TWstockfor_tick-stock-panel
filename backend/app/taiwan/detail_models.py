"""Pydantic schemas and response models for Taiwan Stock Detail Workspace.

Covers:
  - TaiwanStockIdentity: Master identity, classification, exchange, listing status
  - TaiwanStockPriceLimit: Price limit pct, upper/lower bounds, NO_LIMIT flag
  - TaiwanStockRealtime: Live quote, 5-level bids/asks, freshness metadata
  - TaiwanDailyRow: Historical OHLCV item
  - TaiwanHistoricalDaily: Historical daily series wrapper with metadata
  - TaiwanInstitutionalData: Foreign, Investment Trust, Dealer, Total Net + 5D rolling
  - TaiwanMarginData: Margin and Short balances, changes, and short-margin ratio
  - TaiwanFactorsData: Institutional and Margin rolling factors with data dates
  - TaiwanMarketContext: Benchmark index (TAIEX / TPEx Index) status and change %
  - TaiwanMonitorSummary: Configured monitor rules count and active rules
  - TaiwanRecentAlert: Recent alert triggers for the symbol
  - TaiwanStockDetailResponse: Unified, strongly-typed aggregation response
"""
from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field


class SectionMeta(BaseModel):
    source: str = Field(..., description="資料來源標識，如 twse:mis, twse:t86, finmind, yahoo")
    trade_date: Optional[str] = Field(None, description="資料所屬交易日期 YYYY-MM-DD")
    fetched_at: Optional[str] = Field(None, description="資料抓取時間 ISO 字串")
    status: str = Field("available", description="區塊狀態: available, unavailable, stale, fallback")
    is_stale: bool = Field(False, description="是否已被判定為過期資料")
    fallback_reason: Optional[str] = Field(None, description="降級備援原因（若有）")
    source_type: str | None = Field(None, description="資料來源類型")
    freshness_class: str | None = Field(None, description="canonical 行情新鮮度分類")
    is_realtime: bool | None = Field(None, description="來源是否確認為即時行情")


class TaiwanStockIdentity(BaseModel):
    symbol: str = Field(..., description="標準規範代碼，如 2330.TWSE, 8069.TPEX")
    code: str = Field(..., description="股票或標的代碼，如 2330, 8069, 0050")
    name: str = Field(..., description="標的繁體中文全名或簡稱，如 台積電")
    exchange: str = Field(..., description="TWSE 上市 或 TPEX 上櫃")
    instrument_type: str = Field(..., description="標的類型: stock, etf, index, warrant, etc.")
    is_supported: bool = Field(True, description="是否為支援監控與交易之標的")
    listing_status: Optional[str] = Field(None, description="上市狀態，如 active, delisted")
    listing_date: Optional[str] = Field(None, description="掛牌日期 YYYY-MM-DD")
    industry: Optional[str] = Field(None, description="產業類別，如 半導體業, 電子零組件")
    cfi_code: Optional[str] = Field(None, description="ISO 10962 CFI 碼")
    etf_category: Optional[str] = Field(None, description="ETF 分類: domestic_equity, foreign_equity, bond, leveraged, inverse")


class TaiwanStockPriceLimit(BaseModel):
    limit_up: Optional[float] = Field(None, description="當日漲停價")
    limit_down: Optional[float] = Field(None, description="當日跌停價")
    price_limit_pct: Optional[float] = Field(None, description="漲跌幅限制百分比 (如 0.1 代表 10%, 0.2 代表 20%)")
    is_no_limit: bool = Field(False, description="是否為無漲跌幅限制標的 (如 00646)")
    rule_type: str = Field(..., description="漲跌幅規則類型說明，如 普通股±10%, 槓桿ETF±20%, 無漲跌幅限制")


class TaiwanStockRealtime(BaseModel):
    last_price: Optional[float] = Field(None, description="最新成交價")
    prev_close: Optional[float] = Field(None, description="昨日收盤價")
    open: Optional[float] = Field(None, description="今日開盤價")
    high: Optional[float] = Field(None, description="今日最高價")
    low: Optional[float] = Field(None, description="今日最低價")
    change: Optional[float] = Field(None, description="今日漲跌金額")
    change_pct: Optional[float] = Field(None, description="今日漲跌百分比")
    volume: Optional[int] = Field(None, description="累積成交量 (股)")
    amount: Optional[float] = Field(None, description="累積成交金額 (元)")
    quote_time: Optional[str] = Field(None, description="行情時間戳記 ISO 字串 (Asia/Taipei)")
    market_status: str = Field("closed", description="市場狀態: open, closed, pre_open, non_trading_day")
    bid_price: Optional[float] = Field(None, description="買一價")
    ask_price: Optional[float] = Field(None, description="賣一價")
    bids: List[List[float]] = Field(default_factory=list, description="五檔買單 [[價, 股數], ...]")
    asks: List[List[float]] = Field(default_factory=list, description="五檔賣單 [[價, 股數], ...]")
    meta: Optional[SectionMeta] = None


class TaiwanDailyRow(BaseModel):
    date: str = Field(..., description="交易日期 YYYY-MM-DD")
    open: float = Field(..., description="開盤價")
    high: float = Field(..., description="最高價")
    low: float = Field(..., description="最低價")
    close: float = Field(..., description="收盤價")
    volume: int = Field(..., description="成交量 (股)")
    amount: Optional[float] = Field(None, description="成交金額 (元)")


class TaiwanHistoricalDaily(BaseModel):
    status: str = Field("available", description="available, unavailable, error")
    rows: List[TaiwanDailyRow] = Field(default_factory=list)
    meta: Optional[SectionMeta] = None


class TaiwanInstitutionalData(BaseModel):
    status: str = Field("available", description="available, unavailable")
    foreign_net: Optional[int] = Field(None, description="外資及陸資買賣超股數 (股)")
    investment_trust_net: Optional[int] = Field(None, description="投信買賣超股數 (股)")
    dealer_net: Optional[int] = Field(None, description="自營商合計買賣超股數 (股)")
    total_net: Optional[int] = Field(None, description="三大法人合計買賣超股數 (股)")
    foreign_net_5d: Optional[int] = Field(None, description="外資近 5 日累計買賣超 (股)")
    investment_trust_net_5d: Optional[int] = Field(None, description="投信近 5 日累計買賣超 (股)")
    dealer_net_5d: Optional[int] = Field(None, description="自營商近 5 日累計買賣超 (股)")
    trend: List[dict] = Field(default_factory=list, description="近期每日三大法人買賣超趨勢")
    meta: Optional[SectionMeta] = None


class TaiwanMarginData(BaseModel):
    status: str = Field("available", description="available, unavailable")
    margin_balance: Optional[int] = Field(None, description="融資餘額 (股)")
    margin_change: Optional[int] = Field(None, description="融資增減 (股)")
    short_balance: Optional[int] = Field(None, description="融券餘額 (股)")
    short_change: Optional[int] = Field(None, description="融券增減 (股)")
    short_margin_ratio: Optional[float] = Field(None, description="券資比 (%)")
    trend: List[dict] = Field(default_factory=list, description="近期融資融券趨勢")
    meta: Optional[SectionMeta] = None


class TaiwanFactorsData(BaseModel):
    status: str = Field("available", description="available, unavailable")
    foreign_net_5d: Optional[int] = Field(None, description="外資 5 日買賣超 (股)")
    investment_trust_net_5d: Optional[int] = Field(None, description="投信 5 日買賣超 (股)")
    dealer_net_5d: Optional[int] = Field(None, description="自營商 5 日買賣超 (股)")
    margin_balance_change: Optional[int] = Field(None, description="融資餘額增減 (股)")
    short_margin_ratio: Optional[float] = Field(None, description="券資比 (%)")
    meta: Optional[SectionMeta] = None


class TaiwanMarketContext(BaseModel):
    benchmark_symbol: str = Field(..., description="TAIEX 或 TPEX_INDEX")
    benchmark_name: str = Field(..., description="加權指數 或 櫃買指數")
    close: Optional[float] = Field(None, description="指數最新點數")
    change: Optional[float] = Field(None, description="指數漲跌點數")
    change_pct: Optional[float] = Field(None, description="指數漲跌幅 (%)")
    meta: Optional[SectionMeta] = None


class TaiwanMonitorSummary(BaseModel):
    rule_count: int = Field(0, description="此標的已建立的監控規則數")
    active_count: int = Field(0, description="啟用中的監控規則數")
    rules: List[dict] = Field(default_factory=list, description="規則簡要清單")


class TaiwanRecentAlert(BaseModel):
    alert_id: str
    rule_id: str
    rule_name: str
    rule_type: str
    severity: str
    trigger_price: float
    trigger_value: float
    message: str
    triggered_at: str


class TaiwanValuationData(BaseModel):
    pe: Optional[float] = Field(None, description="本益比 PER")
    pb: Optional[float] = Field(None, description="股價淨值比 PBR")
    dividend_yield: Optional[float] = Field(None, description="殖利率 (%)")
    meta: Optional[SectionMeta] = None


class TaiwanMonthRevenueTrendItem(BaseModel):
    date: str = Field(..., description="資料日期 YYYY-MM-DD")
    year_month: str = Field(..., description="營收年月 YYYY-MM")
    revenue: float = Field(..., description="月營收金額 (元)")
    mom: Optional[float] = Field(None, description="月增率 MoM (%)")
    yoy: Optional[float] = Field(None, description="年增率 YoY (%)")


class TaiwanRevenueData(BaseModel):
    latest_revenue: Optional[float] = Field(None, description="最新月營收金額 (元)")
    latest_year_month: Optional[str] = Field(None, description="最新營收年月 YYYY-MM")
    mom: Optional[float] = Field(None, description="月增率 MoM (%)")
    yoy: Optional[float] = Field(None, description="年增率 YoY (%)")
    trend: List[TaiwanMonthRevenueTrendItem] = Field(default_factory=list, description="近6-12個月營收趨勢")
    meta: Optional[SectionMeta] = None


class TaiwanProfitabilityData(BaseModel):
    quarter: Optional[str] = Field(None, description="最新財報季度 YYYY-QX 或 YYYY-MM-DD")
    latest_eps: Optional[float] = Field(None, description="最新單季 EPS")
    operating_revenue: Optional[float] = Field(None, description="營業收入")
    gross_profit: Optional[float] = Field(None, description="營業毛利")
    operating_income: Optional[float] = Field(None, description="營業利益")
    net_income: Optional[float] = Field(None, description="本期稅後淨利")
    recent_eps: List[dict] = Field(default_factory=list, description="近期各季EPS摘要（非相加）")
    meta: Optional[SectionMeta] = None


class TaiwanFundamentalData(BaseModel):
    status: str = Field("available", description="available, unavailable, partial, error")
    valuation: TaiwanValuationData = Field(default_factory=TaiwanValuationData)
    revenue: TaiwanRevenueData = Field(default_factory=TaiwanRevenueData)
    profitability: TaiwanProfitabilityData = Field(default_factory=TaiwanProfitabilityData)
    meta: Optional[SectionMeta] = None


class TaiwanForeignShareholdingData(BaseModel):
    ratio: Optional[float] = Field(None, description="外資持股比例 (%)")
    shares: Optional[int] = Field(None, description="外資持股股數")
    change_5d: Optional[float] = Field(None, description="近5日外資持股比例變化 (%pt)")
    change_20d: Optional[float] = Field(None, description="近20日外資持股比例變化 (%pt)")
    trend: str = Field("flat", description="外資持股趨勢: increasing, decreasing, flat")
    meta: Optional[SectionMeta] = None


class TaiwanSecuritiesLendingData(BaseModel):
    latest_volume: Optional[int] = Field(None, description="最新交易日借券成交量 (股，借券成交明細，非借券餘額/融券)")
    avg_fee_rate: Optional[float] = Field(None, description="最新交易日借券平均成交費率 (%)")
    volume_5d: Optional[int] = Field(None, description="近5日借券成交總量 (股)")
    volume_20d: Optional[int] = Field(None, description="近20日借券成交總量 (股)")
    anomaly_status: str = Field("normal", description="借券異常狀態: normal, abnormal_increase, abnormal_decrease, unavailable")
    meta: Optional[SectionMeta] = None


class TaiwanExtraChipsData(BaseModel):
    status: str = Field("available", description="available, unavailable, partial, error")
    foreign_shareholding: TaiwanForeignShareholdingData = Field(default_factory=TaiwanForeignShareholdingData)
    securities_lending: TaiwanSecuritiesLendingData = Field(default_factory=TaiwanSecuritiesLendingData)
    meta: Optional[SectionMeta] = None


class TaiwanStockDetailResponse(BaseModel):
    """Unified, strongly-typed aggregation response for Taiwan Stock Research Workspace."""
    symbol: str
    identity: TaiwanStockIdentity
    price_limit: TaiwanStockPriceLimit
    realtime: TaiwanStockRealtime
    daily_history: TaiwanHistoricalDaily
    institutional: TaiwanInstitutionalData
    margin: TaiwanMarginData
    factors: TaiwanFactorsData
    market_context: TaiwanMarketContext
    monitor_summary: TaiwanMonitorSummary
    recent_alerts: List[TaiwanRecentAlert] = Field(default_factory=list)
    fundamentals: Optional[TaiwanFundamentalData] = None
    extra_chips: Optional[TaiwanExtraChipsData] = None
    overall_data_quality: str = Field("good", description="good, partial, stale, degraded")
