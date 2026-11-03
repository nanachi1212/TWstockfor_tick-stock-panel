import { describe, expect, it } from 'vitest'
import { dataZoomFor } from './EChartsCandlestick'

describe('dataZoomFor', () => {
  it('觸控: 單指不平移也不擋頁面捲動, 雙指仍可縮放, 另有 slider 平移同一組軸', () => {
    const [inside, slider, ...rest] = dataZoomFor(true, [0, 1])
    expect(inside).toMatchObject({ type: 'inside', xAxisIndex: [0, 1], moveOnMouseMove: false, preventDefaultMouseMove: false })
    expect(inside).not.toHaveProperty('zoomLock', true)
    expect(slider).toMatchObject({ type: 'slider', xAxisIndex: [0, 1] })
    expect(rest).toEqual([])
  })

  it('桌面: 維持原本單一 inside, 滑鼠拖曳平移、滾輪縮放', () => {
    expect(dataZoomFor(false, [0])).toEqual([
      { type: 'inside', xAxisIndex: [0], start: 0, end: 100, moveOnMouseMove: true, zoomOnMouseWheel: true },
    ])
  })
})
