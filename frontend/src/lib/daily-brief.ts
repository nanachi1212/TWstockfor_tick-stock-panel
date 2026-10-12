/** A saved legacy brief may have a TAIEX close without any source date. */
export function taiexFreshnessNote(market: {
  taiex_close?: number | null
  taiex_as_of?: string | null
  taiex_stale?: boolean | null
}): string | null {
  if (market.taiex_close == null || market.taiex_stale === false) return null
  return market.taiex_stale === true && market.taiex_as_of
    ? `資料日為 ${market.taiex_as_of}，不是報告交易日的指數`
    : '資料日期不明，不能視為報告日指數'
}
