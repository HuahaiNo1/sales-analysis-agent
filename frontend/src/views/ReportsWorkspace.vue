<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowLeft, ChevronLeft, ChevronRight, Download, FileText, LoaderCircle, Pencil, RefreshCw, RotateCcw, Trash2 } from '@lucide/vue'
import type { ReportAnnotations, ReportSummary, SalesReport } from '../types/reviews'
import { ApiError } from '../lib/api'
import { reportsApi } from '../lib/reportsApi'
import { readableDate } from '../lib/reviews'
import { periodLabel } from '../lib/format'
import QueryResult from '../components/QueryResult.vue'
import ReviewEvidence from '../components/ReviewEvidence.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

const props = defineProps<{ selectedId: string; active: boolean }>()
const emit = defineEmits<{ select: [id: string]; newReview: []; busy: [value: boolean] }>()
const items = ref<ReportSummary[]>([])
const report = ref<SalesReport | null>(null)
const deleted = ref(false)
const offset = ref(0)
const loading = ref(false)
const error = ref('')
const actionError = ref('')
const notice = ref('')
const action = ref<'saving' | 'deleting' | 'restoring' | 'markdown' | 'html' | null>(null)
const editing = ref(false)
const draft = ref<ReportAnnotations>({ title: '', comment: '', suggestions: '' })
const confirmTrash = ref(false)
const confirmDiscard = ref(false)
const staleRevision = ref(false)
let pendingNavigation: (() => void) | undefined
let cancelNavigation: (() => void) | undefined
let controller: AbortController | undefined
let generation = 0
let alive = true
let listLoaded = false

const dirty = computed(() => editing.value && report.value && (draft.value.title !== report.value.title || draft.value.comment !== report.value.comment || draft.value.suggestions !== report.value.suggestions))
const mutationBusy = computed(() => action.value !== null)
watch(mutationBusy, value => emit('busy', value), { immediate: true })
function resetDraft() {
  if (report.value) draft.value = { title: report.value.title, comment: report.value.comment, suggestions: report.value.suggestions }
  editing.value = false; staleRevision.value = false
}
function requestNavigation(next: () => void, cancelled?: () => void) {
  if (mutationBusy.value) { cancelled?.(); return }
  if (dirty.value) { pendingNavigation = next; cancelNavigation = cancelled; confirmDiscard.value = true; return }
  resetDraft(); next()
}
function discard() {
  confirmDiscard.value = false; resetDraft()
  const next = pendingNavigation; pendingNavigation = undefined; cancelNavigation = undefined; next?.()
}
function keepEditing() {
  confirmDiscard.value = false; pendingNavigation = undefined
  const cancelled = cancelNavigation; cancelNavigation = undefined; cancelled?.()
}
defineExpose({ requestNavigation })

