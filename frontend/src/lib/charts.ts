import type { EChartsOption } from 'echarts'
import type { AnalysisResult, ChartType, MetricKey } from '../types'
import { formatValue, METRIC_LABELS, numberValue } from './format'
const PALETTE = ['#167d91', '#7a88c8', '#edb85f', '#3fa98d', '#c7788a']
const TIME_KEYS = new Set(['day', 'week', 'month', 'date', 'period', 'order_date'])
export function metricKeys(result: AnalysisResult): string[] {
  if (result.metrics?.length) return result.metrics
  if (result.query?.metrics?.length) return result.query.metrics
  return result.columns.filter(column => column.key in METRIC_LABELS).map(column => column.key)
}
export function dimensionKeys(result: AnalysisResult): string[] {
  const metrics = metricKeys(result)
  return result.columns.filter(column => !metrics.includes(column.key) && !column.key.startsWith('comparison_') && !column.key.startsWith('delta_') && !column.key.startsWith('change_pct_') && !column.key.startsWith('contribution_') && !['number','currency','integer','decimal','percent'].includes(column.kind || '')).map(column => column.key)
}
export function isLineAllowed(result: AnalysisResult): boolean { return dimensionKeys(result).some(key => TIME_KEYS.has(key)) }
export function isWaterfallAllowed(result: AnalysisResult): boolean {
  return result.query?.analysis === 'contribution' && Boolean(result.comparison_totals) && metricKeys(result).length === 1 && ['sales_amount', 'gross_profit', 'units_sold'].includes(metricKeys(result)[0] || '')
}
export function preferredChart(result: AnalysisResult): ChartType {
  const chart = result.chart?.type || result.query?.chart
  if (chart === 'waterfall' && isWaterfallAllowed(result)) return chart
  if (chart === 'table' || chart === 'line' || chart === 'bar') return chart
  return dimensionKeys(result).some(key => TIME_KEYS.has(key)) ? 'line' : 'bar'
}
export function chartOption(result: AnalysisResult, type: Exclude<ChartType, 'table'>): EChartsOption {
  const metrics = metricKeys(result)
  const metric = metrics[0] || 'sales_amount'
  const dimensions = dimensionKeys(result)
  const x = result.chart?.x && result.rows.some(row => result.chart!.x! in row) ? result.chart.x : dimensions[0]
  const labels = result.rows.map(row => dimensions.length > 1 ? dimensions.map(key => row[key]).join(' · ') : String(row[x || ''] ?? '合计'))
  const base: EChartsOption = {
    color: PALETTE, animationDuration: 450,
    textStyle: { fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif', color: '#647685' },
    aria: { enabled: true, decal: { show: false } },
    grid: { left: 12, right: 20, top: 42, bottom: labels.length > 10 ? 72 : 30, containLabel: true },
    legend: { top: 0, right: 0, icon: 'roundRect', itemWidth: 12, itemHeight: 4, textStyle: { color: '#647685' } },
    tooltip: { trigger: 'axis', renderMode: 'richText', backgroundColor: '#fff', borderColor: '#e5ebef', textStyle: { color: '#273c4d', fontSize: 12 } },
    xAxis: { type: 'category', data: labels, axisLine: { lineStyle: { color: '#e4ebef' } }, axisTick: { show: false }, axisLabel: { color: '#738390', margin: 15, hideOverlap: true, rotate: labels.some(label => label.length > 15) ? 18 : 0 } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#edf1f4', type: 'dashed' } }, axisLabel: { formatter: (value: number) => Math.abs(value) >= 1e6 ? `${(value / 1e6).toFixed(1)}M` : Math.abs(value) >= 1e3 ? `${(value / 1e3).toFixed(0)}k` : String(value) } },
    ...(labels.length > 10 ? { dataZoom: [{ type: 'slider', height: 15, bottom: 5, borderColor: 'transparent', backgroundColor: '#f4f7f9', fillerColor: '#167d911f', handleStyle: { color: '#167d91' } }, { type: 'inside' }] } : {}),
  }
  if (type === 'waterfall' && isWaterfallAllowed(result)) {
    const start = numberValue(result.comparison_totals?.[metric as MetricKey]) || 0
    const end = numberValue(result.totals[metric as MetricKey]) || 0
    const offsets: number[] = [0]
    const changes: Array<{ value: number; itemStyle: { color: string } }> = [{ value: start, itemStyle: { color: '#60788a' } }]
    let running = start
    result.rows.forEach(row => {
      const delta = numberValue(row[`delta_${metric}`]) || 0
      offsets.push(Math.min(running, running + delta))
      changes.push({ value: Math.abs(delta), itemStyle: { color: delta >= 0 ? '#1c9a84' : '#de7a76' } })
      running += delta
    })
    offsets.push(0)
    changes.push({ value: end, itemStyle: { color: '#167d91' } })
    return { ...base, xAxis: { ...(base.xAxis as object), data: ['对比期', ...labels, '本期'] }, legend: { show: false }, series: [
      { name: '累计基线', type: 'bar', stack: 'bridge', silent: true, itemStyle: { color: 'transparent' }, emphasis: { disabled: true }, data: offsets, tooltip: { show: false } },
      { name: METRIC_LABELS[metric as MetricKey] || metric, type: 'bar', stack: 'bridge', barMaxWidth: 42, data: changes, tooltip: { valueFormatter: (value, dataIndex) => {
        if (dataIndex === 0) return formatValue(start, metric)
        if (dataIndex === changes.length - 1) return formatValue(end, metric)
        return formatValue(result.rows[dataIndex - 1]?.[`delta_${metric}`] ?? String(value), metric)
      } } },
    ] }
  }
  const series: NonNullable<EChartsOption['series']> = metrics.flatMap((key, index) => {
    const name = METRIC_LABELS[key as MetricKey] || key
    const primary = { name, type, data: result.rows.map(row => numberValue(row[key])), smooth: type === 'line' ? 0.25 : undefined, symbol: 'circle', symbolSize: 5, showSymbol: false, barMaxWidth: 38, yAxisIndex: metrics.length > 1 ? index : 0, lineStyle: { width: 3 }, itemStyle: { borderRadius: type === 'bar' ? [4, 4, 0, 0] : 0 }, ...(type === 'line' && metrics.length === 1 ? { areaStyle: { color: '#167d910c' } } : {}), tooltip: { valueFormatter: (value: unknown) => formatValue(value as string | number | null, key) } }
    if (result.rows.some(row => `comparison_${key}` in row)) return [primary, { ...primary, name: `${name} · 对比期`, data: result.rows.map(row => numberValue(row[`comparison_${key}`])), lineStyle: { width: 2, type: 'dashed' as const }, areaStyle: undefined, itemStyle: { color: '#a1b5bf' } }]
    return [primary]
  }) as NonNullable<EChartsOption['series']>
  return { ...base, ...(metrics.length > 1 ? { yAxis: metrics.map((key, index) => ({ ...(base.yAxis as object), name: METRIC_LABELS[key as MetricKey] || key, position: index ? 'right' : 'left', splitLine: { show: !index, lineStyle: { color: '#edf1f4', type: 'dashed' } } })) } : {}), series }
}
