import type { ReviewRequest } from '../types/reviews'
import { METRIC_LABELS, periodLabel } from './format'

const DAY = 86400000
const COVERAGE_START = '2023-01-01'
const COVERAGE_END = '2026-01-01'

export function reviewValidation(request: ReviewRequest): string {
  for (const [label, period] of [['本期', request.period], ['对比期', request.comparison]] as const) {
    const start = Date.parse(`${period.start}T00:00:00Z`)
    const end = Date.parse(`${period.end}T00:00:00Z`)
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return `${label}请选择有效的起止日期，开始日期不能晚于结束日期`
    if (period.start < COVERAGE_START || period.end > COVERAGE_END) return `${label}必须位于 Contoso 数据覆盖的 2023–2025 年`
    if ((end - start) / DAY > 366) return `${label}最多覆盖 366 天，请缩小日期范围`
  }
  if (request.period.start === request.comparison.start && request.period.end === request.comparison.end) return '本期和对比期相同，请选择两个不同的期间'
  return ''
}

export function reviewMessage(request: ReviewRequest): string {
  return `销售复盘：${METRIC_LABELS[request.metric]}；本期 ${periodLabel(request.period)}；对比期 ${periodLabel(request.comparison)}；${request.filters.length ? '所选门店' : '当前全部授权门店'}。`
}

export function readableDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

export function reviewStopLabel(reason: string): string {
  return ({
    no_data: '本期和对比期没有可分析的数据，停止追加拆解',
    no_change: '整体指标没有变化，本次未继续拆解贡献',
    drill_disabled: '已完成整体、类别和门店核对；本次未启用进一步下钻',
    already_product_scoped: '范围已限定到类别或商品，为避免重复收窄，不再追加下钻',
    no_drill_candidate: '没有足够明确的重点类别，不追加商品下钻',
    ambiguous_drill_category: '重点类别无法唯一确认，停止下钻而不猜测筛选范围',
    query_limit_reached: '已完成一次重点类别的商品下钻，并达到 4 次查询上限',
  } as Record<string, string>)[reason] || reason
}
