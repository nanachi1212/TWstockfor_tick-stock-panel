// Phase 8B-5.8: ActiveJobCard 元件本體與 LogViewer 隨已刪除的 Data.tsx(A 股
// 資料管理頁)一併移除(僅該頁使用)。STAGE_LABELS 常數仍被 Dashboard.tsx
// 用於顯示行情同步階段文案,予以保留。

export const STAGE_LABELS: Record<string, string> = {
  init: '初始化',
  resolve_universe: '解析标的池',
  sync_instruments: '同步个股维表',
  sync_daily: '同步日 K',
  sync_adj: '同步除权因子',
  compute_enriched: '计算技术指标',
  sync_minute: '同步分钟 K',
  extend_history: '扩展日K历史',
  extend_minute: '扩展分钟K历史',
  rebuild_enriched: '全量计算',
  refresh_views: '刷新视图',
  done: '完成',
}
