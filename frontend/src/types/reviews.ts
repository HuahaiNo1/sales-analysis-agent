import type { AnalysisResult, Period, Run } from '../types'

export type ReviewMetric = 'sales_amount' | 'units_sold' | 'gross_profit'
export interface ReviewFilter { dimension: 'product' | 'category' | 'store' | 'customer_country' | 'store_country'; op: 'eq' | 'in'; values: string[] }
export interface ReviewRequest {
  period: Period
  comparison: Period
  metric: ReviewMetric
  filters: ReviewFilter[]
  drill_down: boolean
}

export interface ReviewEvidence {
  evidence_id: string
  role: 'overall' | 'category' | 'store' | 'drill'
  title: string
  result: AnalysisResult
}
export interface ReviewPayload {
  template_id: 'sales_review_v1'
  template_version: '1'
  request: ReviewRequest
  evidence: ReviewEvidence[]
  findings: Array<{ text: string; evidence_ids: string[] }>
  flags: Array<{ rule_id: string; label: string; severity: 'info' | 'attention'; text: string; evidence_ids: string[] }>
  assumptions: string[]
  suggestions: string[]
  rule_config: { version: string; relative_change_threshold_pct: string; concentration_threshold_pct: string }
  query_count: number
  max_queries: 4
  stop_reason: string
}
export interface ReviewRun extends Run { kind?: 'query' | 'review'; review?: ReviewPayload | null }
export interface ReportSummary {
  id: string
  title: string
  kind: 'query' | 'review'
  revision: number
  created_at: string
  updated_at: string
  deleted_at: string | null
  source_run_id: string
  scope_label: string
}
export interface ReportFacts {
  schema_version: 'report_v1'
  kind: 'query' | 'review'
  generated_at: string
  dataset_version: string
  metric_version: string
  metric_definitions: Array<{ id: string; label: string; formula: string; unit: string; description: string }>
  scope: { allowed_store_ids: number[]; scope_label: string; scope_version: string }
  result: AnalysisResult
  review: ReviewPayload | null
}
export interface SalesReport extends ReportSummary { comment: string; suggestions: string; facts: ReportFacts }
export interface ReportList { items: ReportSummary[]; limit: number; offset: number }
export interface ReportAnnotations { title: string; comment: string; suggestions: string }
export interface CreateReportRequest extends ReportAnnotations { run_id: string; client_request_id: string }
