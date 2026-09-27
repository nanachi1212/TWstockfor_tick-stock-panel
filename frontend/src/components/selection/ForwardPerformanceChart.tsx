import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { ForwardBatchTimeline } from '@/lib/api'

type Metric = 'average_return_pct' | 'hit_rate' | 'average_excess_return_pct'
type Cohort = 'top10' | 'full_batch'

const metricLabels: Record<Metric, string> = {
  average_return_pct: '平均報酬',
  hit_rate: '命中率',
  average_excess_return_pct: '平均超額報酬',
}

export function ForwardPerformanceChart({ timeline = [] }: { timeline?: ForwardBatchTimeline[] }) {
  const [metric, setMetric] = useState<Metric>('average_return_pct')
  const [cohort, setCohort] = useState<Cohort>('top10')
  const chartRef = useRef<HTMLDivElement>(null)
  const chartInstance = useRef<ECharts | null>(null)
  const rows = useMemo(() => timeline.map(batch => ({
    label: batch.source_date,
    value: batch[cohort]?.['1D']?.[metric] ?? null,
  })), [cohort, metric, timeline])

  useEffect(() => {
    if (!chartRef.current || !rows.length) return
    const chart = echarts.init(chartRef.current)
    chartInstance.current = chart
    const option: EChartsOption = {
      animation: false,
      grid: { left: 42, right: 18, top: 24, bottom: 32 },
      tooltip: {
        trigger: 'axis',
        valueFormatter: value => value == null ? '尚無樣本' : `${Number(value).toFixed(2)}%`,
      },
      xAxis: { type: 'category', data: rows.map(row => row.label) },
      yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
      series: [{
        type: 'line',
        connectNulls: false,
        smooth: false,
        data: rows.map(row => row.value),
        symbolSize: 7,
        itemStyle: { color: '#0ea5e9' },
        lineStyle: { color: '#0ea5e9', width: 2 },
      }],
    }
    chart.setOption(option)
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(chartRef.current)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartInstance.current = null
    }
  }, [rows])

  return (
    <section aria-label="前瞻績效時間趨勢" className="rounded-xl border border-border/60 bg-card p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-sm font-semibold">時間趨勢</h3>
          <p className="mt-1 text-xs text-muted-foreground">以每個正式批次為單位，1D 指標不跨批次重押資金。</p>
        </div>
        <div className="flex gap-2 text-xs">
          <select aria-label="趨勢指標" value={metric} onChange={event => setMetric(event.target.value as Metric)} className="rounded border border-border bg-background px-2 py-1.5">
            {Object.entries(metricLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <select aria-label="趨勢 cohort" value={cohort} onChange={event => setCohort(event.target.value as Cohort)} className="rounded border border-border bg-background px-2 py-1.5">
            <option value="top10">Top10</option>
            <option value="full_batch">整批</option>
          </select>
        </div>
      </div>
      {rows.length ? <div ref={chartRef} className="mt-3 h-64 w-full" /> : <p className="mt-6 text-center text-xs text-muted-foreground">尚無正式批次，累積資料後會顯示趨勢。</p>}
      {rows.length > 0 && <p className="mt-2 text-[11px] text-muted-foreground">目前圖表顯示 1D，統計明細另分 1D / 5D / 20D；空值代表尚未成熟或不可評估。</p>}
    </section>
  )
}
