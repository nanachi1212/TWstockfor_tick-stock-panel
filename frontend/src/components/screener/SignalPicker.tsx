import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { SIGNAL_OPTIONS, cnSignal } from '@/lib/signals'

interface Props {
  /** 當前選中的信號 ID 列表 */
  signals: string[]
  /** 選中變化回調 */
  onChange: (next: string[]) => void
  /** 買點 / 賣點 — 決定自定義信號的過濾與配色主題 */
  kind: 'entry' | 'exit'
  /** 渲染尺寸: dialog = 選股彈窗緊湊樣式; panel = 回測頁設置抽屜樣式 */
  variant?: 'dialog' | 'panel'
  builtinSignals?: { key: string; label: string }[]
  disabledSignals?: string[]
  disabledSignalHint?: string
}

/**
 * 買賣觸發器信號選擇 — 選股頁彈窗 / 回測頁共用。
 *
 * - 內置信號 (signal_*): 全部展示
 * - 自定義信號 (csg_*): 按 kind 過濾 (entry / exit / both)
 * - entry 藍色主題, exit 橙色主題
 */
export function SignalPicker({ signals, onChange, kind, variant = 'panel', builtinSignals, disabledSignals = [], disabledSignalHint }: Props) {
  const customSignalsQuery = useQuery({ queryKey: QK.customSignals, queryFn: api.customSignalsList })

  const customOptions = useMemo(() => {
    const list = (customSignalsQuery.data?.signals ?? [])
      .filter(s => s.enabled && (s.kind === kind || s.kind === 'both'))
    const names: Record<string, string> = {}
    for (const cs of list) names[`csg_${cs.id}`] = cs.name
    return { list, names }
  }, [customSignalsQuery.data, kind])

  const toggle = (sig: string) => {
    const next = signals.includes(sig) ? signals.filter(x => x !== sig) : [...signals, sig]
    onChange(next)
  }

  // 配色: entry 藍色 (accent), exit 橙色 (warning/amber)
  const isEntry = kind === 'entry'
  const active = isEntry
    ? 'border-accent/50 bg-accent/10 text-accent'
    : 'border-warning/50 bg-warning/10 text-warning'
  const idle = variant === 'dialog'
    ? 'border-border bg-base text-muted hover:border-accent/40'
    : 'border-border bg-base text-muted hover:border-accent/40'
  const customActive = isEntry
    ? 'border-accent/50 bg-accent/10 text-accent'
    : 'border-warning/50 bg-warning/10 text-warning'
  const customIdle = 'border-amber-400/30 bg-amber-400/5 text-secondary hover:border-amber-400/50 hover:text-amber-400'

  const btnCls = variant === 'dialog'
    ? 'rounded px-1.5 py-0.5 text-[10px] font-medium border transition-colors cursor-pointer'
    : 'rounded-btn border px-2.5 py-1.5 text-[11px] transition-colors cursor-pointer'
  const builtinOptions = builtinSignals ?? SIGNAL_OPTIONS.map(key => ({ key, label: cnSignal(key) }))

  return (
    <div className="flex flex-wrap gap-1.5">
      {builtinOptions.map(option => {
        const disabled = disabledSignals.includes(option.key) && !signals.includes(option.key)
        return (
          <button
            key={option.key}
            type="button"
            disabled={disabled}
            title={disabled ? disabledSignalHint : undefined}
            onClick={() => toggle(option.key)}
            className={`${btnCls} ${signals.includes(option.key) ? active : idle} disabled:cursor-not-allowed disabled:opacity-40`}
          >
            {option.label}
          </button>
        )
      })}
      {customOptions.list.map(cs => {
        const id = `csg_${cs.id}`
        return (
          <button
            key={id}
            type="button"
            onClick={() => toggle(id)}
            title="自訂訊號"
            className={`${btnCls} ${signals.includes(id) ? customActive : customIdle}`}
          >
            {customOptions.names[id]}
          </button>
        )
      })}
    </div>
  )
}
