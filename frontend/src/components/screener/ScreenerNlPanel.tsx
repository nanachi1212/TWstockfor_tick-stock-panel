import { AlertTriangle, Sparkles, Loader2 } from 'lucide-react'
import type { TaiwanScreenerTranslation } from '@/lib/api'

interface ScreenerNlPanelProps {
  nlQuery: string
  setNlQuery: (value: string) => void
  nlTranslating: boolean
  nlTranslation: TaiwanScreenerTranslation | null
  setNlTranslation: (value: TaiwanScreenerTranslation | null) => void
  nlError: string | null
  handleTranslate: () => void | Promise<void>
  handleApplyTranslation: () => void
}

/** 台股選股頁的「AI 自然語言條件解析」面板。狀態與套用邏輯仍由頁面持有。 */
export function ScreenerNlPanel({ nlQuery, setNlQuery, nlTranslating, nlTranslation, setNlTranslation, nlError, handleTranslate, handleApplyTranslation }: ScreenerNlPanelProps) {
  return (
    <>
    <div className="bg-zinc-900/80 border border-purple-900/40 rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-purple-400" />
          <span className="font-semibold text-sm text-zinc-100">AI 自然語言條件解析 (選股條件轉換層)</span>
          <span className="text-[11px] text-zinc-500">純翻譯層：解析結果將映射至下方篩選表單，不直接出股</span>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
        <input
          type="text"
          value={nlQuery}
          onChange={e => setNlQuery(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleTranslate() }}
          placeholder="例如：找外資買超 1000 張以上、券資比低於 5% 的股票，或是 100 元以下的 ETF"
          className="flex-1 bg-zinc-950 border border-zinc-800 focus:border-purple-500 rounded-lg px-3 py-2 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none transition-colors"
        />
        <button
          onClick={handleTranslate}
          disabled={nlTranslating || !nlQuery.trim()}
          className="px-4 py-2 bg-purple-600 hover:bg-purple-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-colors shrink-0 shadow-sm"
        >
          {nlTranslating ? (
            <>
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              解析中...
            </>
          ) : (
            <>
              <Sparkles className="w-3.5 h-3.5" />
              解析條件
            </>
          )}
        </button>
      </div>

      {/* Translation Error */}
      {nlError && (
        <div className="text-xs text-amber-400 bg-amber-950/40 border border-amber-900/60 rounded-lg p-2.5 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <span className="font-medium">解析未成功：</span> {nlError}
          </div>
        </div>
      )}

      {/* Translation Preview Box */}
      {nlTranslation && (
        <div className="bg-zinc-950/90 border border-zinc-800 rounded-lg p-3 space-y-2.5 text-xs">
          <div className="flex items-center justify-between border-b border-zinc-800/80 pb-2">
            <span className="font-semibold text-zinc-200">條件解析預覽 (確認後填入表單)</span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setNlTranslation(null)}
                className="px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[11px] transition-colors"
              >
                取消
              </button>
              {nlTranslation.request && (
                <button
                  onClick={handleApplyTranslation}
                  className="px-3 py-1 rounded bg-purple-600 hover:bg-purple-500 text-white font-medium text-[11px] transition-colors shadow-sm"
                >
                  套用條件至表單
                </button>
              )}
            </div>
          </div>

          {/* Recognized Conditions */}
          {nlTranslation.recognized_conditions.length > 0 && (
            <div>
              <span className="text-zinc-400 block mb-1">已成功解析條件：</span>
              <div className="flex flex-wrap gap-1.5">
                {nlTranslation.recognized_conditions.map((cond, idx) => (
                  <span
                    key={idx}
                    className="px-2 py-0.5 rounded bg-emerald-950/60 border border-emerald-800/50 text-emerald-300 text-[11px] font-mono"
                  >
                    ✓ {cond}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Unsupported Conditions */}
          {nlTranslation.unsupported_conditions.length > 0 && (
            <div>
              <span className="text-amber-400 block mb-1">未支援或無法量化條件：</span>
              <div className="flex flex-wrap gap-1.5">
                {nlTranslation.unsupported_conditions.map((cond, idx) => (
                  <span
                    key={idx}
                    className="px-2 py-0.5 rounded bg-amber-950/60 border border-amber-800/50 text-amber-300 text-[11px]"
                  >
                    ⚠ {cond}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Clarification Message */}
          {nlTranslation.clarification_message && (
            <div className="text-zinc-400 bg-zinc-900/60 rounded p-2 text-[11px] border border-zinc-800">
              <span className="text-purple-400 font-medium">提示說明：</span> {nlTranslation.clarification_message}
            </div>
          )}
        </div>
      )}
    </div>

    </>
  )
}
