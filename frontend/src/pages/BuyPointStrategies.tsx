import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, Check, Copy, Loader2, Plus, Save, ShieldAlert } from 'lucide-react'
import { api, type BuyPointConditions, type BuyPointSignal, type BuyPointStatus, type BuyPointStrategy } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { PageHeader } from '@/components/PageHeader'
import { toast } from '@/components/Toast'
import { cn } from '@/lib/cn'

const statusLabel: Record<BuyPointStatus, string> = {
  triggered: '已觸發', approaching: '接近', blocked: '風險阻擋', waiting: '等待', unavailable: '不可用',
}
const statusClass: Record<BuyPointStatus, string> = {
  triggered: 'border-bull/40 bg-bull/10 text-bull', approaching: 'border-warning/40 bg-warning/10 text-warning',
  blocked: 'border-danger/40 bg-danger/10 text-danger', waiting: 'border-border bg-elevated text-muted', unavailable: 'border-border bg-elevated/50 text-muted',
}

function Conditions({ strategy }: { strategy: BuyPointStrategy }) {
  const c = strategy.conditions
  const rows = [
    c.quant_min != null && `Quant ≥ ${c.quant_min}`,
    c.pullback_min_pct != null && `回檔 ${c.pullback_min_pct}–${c.pullback_max_pct ?? '以上'}%`,
    c.breakout_window != null && `突破 ${c.breakout_window}D 高點`,
    c.volume_multiplier != null && `量 ≥ 均量 × ${c.volume_multiplier}`,
    c.revenue_yoy_min != null && `營收 YoY ≥ ${c.revenue_yoy_min}%`,
    c.institutional_required && '法人改善',
    c.foreign_shareholding_change_min != null && `外資持股變化 ≥ ${c.foreign_shareholding_change_min}`,
    c.pe_max != null && `PE ≤ ${c.pe_max}`,
    c.pb_max != null && `PB ≤ ${c.pb_max}`,
    c.eps_min != null && `EPS ≥ ${c.eps_min}`,
  ].filter(Boolean) as string[]
  return <div className="flex flex-wrap gap-1.5">{rows.map(row => <span key={row} className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-secondary">{row}</span>)}</div>
}

function SignalRow({ signal, onSnapshot }: { signal: BuyPointSignal; onSnapshot: (signal: BuyPointSignal) => void }) {
  const copy = async () => {
    const prompt = `請分析此買點訊號的支持因素、反對因素與資料不足處，不要替使用者做交易決定。\n策略：${signal.strategy_id}\n標的：${signal.symbol} ${signal.name}\n狀態：${statusLabel[signal.status]}\n支持：${signal.triggered_conditions.join('、') || '無'}\n未達：${signal.failed_conditions.join('、') || '無'}\n風險：${signal.risk_flags.join('、') || '無'}`
    await navigator.clipboard.writeText(prompt)
    toast('已複製買點分析資料', 'success')
  }
  return (
    <div className="rounded-xl border border-border/70 bg-base/50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-accent">{signal.symbol}</span>
        <span className="text-xs font-medium text-foreground">{signal.name || '未命名標的'}</span>
        <span className={cn('rounded-full border px-2 py-0.5 text-[10px] font-medium', statusClass[signal.status])}>{statusLabel[signal.status]}</span>
        <span className="ml-auto text-[10px] text-muted">資料 {signal.data_as_of ?? '不可用'}</span>
      </div>
      <p className="mt-2 text-xs text-secondary">{signal.explanation}</p>
      {(signal.triggered_conditions.length > 0 || signal.failed_conditions.length > 0) && (
        <div className="mt-2 grid gap-1 text-[10px] sm:grid-cols-2">
          <div className="text-bull">✓ {signal.triggered_conditions.join('、') || '無已成立條件'}</div>
          <div className="text-muted">✗ {signal.failed_conditions.join('、') || '無'}</div>
        </div>
      )}
      {signal.risk_flags.length > 0 && <div className="mt-2 flex items-center gap-1 text-[10px] text-danger"><ShieldAlert className="h-3 w-3" />{signal.risk_flags.join('、')}</div>}
      <div className="mt-2 flex gap-2">
        <button type="button" onClick={copy} className="inline-flex items-center gap-1 rounded bg-elevated px-2 py-1 text-[10px] text-secondary hover:text-foreground"><Copy className="h-3 w-3" />複製 AI 分析提示詞</button>
        {(signal.status === 'triggered' || signal.status === 'approaching') && <button type="button" onClick={() => onSnapshot(signal)} className="inline-flex items-center gap-1 rounded bg-accent/10 px-2 py-1 text-[10px] text-accent hover:bg-accent/20"><Save className="h-3 w-3" />保存買點快照</button>}
      </div>
    </div>
  )
}

export function BuyPointStrategies() {
  const qc = useQueryClient()
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null)
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([])
  const [customOpen, setCustomOpen] = useState(false)
  const [customName, setCustomName] = useState('我的買點策略')
  const [customConditions, setCustomConditions] = useState<BuyPointConditions>({ quant_min: 60 })
  const strategies = useQuery({ queryKey: QK.buyPointStrategies, queryFn: api.buyPointStrategies })
  const signals = useQuery({ queryKey: QK.buyPointSignals(), queryFn: () => api.buyPointSignals(), refetchInterval: 60_000 })
  const summary = useQuery({ queryKey: QK.buyPointSummary, queryFn: api.buyPointSummary, refetchInterval: 60_000 })
  const watchlist = useQuery({ queryKey: QK.watchlist, queryFn: api.watchlistList })
  const stats = useQuery({ queryKey: QK.buyPointStats, queryFn: api.buyPointStats })
  const allStrategies = strategies.data?.strategies ?? []
  const presets = allStrategies.filter(item => item.preset)
  const mine = allStrategies.filter(item => !item.preset)
  const selected = allStrategies.find(item => item.id === selectedStrategy)

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: QK.buyPointStrategies })
    void qc.invalidateQueries({ queryKey: QK.buyPointSignals() })
    void qc.invalidateQueries({ queryKey: QK.buyPointSummary })
  }
  const clone = useMutation({ mutationFn: (id: string) => api.buyPointClone(id), onSuccess: data => { invalidate(); setSelectedStrategy(data.id); toast('已複製為我的買點策略', 'success') } })
  const update = useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api.buyPointUpdate(id, { enabled }), onSuccess: invalidate })
  const assign = useMutation({ mutationFn: () => api.buyPointAssign(selectedStrategy!, selectedSymbols), onSuccess: () => { invalidate(); toast('已更新自選股買點策略', 'success') } })
  const create = useMutation({ mutationFn: () => api.buyPointCreate({ name: customName, description: '使用者自訂的 typed 買點條件。', conditions: customConditions, alert_channels: ['app'] }), onSuccess: data => { invalidate(); setCustomOpen(false); setSelectedStrategy(data.id); toast('已建立自訂策略', 'success') } })
  const snapshot = useMutation({ mutationFn: ({ strategyId, symbol }: { strategyId: string; symbol: string }) => api.buyPointSnapshot(strategyId, symbol), onSuccess: () => toast('已保存至選股復盤，來源為 Buy Point', 'success') })

  const signalRows = useMemo(() => signals.data?.signals ?? [], [signals.data])
  const watchSymbols = watchlist.data?.symbols ?? []
  const toggleSymbol = (symbol: string) => setSelectedSymbols(current => current.includes(symbol) ? current.filter(item => item !== symbol) : [...current, symbol])
  const chooseStrategy = (strategy: BuyPointStrategy) => {
    setSelectedStrategy(strategy.id)
    setSelectedSymbols(strategy.assigned_symbols ?? [])
  }

  return (
    <div className="min-h-full bg-base px-4 py-4 text-foreground sm:px-6">
      <PageHeader title="買點策略" titleExtra={<span className="text-[11px] text-muted">僅供研究提醒，不自動交易</span>} right={<button type="button" onClick={() => setCustomOpen(open => !open)} className="inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white"><Plus className="h-3.5 w-3.5" />建立自訂策略</button>} />

      <section className="mb-4 rounded-2xl border border-accent/20 bg-surface/70 p-4">
        <div className="mb-3 flex items-center gap-2"><BellRing className="h-4 w-4 text-accent" /><h2 className="text-sm font-semibold">今日買點</h2><span className="text-[10px] text-muted">只顯示已套用至自選股的策略</span></div>
        <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-5">{(['triggered', 'approaching', 'blocked', 'waiting', 'unavailable'] as BuyPointStatus[]).map(status => <div key={status} className="rounded-lg border border-border/60 bg-base/40 p-2"><div className="text-[10px] text-muted">{statusLabel[status]}</div><div className="mt-1 font-mono text-lg">{summary.data?.counts[status] ?? '—'}</div></div>)}</div>
        {signals.isLoading ? <div className="flex items-center gap-2 py-5 text-xs text-muted"><Loader2 className="h-4 w-4 animate-spin" />正在評估買點條件…</div> : signalRows.length === 0 ? <p className="py-5 text-center text-xs text-muted">尚未有套用策略的自選股，請在下方選擇策略與股票。</p> : <div className="grid gap-2 lg:grid-cols-2">{signalRows.map(signal => <SignalRow key={`${signal.strategy_id}-${signal.symbol}`} signal={signal} onSnapshot={item => snapshot.mutate({ strategyId: item.strategy_id, symbol: item.symbol })} />)}</div>}
      </section>

      {customOpen && <section className="mb-4 rounded-2xl border border-accent/30 bg-surface p-4"><h2 className="mb-3 text-sm font-semibold">建立 typed 自訂策略</h2><div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5"><label className="text-xs text-secondary">名稱<input value={customName} onChange={event => setCustomName(event.target.value)} className="mt-1 block w-full rounded border border-border bg-base px-2 py-1.5 text-xs" /></label><ConditionInput label="Quant 最低分" value={customConditions.quant_min} onChange={value => setCustomConditions(current => ({ ...current, quant_min: value }))} /><ConditionInput label="回檔下限 %" value={customConditions.pullback_min_pct} onChange={value => setCustomConditions(current => ({ ...current, pullback_min_pct: value }))} /><ConditionInput label="回檔上限 %" value={customConditions.pullback_max_pct} onChange={value => setCustomConditions(current => ({ ...current, pullback_max_pct: value }))} /><ConditionInput label="營收 YoY 下限 %" value={customConditions.revenue_yoy_min} onChange={value => setCustomConditions(current => ({ ...current, revenue_yoy_min: value }))} /></div><div className="mt-3 flex items-center gap-2"><button type="button" onClick={() => create.mutate()} disabled={!customName.trim() || create.isPending} className="rounded bg-accent px-3 py-1.5 text-xs text-white disabled:opacity-50">建立</button><span className="text-[10px] text-muted">條件採 typed 欄位，缺資料會標示不可用，不接受任意 Python expression。</span></div></section>}

      <section className="mb-4 rounded-2xl border border-border bg-surface/70 p-4"><h2 className="mb-3 text-sm font-semibold">我的買點策略</h2>{mine.length === 0 ? <p className="text-xs text-muted">尚未建立自訂策略，從內建策略複製即可開始。</p> : <div className="grid gap-2 md:grid-cols-2">{mine.map(strategy => <StrategyCard key={strategy.id} strategy={strategy} selected={selectedStrategy === strategy.id} onSelect={() => chooseStrategy(strategy)} onToggle={() => update.mutate({ id: strategy.id, enabled: !strategy.enabled })} />)}</div>}</section>

      {selected && <section className="mb-4 rounded-2xl border border-accent/30 bg-surface p-4"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-semibold">套用「{selected.name}」至 Watchlist</h2><button type="button" onClick={() => assign.mutate()} disabled={assign.isPending} className="inline-flex items-center gap-1 rounded bg-accent px-3 py-1.5 text-xs text-white disabled:opacity-50"><Check className="h-3.5 w-3.5" />儲存套用</button></div><div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{watchSymbols.map(item => <label key={item.symbol} className="flex items-center gap-2 rounded border border-border/60 bg-base/40 px-2 py-1.5 text-xs"><input type="checkbox" checked={selectedSymbols.includes(item.symbol)} onChange={() => toggleSymbol(item.symbol)} />{item.symbol}<span className="truncate text-muted">{item.name ?? ''}</span></label>)}</div>{watchSymbols.length === 0 && <p className="mt-2 text-xs text-muted">Watchlist 目前沒有股票。</p>}</section>}

      <section className="mb-4 rounded-2xl border border-border bg-surface/70 p-4"><h2 className="mb-3 text-sm font-semibold">內建策略</h2><div className="grid gap-3 xl:grid-cols-2">{presets.map(strategy => <StrategyCard key={strategy.id} strategy={strategy} selected={false} onSelect={() => clone.mutate(strategy.id)} onToggle={() => update.mutate({ id: strategy.id, enabled: !strategy.enabled })} preset />)}</div></section>

      {stats.data && <section className="rounded-2xl border border-border bg-surface/70 p-4"><h2 className="text-sm font-semibold">買點策略統計</h2><p className="mt-1 text-[10px] text-muted">{stats.data.disclaimer}</p>{stats.data.stats.length > 0 ? <div className="mt-3 grid gap-2 sm:grid-cols-2">{stats.data.stats.map(item => <div key={String(item.strategy_id)} className="rounded border border-border/60 bg-base/40 p-2 text-xs"><div className="font-medium">{String(item.strategy_name)}</div><div className="mt-1 text-[10px] text-muted">5D 平均 {item.avg_return_5d == null ? '—' : `${item.avg_return_5d}%`} · 命中率 {item.hit_rate_5d == null ? '—' : `${item.hit_rate_5d}%`}</div></div>)}</div> : <p className="mt-3 text-xs text-muted">尚無成熟訊號統計。</p>}</section>}
    </div>
  )
}

function StrategyCard({ strategy, selected, onSelect, onToggle, preset = false }: { strategy: BuyPointStrategy; selected: boolean; onSelect: () => void; onToggle: () => void; preset?: boolean }) {
  return <article className={cn('rounded-xl border p-3', selected ? 'border-accent/60 bg-accent/5' : 'border-border/70 bg-base/40')}><div className="flex items-start gap-2"><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="text-xs font-semibold">{strategy.name}</h3><span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-muted">{strategy.category}</span><span className={cn('h-2 w-2 rounded-full', strategy.enabled ? 'bg-bull' : 'bg-muted')} title={strategy.enabled ? '啟用' : '停用'} /></div><p className="mt-1 text-[11px] text-secondary">{strategy.description}</p><div className="mt-2"><Conditions strategy={strategy} /></div><p className="mt-2 text-[10px] text-muted">風險否決：處置、暫停、重大事件或資料不可用時不提醒。</p></div><div className="flex shrink-0 flex-col gap-1">{preset && <button type="button" onClick={onSelect} className="rounded bg-accent/10 px-2 py-1 text-[10px] text-accent hover:bg-accent/20">使用此策略</button>}{!preset && <button type="button" onClick={onSelect} className="rounded bg-elevated px-2 py-1 text-[10px] text-secondary hover:text-foreground">套用自選</button>}<button type="button" onClick={onToggle} className="rounded bg-elevated px-2 py-1 text-[10px] text-secondary hover:text-foreground">{strategy.enabled ? '停用' : '啟用'}</button></div></div></article>
}

function ConditionInput({ label, value, onChange }: { label: string; value?: number | null; onChange: (value: number | null) => void }) {
  return <label className="text-xs text-secondary">{label}<input type="number" value={value ?? ''} onChange={event => onChange(event.target.value === '' ? null : Number(event.target.value))} className="mt-1 block w-full rounded border border-border bg-base px-2 py-1.5 text-xs" /></label>
}
