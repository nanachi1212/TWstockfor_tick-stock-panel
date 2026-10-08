import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, History, Loader2, MessageCircleMore, Sparkles } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'

export function DashboardDailyBriefWidget() {
  const briefQuery = useQuery({
    queryKey: QK.dailyBrief(),
    queryFn: () => api.dailyBrief.getDailyBrief(),
    staleTime: 5 * 60 * 1000,
  })
  const historyQuery = useQuery({
    queryKey: QK.dailyBriefHistory(1),
    queryFn: () => api.dailyBrief.listHistory(1),
    staleTime: 5 * 60 * 1000,
  })

  const brief = briefQuery.data
  const latestSaved = historyQuery.data?.[0]
  // 只有同一個 brief_date 的已保存 AI 摘要才算「今日」; 否則顯示確定性 fallback, 不拿昨天的文字配今天的日期
  const hasAi = !!latestSaved?.ai_summary && !!brief && latestSaved.brief_date === brief.brief_date

  return (
    <section className="mb-2.5 rounded-card border border-border bg-surface/85 p-3 shadow-sm backdrop-blur-sm">
      <div className="flex items-center justify-between border-b border-border/60 pb-2 mb-2">
        <div className="flex items-center gap-1.5">
          <span className="h-3.5 w-1 rounded-full bg-gradient-to-b from-primary to-primary/40" />
          <Sparkles className="h-4 w-4 text-primary" />
          <h2 className="text-xs font-bold text-foreground">每日 AI 盤勢與候選摘要</h2>
          <span className="rounded bg-primary/10 px-1.5 py-0.2 text-[10px] font-semibold text-primary">
            7 段式架構 · 零幻覺
          </span>
        </div>
        <Link
          to="/daily-brief"
          className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
        >
          <span>進入每日摘要</span>
          <ArrowUpRight className="h-3 w-3" />
        </Link>
      </div>

      {briefQuery.isLoading ? (
        <div className="flex items-center justify-center py-4 text-xs text-muted gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
          正在組裝今日確定性事實…
        </div>
      ) : !brief ? (
        <div className="py-3 text-center text-xs text-muted">
          今日市場簡報尚未就緒。
        </div>
      ) : (
        <div className="space-y-2 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-muted">
              基準交易日：<strong className="text-foreground font-mono">{brief.brief_date}</strong>
            </span>
            <span className="text-muted">
              今日候選股：<strong className="text-primary font-mono">{brief.candidates.new_top10.length + brief.candidates.strategy_matches.length}</strong> 檔
            </span>
          </div>
          {hasAi && latestSaved?.ai_summary?.section_b_key_changes?.[0] ? (
            <div className="p-2 rounded bg-primary/5 border border-primary/20 text-[11px] text-foreground/90">
              <span className="font-semibold text-primary mr-1">今日核心亮點:</span>
              {latestSaved.ai_summary.section_b_key_changes[0]}
            </div>
          ) : (
            <p className="text-[11px] text-muted leading-relaxed">
              大盤加權指數 {brief.market.taiex_close?.toFixed(1) ?? '—'}，情緒 {brief.market.sentiment_label}。點擊進入可手動產生今日 AI 深度解讀。
            </p>
          )}
        </div>
      )}
    </section>
  )
}

