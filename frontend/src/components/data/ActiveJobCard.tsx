// Phase 8B-5.8: ActiveJobCard 元件本體與 LogViewer 隨已刪除的 Data.tsx(A 股
// 資料管理頁)一併移除(僅該頁使用)。STAGE_LABELS 常數仍被 Dashboard.tsx
// 用於顯示行情同步階段文案,予以保留。

export const STAGE_LABELS: Record<string, string> = {
  init: '初始化',
  resolve_universe: '解析標的池',
  sync_instruments: '同步個股維表',
  sync_daily: '同步日 K',
  sync_adj: '同步除權因子',
  compute_enriched: '計算技術指標',
  sync_minute: '同步分鐘 K',
  extend_history: '擴展日K歷史',
  extend_minute: '擴展分鐘K歷史',
  rebuild_enriched: '全量計算',
  refresh_views: '刷新視圖',
  done: '完成',
}
