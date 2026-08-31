import { AlertTriangle, RefreshCw } from 'lucide-react'
import type { TaiwanCurrentDataResponse, TaiwanDataSection, TaiwanUsageScope } from '@/lib/api'
import { cn } from '@/lib/cn'

type Props = {
  response?: TaiwanCurrentDataResponse
  isLoading: boolean
  isError: boolean
  isFetching: boolean
  onRetry: () => void
}

const STATUS_LABELS: Record<string, string> = {
  official: '官方資料',
  unsupported: '不適用',
  error: '暫時讀取失敗',
}

const USAGE_LABELS: Record<TaiwanUsageScope, string> = {
  current_reference: '目前參考',
  historical_reference: '歷史參考',
  pit_historical: '可按公開時間查詢',
  unsupported: '不適用',
}

function statusLabel(section: TaiwanDataSection) {
  if (section.status === 'data_insufficient' && section.usage_scope === 'current_reference') {
    return '目前參考'
  }
  return STATUS_LABELS[section.status] ?? section.status
}

function record(value: unknown): Record<string, unknown> {
  return value != null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function present(value: unknown): value is string | number {
  return typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')
}

function numberValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function text(value: unknown) {
  return present(value) ? String(value) : '--'
}

function number(value: unknown, digits = 2) {
  const parsed = numberValue(value)
  return parsed == null
    ? '--'
    : parsed.toLocaleString('zh-TW', { maximumFractionDigits: digits })
}

function money(value: unknown) {
  const parsed = numberValue(value)
  if (parsed == null) return '--'
  const absolute = Math.abs(parsed)
  if (absolute >= 100_000_000) return `${number(parsed / 100_000_000)} 億元`
  if (absolute >= 10_000) return `${number(parsed / 10_000)} 萬元`
  return `${number(parsed, 0)} 元`
}

function percent(value: unknown) {
  const parsed = numberValue(value)
  return parsed == null ? '--' : `${number(parsed)}%`
}

function DataRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border/50 py-1.5 last:border-0">
      <span className="text-muted">{label}</span>
      <span className="text-right font-mono font-medium text-foreground">{value}</span>
    </div>
  )
}

function SectionCard({
  title,
  section,
  children,
}: {
  title: string
  section?: TaiwanDataSection
  children: React.ReactNode
}) {
  if (!section) return null
  const failed = section.status === 'error'
  return (
    <article className="rounded-2xl border border-border bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-bold text-foreground">{title}</h4>
        <div className="flex flex-wrap items-center justify-end gap-1.5 text-[10px]">
          <span className={cn(
            'rounded-full px-2 py-0.5 font-medium',
            failed ? 'bg-rose-500/10 text-rose-500' : 'bg-accent/10 text-accent',
          )}>
            {statusLabel(section)}
          </span>
          <span className="rounded-full bg-elevated px-2 py-0.5 text-muted">
            {USAGE_LABELS[section.usage_scope]}
          </span>
          {!section.historically_eligible && (
            <span className="rounded-full bg-elevated px-2 py-0.5 text-muted">不供歷史回測</span>
          )}
        </div>
      </div>
      {failed ? (
        <div className="rounded-xl border border-rose-500/20 bg-rose-500/5 p-3 text-xs text-muted">
          此項官方資料暫時無法讀取，其他區塊不受影響。
        </div>
      ) : children}
      <div className="mt-3 flex flex-wrap justify-between gap-2 border-t border-border/60 pt-2 text-[10px] text-muted">
        <span>來源：{section.provider || section.source || '--'}</span>
        <span>{section.available_at ? `可用時間：${section.available_at}` : '未提供可驗證的首次公開時間'}</span>
      </div>
    </article>
  )
}