export function DashboardSelectionReviewWidget() {
  const snapshotsQuery = useQuery({
    queryKey: QK.selectionSnapshots(),
    queryFn: () => api.selectionReview.listSnapshots({ limit: 3 }),
    staleTime: 5 * 60 * 1000,
  })

  const snapshots = snapshotsQuery.data ?? []
  const latestSnapshot = snapshots[0]

  return (
    <section className="mb-2.5 rounded-card border border-border bg-surface/85 p-3 shadow-sm backdrop-blur-sm">
      <div className="flex items-center justify-between border-b border-border/60 pb-2 mb-2">
        <div className="flex items-center gap-1.5">
          <span className="h-3.5 w-1 rounded-full bg-gradient-to-b from-accent to-accent/40" />
          <History className="h-4 w-4 text-accent" />
          <h2 className="text-xs font-bold text-foreground">選股復盤與策略成效</h2>
          <span className="rounded bg-accent/10 px-1.5 py-0.2 text-[10px] font-semibold text-accent">
            1D · 5D · 20D 追蹤
          </span>
        </div>
        <Link
          to="/selection-review"
          className="inline-flex items-center gap-1 text-[11px] font-medium text-accent hover:underline"
        >
          <span>進入選股復盤</span>
          <ArrowUpRight className="h-3 w-3" />
        </Link>
      </div>

      {snapshotsQuery.isLoading ? (
        <div className="flex items-center justify-center py-4 text-xs text-muted gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
          正在讀取選股快照…
        </div>
      ) : snapshots.length === 0 ? (
        <div className="py-3 px-2 text-xs text-muted rounded bg-elevated/30 flex items-center justify-between">
          <span>尚未保存任何選股快照。可於「台股選股」篩選後一鍵保存。</span>
          <Link to="/taiwan-screener" className="text-accent hover:underline shrink-0 text-[11px] ml-2">
            前往選股
          </Link>
        </div>
      ) : (
        <div className="space-y-2 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-muted">
              最新快照：<strong className="text-foreground">{latestSnapshot.strategy_name}</strong>
            </span>
            <span className="font-mono text-muted text-[11px]">{latestSnapshot.as_of_date}</span>
          </div>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div className="bg-elevated/40 p-1.5 rounded border border-border/40">
              <span className="text-muted block text-[10px]">5D 交易日超額 (vs 0050)</span>
              <span className={cn('font-semibold font-mono', (latestSnapshot.h5d_excess_pct ?? 0) >= 0 ? 'text-bull' : 'text-bear')}>
                {latestSnapshot.h5d_evaluated_count > 0 && latestSnapshot.h5d_excess_pct !== null
                  ? `${latestSnapshot.h5d_excess_pct >= 0 ? '+' : ''}${latestSnapshot.h5d_excess_pct.toFixed(2)}%`
                  : '追蹤中'}
              </span>
            </div>
            <div className="bg-elevated/40 p-1.5 rounded border border-border/40">
              <span className="text-muted block text-[10px]">20D 交易日超額 (vs 0050)</span>
              <span className={cn('font-semibold font-mono', (latestSnapshot.h20d_excess_pct ?? 0) >= 0 ? 'text-bull' : 'text-bear')}>
                {latestSnapshot.h20d_evaluated_count > 0 && latestSnapshot.h20d_excess_pct !== null
                  ? `${latestSnapshot.h20d_excess_pct >= 0 ? '+' : ''}${latestSnapshot.h20d_excess_pct.toFixed(2)}%`
                  : '追蹤中'}
              </span>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

export function DashboardSocialSentimentWidget() {
  const social = useQuery({
    queryKey: QK.taiwanSocialSentiment(),
    queryFn: () => api.taiwanSocialSentiment(),
    staleTime: 5 * 60 * 1000,
  })
  const rows = social.data?.rankings.slice(0, 5) ?? []

  return (
    <section className="mb-2.5 rounded-card border border-border bg-surface/85 p-3 shadow-sm backdrop-blur-sm">
      <div className="mb-2 flex items-center justify-between border-b border-border/60 pb-2">
        <div className="flex items-center gap-1.5">
          <span className="h-3.5 w-1 rounded-full bg-gradient-to-b from-cyan-400 to-cyan-400/40" />
          <MessageCircleMore className="h-4 w-4 text-cyan-500" />
          <h2 className="text-xs font-bold text-foreground">社群熱門標的</h2>
          {social.data?.status === 'partial' && <span className="rounded bg-warning/10 px-1.5 py-0.5 text-[10px] font-semibold text-warning">部分來源</span>}
        </div>
        <Link to="/social-sentiment" className="inline-flex items-center gap-1 text-[11px] font-medium text-cyan-600 hover:underline dark:text-cyan-400">
          社群聲量完整頁<ArrowUpRight className="h-3 w-3" />
        </Link>
      </div>
      {social.isLoading ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在讀取社群聲量…</div>
      ) : social.isError || rows.length === 0 ? (
        <p className="py-3 text-center text-xs text-muted">社群聲量尚未就緒，排行榜不以零值代替。</p>
      ) : (
        <div className="space-y-1.5">
          {rows.map(row => (
            <Link key={row.symbol} to={`/stocks/${encodeURIComponent(row.symbol)}`} className="grid min-h-9 grid-cols-[1fr_auto_auto] items-center gap-3 rounded-lg border border-border/40 bg-base/40 px-2 text-xs hover:border-cyan-500/30">
              <span className="min-w-0 truncate font-medium text-foreground">{row.company_name} <span className="font-mono text-[10px] text-muted">{row.code}</span></span>
              <span className="font-mono text-cyan-600 dark:text-cyan-400">熱度 {row.social_heat_score.toFixed(1)}</span>
              <span className="font-mono text-muted">{row.total_mentions} 則 · {row.sentiment_status === 'available' ? row.sentiment : '情緒不可用'}</span>
            </Link>
          ))}
        </div>
      )}
    </section>
  )
}
