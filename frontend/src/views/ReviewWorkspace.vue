<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, FileText, LoaderCircle, Square } from '@lucide/vue'
import type { Catalog, User } from '../types'
import type { CreateReportRequest, ReportAnnotations, ReviewRequest } from '../types/reviews'
import { useSalesReview } from '../composables/useSalesReview'
import { ApiError } from '../lib/api'
import { reportsApi } from '../lib/reportsApi'
import { METRIC_LABELS, statusLabel, TERMINAL_STATUSES } from '../lib/format'
import ReviewSetup from '../components/ReviewSetup.vue'
import ReviewEvidence from '../components/ReviewEvidence.vue'
import SaveReportForm from '../components/SaveReportForm.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

const props = defineProps<{ user: User; catalog: Catalog; scopeLabel: string }>()
const emit = defineEmits<{ busy: [value: boolean]; openReport: [id: string] }>()
const { run, restoring, submitting, cancelling, busy, error, notice, start, cancel, restore } = useSalesReview(props.user.username)
const showSave = ref(false)
const saveBusy = ref(false)
const saveError = ref('')
const savedReport = ref('')
const queuedRequest = ref<ReviewRequest | null>(null)
const allBusy = computed(() => busy.value || saveBusy.value)
const ready = computed(() => Boolean(run.value?.review && ['succeeded', 'no_data'].includes(run.value.status)))
const phases = [
  { id: 'review_overall', label: '整体比较' }, { id: 'review_category', label: '类别贡献' },
  { id: 'review_store', label: '门店贡献' }, { id: 'review_drill', label: '重点下钻（可跳过）' }, { id: 'analyzing', label: '核对与归纳' },
]
const phaseIndex = computed(() => phases.findIndex(item => item.id === run.value?.progress_stage))
const progressText = computed(() => restoring.value ? '正在恢复上次复盘' : submitting.value && !run.value ? '正在提交复盘范围' : phases.find(item => item.id === run.value?.progress_stage)?.label || statusLabel(run.value?.status || 'queued'))
const defaultTitle = computed(() => run.value?.review ? `${run.value.review.request.period.start} 销售复盘 · ${METRIC_LABELS[run.value.review.request.metric]}` : '销售复盘报告')
let pendingSave: CreateReportRequest | undefined
let alive = true

watch(allBusy, value => emit('busy', value), { immediate: true })
watch(() => run.value?.run_id, () => {
  showSave.value = false; saveError.value = ''; pendingSave = undefined; savedReport.value = ''
  try { savedReport.value = localStorage.getItem(`contoso:${props.user.username}:saved-review:${run.value?.run_id}`) || '' } catch { /* Optional convenience only. */ }
})
function requestStart(request: ReviewRequest) {
  if (allBusy.value) return
  if (ready.value && !savedReport.value) { queuedRequest.value = request; return }
  void start(request)
}
function confirmStart() {
  const request = queuedRequest.value; queuedRequest.value = null
  if (request && !allBusy.value) void start(request)
}
async function save(annotations: ReportAnnotations) {
  if (saveBusy.value || !ready.value || !run.value) return
  saveBusy.value = true; saveError.value = ''
  const runId = run.value.run_id
  const body = { run_id: runId, ...annotations }
  const key = `contoso:${props.user.username}:pending-report:${runId}`
  if (!pendingSave || JSON.stringify({ ...pendingSave, client_request_id: undefined }) !== JSON.stringify(body)) {
    try {
      const stored = JSON.parse(sessionStorage.getItem(key) || 'null') as CreateReportRequest | null
      if (stored && JSON.stringify({ ...stored, client_request_id: undefined }) === JSON.stringify(body)) pendingSave = stored
    } catch { /* Use a fresh id when storage is unavailable or invalid. */ }
    if (!pendingSave || JSON.stringify({ ...pendingSave, client_request_id: undefined }) !== JSON.stringify(body)) pendingSave = { ...body, client_request_id: crypto.randomUUID() }
    try { sessionStorage.setItem(key, JSON.stringify(pendingSave)) } catch { /* In-memory retry still preserves idempotency. */ }
  }
  try {
    const report = await reportsApi.create(pendingSave)
    if (!alive) return
    savedReport.value = report.id; showSave.value = false
    try { localStorage.setItem(`contoso:${props.user.username}:saved-review:${runId}`, report.id); sessionStorage.removeItem(key) } catch { /* The saved report remains in the server report shelf. */ }
    emit('openReport', report.id)
  } catch (cause) {
    if (!alive) return
    if (cause instanceof ApiError && [401, 403, 404, 410].includes(cause.status)) {
      run.value = null
      error.value = '来源证据已过期或当前权限无法保存，请重新登录或按当前范围重新分析。已保存的报告仍可在报告列表查看。'
    } else saveError.value = cause instanceof Error ? cause.message : '保存未确认，请重试'
  } finally { if (alive) saveBusy.value = false }
}
onMounted(() => void restore())
// The owning workspace is removed on logout. Never publish a late save into another account.
onBeforeUnmount(() => { alive = false; emit('busy', false) })
</script>