function StockSections({ sections }: { sections: Record<string, TaiwanDataSection> }) {
  const revenue = sections.monthly_revenue
  const revenueData = record(revenue?.data)
  const revenueValues = record(revenueData.values)
  const statement = sections.financial_statement
  const statementData = record(statement?.data)
  const statementValues = record(statementData.values)
  const valuation = sections.valuation
  const valuationData = record(valuation?.data)
  const valuationValues = record(valuationData.values)
  const capital = sections.share_capital_record
  const capitalData = record(capital?.data)
  const capitalValues = record(capitalData.values)

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <SectionCard title="月營收" section={revenue}>
        <div className="text-xs">
          <DataRow label="資料月份" value={text(revenueData.period_end)} />
          <DataRow label="當月營收" value={money(revenueValues.revenue)} />
          <DataRow label="月增率" value={percent(revenueValues.mom)} />
          <DataRow label="年增率" value={percent(revenueValues.yoy)} />
          <DataRow label="累計營收" value={money(revenueValues.cumulative)} />
        </div>
      </SectionCard>
      <SectionCard title="財務報表" section={statement}>
        <div className="text-xs">
          <DataRow label="報表期間" value={text(statementData.period_end)} />
          <DataRow label="營業收入" value={money(statementValues.revenue)} />
          <DataRow label="營業利益" value={money(statementValues.operating_income)} />
          <DataRow label="本期淨利" value={money(statementValues.net_income)} />
          <DataRow label="累計 EPS" value={number(statementValues.cumulative_eps)} />
          <DataRow label="權益總計" value={money(statementValues.equity)} />
        </div>
      </SectionCard>
      <SectionCard title="估值參考" section={valuation}>
        <div className="text-xs">
          <DataRow label="交易日期" value={text(valuationData.period_end)} />
          <DataRow label="本益比（P/E）" value={number(valuationValues.pe)} />
          <DataRow label="股價淨值比（P/B）" value={number(valuationValues.pb)} />
          <DataRow label="殖利率" value={percent(valuationValues.dividend_yield)} />
        </div>
      </SectionCard>
      <SectionCard title="股本資料" section={capital}>
        <div className="text-xs">
          <DataRow label="出表日期" value={text(capitalData.period_end)} />
          <DataRow label="已發行普通股數" value={number(capitalValues.issued_shares, 0)} />
          <DataRow label="實收資本額" value={money(capitalValues.capital_twd)} />
          <DataRow label="每股面額" value={present(capitalValues.par_value_twd) ? `${number(capitalValues.par_value_twd)} 元` : '--'} />
          <DataRow label="流通股數" value={number(capitalValues.float_shares, 0)} />
        </div>
      </SectionCard>
    </div>
  )
}

function EtfSections({ sections }: { sections: Record<string, TaiwanDataSection> }) {
  const profile = sections.profile
  const profileData = record(profile?.data)
  const snapshot = sections.snapshot
  const snapshotData = record(snapshot?.data)
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <SectionCard title="ETF 基本資料" section={profile}>
        <div className="text-xs">
          <DataRow label="基金名稱" value={text(profileData.name)} />
          <DataRow label="追蹤指數" value={text(profileData.benchmark)} />
          <DataRow label="基金類型" value={text(profileData.etf_type)} />
          <DataRow label="成立日期" value={text(profileData.inception_date)} />
          <DataRow label="上市日期" value={text(profileData.listing_date)} />
          <DataRow label="計價幣別" value={text(profileData.currency)} />
        </div>
      </SectionCard>
      <SectionCard title="ETF 目前快照" section={snapshot}>
        <div className="text-xs">
          <DataRow label="資料日期" value={text(snapshotData.as_of_date)} />
          <DataRow label="發行單位數" value={number(snapshotData.issued_units, 0)} />
          <DataRow label="流通單位數" value={number(snapshotData.outstanding_units, 0)} />
          <DataRow label="資產淨值（NAV）" value={number(snapshotData.nav)} />
          <DataRow label="資產規模（AUM）" value={money(snapshotData.aum)} />
        </div>
        <p className="mt-3 rounded-xl bg-base p-2.5 text-[11px] leading-5 text-muted">
          歷史 NAV、資產規模與配息目前尚未納入可安全使用的正式介面；缺值不代表為 0。
        </p>
      </SectionCard>
    </div>
  )
}

export function TaiwanReferenceData({ response, isLoading, isError, isFetching, onRetry }: Props) {
  return (
    <section aria-labelledby="taiwan-reference-heading" className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-border bg-surface p-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 id="taiwan-reference-heading" className="text-sm font-bold text-foreground">官方基本面資料</h3>
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">目前參考</span>
          </div>
          <p className="mt-1 max-w-3xl text-[11px] leading-5 text-muted">
            這些是官方目前或參考資料，不等同於可還原當時資訊的歷史資料，請勿直接用於歷史回測。
          </p>
        </div>
        <button
          type="button"
          onClick={onRetry}
          disabled={isFetching}
          className="flex items-center gap-1.5 rounded-lg border border-border bg-base px-2.5 py-1.5 text-xs text-muted transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RefreshCw className={cn('h-3.5 w-3.5', isFetching && 'animate-spin')} />
          更新官方資料
        </button>
      </div>

      {isLoading && (
        <div className="flex min-h-28 items-center justify-center rounded-2xl border border-border bg-surface text-xs text-muted">
          <RefreshCw className="mr-2 h-4 w-4 animate-spin text-accent" />
          讀取官方參考資料中...
        </div>
      )}
      {isError && (
        <div className="rounded-2xl border border-rose-500/30 bg-rose-500/5 p-4 text-xs text-muted">
          <div className="flex items-center gap-2 font-medium text-rose-500">
            <AlertTriangle className="h-4 w-4" />
            官方基本面資料暫時讀取失敗
          </div>
          <p className="mt-1">即時行情、K 線與監控功能仍可正常使用。</p>
        </div>
      )}
      {response && !isLoading && !isError && (
        response.security_type === 'etf'
          ? <EtfSections sections={response.sections} />
          : <StockSections sections={response.sections} />
      )}
    </section>
  )
}