function accessMessage(cause: unknown): string {
  if (cause instanceof ApiError && [403, 404, 410].includes(cause.status)) return '报告不存在或当前账号已无权查看完整范围。为避免泄露，不展示任何报告数字；请按当前授权范围重新生成。'
  if (cause instanceof ApiError && cause.status === 401) return '登录已过期，请退出后重新登录，再打开报告'
  return cause instanceof Error ? cause.message : '无法读取报告，请重试'
}
async function load(force = false) {
  if (mutationBusy.value) return
  if (!force && (props.selectedId ? report.value?.id === props.selectedId : listLoaded)) return
  controller?.abort(); controller = new AbortController()
  const currentGeneration = ++generation
  loading.value = true; error.value = ''; actionError.value = ''; notice.value = ''
  const id = props.selectedId
  report.value = null; editing.value = false; staleRevision.value = false
  if (!id) items.value = []
  try {
    if (id) {
      const received = await reportsApi.get(id, controller.signal)
      if (currentGeneration !== generation) return
      report.value = received; resetDraft()
    } else {
      const received = await reportsApi.list(deleted.value, offset.value, controller.signal)
      if (currentGeneration !== generation) return
      items.value = received.items; offset.value = received.offset; listLoaded = true
    }
  } catch (cause) {
    if (currentGeneration !== generation || cause instanceof DOMException && cause.name === 'AbortError') return
    error.value = accessMessage(cause)
  } finally { if (currentGeneration === generation) loading.value = false }
}
watch(() => [props.active, props.selectedId] as const, ([active, id], previous) => {
  if (!active) return
  if (id !== previous?.[1]) { generation++; controller?.abort(); report.value = null; resetDraft(); if (id) listLoaded = false }
  void load(previous?.[0] === false)
}, { immediate: true })
function select(id: string) { requestNavigation(() => { actionError.value = ''; notice.value = ''; emit('select', id) }) }
function toggleTrash(value: boolean) {
  if (loading.value || mutationBusy.value || deleted.value === value) return
  deleted.value = value; offset.value = 0; listLoaded = false; void load(true)
}
function paginate(direction: number) {
  if (loading.value || mutationBusy.value) return
  offset.value = Math.max(0, offset.value + direction * 30); listLoaded = false; void load(true)
}
function refresh() { requestNavigation(() => void load(true)) }
function edit() { if (!report.value || mutationBusy.value || report.value.deleted_at) return; resetDraft(); editing.value = true; actionError.value = ''; notice.value = '' }
function handleActionError(cause: unknown) {
  if (cause instanceof ApiError && [401, 403, 404, 410].includes(cause.status)) {
    report.value = null; editing.value = false; error.value = accessMessage(cause); actionError.value = ''; return
  }
  if (cause instanceof ApiError && cause.status === 409) {
    staleRevision.value = true
    actionError.value = '报告已在其他页面更新或状态已改变。你的输入仍在这里，请先复制需要保留的文字，再重新读取最新报告。'
    return
  }
  actionError.value = cause instanceof Error ? cause.message : '操作未完成，请重试'
}
async function save() {
  if (!report.value || mutationBusy.value || staleRevision.value || !dirty.value) return
  if (!draft.value.title.trim()) { actionError.value = '报告标题不能为空'; return }
  action.value = 'saving'; actionError.value = ''
  try {
    const updated = await reportsApi.update(report.value.id, report.value.revision, { ...draft.value, title: draft.value.title.trim() })
    if (!alive) return
    report.value = updated; resetDraft(); listLoaded = false; notice.value = '标题与批注已保存，冻结数据事实未改变'
  } catch (cause) { if (alive) handleActionError(cause) }
  finally { if (alive) action.value = null }
}
async function transition(restore: boolean) {
  if (!report.value || mutationBusy.value) return
  const current = report.value
  confirmTrash.value = false; action.value = restore ? 'restoring' : 'deleting'; actionError.value = ''; notice.value = ''
  try {
    const updated = restore ? await reportsApi.restore(current.id, current.revision) : await reportsApi.trash(current.id, current.revision)
    if (!alive) return
    report.value = updated; resetDraft(); listLoaded = false
    notice.value = restore ? '报告已恢复，可以继续查看和导出' : '报告已移入回收站，可以恢复；未永久删除'
  } catch (cause) { if (alive) handleActionError(cause) }
  finally { if (alive) action.value = null }
}
async function download(format: 'markdown' | 'html') {
  if (!report.value || mutationBusy.value || report.value.deleted_at || editing.value) return
  action.value = format; actionError.value = ''; notice.value = ''
  try { await reportsApi.download(report.value.id, format); if (alive) notice.value = `${format === 'markdown' ? 'Markdown' : 'HTML'} 报告已导出，使用保存的快照与批注` }
  catch (cause) { if (alive) handleActionError(cause) }
  finally { if (alive) action.value = null }
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => { alive = false; generation++; controller?.abort(); window.removeEventListener('beforeunload', beforeUnload); emit('busy', false) })
</script>

