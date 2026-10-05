import { Loader2 } from 'lucide-react'
import {
  EmptyPicks,
  MarketSummaryCard,
  NotSelectedList,
  PickCard,
  STRENGTH_DISCLAIMER,
  useBeginnerSelection,
} from '@/components/beginner/BeginnerPicker'

export function BeginnerPicks() {
  const query = useBeginnerSelection()
  const data = query.data
  return (
    <div className="min-h-full bg-base p-2 sm:p-3">
      <div className="mx-auto w-full max-w-6xl space-y-2">
        <header>
          <h1 className="text-lg font-semibold text-foreground">今日選股</h1>
          <p className="text-xs text-muted">用固定、透明的規則挑出今天值得看的股票，並告訴你現在該怎麼做。這不是報酬預測，AI 不參與排名。</p>
        </header>
        {query.isLoading && (
          <p className="flex items-center gap-2 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在整理今天的選股…</p>
        )}
        {query.isError && <p className="text-xs text-danger">今日選股暫時無法讀取，請稍後再試。</p>}
        {data && (
          <>
            <MarketSummaryCard market={data.market} />
            <p className="text-[11px] text-muted">{STRENGTH_DISCLAIMER}</p>
            {data.candidates.length === 0 ? (
              <EmptyPicks gaps={data.data_gaps} />
            ) : (
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
                {data.candidates.map(c => <PickCard key={c.symbol} candidate={c} />)}
              </div>
            )}
            <NotSelectedList items={data.not_selected} />
            <p className="text-[11px] text-muted">
              規則版本 {data.version}｜候選池：成交金額前 {data.universe_count} 檔普通股，通過資料檢查 {data.eligible_count} 檔
              {data.data_gaps.length > 0 && `｜資料缺口：${data.data_gaps.join('、')}`}
            </p>
          </>
        )}
      </div>
    </div>
  )
}
