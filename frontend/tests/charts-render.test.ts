// Actual ECharts SSR, not browser/canvas visual acceptance.
import { describe, expect, it } from 'vitest'
import { init, use } from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { AriaComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { chartOption } from '../src/lib/charts'
import type { AnalysisResult } from '../src/types'

use([BarChart, LineChart, AriaComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent, SVGRenderer])

describe('waterfall ECharts option integration', () => {
  it('renders totals and both signed contribution directions with the registered bar implementation', () => {
    const result: AnalysisResult = {
      id: 'test-only-waterfall-render', query: { analysis: 'contribution' }, metrics: ['sales_amount'],
      columns: [{ key: 'category', label: '类别', kind: 'dimension' }, { key: 'sales_amount', label: '销售额', kind: 'money' }],
      rows: [{ category: 'A', sales_amount: '120', delta_sales_amount: '20' }, { category: 'B', sales_amount: '40', delta_sales_amount: '-10' }],
      totals: { sales_amount: '160' }, comparison_totals: { sales_amount: '150' }, metadata: {},
    }
    const chart = init(null, undefined, { renderer: 'svg', ssr: true, width: 720, height: 330 })
    try {
      // Exercise replacement as well as initial creation, matching ChartView.setOption.
      chart.setOption({ ...chartOption(result, 'bar'), animation: false }, true)
      chart.setOption({ ...chartOption(result, 'waterfall'), animation: false }, true)
      const svg = chart.renderToSVGString()
      expect(svg).toContain('<svg')
      expect(svg).toContain('对比期')
      expect(svg).toContain('本期')
      expect(svg).toContain('#1c9a84')
      expect(svg).toContain('#de7a76')
      expect(svg).not.toMatch(/NaN|Infinity/)
    } finally { chart.dispose() }
  })
})
