import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

// Phase 8B-5.3: A 股财务分析产品(状态/利润表/资产负债表/现金流量表/历史股本/同步)
// 已整体下线。仅保留 metrics —— 它被 StockPanel/StockInfoBar 的信息条「财务」
// 字段组 (EPS/BPS/ROE/PE/PB 等) 复用, 与已下线的财务分析页面无关。
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
