<script setup lang="ts">
import { computed, defineAsyncComponent, ref, useId, watch } from 'vue'
import { BarChart3, ChartNoAxesCombined, Download, Info, LineChart, Table2, ChevronDown, ChevronUp, CircleCheck, AlertTriangle } from '@lucide/vue'
import type { AnalysisResult, ChartType, Decimal } from '../types'
import { api } from '../lib/api'
import { chartOption, isLineAllowed, isWaterfallAllowed, metricKeys, preferredChart, waterfallUnavailableReason } from '../lib/charts'
import { formatValue, METRIC_LABELS, periodLabel } from '../lib/format'
const ChartView = defineAsyncComponent(() => import('./ChartView.vue'))
const props = withDefaults(defineProps<{ result: AnalysisResult; title?: string; compact?: boolean; exportable?: boolean; presentationChart?: ChartType | null }>(), { title: '分析结果', compact: false, exportable: true })
function selectedPresentation(): ChartType {
  const chart = props.presentationChart
  if (chart === 'table' || chart === 'bar' || (chart === 'line' && isLineAllowed(props.result)) || (chart === 'waterfall' && isWaterfallAllowed(props.result))) return chart
  return preferredChart(props.result)
}
const chartType = ref<ChartType>(selectedPresentation())
const showDetails = ref(false)
const downloadBusy = ref(false)
const error = ref('')
watch(() => [props.result.id, props.presentationChart] as const, () => { chartType.value = selectedPresentation(); error.value = ''; showDetails.value = false })
const metrics = computed(() => metricKeys(props.result))
const waterfallReason = computed(() => waterfallUnavailableReason(props.result))
const waterfallHintId = useId()
const option = computed(() => chartOption(props.result, chartType.value === 'table' ? 'bar' : chartType.value))
const period = computed(() => props.result.period || props.result.query?.period)
const tabs: Array<{ id: ChartType; label: string; icon: typeof BarChart3 }> = [{ id: 'line', label: '折线', icon: LineChart }, { id: 'bar', label: '柱状', icon: BarChart3 }, { id: 'waterfall', label: '瀑布', icon: ChartNoAxesCombined }, { id: 'table', label: '表格', icon: Table2 }]
function cellValue(value: Decimal | undefined, key: string, kind?: string): string {
  if (value === null || value === undefined) return '—'
  if (key in METRIC_LABELS || /^(comparison_|delta_|change_pct_|contribution_)/.test(key) || ['currency', 'number', 'integer', 'decimal', 'percent'].includes(kind || '')) return formatValue(value, key)
  return String(value)
}
async function download() {
  downloadBusy.value = true; error.value = ''
  try { await api.download(props.result.id || props.result.result_id!)} catch (err) { error.value = err instanceof Error ? err.message : '下载失败' } finally { downloadBusy.value = false }
}
</script>
<template>
  <section class="result-card panel">
    <div class="panel-heading result-heading"><div><div class="eyebrow" v-if="!compact">QUERY RESULT</div><h2>{{ title }}</h2><p>{{ periodLabel(period) }}<span v-if="result.metadata.currency"> · {{ result.metadata.currency }}</span></p></div><button v-if="exportable" class="button button-outline button-small" @click="download" :disabled="downloadBusy" :aria-busy="downloadBusy"><Download :size="14" />{{ downloadBusy ? '下载中' : '导出 CSV' }}</button></div>
    <div v-if="error" role="alert" class="alert alert-error">{{ error }}</div>
    <div v-if="result.truncated" class="alert alert-warning"><AlertTriangle :size="16" />{{ result.query?.analysis === 'contribution' ? '其余分组已合并为“其他”，贡献合计仍与总变化对账' : '当前为展示截取，不能把可见行当成全量数据' }}</div>
    <div v-if="!compact" class="result-summary"><div v-for="key in metrics" :key="key"><span>{{ METRIC_LABELS[key as keyof typeof METRIC_LABELS] || key }}</span><strong>{{ formatValue(result.totals[key as keyof typeof result.totals], key) }}</strong><small v-if="result.comparison_totals">对比期 {{ formatValue(result.comparison_totals[key as keyof typeof result.comparison_totals], key) }}<span v-if="result.deltas"> · 差额 {{ formatValue(result.deltas[key as keyof typeof result.deltas], key) }}</span></small></div></div>
    <div class="visualization-tools"><span class="muted">{{ result.rows.length }} 组数据</span><div class="segmented" role="group" aria-label="图表类型"><button v-for="tab in tabs" :key="tab.id" :class="{ selected: chartType === tab.id }" @click="chartType = tab.id" :disabled="(tab.id === 'waterfall' && Boolean(waterfallReason)) || (tab.id === 'line' && !isLineAllowed(result))" :title="tab.id === 'waterfall' && waterfallReason ? waterfallReason : tab.label" :aria-label="tab.label" :aria-describedby="tab.id === 'waterfall' && waterfallReason ? waterfallHintId : undefined" :aria-pressed="chartType === tab.id"><component :is="tab.icon" :size="14" /><span>{{ tab.label }}</span></button></div></div>
    <p v-if="waterfallReason" :id="waterfallHintId" class="inline-warning waterfall-hint"><Info :size="13" />{{ waterfallReason }}</p>
    <div v-if="result.no_data || !result.rows.length" class="empty-state"><Info :size="28" /><h3>这个范围没有匹配数据</h3><p>请调整日期或筛选条件。平均订单金额在无订单时不适用。</p></div>
    <ChartView v-else-if="chartType !== 'table'" :option="option" :height="compact ? '285px' : '330px'" />
    <div class="table-scroll" v-if="chartType === 'table' && !result.no_data && result.rows.length"><table><caption class="sr-only">授权范围内的查询结果数据</caption><thead><tr><th v-for="column in result.columns" :key="column.key" :class="{ numeric: column.key in METRIC_LABELS || /^(comparison_|delta_|change_pct_)/.test(column.key) }">{{ column.label }}</th></tr></thead><tbody><tr v-for="(row, index) in result.rows" :key="index"><td v-for="column in result.columns" :key="column.key" :class="{ numeric: column.key in METRIC_LABELS || /^(comparison_|delta_|change_pct_)/.test(column.key) }">{{ cellValue(row[column.key], column.key, column.kind) }}</td></tr></tbody></table></div>
    <div v-if="result.observations?.length && !compact" class="observations"><div class="section-label"><CircleCheck :size="16" />从数据中可以看到</div><p v-for="(observation, index) in result.observations" :key="index">{{ observation }}</p><small v-if="result.query?.analysis === 'contribution'">贡献表示算术变化拆解，不证明业务因果</small></div>
    <p v-for="warning in result.warnings" :key="warning" class="inline-warning"><Info :size="13" />{{ warning }}</p>
    <div class="result-foot"><span><span class="tiny-dot"></span>Contoso 模拟数据 · {{ result.metadata.scope_label || '当前授权范围' }}</span><button class="text-button" @click="showDetails = !showDetails" :aria-expanded="showDetails">口径与来源<component :is="showDetails ? ChevronUp : ChevronDown" :size="13" /></button></div>
    <div v-if="showDetails" class="details-grid"><div><span>数据版本</span><strong>{{ result.metadata.dataset_version || '未提供' }}</strong></div><div><span>结果 ID</span><strong>{{ exportable ? result.id : '概览汇总（未保存为查询结果）' }}</strong></div><div><span>数据来源</span><strong>{{ result.metadata.source || 'Contoso 合成零售订单' }}</strong></div><div><span>期间口径</span><strong>订单日期 · 左闭右开 [{{ period?.start }}, {{ period?.end }})</strong></div><div class="detail-full"><span>安全说明</span><strong>查询和 CSV 在服务端重新校验权限；图表切换复用同一结果，不改动计算口径。</strong></div></div>
  </section>
</template>
