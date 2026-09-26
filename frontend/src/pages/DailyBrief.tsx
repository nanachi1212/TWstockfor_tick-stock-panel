import { useState, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Sparkles,
  TrendingUp,
  TrendingDown,
  Star,
  ShieldAlert,
  AlertTriangle,
  ArrowUpRight,
  BookmarkPlus,
  Loader2,
  Info,
  History,
  FileText,
} from 'lucide-react'
import {
  api,
  type DeterministicDailyBrief,
  type DailyBriefAISummary,
  type SavedDailyBrief,
  type CandidateItem,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'
import { cn } from '@/lib/cn'
import { CopyButton } from '@/components/CopyButton'
import { formatDailyBriefCopy, formatDailyBriefPrompt } from '@/lib/copy-formatters'

export function DailyBrief() {
  const qc = useQueryClient()
  const navigate = useNavigate()

  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null)
  const [showHistoryModal, setShowHistoryModal] = useState(false)
  const [liveAiSummary, setLiveAiSummary] = useState<DailyBriefAISummary | null>(null)
  const [isAiLoading, setIsAiLoading] = useState(false)

  // 1. 查詢今日客觀確定性事實簡報
  const briefQuery = useQuery({
    queryKey: QK.dailyBrief(),
    queryFn: () => api.dailyBrief.getDailyBrief(),
  })

  // 2. 歷史列表
  const historyQuery = useQuery({
    queryKey: QK.dailyBriefHistory(),
    queryFn: () => api.dailyBrief.listHistory(30),
  })

  // 3. 自選股列表 (比對是否已加入)
  const watchlistQuery = useQuery({
    queryKey: QK.watchlist,
    queryFn: api.watchlistList,
  })

  const watchlistSymbols = useMemo(
    () => new Set((watchlistQuery.data?.symbols ?? []).map((s) => s.symbol)),
    [watchlistQuery.data],
  )

  // 加入/移除自選 Mutation
  const toggleWatchlistMutation = useMutation({
    mutationFn: ({ symbol, action }: { symbol: string; action: 'add' | 'remove' }) =>
      action === 'remove' ? api.watchlistRemove(symbol) : api.watchlistAdd(symbol, ''),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.watchlist })
      qc.invalidateQueries({ queryKey: ['watchlist-enriched'] })
      toast.success('已更新自選清單')
    },
    onError: (err: any) => {
      toast.error('操作自選失敗: ' + (err.message || '未知錯誤'))
    },
  })

  // 4. 手動觸發 AI 摘要生成 Mutation
  const generateAiMutation = useMutation({
    mutationFn: (brief: DeterministicDailyBrief) => api.dailyBrief.generateAiSummary(brief),
    onMutate: () => setIsAiLoading(true),
    onSuccess: (data) => {
      setLiveAiSummary(data)
      setIsAiLoading(false)
      toast.success('已生成今日 AI 深度摘要')
    },
    onError: (err: any) => {
      setIsAiLoading(false)
      toast.error('AI 摘要生成失敗: ' + (err.message || '請確認本機 AI / 模型連線'))
    },
  })

  // 5. 保存日報到歷史 Mutation
  const saveHistoryMutation = useMutation({
    mutationFn: (payload: SavedDailyBrief) => api.dailyBrief.saveDailyBrief(payload),
    onSuccess: () => {
      toast.success('已保存至日報歷史')
      qc.invalidateQueries({ queryKey: QK.dailyBriefHistory() })
    },
    onError: (err: any) => {
      toast.error('保存失敗: ' + (err.message || '未知錯誤'))
    },
  })

  // 6. 保存候選股為快照 Mutation
  const saveSnapshotMutation = useMutation({
    mutationFn: (candidates: CandidateItem[]) => {
      const today = briefQuery.data?.brief_date || new Date().toISOString().slice(0, 10)
      return api.selectionReview.saveSnapshot({
        strategy_id: 'daily_brief_candidates',
        strategy_name: '每日摘要今日候選股',
        as_of_date: today,
        market_context_summary: `加權指數 ${briefQuery.data?.market.taiex_close || '-'}，情緒 ${briefQuery.data?.market.sentiment_label || '中性'}`,
        items: candidates.map((c, idx) => ({
          symbol: c.symbol,
          name: c.name,
          rank: c.rank ?? idx + 1,
          quant_score: c.quant_score,
          match_reasons: c.match_reasons.length > 0 ? c.match_reasons : [c.source_name],
          strategy_conditions: { reason_type: c.reason_type },
          price: c.close ?? 100.0,
          fundamental_summary: null,
          chips_summary: null,
          event_risk_summary: null,
        })),
      })
    },
    onSuccess: () => {
      toast.success('已將候選股保存為選股快照！')
      qc.invalidateQueries({ queryKey: ['selection-snapshots'] })
      navigate('/selection-review')
    },
    onError: (err: any) => {
      toast.error('保存快照失敗: ' + (err.message || '未知錯誤'))
    },
  })

  // 若使用者選取了歷史日報
  const activeHistoryBrief = useMemo(() => {
    if (!selectedHistoryId || !historyQuery.data) return null
    return historyQuery.data.find((h) => h.brief_id === selectedHistoryId) || null
  }, [selectedHistoryId, historyQuery.data])

  const brief = activeHistoryBrief ? activeHistoryBrief.structured_brief : briefQuery.data
  const aiSummary = activeHistoryBrief ? activeHistoryBrief.ai_summary : liveAiSummary
  const [includePortfolioInCopy, setIncludePortfolioInCopy] = useState<boolean>(false)

  const allCandidates: CandidateItem[] = useMemo(() => {
    if (!brief) return []
    return [
      ...brief.candidates.new_top10,
      ...brief.candidates.strategy_matches,
      ...brief.candidates.dropped_top10,
    ]
  }, [brief])

  const handleGenerateAi = () => {
    if (!brief) return
    generateAiMutation.mutate(brief)
  }

  const handleSaveToHistory = () => {
    if (!brief) return
    const id = `brief_${brief.brief_date}_${Date.now().toString(36)}`
    const payload: SavedDailyBrief = {
      brief_id: id,
      brief_date: brief.brief_date,
      generated_at: new Date().toISOString(),
      data_as_of: brief.brief_date,
      structured_brief: brief,
      ai_summary: aiSummary,
      ai_status: aiSummary ? 'success' : 'not_generated',
      ai_error: null,
    }
    saveHistoryMutation.mutate(payload)
  }

  return (
    <div className="space-y-6 max-w-7xl mx-auto px-4 py-6">
      {/* 頂部標頭列 */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-border/40 pb-4">
        <div>
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-lg bg-primary/10 text-primary">
              <Sparkles className="h-6 w-6" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-2xl font-bold tracking-tight">每日台股摘要與 AI 解讀</h1>
                {brief && (
                  <span className="text-xs px-2.5 py-0.5 rounded-full bg-muted text-muted-foreground font-mono font-medium">
                    {brief.brief_date}
                  </span>
                )}
              </div>
              <p className="text-sm text-muted-foreground mt-0.5">
                整合既有看板、籌碼、量化模型、自選與事件中心，提供確定性事實與客觀 7 段式 AI 剖析
              </p>
            </div>
          </div>
        </div>

        {/* 右側操作群組 */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* 複製資料與隱私開關 */}
          {brief && (
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={includePortfolioInCopy}
                  onChange={e => setIncludePortfolioInCopy(e.target.checked)}
                  className="rounded border-border bg-base text-accent focus:ring-accent"
                />
                <span>包含個人持股資料</span>
              </label>
              <CopyButton
                label="複製資料"
                getText={() => formatDailyBriefCopy({
                  brief_date: brief.brief_date,
                  evidence_date: (brief as any).evidence_date || brief.brief_date,
                  market: brief.market ? {
                    taiex_close: brief.market.taiex_close,
                    change: brief.market.taiex_change,
                    change_pct: brief.market.taiex_change_pct,
                    turnover: brief.market.total_turnover,
                    sentiment_label: brief.market.sentiment_label,
                    foreign_net: brief.market.foreign_net,
                    trust_net: brief.market.investment_trust_net,
                    dealer_net: brief.market.dealer_net,
                    advances: brief.market.advance_count,
                    declines: brief.market.decline_count,
                    unchanged: brief.market.flat_count,
                    limit_up: brief.market.upper_limit_count,
                    limit_down: brief.market.lower_limit_count,
                  } : null,
                  sectors: brief.market ? {
                    strong: brief.market.strongest_sectors.map(s => ({ name: s.industry, change_pct: s.change_pct })),
                    weak: brief.market.weakest_sectors.map(s => ({ name: s.industry, change_pct: s.change_pct })),
                  } : null,
                  portfolio: brief.portfolio ? {
                    holdings_count: brief.portfolio.holdings_count,
                    items: brief.portfolio.biggest_movers?.map(m => ({
                      symbol: m.symbol,
                      name: m.name,
                      shares: m.shares,
                      avg_cost: m.average_cost,
                      close: m.close,
                      change_pct: m.change_pct,
                    })),
                  } : null,
                  includePortfolio: includePortfolioInCopy,
                  watchlist: brief.watchlist?.quant_leaders?.map(w => ({
                    symbol: w.symbol,
                    name: w.name,
                    close: w.close,
                    change_pct: w.change_pct,
                    quant_score: w.quant_score,
                  })),
                  candidates: allCandidates.map(c => ({
                    symbol: c.symbol,
                    name: c.name,
                    score: c.quant_score,
                    reason: c.match_reasons?.[0],
                    close: c.close,
                    change_pct: c.change_pct,
                  })),
                  events: (brief.events?.risk_events || []).concat(brief.events?.attention_events || []).map((e: any) => ({
                    symbol: e.symbol,
                    name: e.name,
                    date: e.event_date,
                    type: e.event_type_label,
                    title: e.title || e.event_type_label,
                  })),
                  news: brief.news?.items?.map((n: any) => ({
                    title: n.title,
                    source: n.source,
                    date: n.published_at,
                  })),
                  aiSummary: aiSummary ? [
                    aiSummary.section_a_market,
                    ...(aiSummary.section_b_key_changes ?? []),
                    aiSummary.section_c_portfolio,
                    aiSummary.section_d_watchlist,
                    aiSummary.section_e_candidates,
                    aiSummary.section_f_risks,
                    aiSummary.section_g_tracking,
                  ].filter(Boolean).join('\n\n') : null,
                })}
              />
              <CopyButton
                label="複製 AI 提示詞"
                successLabel="已複製提示詞"
                getText={() => formatDailyBriefPrompt({
                  brief_date: brief.brief_date,
                  evidence_date: (brief as any).evidence_date || brief.brief_date,
                  market: brief.market ? {
                    taiex_close: brief.market.taiex_close,
                    change: brief.market.taiex_change,
                    change_pct: brief.market.taiex_change_pct,
                    turnover: brief.market.total_turnover,
                    sentiment_label: brief.market.sentiment_label,
                    foreign_net: brief.market.foreign_net,
                    trust_net: brief.market.investment_trust_net,
                    dealer_net: brief.market.dealer_net,
                    advances: brief.market.advance_count,
                    declines: brief.market.decline_count,
                    unchanged: brief.market.flat_count,
                    limit_up: brief.market.upper_limit_count,
                    limit_down: brief.market.lower_limit_count,
                  } : null,
                  sectors: brief.market ? {
                    strong: brief.market.strongest_sectors.map(s => ({ name: s.industry, change_pct: s.change_pct })),
                    weak: brief.market.weakest_sectors.map(s => ({ name: s.industry, change_pct: s.change_pct })),
                  } : null,
                  portfolio: brief.portfolio ? {
                    holdings_count: brief.portfolio.holdings_count,
                    items: brief.portfolio.biggest_movers?.map(m => ({
                      symbol: m.symbol,
                      name: m.name,
                      shares: m.shares,
                      avg_cost: m.average_cost,
                      close: m.close,
                      change_pct: m.change_pct,
                    })),
                  } : null,
                  includePortfolio: includePortfolioInCopy,
                  watchlist: brief.watchlist?.quant_leaders?.map(w => ({
                    symbol: w.symbol,
                    name: w.name,
                    close: w.close,
                    change_pct: w.change_pct,
                    quant_score: w.quant_score,
                  })),
                  candidates: allCandidates.map(c => ({
                    symbol: c.symbol,
                    name: c.name,
                    score: c.quant_score,
                    reason: c.match_reasons?.[0],
                    close: c.close,
                    change_pct: c.change_pct,
                  })),
                  events: (brief.events?.risk_events || []).concat(brief.events?.attention_events || []).map((e: any) => ({
                    symbol: e.symbol,
                    name: e.name,
                    date: e.event_date,
                    type: e.event_type_label,
                    title: e.title || e.event_type_label,
                  })),
                  news: brief.news?.items?.map((n: any) => ({
                    title: n.title,
                    source: n.source,
                    date: n.published_at,
                  })),
                  aiSummary: aiSummary ? [
                    aiSummary.section_a_market,
                    ...(aiSummary.section_b_key_changes ?? []),
                    aiSummary.section_c_portfolio,
                    aiSummary.section_d_watchlist,
                    aiSummary.section_e_candidates,
                    aiSummary.section_f_risks,
                    aiSummary.section_g_tracking,
                  ].filter(Boolean).join('\n\n') : null,
                })}
              />
            </div>
          )}
          {/* 歷史紀錄按鈕 */}
          <button
            onClick={() => setShowHistoryModal(true)}
            className="inline-flex items-center gap-1.5 text-xs font-medium bg-muted/60 hover:bg-muted text-foreground px-3 py-2 rounded-lg border border-border/60 transition-colors"
          >
            <History className="h-4 w-4" />
            <span>歷史日報 ({historyQuery.data?.length || 0})</span>
          </button>

          {/* 產生 AI 摘要按鈕 */}
          <button
            onClick={handleGenerateAi}
            disabled={isAiLoading || !brief}
            className="inline-flex items-center gap-1.5 text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-2 rounded-lg shadow-sm transition-all disabled:opacity-50"
          >
            {isAiLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                <span>AI 深入解讀中...</span>
              </>
            ) : (
              <>
                <Sparkles className="h-4 w-4" />
                <span>{aiSummary ? '重新產生 AI 摘要' : '產生今日 AI 摘要'}</span>
              </>
            )}
          </button>

          {/* 保存當前摘要 */}
          {aiSummary && !activeHistoryBrief && (
            <button
              onClick={handleSaveToHistory}
              disabled={saveHistoryMutation.isPending}
              className="inline-flex items-center gap-1.5 text-xs font-medium bg-secondary text-secondary-foreground hover:bg-secondary/80 px-3 py-2 rounded-lg border border-border/60 transition-colors"
            >
              <BookmarkPlus className="h-4 w-4" />
              <span>保存此份</span>
            </button>
          )}

          {activeHistoryBrief && (
            <button
              onClick={() => setSelectedHistoryId(null)}
              className="text-xs text-primary hover:underline px-2 py-1"
            >
              返回今日最新
            </button>
          )}
        </div>
      </div>

      {briefQuery.isLoading ? (
        <div className="text-center py-20 text-sm text-muted-foreground">彙整今日客觀數據中...</div>
      ) : !brief ? (
        <div className="text-center py-20 border border-dashed rounded-xl p-8 bg-card">
          <Info className="h-10 w-10 text-muted-foreground/40 mx-auto mb-3" />
          <h3 className="font-medium text-base">今日市場簡報尚未就緒</h3>
          <p className="text-xs text-muted-foreground mt-1">
            資料庫可能尚無最新行情或日線資料，請先至看板確認資料更新狀態。
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          {/* 1. 確定性層：市場與大盤事實 */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div className="bg-card border border-border/60 rounded-xl p-4">
              <span className="text-xs text-muted-foreground">加權指數收盤</span>
              <div className="flex items-baseline gap-2 mt-1">
                <span className="text-xl font-bold font-mono">
                  {brief.market.taiex_close?.toFixed(2) ?? '—'}
                </span>
                {brief.market.taiex_change_pct != null ? (
                  <span
                    className={cn(
                      'text-xs font-semibold',
                      brief.market.taiex_change_pct >= 0 ? 'text-rose-500' : 'text-emerald-500',
                    )}
                  >
                    {brief.market.taiex_change_pct >= 0 ? '+' : ''}
                    {brief.market.taiex_change_pct.toFixed(2)}%
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground">—</span>
                )}
              </div>
              <div className="text-[11px] text-muted-foreground mt-1">
                成交量：{brief.market.total_turnover > 0
                  ? `${(brief.market.total_turnover / 1e8).toFixed(1)} 億元`
                  : '—'}
              </div>
            </div>

            <div className="bg-card border border-border/60 rounded-xl p-4">
              <span className="text-xs text-muted-foreground">全市場漲跌家數</span>
              <div className="flex items-center gap-3 mt-1.5 text-xs font-medium">
                <span className="text-rose-500 flex items-center gap-1">
                  <TrendingUp className="h-3.5 w-3.5" /> 上漲 {brief.market.advance_count} (漲停{' '}
                  {brief.market.upper_limit_count})
                </span>
                <span className="text-emerald-500 flex items-center gap-1">
                  <TrendingDown className="h-3.5 w-3.5" /> 下跌 {brief.market.decline_count} (跌停{' '}
                  {brief.market.lower_limit_count})
                </span>
              </div>
              <div className="text-[11px] text-muted-foreground mt-1.5">
                平盤 {brief.market.flat_count} 檔
              </div>
            </div>

            <div className="bg-card border border-border/60 rounded-xl p-4">
              <span className="text-xs text-muted-foreground">三大法人籌碼</span>
              <div className="mt-1 space-y-0.5 text-xs">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">外資</span>
                  <span
                    className={cn(
                      'font-medium font-mono',
                      (brief.market.foreign_net ?? 0) >= 0 ? 'text-rose-500' : 'text-emerald-500',
                    )}
                  >
                    {brief.market.foreign_net ? `${(brief.market.foreign_net / 1e8).toFixed(1)} 億` : '-'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">投信</span>
                  <span
                    className={cn(
                      'font-medium font-mono',
                      (brief.market.investment_trust_net ?? 0) >= 0 ? 'text-rose-500' : 'text-emerald-500',
                    )}
                  >
                    {brief.market.investment_trust_net
                      ? `${(brief.market.investment_trust_net / 1e8).toFixed(1)} 億`
                      : '-'}
                  </span>
                </div>
              </div>
            </div>

            <div className="bg-card border border-border/60 rounded-xl p-4">
              <span className="text-xs text-muted-foreground">市場情緒與主線</span>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-sm font-bold text-primary">{brief.market.sentiment_label}</span>
                <span className="text-xs text-muted-foreground line-clamp-1">
                  {brief.market.sentiment_description}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                {brief.market.strongest_sectors.slice(0, 2).map((s, i) => (
                  <span
                    key={i}
                    className="text-[10px] bg-rose-500/10 text-rose-600 dark:text-rose-400 px-1.5 py-0.5 rounded font-medium"
                  >
                    {s.industry} +{s.change_pct?.toFixed(1)}%
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* 2. AI 深度解讀區塊 (若已產生或正在產生) */}
          <div className="bg-card border border-border/80 rounded-xl p-6 shadow-sm relative overflow-hidden">
            <div className="flex items-center justify-between border-b border-border/40 pb-3 mb-4">
              <div className="flex items-center gap-2">
                <Sparkles className="h-5 w-5 text-primary" />
                <h3 className="font-bold text-base text-foreground">今日 AI 深度結構剖析</h3>
                <span className="text-xs px-2 py-0.5 rounded bg-primary/10 text-primary font-medium">
                  7 大重點段落
                </span>
              </div>
              <span className="text-xs text-muted-foreground">
                依據客觀事實推導 • 不下買賣指令 • 僅供研究
              </span>
            </div>

            {isAiLoading ? (
              <div className="py-12 text-center space-y-3">
                <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto" />
                <p className="text-sm font-medium text-foreground">AI 正在深度解讀今日事實與異動...</p>
                <p className="text-xs text-muted-foreground max-w-md mx-auto">
                  綜合分析大盤成交結構、強弱族群、持股與自選動態，嚴格依 7 大架構輸出
                </p>
              </div>
            ) : aiSummary ? (
              <div className="space-y-5 text-sm">
                {/* A. 今日市場概況 */}
                <div className="space-y-1.5">
                  <h4 className="font-semibold text-foreground flex items-center gap-1.5 text-xs text-primary">
                    <span className="w-1.5 h-1.5 rounded-full bg-primary" />
                    A. 今日市場概況與盤勢解讀
                  </h4>
                  <p className="text-muted-foreground text-xs leading-relaxed pl-3 border-l-2 border-primary/20">
                    {aiSummary.section_a_market}
                  </p>
                </div>

                {/* B. 3～5 個市場/持股變化 */}
                <div className="space-y-1.5">
                  <h4 className="font-semibold text-foreground flex items-center gap-1.5 text-xs text-primary">
                    <span className="w-1.5 h-1.5 rounded-full bg-primary" />
                    B. 今日最重要的 3～5 個核心變化
                  </h4>
                  <ul className="text-muted-foreground text-xs space-y-1 pl-3 border-l-2 border-primary/20 list-disc list-inside">
                    {aiSummary.section_b_key_changes.map((item, idx) => (
                      <li key={idx} className="leading-relaxed">
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>

                {/* C & D. 持股與自選 */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <h4 className="font-semibold text-foreground flex items-center gap-1.5 text-xs text-primary">
                      <span className="w-1.5 h-1.5 rounded-full bg-primary" />
                      C. 我的持股重點追蹤
                    </h4>
                    <p className="text-muted-foreground text-xs leading-relaxed pl-3 border-l-2 border-primary/20">
                      {aiSummary.section_c_portfolio}
                    </p>
                  </div>

                  <div className="space-y-1.5">
                    <h4 className="font-semibold text-foreground flex items-center gap-1.5 text-xs text-primary">
                      <span className="w-1.5 h-1.5 rounded-full bg-primary" />
                      D. 我的觀察清單動態
                    </h4>
                    <p className="text-muted-foreground text-xs leading-relaxed pl-3 border-l-2 border-primary/20">
                      {aiSummary.section_d_watchlist}
                    </p>
                  </div>
                </div>

                {/* E. 候選股與新機會 */}
                <div className="space-y-1.5">
                  <h4 className="font-semibold text-foreground flex items-center gap-1.5 text-xs text-primary">
                    <span className="w-1.5 h-1.5 rounded-full bg-primary" />
                    E. 今日候選股與新機會亮點
                  </h4>
                  <p className="text-muted-foreground text-xs leading-relaxed pl-3 border-l-2 border-primary/20">
                    {aiSummary.section_e_candidates}
                  </p>
                </div>

                {/* F & G. 風險與明日追蹤 */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <h4 className="font-semibold text-foreground flex items-center gap-1.5 text-xs text-primary">
                      <span className="w-1.5 h-1.5 rounded-full bg-primary" />
                      F. 官方重大事件與風險警示
                    </h4>
                    <p className="text-muted-foreground text-xs leading-relaxed pl-3 border-l-2 border-primary/20">
                      {aiSummary.section_f_risks}
                    </p>
                  </div>

                  <div className="space-y-1.5">
                    <h4 className="font-semibold text-foreground flex items-center gap-1.5 text-xs text-primary">
                      <span className="w-1.5 h-1.5 rounded-full bg-primary" />
                      G. 明日／下一交易日核心觀察重點
                    </h4>
                    <p className="text-muted-foreground text-xs leading-relaxed pl-3 border-l-2 border-primary/20">
                      {aiSummary.section_g_tracking}
                    </p>
                  </div>
                </div>

                {/* 客觀依據 */}
                {aiSummary.evidence_sources && aiSummary.evidence_sources.length > 0 && (
                  <div className="pt-3 border-t border-border/40 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                    <span>客觀依據：</span>
                    {aiSummary.evidence_sources.map((src, i) => (
                      <span key={i} className="bg-muted px-2 py-0.5 rounded text-[10px]">
                        {src}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="py-8 text-center bg-muted/20 border border-dashed rounded-xl">
                <Sparkles className="h-8 w-8 text-muted-foreground/40 mx-auto mb-2" />
                <p className="text-sm font-medium text-foreground">尚未產生今日 AI 深度摘要</p>
                <p className="text-xs text-muted-foreground mt-1 max-w-sm mx-auto">
                  為節省模型資源與確保使用者授權，系統不會在背景自動呼叫 AI。請點擊上方按鈕主動觸發。
                </p>
                <button
                  onClick={handleGenerateAi}
                  disabled={isAiLoading}
                  className="inline-flex items-center gap-1.5 mt-3 text-xs font-medium bg-primary text-primary-foreground px-3.5 py-1.5 rounded-lg hover:bg-primary/90 transition-colors"
                >
                  <Sparkles className="h-3.5 w-3.5" />
                  產生今日 AI 摘要
                </button>
              </div>
            )}
          </div>

          {/* 3. 今日新候選股與選股機會 */}
          <div className="bg-card border border-border/60 rounded-xl p-5 space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-border/40 pb-3">
              <div>
                <h3 className="font-bold text-sm text-foreground flex items-center gap-1.5">
                  <TrendingUp className="h-4 w-4 text-primary" />
                  今日新候選股清單 ({allCandidates.length} 檔)
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  來自 Live Quant Top 10 新進/跌出標的與既有選股策略匹配，支援一鍵加入自選或保存快照
                </p>
              </div>

              {allCandidates.length > 0 && (
                <button
                  onClick={() => saveSnapshotMutation.mutate(allCandidates)}
                  disabled={saveSnapshotMutation.isPending}
                  className="inline-flex items-center gap-1.5 text-xs font-medium bg-secondary text-secondary-foreground hover:bg-secondary/80 border border-border/60 px-3 py-1.5 rounded-lg transition-colors"
                >
                  <BookmarkPlus className="h-3.5 w-3.5" />
                  <span>保存本次為選股快照</span>
                </button>
              )}
            </div>

            {allCandidates.length === 0 ? (
              <div className="text-center py-6 text-xs text-muted-foreground">
                今日尚無顯著新進候選股
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {allCandidates.map((c) => {
                  const isWatchlisted = watchlistSymbols.has(c.symbol)
                  return (
                    <div
                      key={c.symbol}
                      className="border border-border/60 rounded-lg p-3 bg-muted/20 flex flex-col justify-between"
                    >
                      <div>
                        <div className="flex items-start justify-between">
                          <div>
                            <Link
                              to={`/stocks/${c.symbol}`}
                              className="font-medium text-sm text-foreground hover:text-primary transition-colors flex items-center gap-1"
                            >
                              <span>{c.name}</span>
                              <span className="text-xs text-muted-foreground font-mono">
                                ({c.symbol})
                              </span>
                              <ArrowUpRight className="h-3 w-3 opacity-60" />
                            </Link>
                            <span className="text-[10px] text-muted-foreground block mt-0.5">
                              來源：{c.source_name}
                            </span>
                          </div>

                          {c.quant_score !== null && (
                            <span className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-primary/10 text-primary font-semibold">
                              QS {c.quant_score.toFixed(1)}
                            </span>
                          )}
                        </div>

                        <div className="flex items-center gap-2 mt-2 text-xs">
                          <span className="font-mono">{c.close ? c.close.toFixed(2) : '-'}</span>
                          <span
                            className={cn(
                              'font-medium',
                              (c.change_pct ?? 0) >= 0 ? 'text-rose-500' : 'text-emerald-500',
                            )}
                          >
                            {(c.change_pct ?? 0) >= 0 ? '+' : ''}
                            {c.change_pct?.toFixed(2) ?? '-'}%
                          </span>
                        </div>

                        {c.match_reasons.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-2">
                            {c.match_reasons.map((r, i) => (
                              <span
                                key={i}
                                className="text-[10px] bg-background px-1.5 py-0.5 rounded border border-border/40 text-muted-foreground"
                              >
                                {r}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>

                      {/* 動作按鈕：只支援查看個股與加入自選，絕不自動買入 */}
                      <div className="mt-3 pt-2 border-t border-border/40 flex items-center justify-between text-xs">
                        <Link
                          to={`/stocks/${c.symbol}`}
                          className="text-primary hover:underline text-[11px] font-medium"
                        >
                          查看個股分析
                        </Link>

                        <button
                          onClick={() =>
                            toggleWatchlistMutation.mutate({
                              symbol: c.symbol,
                              action: isWatchlisted ? 'remove' : 'add',
                            })
                          }
                          disabled={toggleWatchlistMutation.isPending}
                          className={cn(
                            'inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded transition-colors',
                            isWatchlisted
                              ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400 font-medium'
                              : 'bg-muted hover:bg-muted/80 text-muted-foreground',
                          )}
                        >
                          <Star className="h-3 w-3" />
                          <span>{isWatchlisted ? '已在自選' : '加入自選'}</span>
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* 4. 重大事件與新聞摘要 */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-card border border-border/60 rounded-xl p-4 space-y-3">
              <h3 className="font-bold text-sm text-foreground flex items-center gap-1.5">
                <ShieldAlert className="h-4 w-4 text-rose-500" />
                官方重大事件與處置/注意警示
              </h3>
              {brief.events.risk_events.length === 0 && brief.events.attention_events.length === 0 ? (
                <p className="text-xs text-muted-foreground py-4 text-center">今日無重大風險處置或事件</p>
              ) : (
                <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
                  {brief.events.risk_events.map((ev, i) => (
                    <div
                      key={i}
                      className="text-xs p-2 rounded bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 flex items-start gap-1.5"
                    >
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                      <div>
                        <span className="font-semibold">
                          {ev.symbol} {ev.title}
                        </span>
                        <p className="text-[11px] mt-0.5 opacity-90">{ev.description}</p>
                      </div>
                    </div>
                  ))}
                  {brief.events.attention_events.map((ev, i) => (
                    <div
                      key={i}
                      className="text-xs p-2 rounded bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 flex items-start gap-1.5"
                    >
                      <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                      <div>
                        <span className="font-semibold">
                          {ev.symbol} {ev.title}
                        </span>
                        <p className="text-[11px] mt-0.5 opacity-90">{ev.description}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="bg-card border border-border/60 rounded-xl p-4 space-y-3">
              <h3 className="font-bold text-sm text-foreground flex items-center gap-1.5">
                <FileText className="h-4 w-4 text-primary" />
                重要台股個股新聞
              </h3>
              {brief.news.items.length === 0 ? (
                <p className="text-xs text-muted-foreground py-4 text-center">今日尚無相關個股新聞</p>
              ) : (
                <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
                  {brief.news.items.map((news, i) => (
                    <div key={i} className="text-xs p-2 rounded bg-muted/30 border border-border/40">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-foreground line-clamp-1">{news.title}</span>
                        <span className="text-[10px] text-muted-foreground shrink-0">{news.source}</span>
                      </div>
                      <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">
                        {news.summary}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 歷史日報 Modal */}
      {showHistoryModal && (
        <div className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-card border border-border rounded-xl shadow-lg max-w-md w-full p-5 space-y-4">
            <div className="flex items-center justify-between border-b border-border/60 pb-3">
              <h3 className="font-bold text-base flex items-center gap-2">
                <History className="h-4 w-4 text-primary" />
                已保存的每日摘要歷史
              </h3>
              <button
                onClick={() => setShowHistoryModal(false)}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                關閉
              </button>
            </div>

            {!historyQuery.data || historyQuery.data.length === 0 ? (
              <p className="text-xs text-muted-foreground py-6 text-center">暫無已保存的日報歷史紀錄</p>
            ) : (
              <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                {historyQuery.data.map((h) => (
                  <div
                    key={h.brief_id}
                    onClick={() => {
                      setSelectedHistoryId(h.brief_id)
                      setShowHistoryModal(false)
                    }}
                    className={cn(
                      'p-3 rounded-lg border text-xs cursor-pointer transition-colors flex items-center justify-between',
                      selectedHistoryId === h.brief_id
                        ? 'border-primary bg-primary/10'
                        : 'border-border/60 hover:bg-muted/40',
                    )}
                  >
                    <div>
                      <div className="font-medium text-foreground">{h.brief_date} 每日摘要</div>
                      <div className="text-[10px] text-muted-foreground mt-0.5">
                        大盤 {h.structured_brief.market.taiex_close?.toFixed(1) || '-'} • 情緒{' '}
                        {h.structured_brief.market.sentiment_label}
                      </div>
                    </div>
                    {h.ai_summary && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/20 text-primary font-medium">
                        含 AI 解讀
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
