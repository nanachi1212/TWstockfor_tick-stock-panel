/**
 * 儀表板「日行情是否為最新」的唯一判斷:個股快照自己的狀態與全市場日行情狀態
 * 都必須是 current。其中任何一個缺值或不是 current,都不可當成今日資料使用。
 */
export function isDailyCurrent(
  snapshotDailyStatus: string | null | undefined,
  marketDailyStatus: string | null | undefined,
): boolean {
  return snapshotDailyStatus === 'current' && marketDailyStatus === 'current'
}
