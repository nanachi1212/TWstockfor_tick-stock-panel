// The only ECharts entry point: registers just the chart types and components this app draws,
// so the bundle does not carry the whole library. Add to the list here when a chart needs more.
import * as echarts from 'echarts/core'
import { BarChart, CandlestickChart, LineChart, ScatterChart, TreemapChart } from 'echarts/charts'
import {
  AxisPointerComponent, DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent,
  TitleComponent, TooltipComponent, VisualMapComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart, CandlestickChart, LineChart, ScatterChart, TreemapChart,
  AxisPointerComponent, DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent,
  TitleComponent, TooltipComponent, VisualMapComponent,
  CanvasRenderer,
])

export { echarts }
export type { ECharts } from 'echarts/core'