<template>
  <div class="reports-workspace">
    <div class="page-heading"><div><div class="eyebrow">SAVED REPORTS</div><h1>{{ selectedId ? '报告详情' : '报告' }}</h1><p>独立留存的分析快照，可补充批注并导出</p></div><button class="button button-outline" :disabled="loading || mutationBusy" @click="refresh"><RefreshCw :size="14" :class="{ spin: loading }" />重新读取</button></div>
    <div v-if="selectedId" class="report-back"><button class="text-button" :disabled="mutationBusy" @click="select('')"><ArrowLeft :size="15" />返回报告列表</button><span>报告独立保存，不受原会话或查询结果有效期影响</span></div>
    <div v-if="error" class="alert alert-error report-alert" role="alert">{{ error }}<button class="text-button" @click="refresh">重试读取</button></div>
    <div v-if="loading" class="panel empty-state" role="status"><LoaderCircle class="spin" :size="25" /><p>{{ selectedId ? '正在校验权限并读取报告' : '正在读取当前账号的报告' }}</p></div>
    <template v-else-if="!selectedId">
      <div class="report-list-toolbar"><div class="segmented" role="group" aria-label="报告状态"><button :class="{ selected: !deleted }" :aria-pressed="!deleted" @click="toggleTrash(false)">已保存报告</button><button :class="{ selected: deleted }" :aria-pressed="deleted" @click="toggleTrash(true)"><Trash2 :size="13" />回收站</button></div><button class="button button-primary" @click="emit('newReview')">新建销售复盘</button></div>
      <section class="panel report-list"><div v-if="!items.length && !error" class="empty-state"><FileText :size="32" /><h2>{{ deleted ? '回收站是空的' : '还没有保存的报告' }}</h2><p>{{ deleted ? '移入回收站的报告可以恢复，不会自动永久删除。' : '完成销售复盘后点击“保存报告”，就能在这里重新打开。' }}</p></div><button v-for="item in items" :key="item.id" class="report-list-item" @click="select(item.id)"><span class="history-icon"><FileText :size="20" /></span><span class="report-list-copy"><strong>{{ item.title }}</strong><small>{{ item.kind === 'review' ? '销售复盘' : '查询快照' }} · {{ item.scope_label }}</small><small>{{ deleted ? '移入回收站' : '最近更新' }} {{ readableDate(item.deleted_at || item.updated_at) }}</small></span><ChevronRight :size="18" /></button></section>
      <div v-if="offset > 0 || items.length === 30" class="report-pagination"><button class="button button-outline button-small" :disabled="offset === 0 || loading" @click="paginate(-1)"><ChevronLeft :size="13" />上一页</button><span>第 {{ Math.floor(offset / 30) + 1 }} 页</span><button class="button button-outline button-small" :disabled="items.length < 30 || loading" @click="paginate(1)">下一页<ChevronRight :size="13" /></button></div>
      <p class="report-access-note">仅显示当前账号有权完整读取的报告。权限范围缩小后，旧报告可能不再出现在列表中。</p>
    </template>
    <template v-else-if="report">
      <section class="panel report-summary"><div class="report-title-row"><div><span class="review-label fact-label">{{ report.deleted_at ? '回收站 · 可恢复' : '独立保存的快照' }}</span><h2>{{ report.title }}</h2><p>{{ report.facts.scope.scope_label }} · 保存于 {{ readableDate(report.created_at) }}</p></div><div class="report-actions"><template v-if="!report.deleted_at"><button class="button button-outline button-small" :disabled="mutationBusy || editing" @click="edit"><Pencil :size="13" />编辑批注</button><button class="button button-outline button-small" :disabled="mutationBusy || editing" @click="download('markdown')"><Download :size="13" />{{ action === 'markdown' ? '导出中' : '导出 Markdown' }}</button><button class="button button-outline button-small" :disabled="mutationBusy || editing" @click="download('html')"><Download :size="13" />{{ action === 'html' ? '导出中' : '导出 HTML' }}</button><button class="button button-outline button-small" :disabled="mutationBusy || editing" @click="confirmTrash = true"><Trash2 :size="13" />移入回收站</button></template><button v-else class="button button-primary" :disabled="mutationBusy" @click="transition(true)"><RotateCcw :size="14" />{{ action === 'restoring' ? '正在恢复' : '恢复报告' }}</button></div></div><dl class="report-metadata"><div><dt>本期</dt><dd>{{ periodLabel(report.facts.result.period || report.facts.result.query?.period) }}</dd></div><div><dt>数据版本</dt><dd>{{ report.facts.dataset_version }}</dd></div><div><dt>指标版本</dt><dd>{{ report.facts.metric_version }}</dd></div><div><dt>快照生成</dt><dd>{{ readableDate(report.facts.generated_at) }}</dd></div></dl><p class="report-frozen-note">数字、图表和证据不可编辑。重新分析并保存会创建新报告，不会覆盖这份快照。</p></section>
      <div v-if="notice" class="alert alert-success report-alert" role="status">{{ notice }}</div><div v-if="actionError" class="alert alert-error report-alert" role="alert"><span>{{ actionError }}</span><button v-if="staleRevision" class="text-button" :disabled="mutationBusy" @click="refresh">重新读取最新报告</button></div>
      <form v-if="editing" class="panel report-editor" aria-label="编辑报告批注" @submit.prevent="save"><div class="panel-heading"><div><h2>编辑标题与我的批注</h2><p>这里的文字与冻结事实分开保存；不会修改原始分析建议</p></div></div><div class="report-fields"><label>报告标题<input v-model="draft.title" maxlength="120" required :disabled="mutationBusy" /></label><label>我的备注<textarea v-model="draft.comment" rows="4" maxlength="4000" :disabled="mutationBusy"></textarea></label><label>我的补充建议<textarea v-model="draft.suggestions" rows="4" maxlength="4000" :disabled="mutationBusy"></textarea></label></div><div class="report-form-actions"><button type="button" class="button button-outline" :disabled="mutationBusy" @click="requestNavigation(resetDraft)">取消编辑</button><button type="submit" class="button button-primary" :disabled="mutationBusy || !dirty || staleRevision">{{ action === 'saving' ? '正在保存' : '保存批注' }}</button></div></form>
      <section v-else class="panel report-annotations"><div class="panel-heading"><div><span class="review-label proposal-label">作者补充 · 可编辑</span><h2>我的批注</h2></div></div><div class="report-annotation-grid"><div><h3>我的备注</h3><p>{{ report.comment || '尚未添加备注' }}</p></div><div><h3>我的补充建议</h3><p>{{ report.suggestions || '尚未添加补充建议' }}</p></div></div></section>
      <details class="panel report-definitions"><summary>报告保存时的指标口径</summary><dl><div v-for="definition in report.facts.metric_definitions" :key="definition.id"><dt>{{ definition.label }} · {{ definition.unit }}</dt><dd>{{ definition.formula }}</dd><dd>{{ definition.description }}</dd></div></dl></details>
      <ReviewEvidence v-if="report.facts.review" :review="report.facts.review" /><QueryResult v-else :result="report.facts.result" title="报告中的冻结查询结果" :exportable="false" snapshot />
      <div class="report-provenance"><span>报告 ID {{ report.id }}</span><span>来源运行 {{ report.source_run_id }}</span><span>批注版本 {{ report.revision }} · 最后更新 {{ readableDate(report.updated_at) }}</span><span>读取与导出均按当前身份校验，原结果有效期只是历史元数据</span></div>
    </template>
    <ConfirmDialog v-if="confirmTrash" title="把这份报告移入回收站？" confirm-label="移入回收站" cancel-label="保留报告" @confirm="transition(false)" @cancel="confirmTrash = false"><p>报告可以在回收站恢复。恢复前无法编辑批注或导出，冻结证据仍会保留。</p></ConfirmDialog>
    <ConfirmDialog v-if="confirmDiscard" title="放弃尚未保存的批注？" confirm-label="放弃修改并继续" cancel-label="继续编辑" @confirm="discard" @cancel="keepEditing"><p>只有已保存的标题、备注和建议会被保留。数字与冻结证据不会改变。</p></ConfirmDialog>
  </div>
</template>
