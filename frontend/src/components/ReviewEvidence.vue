<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import { AlertTriangle, Check, FileSearch, Info, Lightbulb } from '@lucide/vue'
import type { ReviewPayload } from '../types/reviews'
import { METRIC_LABELS, periodLabel } from '../lib/format'
import { reviewStopLabel } from '../lib/reviews'
import QueryResult from './QueryResult.vue'

const props = defineProps<{ review: ReviewPayload }>()
const selectedId = ref(props.review.evidence[0]?.evidence_id || '')
const evidencePanel = ref<HTMLElement>()
const headingPrefix = useId()
watch(() => props.review, () => { selectedId.value = props.review.evidence[0]?.evidence_id || '' })
const selected = computed(() => props.review.evidence.find(item => item.evidence_id === selectedId.value) || props.review.evidence[0])
const filterLabels: Record<string, string> = { store: '门店', product: '商品', category: '类别', customer_country: '客户国家', store_country: '门店国家' }
const scopeSummary = computed(() => props.review.request.filters.length ? props.review.request.filters.map(filter => `${filterLabels[filter.dimension] || filter.dimension}：${filter.values.join('、')}`).join('；') : '当前账号授权范围内的全部门店')
function showEvidence(id: string) {
  if (!props.review.evidence.some(item => item.evidence_id === id)) return
  selectedId.value = id
  void nextTick(() => { evidencePanel.value?.focus(); evidencePanel.value?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }) })
}
function evidenceTitle(id: string) { return props.review.evidence.find(item => item.evidence_id === id)?.title || '关联证据' }
</script>

<template>
  <div class="review-evidence">
    <div class="review-context"><span>{{ METRIC_LABELS[review.request.metric] }}</span><span>本期 {{ periodLabel(review.request.period) }}</span><span>对比期 {{ periodLabel(review.request.comparison) }}</span><span>复盘筛选 {{ scopeSummary }}</span></div>
    <section class="panel review-facts" :aria-labelledby="`${headingPrefix}-facts`"><div class="panel-heading"><div><span class="review-label fact-label"><Check :size="13" />数据事实</span><h2 :id="`${headingPrefix}-facts`">先看可复核的变化</h2><p>确定性计算，点击关联证据查看原始聚合口径</p></div></div><div class="review-text-body"><div v-for="(finding, index) in review.findings" :key="index" class="review-finding"><p>{{ finding.text }}</p><div class="evidence-links"><button v-for="id in finding.evidence_ids" :key="id" class="text-button" @click="showEvidence(id)"><FileSearch :size="13" />{{ evidenceTitle(id) }}</button></div></div><p v-if="!review.findings.length" class="muted">这个范围尚无可发布的数据发现，请查看停止说明与证据。</p></div></section>
    <section v-if="review.flags.length" class="panel review-flags" :aria-labelledby="`${headingPrefix}-flags`"><div class="panel-heading"><div><span class="review-label rule-label"><AlertTriangle :size="13" />规则提示</span><h2 :id="`${headingPrefix}-flags`">值得继续核实的信号</h2><p>阈值触发提示，不是异常成因或真实业务结论</p></div></div><div class="review-text-body"><article v-for="flag in review.flags" :key="flag.rule_id" class="review-flag" :class="flag.severity"><strong>{{ flag.label }}</strong><p>{{ flag.text }}</p><div class="evidence-links"><button v-for="id in flag.evidence_ids" :key="id" class="text-button" @click="showEvidence(id)">{{ evidenceTitle(id) }}</button></div></article><p class="field-hint">规则 {{ review.rule_config.version }} · 相对变化阈值 {{ review.rule_config.relative_change_threshold_pct }}% · 集中度阈值 {{ review.rule_config.concentration_threshold_pct }}%</p></div></section>
    <section ref="evidencePanel" class="review-evidence-panel" tabindex="-1" aria-label="冻结聚合证据"><div class="review-evidence-heading"><h2>证据明细</h2><span>{{ review.query_count }} / {{ review.max_queries }} 次受控查询</span></div><div class="review-evidence-tabs" role="group" aria-label="选择复盘证据"><button v-for="evidence in review.evidence" :key="evidence.evidence_id" :class="{ selected: selectedId === evidence.evidence_id }" :aria-pressed="selectedId === evidence.evidence_id" @click="selectedId = evidence.evidence_id">{{ evidence.title }}</button></div><QueryResult v-if="selected" :key="selected.evidence_id" :result="selected.result" :title="selected.title" :exportable="false" snapshot /><div v-else class="panel empty-state"><Info :size="25" /><h3>本次没有可展示的证据</h3></div></section>
    <div class="review-interpretation-grid"><section class="panel" :aria-labelledby="`${headingPrefix}-assumptions`"><div class="panel-heading"><div><span class="review-label assumption-label"><Info :size="13" />假设与局限</span><h2 :id="`${headingPrefix}-assumptions`">解释需要额外证据</h2></div></div><div class="review-text-body"><ul v-if="review.assumptions.length" class="review-prose-list"><li v-for="text in review.assumptions" :key="text">{{ text }}</li></ul><p v-else>未提供待验证解释，不能从金额贡献直接推断业务原因。</p></div></section><section class="panel" :aria-labelledby="`${headingPrefix}-proposals`"><div class="panel-heading"><div><span class="review-label proposal-label"><Lightbulb :size="13" />后续建议</span><h2 :id="`${headingPrefix}-proposals`">下一步可以核实什么</h2></div></div><div class="review-text-body"><ul v-if="review.suggestions.length" class="review-prose-list"><li v-for="text in review.suggestions" :key="text">{{ text }}</li></ul><p v-else>本次未生成后续建议。</p><p class="field-hint">建议仅供判断，不会自动创建任务或执行业务操作</p></div></section></div>
    <div class="review-stop-note" role="status"><Info :size="17" /><div><strong>本次分析止于这里</strong><p>{{ reviewStopLabel(review.stop_reason) }}</p><small>固定路径，最多一层下钻。Contoso 合成数据不代表真实市场，也不能证明业务因果。</small></div></div>
  </div>
</template>
