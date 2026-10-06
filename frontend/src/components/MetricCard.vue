<script setup lang="ts">
import { computed } from 'vue'
import { Banknote, ShoppingBag, Package, Receipt, TrendingUp } from '@lucide/vue'
import { compactValue, formatValue, METRICS, numberValue } from '../lib/format'
import type { Decimal, MetricKey } from '../types'
const props = defineProps<{ metric: MetricKey; value?: Decimal; delta?: Decimal; loading?: boolean }>()
const definition = computed(() => METRICS.find(item => item.id === props.metric)!)
const icon = computed(() => ({ sales_amount: Banknote, order_count: ShoppingBag, units_sold: Package, avg_order_value: Receipt, gross_profit: TrendingUp })[props.metric])
const deltaNumber = computed(() => numberValue(props.delta))
</script>
<template>
  <article class="metric-card" :class="{ 'metric-primary': metric === 'sales_amount' }">
    <div class="metric-heading"><span>{{ definition.label }}</span><component :is="icon" :size="17" stroke-width="1.7" aria-hidden="true" /></div>
    <div v-if="loading" class="skeleton metric-skeleton"></div>
    <div v-else class="metric-value" :title="formatValue(value, metric)">{{ compactValue(value, metric) }}</div>
    <div class="metric-footer"><span>{{ definition.unit }}<span v-if="value !== undefined && value !== null && Math.abs(Number(value)) >= 1e6"> · {{ formatValue(value, metric) }}</span></span><span v-if="deltaNumber !== null" class="metric-delta" :class="{ negative: deltaNumber < 0 }">{{ deltaNumber > 0 ? '+' : '' }}{{ deltaNumber.toFixed(1) }}%</span></div>
  </article>
</template>
