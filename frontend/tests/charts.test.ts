import { describe, expect, it } from 'vitest'
import type { AnalysisResult } from '../src/types'
import { chartOption, dimensionKeys, isWaterfallAllowed, preferredChart } from '../src/lib/charts'
const result: AnalysisResult = { id: 'test-only-unit-fixture', columns: [{ key: 'category', label: '类别', kind: 'string' }, { key: 'sales_amount', label: '销售额', kind: 'currency' }, { key: 'delta_sales_amount', label: '差额', kind: 'currency' }], rows: [{ category: 'A', sales_amount: '120', delta_sales_amount: '20' }, { category: 'B', sales_amount: '40', delta_sales_amount: '-10' }], metrics: ['sales_amount'], totals: { sales_amount: '160' }, comparison_totals: { sales_amount: '150' }, query: { analysis: 'contribution' }, metadata: { simulated: true } }
describe('bounded chart rendering', () => {
  it('offers waterfall only for additive contribution queries', () => { expect(isWaterfallAllowed(result)).toBe(true); expect(isWaterfallAllowed({ ...result, query: { analysis: 'summary' } })).toBe(false); expect(isWaterfallAllowed({ ...result, metrics: ['order_count'] })).toBe(false) })
  it('ignores numeric contribution fields as dimensions', () => { expect(dimensionKeys(result)).toEqual(['category']) })
  it('uses rich-text tooltip rather than HTML generated from dataset values', () => { expect(chartOption(result, 'bar').tooltip).toMatchObject({ renderMode: 'richText' }) })
  it('waterfall starts at comparison total and ends at current total', () => { const options = chartOption(result, 'waterfall'); const series = options.series as Array<{ data: unknown[] }>; expect(series[0]!.data).toEqual([0, 150, 160, 0]); expect(series[1]!.data).toMatchObject([{ value: 150 }, { value: 20 }, { value: 10 }, { value: 160 }]) })
  it('chooses timeline based on actual dimensions', () => { expect(preferredChart({ ...result, columns: [{ key: 'month', label: '月份' }], metrics: ['sales_amount'] })).toBe('line') })
})
