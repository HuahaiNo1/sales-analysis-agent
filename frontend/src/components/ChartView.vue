<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use, type ECharts } from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { AriaComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
use([BarChart, LineChart, AriaComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
const props = withDefaults(defineProps<{ option: EChartsOption; label?: string; height?: string }>(), { label: '销售分析图表。下方数据表提供完整数值。', height: '310px' })
const container = ref<HTMLElement>()
let chart: ECharts | undefined
let observer: ResizeObserver | undefined
onMounted(() => {
  if (!container.value) return
  chart = init(container.value, undefined, { renderer: 'canvas' })
  chart.setOption(props.option, true)
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(container.value)
})
watch(() => props.option, async option => { await nextTick(); chart?.setOption(option, true) }, { deep: true })
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose() })
</script>
<template><div ref="container" class="echart" :style="{ height }" role="img" :aria-label="label"></div></template>
