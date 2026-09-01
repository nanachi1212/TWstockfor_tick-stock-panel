import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  Search,
  X,
  AlertTriangle,
  Sparkles,
  Loader2,
  Scale,
} from 'lucide-react'
import {
  api,
  type TaiwanSearchResult,
  type TaiwanComparisonAIStockResearchReport,
} from '@/lib/api'
import { cn } from '@/lib/cn'
import { useCompareSymbols, MAX_COMPARE_SYMBOLS, MIN_COMPARE_SYMBOLS } from '@/lib/useCompareSymbols'

/** 台股多標的客觀比較頁 (Phase 7G / 7H)。
 * URL 查詢參數 (?symbols=A,B) 為選取狀態之唯一真實來源，重新整理或分享連結皆可還原。
 * 確定性比較資料自動載入；AI 客觀比較報告需使用者主動點擊觸發（絕不自動呼叫）。
 */
export function TaiwanStockCompare() {
  const navigate = useNavigate()
  const { selected, addSymbol, removeSymbol } = useCompareSymbols()
  const [searchQuery, setSearchQuery] = useState('')
  const [isSearchOpen, setIsSearchOpen] = useState(false)

  const searchQueryResult = useQuery({
    queryKey: ['taiwanSearch', searchQuery],
    queryFn: () => api.taiwanSearch(searchQuery, 10),
    enabled: searchQuery.trim().length > 0,
    staleTime: 60_000,
  })

  const canAddMore = selected.length < MAX_COMPARE_SYMBOLS
  const canCompare = selected.length >= MIN_COMPARE_SYMBOLS

  const handleAddSymbol = (symbol: string) => {
    addSymbol(symbol)
    setSearchQuery('')
    setIsSearchOpen(false)
  }

  // 確定性比較：symbols 有效時自動載入 (不涉及 AI，0 AI 成本)
  const comparisonQuery = useQuery({
    queryKey: ['taiwanStockCompare', selected],
    queryFn: () => api.taiwanStockCompare(selected),
    enabled: canCompare,
    staleTime: 60_000,
  })

  // AI 客觀比較：手動觸發狀態 (MANUAL ONLY, NEVER AUTO-TRIGGER — 同 TaiwanStockDetail.tsx 慣例)
  const [aiComparison, setAiComparison] = useState<TaiwanComparisonAIStockResearchReport | null>(null)
  const [isAiLoading, setIsAiLoading] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)

  // 過期 AI 回應防護：以「選取當下的正規化標的清單」作為請求鍵。
  // 若回應抵達時目前選取已變更（鍵不相符），該回應一律捨棄，絕不套用於 UI。
  const activeAiRequestKey = useRef<string | null>(null)
  const selectedKey = selected.join(',')

  // 選取變更時：立即清除舊 AI 結果/錯誤/載入狀態，並讓任何仍在途的舊請求失效。
  useEffect(() => {
    activeAiRequestKey.current = null
    setAiComparison(null)
    setAiError(null)
    setIsAiLoading(false)
  }, [selectedKey])

  const handleGenerateAiComparison = async () => {
    const requestKey = selectedKey
    activeAiRequestKey.current = requestKey
    setIsAiLoading(true)
    setAiError(null)
    try {
      const res = await api.taiwanStockCompareAIResearch(selected)
      if (activeAiRequestKey.current !== requestKey) return // 選取已變更，捨棄過期回應
      if (res.status === 'success' && res.report) {
        setAiComparison(res.report)
      } else {
        setAiError(res.error_message || 'AI 客觀比較生成失敗')
      }
    } catch (e: any) {
      if (activeAiRequestKey.current !== requestKey) return // 選取已變更，捨棄過期錯誤
      setAiError(e?.message || 'AI 服務調用失敗，請稍後重試')
    } finally {
      if (activeAiRequestKey.current === requestKey) setIsAiLoading(false)
    }
  }

  const data = comparisonQuery.data
  const comparisonErrorMessage =
    comparisonQuery.error instanceof Error ? comparisonQuery.error.message : '比較資料載入失敗，請重試'
  const fmtPct = (v: number | null | undefined) => (v == null ? 'N/A（不適用）' : `${(v * 100).toFixed(2)}%`)
  const fmtNum = (v: number | null | undefined, digits = 2) => (v == null ? 'N/A（不適用）' : v.toFixed(digits))

  return (
    <div className="flex flex-col min-h-screen bg-base text-foreground pb-12">
      {/* 頂部導航列 */}
      <div className="sticky top-0 z-30 flex items-center justify-between border-b border-border bg-surface/90 px-4 py-2.5 backdrop-blur-md">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/monitor')}
            className="flex items-center gap-1.5 rounded-lg border border-border/80 bg-base px-2.5 py-1 text-xs font-medium text-muted hover:border-accent/50 hover:text-foreground transition-all cursor-pointer"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span>返回即時監控</span>
          </button>
          <div className="h-4 w-px bg-border" />
          <div className="flex items-center gap-2">
            <Scale className="h-4 w-4 text-accent" />
            <span className="text-sm font-semibold">台股多標的客觀比較</span>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto w-full px-4 py-5 space-y-5">
        {/* 標的選擇器 */}
        <div className="rounded-2xl border border-border bg-surface p-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-foreground">
              選擇比較標的（{selected.length}/{MAX_COMPARE_SYMBOLS}，至少需 {MIN_COMPARE_SYMBOLS} 檔）
            </span>
          </div>

          {/* 已選標的 chips */}
          <div className="flex flex-wrap gap-2">
            {selected.map(sym => (
              <span
                key={sym}
                className="inline-flex items-center gap-1.5 rounded-lg border border-accent/40 bg-accent/10 px-2.5 py-1 text-xs font-mono font-bold text-accent"
              >
                {sym}
                <button
                  onClick={() => removeSymbol(sym)}
                  className="hover:text-red-400 transition-colors cursor-pointer"
                  aria-label={`移除 ${sym}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
            {selected.length === 0 && (
              <span className="text-[11px] text-muted py-1">尚未選擇任何標的，請於下方搜尋新增</span>
            )}
          </div>

          {/* 搜尋新增 */}
          {canAddMore && (
            <div className="relative">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={e => {
                    setSearchQuery(e.target.value)
                    setIsSearchOpen(true)
                  }}
                  onFocus={() => setIsSearchOpen(true)}
                  placeholder="搜尋台股代號或名稱以加入比較..."
                  className="w-full rounded-lg border border-border bg-base pl-8 pr-3 py-1.5 text-xs text-foreground placeholder:text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                />
              </div>
              {isSearchOpen && searchQuery.trim() && (
                <div className="absolute top-full left-0 mt-1.5 w-full max-h-72 overflow-y-auto rounded-xl border border-border bg-elevated p-1.5 shadow-2xl z-50">
                  {searchQueryResult.isLoading && (
                    <div className="p-4 text-center text-xs text-muted">搜尋中...</div>
                  )}
                  {searchQueryResult.data?.results?.length === 0 && (
                    <div className="p-4 text-center text-xs text-muted">查無符合標的</div>
                  )}
                  {searchQueryResult.data?.results?.map((item: TaiwanSearchResult) => (
                    <button
                      key={item.symbol}
                      onClick={() => handleAddSymbol(item.symbol)}
                      disabled={selected.includes(item.symbol.toUpperCase())}
                      className="flex w-full items-center justify-between rounded-lg p-2 text-left hover:bg-surface transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-bold text-accent">{item.symbol}</span>
                        <span className="text-xs font-medium text-foreground">{item.name}</span>
                      </div>
                      <span className="text-[10px] text-muted">{item.exchange === 'TPEX' ? '上櫃' : '上市'}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 確定性比較表 */}
        {canCompare && (
          <div className="rounded-2xl border border-border bg-surface p-4 space-y-3">
            <h3 className="text-xs font-semibold text-foreground">確定性客觀比較（純本地計算，非 AI）</h3>

            {comparisonQuery.isLoading && (
              <div className="py-8 text-center text-xs text-muted">載入比較資料中...</div>
            )}
            {comparisonQuery.isError && (
              <div className="p-3 bg-red-950/30 border border-red-900/50 rounded-lg text-xs text-red-400 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                <span>{comparisonErrorMessage}</span>
              </div>
            )}

            {data && (
              <>
                {data.unsupported_symbols.length > 0 && (
                  <div className="p-2.5 bg-amber-950/30 border border-amber-900/50 rounded-lg text-[11px] text-amber-400">
                    無法解析之代碼：{data.unsupported_symbols.join(', ')}
                  </div>
                )}
                <div className="overflow-x-auto">
                  <table className="w-full text-xs border-collapse min-w-[600px]">
                    <thead>
                      <tr className="border-b border-border">
                        <th className="sticky left-0 bg-surface text-left py-2 pr-3 font-semibold text-muted min-w-[140px]">
                          比較維度
                        </th>
                        {data.instruments.map(inst => (
                          <th key={inst.symbol} className="text-left py-2 px-3 font-mono font-bold text-accent min-w-[140px]">
                            <div>{inst.context.identity.name}</div>
                            <div className="text-[10px] text-muted font-normal">{inst.symbol}</div>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/40">
                      <tr>
                        <td className="sticky left-0 bg-surface py-2 pr-3 text-muted">標的類型</td>
                        {data.instruments.map(inst => (
                          <td key={inst.symbol} className="py-2 px-3">
                            {inst.context.identity.instrument_type === 'etf' ? 'ETF' : '股票'}
                            {inst.context.etf_context.leverage_multiplier != null && (
                              <span className="ml-1 text-[10px] text-muted">
                                (槓桿 {inst.context.etf_context.leverage_multiplier}x)
                              </span>
                            )}
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td className="sticky left-0 bg-surface py-2 pr-3 text-muted">收盤價</td>
                        {data.instruments.map(inst => (
                          <td key={inst.symbol} className="py-2 px-3 font-mono">
                            {fmtNum(inst.context.price_context.close)}
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td className="sticky left-0 bg-surface py-2 pr-3 text-muted">5 日報酬率</td>
                        {data.instruments.map(inst => (
                          <td key={inst.symbol} className="py-2 px-3 font-mono">
                            {fmtPct(inst.context.price_context.return_5d)}
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td className="sticky left-0 bg-surface py-2 pr-3 text-muted">20 日報酬率</td>
                        {data.instruments.map(inst => (
                          <td key={inst.symbol} className="py-2 px-3 font-mono">
                            {fmtPct(inst.context.price_context.return_20d)}
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td className="sticky left-0 bg-surface py-2 pr-3 text-muted">RSI(14)</td>
                        {data.instruments.map(inst => (
                          <td key={inst.symbol} className="py-2 px-3 font-mono">
                            {fmtNum(inst.context.technical_context.rsi14)}
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td className="sticky left-0 bg-surface py-2 pr-3 text-muted">外資買賣超 (1日)</td>
                        {data.instruments.map(inst => (
                          <td key={inst.symbol} className="py-2 px-3 font-mono">
                            {inst.context.institutional_context.foreign_net_1d == null
                              ? 'N/A（不適用）'
                              : inst.context.institutional_context.foreign_net_1d.toLocaleString()}
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td className="sticky left-0 bg-surface py-2 pr-3 text-muted">本益比 (PE)</td>
                        {data.instruments.map(inst => (
                          <td key={inst.symbol} className="py-2 px-3 font-mono">
                            {inst.context.fundamentals_context.status === 'not_applicable'
                              ? 'ETF 不適用'
                              : fmtNum(inst.context.fundamentals_context.pe)}
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td className="sticky left-0 bg-surface py-2 pr-3 text-muted">異常訊號數</td>
                        {data.instruments.map(inst => (
                          <td key={inst.symbol} className="py-2 px-3 font-mono">
                            {inst.diagnostic_item?.signal_count ?? 0}
                          </td>
                        ))}
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p className="text-[10px] text-muted">比較基準日：{data.comparison_date}（所有標的共用同一交易日資料，無前視偏誤）</p>
              </>
            )}
          </div>
        )}

        {!canCompare && (
          <div className="py-10 text-center text-xs text-muted border border-dashed border-border/40 rounded-2xl bg-surface/40">
            請至少選擇 {MIN_COMPARE_SYMBOLS} 檔標的以開始比較
          </div>
        )}

        {/* AI 客觀比較卡片 (獨立於確定性比較，需使用者主動觸發) */}
        {canCompare && data && (
          <div className="bg-surface border border-purple-900/40 rounded-xl p-5 shadow-sm space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border/40 pb-3">
              <div className="flex items-center gap-2.5">
                <Sparkles className="w-5 h-5 text-purple-400" />
                <div>
                  <h3 className="font-semibold text-sm text-foreground flex items-center gap-2">
                    AI 客觀比較報告
                    <span className="text-[10px] bg-purple-950/60 border border-purple-800 text-purple-300 px-2 py-0.5 rounded font-mono">
                      無優劣排序、無投資建議
                    </span>
                  </h3>
                  <p className="text-[11px] text-muted">僅依上方確定性資料進行客觀比較解讀，絕不給予優劣排序或投資建議</p>
                </div>
              </div>
              <button
                type="button"
                onClick={handleGenerateAiComparison}
                disabled={isAiLoading}
                className={cn(
                  'px-3.5 py-1.5 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-colors border',
                  isAiLoading
                    ? 'bg-purple-950/40 border-purple-800/40 text-purple-400 cursor-not-allowed'
                    : 'bg-purple-600 hover:bg-purple-500 text-white border-purple-500 shadow-sm',
                )}
              >
                {isAiLoading ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    正在客觀比較中...
                  </>
                ) : (
                  <>
                    <Sparkles className="w-3.5 h-3.5" />
                    {aiComparison ? '重新生成 AI 客觀比較' : '生成 AI 客觀比較'}
                  </>
                )}
              </button>
            </div>

            {aiError && (
              <div className="p-3 bg-red-950/30 border border-red-900/50 rounded-lg text-xs text-red-400 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0 text-red-400" />
                <span>{aiError}</span>
              </div>
            )}

            {!aiComparison && !isAiLoading && !aiError && (
              <div className="py-8 text-center text-muted text-xs border border-dashed border-border/40 rounded-xl bg-base/30">
                <Sparkles className="w-8 h-8 mx-auto mb-2 text-purple-400/40" />
                <p className="font-medium text-foreground">尚未生成 AI 客觀比較</p>
                <p className="text-[11px] text-muted mt-1">點擊上方按鈕以進行封閉事實邊界之客觀比較解讀 (不自動呼叫)</p>
              </div>
            )}

            {aiComparison && (
              <div className="space-y-4 text-xs">
                <div className="bg-base/60 p-3.5 rounded-lg border border-border/50">
                  <span className="text-[11px] font-semibold text-purple-300 block mb-1">【比較摘要】</span>
                  <p className="text-foreground leading-relaxed text-xs">{aiComparison.comparison_overview}</p>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {aiComparison.price_technical_comparison && (
                    <div className="bg-base/40 p-3 rounded-lg border border-border/40">
                      <span className="text-[11px] font-semibold text-muted block mb-1">量價與技術位階比較</span>
                      <p className="text-foreground text-[11px] leading-normal">{aiComparison.price_technical_comparison}</p>
                    </div>
                  )}
                  {aiComparison.institutional_comparison && (
                    <div className="bg-base/40 p-3 rounded-lg border border-border/40">
                      <span className="text-[11px] font-semibold text-muted block mb-1">三大法人籌碼比較</span>
                      <p className="text-foreground text-[11px] leading-normal">{aiComparison.institutional_comparison}</p>
                    </div>
                  )}
                  {aiComparison.margin_comparison && (
                    <div className="bg-base/40 p-3 rounded-lg border border-border/40">
                      <span className="text-[11px] font-semibold text-muted block mb-1">融資融券比較</span>
                      <p className="text-foreground text-[11px] leading-normal">{aiComparison.margin_comparison}</p>
                    </div>
                  )}
                  {aiComparison.fundamentals_comparison && (
                    <div className="bg-base/40 p-3 rounded-lg border border-border/40">
                      <span className="text-[11px] font-semibold text-muted block mb-1">基本面 / ETF 屬性比較</span>
                      <p className="text-foreground text-[11px] leading-normal">{aiComparison.fundamentals_comparison}</p>
                    </div>
                  )}
                  {aiComparison.abnormal_diagnostics_comparison && (
                    <div className="bg-base/40 p-3 rounded-lg border border-border/40 md:col-span-2">
                      <span className="text-[11px] font-semibold text-muted block mb-1">異常訊號比較</span>
                      <p className="text-foreground text-[11px] leading-normal">{aiComparison.abnormal_diagnostics_comparison}</p>
                    </div>
                  )}
                </div>

                {aiComparison.key_observations.length > 0 && (
                  <div className="bg-base/40 p-3.5 rounded-lg border border-border/40 space-y-2">
                    <span className="text-[11px] font-semibold text-emerald-400 block">【重點客觀比較觀察】</span>
                    <div className="space-y-2">
                      {aiComparison.key_observations.map((obs, idx) => (
                        <div key={idx} className="flex flex-col gap-1 bg-surface/60 p-2 rounded border border-border/30">
                          <span className="text-foreground text-xs leading-normal">{obs.text}</span>
                          <div className="flex flex-wrap gap-1 mt-0.5">
                            {obs.evidence_refs.map((ref, rIdx) => (
                              <span key={rIdx} className="text-[10px] font-mono px-1.5 py-0.2 bg-emerald-950/40 border border-emerald-800/40 text-emerald-300 rounded">
                                證據: {ref}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {aiComparison.risk_factors.length > 0 && (
                  <div className="bg-base/40 p-3.5 rounded-lg border border-border/40 space-y-2">
                    <span className="text-[11px] font-semibold text-amber-400 block">【數據揭示之風險特徵】</span>
                    <div className="space-y-2">
                      {aiComparison.risk_factors.map((rsk, idx) => (
                        <div key={idx} className="flex flex-col gap-1 bg-surface/60 p-2 rounded border border-border/30">
                          <span className="text-foreground text-xs leading-normal">{rsk.text}</span>
                          <div className="flex flex-wrap gap-1 mt-0.5">
                            {rsk.evidence_refs.map((ref, rIdx) => (
                              <span key={rIdx} className="text-[10px] font-mono px-1.5 py-0.2 bg-amber-950/40 border border-amber-800/40 text-amber-300 rounded">
                                依據: {ref}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {aiComparison.missing_information.length > 0 && (
                  <div className="bg-base/30 p-2.5 rounded-lg border border-border/30 text-[11px] text-muted">
                    <span className="font-semibold text-zinc-400 block mb-1">【系統數據覆蓋度說明】:</span>
                    <ul className="list-disc list-inside space-y-0.5 font-mono text-[10px]">
                      {aiComparison.missing_information.map((item, idx) => (
                        <li key={idx} className="text-zinc-400">{item}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="p-2.5 bg-zinc-950/40 border border-zinc-800/60 rounded text-[10px] text-zinc-500 font-sans text-center">
                  {aiComparison.disclaimer}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
