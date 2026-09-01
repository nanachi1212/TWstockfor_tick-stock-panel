import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  Search,
  ShieldAlert,
  X,
} from 'lucide-react'

import {
  api,
  type TaiwanMonitorRule,
  type TaiwanRealtimeQuote,
  type TaiwanRuleType,
  type TaiwanSearchResult,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'

interface TaiwanRuleEditorDialogProps {
  open: boolean
  rule: TaiwanMonitorRule | null
  presetSymbol?: string | null
  presetQuote?: TaiwanRealtimeQuote | null
  onClose: () => void
}

const RULE_TYPE_OPTIONS: { key: TaiwanRuleType; label: string; unit: string; desc: string }[] = [
  { key: 'price_above', label: '價格高於', unit: 'TWD', desc: '成交價突破或高於指定價格時觸發' },
  { key: 'price_below', label: '價格低於', unit: 'TWD', desc: '成交價跌破或低於指定價格時觸發' },
  { key: 'change_pct_above', label: '漲幅高於', unit: '%', desc: '相較昨收之上漲幅度超越指定百分比時觸發' },
  { key: 'change_pct_below', label: '跌幅低於', unit: '%', desc: '相較昨收之下跌幅度超過指定百分比時觸發' },
  { key: 'volume_above', label: '成交量高於', unit: '股 (可切換張)', desc: '累積成交量超越門檻時觸發 (後端以股為規範單位)' },
  { key: 'volume_spike', label: '成交量異常放大', unit: '倍數', desc: '目前量能超越基準參考成交量之倍數時觸發' },
  { key: 'near_upper_limit', label: '接近漲停', unit: '%', desc: '距官方漲停價距離百分比 ≤ 門檻時觸發 (無限制商品不適用)' },
  { key: 'near_lower_limit', label: '接近跌停', unit: '%', desc: '距官方跌停價距離百分比 ≤ 門檻時觸發 (無限制商品不適用)' },
]

export function TaiwanRuleEditorDialog({
  open,
  rule,
  presetSymbol,
  presetQuote,
  onClose,
}: TaiwanRuleEditorDialogProps) {
  const qc = useQueryClient()

  // 表單內部狀態
  const [name, setName] = useState('')
  const [symbol, setSymbol] = useState('2330.TWSE')
  const [ruleType, setRuleType] = useState<TaiwanRuleType>('price_above')
  const [threshold, setThreshold] = useState<number>(2500)
  const [cooldown, setCooldown] = useState<number>(300)
  const [hysteresis, setHysteresis] = useState<number | ''>('')
  const [refVolume, setRefVolume] = useState<number | ''>('')
  const [severity, setSeverity] = useState<'info' | 'warning' | 'critical'>('warning')
  const [volInputMode, setVolInputMode] = useState<'shares' | 'lots'>('lots')

  // 主檔搜尋下拉狀態
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQ, setSearchQ] = useState('')
  const [selectedInst, setSelectedInst] = useState<TaiwanSearchResult | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  // 搜尋 Query
  const searchQuery = useQuery({
    queryKey: QK.taiwanSearch(searchQ),
    queryFn: () => api.taiwanSearch(searchQ),
    enabled: searchOpen && searchQ.trim().length > 0,
    staleTime: 30000,
  })

  // 初始化資料
  useEffect(() => {
    if (rule) {
      setName(rule.name)
      setSymbol(rule.symbol)
      setRuleType(rule.rule_type)
      setThreshold(rule.threshold)
      setCooldown(rule.cooldown_seconds || 300)
      setHysteresis(rule.hysteresis ?? '')
      setRefVolume(rule.reference_volume ?? '')
      setSeverity(rule.severity as any)
      setVolInputMode(rule.rule_type === 'volume_above' && rule.threshold >= 1000 ? 'lots' : 'shares')
      setErrorMessage(null)
    } else {
      const initSym = presetQuote?.symbol || presetSymbol || '2330.TWSE'
      setSymbol(initSym)
      setName(presetQuote ? `${presetQuote.name} 監控` : '台積電 價格監控')
      setRuleType('price_above')
      setThreshold(presetQuote?.last_price ? Math.round(presetQuote.last_price * 1.02) : 2500)
      setCooldown(300)
      setHysteresis('')
      setRefVolume('')
      setSeverity('warning')
      setErrorMessage(null)
    }
  }, [rule, presetSymbol, presetQuote, open])

  // 新增 / 修改 Mutation
  const saveMut = useMutation({
    mutationFn: async () => {
      setErrorMessage(null)
      // 依單位轉換成交量
      let finalThreshold = threshold
      if (ruleType === 'volume_above' && volInputMode === 'lots') {
        finalThreshold = threshold * 1000
      }

      if (rule) {
        return api.taiwanRuleUpdate(rule.rule_id, {
          name,
          threshold: finalThreshold,
          cooldown_seconds: cooldown,
          hysteresis: hysteresis === '' ? null : Number(hysteresis),
          reference_volume: refVolume === '' ? null : Number(refVolume),
          severity,
        })
      } else {
        return api.taiwanRuleSave({
          name: name.trim() || `${symbol} 監控規則`,
          symbol,
          rule_type: ruleType,
          threshold: finalThreshold,
          cooldown_seconds: cooldown,
          hysteresis: hysteresis === '' ? null : Number(hysteresis),
          reference_volume: refVolume === '' ? null : Number(refVolume),
          severity,
        })
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.taiwanRules })
      onClose()
    },
    onError: (err: any) => {
      setErrorMessage(err.message || '儲存規則失敗，請檢查輸入參數與商品限制')
    },
  })

  if (!open) return null

  // 判斷是否為無漲跌幅限制 (NO_LIMIT) 商品
  const isNoLimit = selectedInst?.is_no_limit || presetQuote?.is_no_limit || symbol === '00646.TWSE'
  const isNearLimitType = ruleType === 'near_upper_limit' || ruleType === 'near_lower_limit'
  const limitRuleDisabled = isNoLimit && isNearLimitType

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="w-full max-w-lg overflow-hidden rounded-2xl border border-border/80 bg-surface shadow-2xl flex flex-col max-h-[90vh]"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border/60 px-5 py-3.5 bg-surface/80">
          <div>
            <h3 className="text-base font-bold text-foreground">
              {rule ? '編輯台股即時監控規則' : '新建台股即時監控規則'}
            </h3>
            <p className="text-xs text-muted">盤中行情邊緣觸發・支援漲跌幅限制與防抖冷卻</p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-muted hover:bg-elevated hover:text-foreground transition-colors cursor-pointer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Form Body */}
        <div className="p-5 space-y-4 overflow-y-auto flex-1 text-xs">
          {errorMessage && (
            <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-rose-600 dark:text-rose-400 flex items-start gap-2">
              <ShieldAlert className="h-4 w-4 shrink-0 mt-0.5" />
              <div>
                <span className="font-semibold">操作被拒絕：</span>
                <span>{errorMessage}</span>
              </div>
            </div>
          )}

          {/* 標的選擇器 (含 Security Master 搜尋) */}
          <div>
            <label className="block text-[11px] font-semibold text-foreground/80 mb-1">
              監控標的 (Security Master)
            </label>
            <div className="relative">
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={symbol}
                  onChange={e => setSymbol(e.target.value.toUpperCase())}
                  placeholder="如 2330.TWSE 或 8069.TPEX"
                  className="flex-1 rounded-lg border border-border bg-elevated/40 px-3 py-2 text-xs font-mono font-semibold focus:border-accent focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => setSearchOpen(!searchOpen)}
                  className="inline-flex items-center gap-1 rounded-lg border border-border/80 bg-surface px-3 py-2 font-medium hover:border-accent/40 hover:text-accent transition-colors cursor-pointer"
                >
                  <Search className="h-3.5 w-3.5" />
                  搜尋主檔
                </button>
              </div>

              {/* 搜尋下拉選單 */}
              {searchOpen && (
                <div className="absolute left-0 right-0 top-full mt-1.5 z-20 rounded-xl border border-border bg-surface shadow-xl p-2">
                  <input
                    type="text"
                    value={searchQ}
                    onChange={e => setSearchQ(e.target.value)}
                    placeholder="輸入股票代號或名稱 (例: 2330, 台積電, 0050, 元太)..."
                    className="w-full rounded-lg border border-border/80 bg-elevated/60 px-2.5 py-1.5 text-xs focus:border-accent focus:outline-none mb-2"
                    autoFocus
                  />
                  <div className="max-h-48 overflow-y-auto space-y-1">
                    {searchQuery.data?.results?.map(item => {
                      const disabled = !item.is_supported
                      return (
                        <div
                          key={item.symbol}
                          onClick={() => {
                            if (disabled) return
                            setSymbol(item.symbol)
                            setName(`${item.name} 監控`)
                            setSelectedInst(item)
                            setSearchOpen(false)
                          }}
                          className={cn(
                            'flex items-center justify-between p-2 rounded-lg transition-colors',
                            disabled
                              ? 'opacity-40 cursor-not-allowed bg-elevated/20'
                              : 'cursor-pointer hover:bg-accent/15'
                          )}
                        >
                          <div className="flex items-center gap-2">
                            <span className="font-mono font-bold text-accent">{item.symbol}</span>
                            <span className="font-medium text-foreground">{item.name}</span>
                            <span className="rounded bg-elevated px-1 text-[9px] text-muted">{item.exchange}</span>
                          </div>
                          <div>
                            {disabled ? (
                              <span className="text-[10px] text-rose-500">未支援監控 (權證/ETN)</span>
                            ) : item.is_no_limit ? (
                              <span className="text-[10px] text-purple-400">無漲跌幅限制</span>
                            ) : item.price_limit_pct != null ? (
                              <span className="text-[10px] text-muted">限制: ±{item.price_limit_pct}%</span>
                            ) : null}
                          </div>
                        </div>
                      )
                    })}
                    {searchQ && searchQuery.data?.results?.length === 0 && (
                      <div className="p-3 text-center text-muted">查無相符的台灣證券標的</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* 規則名稱 */}
          <div>
            <label className="block text-[11px] font-semibold text-foreground/80 mb-1">
              規則描述名稱
            </label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="例: 台積電突破 2500 元"
              className="w-full rounded-lg border border-border bg-elevated/40 px-3 py-2 text-xs focus:border-accent focus:outline-none"
            />
          </div>

          {/* 規則型態 (Rule Type) */}
          <div>
            <label className="block text-[11px] font-semibold text-foreground/80 mb-1">
              監控規則型態
            </label>
            <div className="grid grid-cols-2 gap-1.5">
              {RULE_TYPE_OPTIONS.map(opt => {
                const isSelected = ruleType === opt.key
                const isNearLimit = opt.key === 'near_upper_limit' || opt.key === 'near_lower_limit'
                const optDisabled = isNoLimit && isNearLimit

                return (
                  <button
                    key={opt.key}
                    type="button"
                    disabled={optDisabled}
                    onClick={() => setRuleType(opt.key)}
                    className={cn(
                      'flex flex-col items-start p-2 rounded-xl border text-left transition-all',
                      isSelected
                        ? 'border-accent bg-accent/15 text-accent shadow-sm'
                        : optDisabled
                        ? 'border-border/30 bg-elevated/20 text-muted opacity-40 cursor-not-allowed'
                        : 'border-border/60 bg-surface text-secondary hover:border-accent/40 cursor-pointer'
                    )}
                  >
                    <div className="flex items-center justify-between w-full">
                      <span className="font-semibold text-xs">{opt.label}</span>
                      {optDisabled && <span className="text-[9px] text-purple-400">不可用 (無限制)</span>}
                    </div>
                    <span className="text-[10px] text-muted mt-0.5 line-clamp-1">{opt.desc}</span>
                  </button>
                )
              })}
            </div>
            {limitRuleDisabled && (
              <p className="mt-1 text-[10px] text-rose-500 font-medium">
                ⚠️ 本商品 ({symbol}) 屬於無漲跌幅限制商品，無法設定「接近漲跌停」規則。
              </p>
            )}
          </div>

          {/* 門檻數值 (動態切換單位) */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[11px] font-semibold text-foreground/80">
                觸發門檻數值
              </label>
              {ruleType === 'volume_above' && (
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setVolInputMode('lots')}
                    className={cn('px-1.5 py-0.5 rounded text-[10px] cursor-pointer', volInputMode === 'lots' ? 'bg-accent text-accent-foreground' : 'text-muted hover:text-foreground')}
                  >
                    以「張」輸入 (1張=1000股)
                  </button>
                  <button
                    type="button"
                    onClick={() => setVolInputMode('shares')}
                    className={cn('px-1.5 py-0.5 rounded text-[10px] cursor-pointer', volInputMode === 'shares' ? 'bg-accent text-accent-foreground' : 'text-muted hover:text-foreground')}
                  >
                    以「股」輸入
                  </button>
                </div>
              )}
            </div>

            <div className="relative">
              <input
                type="number"
                step={ruleType.includes('pct') || ruleType.includes('near') ? '0.1' : '1'}
                value={threshold}
                onChange={e => setThreshold(parseFloat(e.target.value) || 0)}
                className="w-full rounded-lg border border-border bg-elevated/40 px-3 py-2 pr-14 text-xs font-mono font-semibold focus:border-accent focus:outline-none"
              />
              <span className="absolute right-3 top-2 text-[11px] text-muted font-medium pointer-events-none">
                {ruleType === 'volume_above'
                  ? volInputMode === 'lots' ? '張' : '股'
                  : RULE_TYPE_OPTIONS.find(o => o.key === ruleType)?.unit}
              </span>
            </div>
          </div>

          {/* 爆量專用: 基準參考成交量 */}
          {ruleType === 'volume_spike' && (
            <div>
              <label className="block text-[11px] font-semibold text-foreground/80 mb-1">
                基準參考成交量 (股)
              </label>
              <input
                type="number"
                value={refVolume}
                onChange={e => setRefVolume(e.target.value === '' ? '' : parseInt(e.target.value))}
                placeholder="例如: 5000000 股"
                className="w-full rounded-lg border border-border bg-elevated/40 px-3 py-2 text-xs font-mono focus:border-accent focus:outline-none"
              />
              <p className="text-[10px] text-muted mt-0.5">當日累積成交量達基準量之 {threshold} 倍時觸發警報。</p>
            </div>
          )}

          {/* 高級配置: 冷卻時間、遲滯防抖、嚴重等級 */}
          <div className="grid grid-cols-3 gap-2 pt-1 border-t border-border/40">
            <div>
              <label className="block text-[10px] font-semibold text-muted mb-1">冷卻時間 (秒)</label>
              <input
                type="number"
                value={cooldown}
                onChange={e => setCooldown(parseInt(e.target.value) || 0)}
                className="w-full rounded-lg border border-border bg-elevated/40 px-2.5 py-1.5 text-xs font-mono focus:border-accent focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-muted mb-1">遲滯防抖回調</label>
              <input
                type="number"
                step="0.1"
                value={hysteresis}
                onChange={e => setHysteresis(e.target.value === '' ? '' : parseFloat(e.target.value))}
                placeholder="無"
                className="w-full rounded-lg border border-border bg-elevated/40 px-2.5 py-1.5 text-xs font-mono focus:border-accent focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-muted mb-1">嚴重級別</label>
              <select
                value={severity}
                onChange={e => setSeverity(e.target.value as any)}
                className="w-full rounded-lg border border-border bg-elevated/40 px-2 py-1.5 text-xs focus:border-accent focus:outline-none"
              >
                <option value="info">一般 (INFO)</option>
                <option value="warning">提醒 (WARN)</option>
                <option value="critical">緊急 (CRITICAL)</option>
              </select>
            </div>
          </div>
        </div>

        {/* Footer Buttons */}
        <div className="flex items-center justify-end gap-2 border-t border-border/60 px-5 py-3 bg-surface/80">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-border/80 px-4 py-2 text-xs font-medium text-muted hover:bg-elevated hover:text-foreground transition-colors cursor-pointer"
          >
            取消
          </button>
          <button
            type="button"
            disabled={limitRuleDisabled || saveMut.isPending}
            onClick={() => saveMut.mutate()}
            className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-accent-foreground shadow-sm hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          >
            {saveMut.isPending ? '儲存中...' : rule ? '確認更新' : '建立規則'}
          </button>
        </div>
      </motion.div>
    </div>
  )
}