<template>
  <div class="review-workspace">
    <div class="page-heading"><div><div class="eyebrow">SALES REVIEW</div><h1>把变化，复盘成一份证据</h1><p>为销售运营与分析师准备的固定复盘路径</p></div><span class="scope-chip">{{ scopeLabel }}</span></div>
    <ol class="review-workflow" aria-label="复盘流程"><li><span>1</span>明确期间与范围</li><li><span>2</span>核对贡献与解释</li><li><span>3</span>保存、补充与导出</li></ol>
    <ReviewSetup :catalog="catalog" :scope-label="scopeLabel" :busy="allBusy" @start="requestStart" />
    <div v-if="error" class="alert alert-error review-alert" role="alert">{{ error }}</div>
    <div v-if="notice" class="alert alert-warning review-alert" role="status">{{ notice }}</div>
    <section v-if="busy" class="panel review-progress" role="status" aria-live="polite"><div class="review-progress-top"><div><LoaderCircle class="spin" :size="19" /><strong>{{ cancelling ? '正在请求停止，等待服务端确认' : progressText }}</strong></div><button v-if="run && !TERMINAL_STATUSES.has(run.status)" class="button button-outline button-small" :disabled="cancelling" @click="cancel"><Square :size="12" />{{ cancelling ? '停止中' : '停止复盘' }}</button></div><ol><li v-for="(phase, index) in phases" :key="phase.id" :class="{ current: index === phaseIndex, passed: index < phaseIndex }"><Check v-if="index < phaseIndex" :size="13" /><span v-else>{{ index + 1 }}</span>{{ phase.label }}</li></ol><p>最多 4 次受控查询，不会无限下钻。停止后不发布不完整结果。</p></section>
    <div v-else-if="run && !ready" class="panel empty-state review-terminal"><FileText :size="27" /><h2>{{ statusLabel(run.status) }}</h2><p>{{ run.message || '本次没有可发布的完整复盘，请调整设置后再试。' }}</p></div>
    <template v-if="ready && run?.review"><div class="review-result-heading"><div><h2>本次复盘已完成</h2><p>查看下面的证据后，保存为独立报告</p></div><button v-if="savedReport" class="button button-outline" @click="emit('openReport', savedReport)"><Check :size="15" />打开已保存报告</button><button v-else class="button button-primary" :disabled="showSave || allBusy" @click="showSave = true"><FileText :size="15" />保存报告</button></div><SaveReportForm v-if="showSave" :initial-title="defaultTitle" :busy="saveBusy" :error="saveError" @save="save" @cancel="showSave = false" /><ReviewEvidence :review="run.review" /></template>
    <ConfirmDialog v-if="queuedRequest" title="开始新的复盘？" confirm-label="开始新的复盘" cancel-label="返回查看本次结果" @confirm="confirmStart" @cancel="queuedRequest = null"><p>本次结果还没有保存为报告。新的分析会替换这里的预览；如需独立留存，请先返回保存报告。</p></ConfirmDialog>
  </div>
</template>
