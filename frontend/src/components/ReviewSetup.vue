<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowRight, ShieldCheck } from '@lucide/vue'
import type { Catalog } from '../types'
import type { ReviewMetric, ReviewRequest } from '../types/reviews'
import { exclusiveEnd, METRICS } from '../lib/format'
import { reviewValidation } from '../lib/reviews'

const props = defineProps<{ catalog: Catalog; scopeLabel: string; busy: boolean }>()
const emit = defineEmits<{ start: [request: ReviewRequest] }>()
const metric = ref<ReviewMetric>('sales_amount')
const currentStart = ref('2025-09-01')
const currentEnd = ref('2025-09-30')
const comparisonStart = ref('2025-08-01')
const comparisonEnd = ref('2025-08-31')
const store = ref('')
const drillDown = ref(true)
const error = ref('')
const metrics = METRICS.filter(item => ['sales_amount', 'units_sold', 'gross_profit'].includes(item.id))
const stores = computed(() => (props.catalog.stores || []).filter(item => item.key !== undefined || item.id !== undefined))

function preset(quarter: boolean) {
  if (props.busy) return
  currentStart.value = quarter ? '2025-07-01' : '2025-09-01'
  currentEnd.value = '2025-09-30'
  comparisonStart.value = quarter ? '2025-04-01' : '2025-08-01'
  comparisonEnd.value = quarter ? '2025-06-30' : '2025-08-31'
  error.value = ''
}
function submit() {
  if (props.busy) return
  if (![currentStart.value, currentEnd.value, comparisonStart.value, comparisonEnd.value].every(Boolean)) {
    error.value = '请填写本期与对比期的起止日期'; return
  }
  const request: ReviewRequest = {
    period: { start: currentStart.value, end: exclusiveEnd(currentEnd.value) },
    comparison: { start: comparisonStart.value, end: exclusiveEnd(comparisonEnd.value) },
    metric: metric.value,
    filters: store.value ? [{ dimension: 'store', op: 'eq', values: [store.value] }] : [],
    drill_down: drillDown.value,
  }
  error.value = reviewValidation(request)
  if (!error.value) emit('start', request)
}
</script>

<template>
  <form class="panel review-setup" aria-label="销售复盘设置" @submit.prevent="submit">
    <div class="panel-heading"><div><h2>先明确这次要复盘什么</h2><p>本期和对比期都按订单日期计算，结束日期包含当日</p></div><span class="subtle-tag">固定分析路径</span></div>
    <fieldset :disabled="busy" class="review-fields">
      <legend class="sr-only">期间、指标与门店范围</legend>
      <div class="review-presets" role="group" aria-label="复盘期间示例"><span>快速填入</span><button type="button" class="button button-outline button-small" @click="preset(false)">2025 年 9 月 vs 8 月</button><button type="button" class="button button-outline button-small" @click="preset(true)">2025 年 Q3 vs Q2</button></div>
      <div class="review-period-grid">
        <div class="review-period"><h3>本期</h3><div class="review-date-pair"><label>本期开始<input v-model="currentStart" type="date" min="2023-01-01" max="2025-12-31" required /></label><label>本期结束<input v-model="currentEnd" type="date" min="2023-01-01" max="2025-12-31" required /></label></div></div>
        <div class="review-period"><h3>对比期</h3><div class="review-date-pair"><label>对比期开始<input v-model="comparisonStart" type="date" min="2023-01-01" max="2025-12-31" required /></label><label>对比期结束<input v-model="comparisonEnd" type="date" min="2023-01-01" max="2025-12-31" required /></label></div></div>
      </div>
      <div class="review-select-grid"><label>主要复盘指标<select v-model="metric"><option v-for="item in metrics" :key="item.id" :value="item.id">{{ item.label }}</option></select><small>仅使用可加总指标做变化贡献拆解</small></label><label>门店范围<select v-model="store"><option value="">当前全部授权门店</option><option v-for="item in stores" :key="String(item.key ?? item.id)" :value="String(item.key ?? item.id)">{{ item.label || item.name || `门店 ${item.key ?? item.id}` }}</option></select><small><ShieldCheck :size="12" />{{ scopeLabel }}，服务端再次校验</small></label></div>
      <label class="review-checkbox"><input v-model="drillDown" type="checkbox" /><span>允许一次重点类别的商品下钻<small>只沿有数据证据的重点继续；最多一层，无法确认时明确停止</small></span></label>
    </fieldset>
    <div v-if="error" class="alert alert-error" role="alert">{{ error }}</div>
    <div class="review-setup-footer"><p>输出会区分数据事实、待验证解释与行动建议<br />Contoso 为模拟数据，贡献拆解不等于业务因果</p><button class="button button-primary" type="submit" :disabled="busy" :aria-busy="busy">{{ busy ? '复盘处理中' : '开始销售复盘' }}<ArrowRight :size="16" /></button></div>
  </form>
</template>
