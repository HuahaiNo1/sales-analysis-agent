import type { Decimal, MetricKey, Period } from '../types'
export const METRICS: Array<{ id: MetricKey; label: string; short: string; unit: string; definition: string }> = [
  { id: 'sales_amount', label: '折扣后销售额', short: '销售额', unit: 'USD', definition: 'SUM(Quantity × NetPrice)。按订单日期计算，使用折扣后单位售价，不重复减折扣。' },
  { id: 'order_count', label: '订单数', short: '订单数', unit: '单', definition: 'COUNT(DISTINCT OrderKey)。在当前授权范围内对订单去重；按商品或类别分组的订单数不能直接相加。' },
  { id: 'units_sold', label: '销售数量', short: '销量', unit: '件', definition: 'SUM(Quantity)。原始数量单位合计；不同品类的件数不代表同等重量或体积。' },
  { id: 'avg_order_value', label: '平均订单金额', short: '平均订单金额', unit: 'USD', definition: '折扣后销售额 ÷ 去重订单数。分母为 0 时不适用；禁止在商品或类别分组/筛选下解释为完整订单客单价。' },
  { id: 'gross_profit', label: '商品毛利', short: '商品毛利', unit: 'USD', definition: 'SUM(Quantity × (NetPrice − UnitCost))。不包含税费、物流、营销和经营费用，不是企业净利润。' },
]
export const METRIC_LABELS = Object.fromEntries(METRICS.map(item => [item.id, item.label])) as Record<MetricKey, string>
export function numberValue(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}
export function isMoney(key: string): boolean { return ['sales_amount', 'avg_order_value', 'gross_profit'].some(metric => key.endsWith(metric)) }
export function formatValue(value: Decimal | undefined, key = ''): string {
  const number = numberValue(value)
  if (number === null) return '—'
  if (key.startsWith('change_pct') || key.includes('percent') || key.endsWith('_pct')) return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
  const currency = isMoney(key)
  return new Intl.NumberFormat('en-US', { ...(currency ? { style: 'currency', currency: 'USD' } : {}), minimumFractionDigits: currency ? 2 : 0, maximumFractionDigits: currency ? 2 : 0 }).format(number)
}
export function compactValue(value: Decimal | undefined, key: string): string {
  const number = numberValue(value)
  if (number === null) return '—'
  if (Math.abs(number) < 1_000_000) return formatValue(value, key)
  return `${isMoney(key) ? '$' : ''}${(number / 1_000_000).toFixed(2)}M`
}
export function periodLabel(period?: Period): string {
  if (!period) return '查询期间'
  const end = new Date(`${period.end}T00:00:00Z`)
  end.setUTCDate(end.getUTCDate() - 1)
  return `${period.start} 至 ${end.toISOString().slice(0, 10)}`
}
export function exclusiveEnd(inclusiveDate: string): string {
  const value = new Date(`${inclusiveDate}T00:00:00Z`)
  value.setUTCDate(value.getUTCDate() + 1)
  return value.toISOString().slice(0, 10)
}
export function statusLabel(status?: string): string {
  return ({ queued: '排队中', querying: '查询中', analyzing: '分析中', running: '分析中', succeeded: '已完成', needs_clarification: '等待补充', unsupported: '超出分析范围', no_data: '无匹配数据', failed: '未完成', cancelled: '已停止', cancelling: '正在停止', expired: '已过期' } as Record<string, string>)[status || ''] || status || ''
}
export const TERMINAL_STATUSES = new Set(['succeeded', 'needs_clarification', 'unsupported', 'no_data', 'failed', 'cancelled', 'expired'])
