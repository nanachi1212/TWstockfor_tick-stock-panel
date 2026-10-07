import { Loader2 } from 'lucide-react'
import {
  EmptyPicks,
  MarketSummaryCard,
  NotSelectedList,
  STRENGTH_DISCLAIMER,
} from '@/components/beginner/BeginnerPicker'
import { EntryRadarBoard, useEntryRadar } from '@/components/beginner/EntryRadar'

export function BeginnerPicks() {
  const query = useEntryRadar()
  const data = query.data
  return (
    <div className="min-h-full bg-base p-2 sm:p-3">
      <div className="mx-auto w-full max-w-6xl space-y-2">
        <header>
          <h1 className="text-lg font-semibold text-foreground">每日承接雷達</h1>
          <p className="text-xs text-muted">今日選股加上你的持股和自選股，用固定規則分成「可觀察／等拉回／等突破／不追」，盤中自動比對現價和承接區、失效位。這不是報酬預測，AI 不參與排名。</p>
        </header>
        {query.isLoading && (
          <p className="flex items-center gap-2 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在整理今天的雷達…</p>
        )}
        {query.isError && <p className="text-xs text-danger">承接雷達暫時無法讀取，請稍後再試。</p>}
        {data && (
          <>
            <MarketSummaryCard market={data.market} />
            <p className="text-[11px] text-muted">{STRENGTH_DISCLAIMER} {data.disclaimer}</p>
            {data.items.length === 0 ? (
              <EmptyPicks gaps={data.data_gaps} />
            ) : (
              <EntryRadarBoard items={data.items} />
            )}
            <NotSelectedList items={data.not_selected} />
            <p className="text-[11px] text-muted">
              規則版本 {data.selection_version} / {data.version}｜候選池：成交金額前 {data.universe_count} 檔普通股，通過資料檢查 {data.eligible_count} 檔
              {data.market_session === 'open' ? '｜盤中每 30 秒更新' : '｜非盤中，每 5 分鐘更新'}
              {data.data_gaps.length > 0 && `｜資料缺口：${data.data_gaps.join('、')}`}
            </p>
          </>
        )}
      </div>
    </div>
  )
}
