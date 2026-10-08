import { useQuery } from '@tanstack/react-query'
import { AlertCircle, Database, Clock, CheckCircle2, AlertTriangle } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

export function ScreenerDataStatusPanel() {
  // Taiwan Data Freshness & Automation Status query (5-minute staleTime, no rapid polling)
  const {
    data: statusData,
    isLoading: isStatusLoading,
    isError: isStatusError,
  } = useQuery({
    queryKey: QK.taiwanDataStatus,
    queryFn: () => api.taiwanDataStatus(),
    staleTime: 5 * 60 * 1000,
  })

  return (
    <>
  {/* Data Operations Visibility Panel (Phase 6C) */}
  <div className="bg-zinc-900/90 border border-zinc-800 rounded-xl p-4 text-xs">
    <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
      {/* Left: Overall Freshness & Datasets */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2 pr-2 border-r border-zinc-800">
          <Database className="w-4 h-4 text-purple-400" />
          <span className="font-semibold text-zinc-200">資料更新狀態</span>
          {isStatusLoading ? (
            <span className="text-zinc-500 flex items-center gap-1">
              <Clock className="w-3.5 h-3.5 animate-spin" /> 載入中...
            </span>
          ) : isStatusError ? (
            <span className="text-amber-400 flex items-center gap-1">
              <AlertTriangle className="w-3.5 h-3.5" /> 資料狀態暫時無法取得
            </span>
          ) : statusData ? (
            statusData.is_fully_current ? (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-emerald-950/80 text-emerald-400 border border-emerald-800/60">
                <CheckCircle2 className="w-3 h-3" /> 資料已是最新
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-950/80 text-amber-400 border border-amber-800/60">
                <AlertCircle className="w-3 h-3" /> 資料尚未完全更新
              </span>
            )
          ) : null}
        </div>

        {/* Individual Datasets Status */}
        {statusData && (
          <div className="flex flex-wrap items-center gap-3">
            {/* Daily OHLCV */}
            <div className="flex items-center gap-1.5">
              <span className="text-zinc-400">日線:</span>
              <span className="font-mono text-zinc-200">{statusData.daily_as_of || '—'}</span>
              {statusData.daily_status === 'current' ? (
                <span className="px-1.5 py-0.5 rounded bg-emerald-900/50 text-emerald-400 font-medium text-[10px]">最新</span>
              ) : statusData.daily_status === 'stale' ? (
                <span className="px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400 font-medium text-[10px]">
                  待更新{statusData.daily_days_behind > 0 ? ` (${statusData.daily_days_behind}日)` : ''}
                </span>
              ) : (
                <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[10px]">無資料</span>
              )}
            </div>

            <span className="text-zinc-700">•</span>

            {/* Institutional */}
            <div className="flex items-center gap-1.5">
              <span className="text-zinc-400">法人:</span>
              <span className="font-mono text-zinc-200">{statusData.institutional_as_of || '—'}</span>
              {statusData.institutional_status === 'current' ? (
                <span className="px-1.5 py-0.5 rounded bg-emerald-900/50 text-emerald-400 font-medium text-[10px]">最新</span>
              ) : statusData.institutional_status === 'stale' ? (
                <span className="px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400 font-medium text-[10px]">
                  待更新{statusData.institutional_days_behind > 0 ? ` (${statusData.institutional_days_behind}日)` : ''}
                </span>
              ) : (
                <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[10px]">無資料</span>
              )}
            </div>

            <span className="text-zinc-700">•</span>

            {/* Margin */}
            <div className="flex items-center gap-1.5">
              <span className="text-zinc-400">資券:</span>
              <span className="font-mono text-zinc-200">{statusData.margin_as_of || '—'}</span>
              {statusData.margin_status === 'current' ? (
                <span className="px-1.5 py-0.5 rounded bg-emerald-900/50 text-emerald-400 font-medium text-[10px]">最新</span>
              ) : statusData.margin_status === 'stale' ? (
                <span className="px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400 font-medium text-[10px]">
                  待更新{statusData.margin_days_behind > 0 ? ` (${statusData.margin_days_behind}日)` : ''}
                </span>
              ) : (
                <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[10px]">無資料</span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Right: Target Date & Schedule Info */}
      {statusData && (
        <div className="flex items-center gap-3 text-zinc-400 shrink-0 text-[11px]">
          <div>
            應有最新交易日: <strong className="text-zinc-200 font-mono">{statusData.target_latest_trading_date}</strong>
          </div>
          <span className="text-zinc-700">|</span>
          <div className="flex items-center gap-1">
            <Clock className="w-3 h-3 text-purple-400" />
            <span>自動更新: <strong className="text-zinc-300">交易日 {statusData.scheduled_update_time}</strong> ({statusData.scheduled_timezone})</span>
          </div>
        </div>
      )}
    </div>
  </div>

    </>
  )
}
