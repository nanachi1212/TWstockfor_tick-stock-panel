import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

// Phase 8B-5.3: A 股財務分析產品(狀態/利潤表/資產負債表/現金流量表/歷史股本/同步)
// 已整體下線。僅保留 metrics —— 它被 StockPanel/StockInfoBar 的信息條「財務」
// 字段組 (EPS/BPS/ROE/PE/PB 等) 複用, 與已下線的財務分析頁面無關。
export const FINANCIAL_QK = {
  metrics: (symbol?: string) => ['financials', 'metrics', symbol],
}

export function useFinancialMetrics(symbol?: string) {
  return useQuery({
    queryKey: FINANCIAL_QK.metrics(symbol),
    queryFn: () => api.financialMetrics(symbol),
    enabled: !!symbol,
    staleTime: 300_000,
  })
}
