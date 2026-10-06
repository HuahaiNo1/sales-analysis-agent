export type MetricKey = 'sales_amount' | 'order_count' | 'units_sold' | 'avg_order_value' | 'gross_profit'
export type ChartType = 'line' | 'bar' | 'waterfall' | 'table'
export type Decimal = string | number | null
export interface User { id?: string; username: string; display_name?: string; role?: string; scope_label?: string; allowed_store_ids?: number[] }
export interface Metric { id: MetricKey; label: string; definition?: string; unit?: string }
export interface Period { start: string; end: string }
export interface Budget { spent_rmb?: number | string; exposure_rmb?: number | string; reserved_rmb?: number | string; cap_rmb?: number | string; remaining_rmb?: number | string; calls_reserved?: number; spent_usd?: number | string; limit_usd?: number | string; remaining_usd?: number | string; estimated_cost_usd?: number | string }
export interface Catalog {
  metrics?: Metric[]; dimensions?: Array<{ id: string; label: string }>; stores?: Array<{ id?: number | string; key?: number | string; name?: string; label?: string }>;
  date_coverage?: { start: string; end: string }; coverage?: { start: string; end: string }; verified?: boolean; model_mode?: string; mode?: string; budget?: Budget;
  dataset_version?: string; dataset?: { version?: string; source?: string; order_count?: number; row_count?: number; start?: string; end?: string; simulated?: boolean };
  source?: string; orders?: number; sales_lines?: number; scope_label?: string; example_queries?: string[]; [key: string]: unknown
}
export interface Column { key: string; label: string; kind?: string }
export interface QuerySpec { metrics?: MetricKey[]; period?: Period; comparison?: Period | { period?: Period }; group_by?: string[]; analysis?: string; chart?: string; limit?: number; filters?: unknown; [key: string]: unknown }
export interface AnalysisResult {
  id: string; result_id?: string; query?: QuerySpec; period?: Period; comparison?: Period;
  metrics?: MetricKey[]; columns: Column[]; rows: Record<string, Decimal>[];
  totals: Partial<Record<MetricKey, Decimal>>; comparison_totals?: Partial<Record<MetricKey, Decimal>>;
  deltas?: Partial<Record<MetricKey, Decimal>>; change_pct?: Partial<Record<MetricKey, Decimal>>;
  chart?: { type: string; x?: string; y?: string | string[]; series?: unknown; waterfall?: Array<{ name: string; value: Decimal; kind: 'total' | 'delta' }> };
  observations?: string[]; metadata: { dataset_version?: string; currency?: string; scope_label?: string; source?: string; simulated?: boolean; row_count?: number; generated_at?: string; [key: string]: unknown };
  truncated?: boolean; no_data?: boolean; warnings?: string[]; budget?: Budget
}
export interface RunPresentation { chart_type: ChartType; reused_result_id: string }
export interface Run { presentation?: RunPresentation | null; id?: string; run_id: string; status: string; progress_stage?: string; message?: string; error_code?: string; result_id?: string; result?: AnalysisResult; state_version?: number; budget?: Budget; clarification?: string; [key: string]: unknown }
export interface ConversationMessage { role: string; content: string; run_id?: string; result_id?: string; created_at?: string; status?: string }
export interface Conversation { id: string; conversation_id?: string; state_version: number; messages?: ConversationMessage[]; history?: ConversationMessage[]; latest_run_id?: string; current_run_id?: string; last_result_id?: string; [key: string]: unknown }
export interface Dashboard { totals?: Partial<Record<MetricKey, Decimal>>; metrics?: Partial<Record<MetricKey, Decimal>>; period?: Period; monthly?: Record<string, Decimal>[]; trend?: AnalysisResult | Record<string, Decimal>[]; categories?: AnalysisResult | Record<string, Decimal>[]; result?: AnalysisResult; metadata?: AnalysisResult['metadata']; [key: string]: unknown }
