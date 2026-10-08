import { computed, onBeforeUnmount, ref } from 'vue'
import type { Conversation } from '../types'
import type { ReviewRequest, ReviewRun } from '../types/reviews'
import { api, ApiError } from '../lib/api'
import { reportsApi } from '../lib/reportsApi'
import { reviewMessage } from '../lib/reviews'
import { TERMINAL_STATUSES } from '../lib/format'

export function useSalesReview(username: string) {
  const conversation = ref<Conversation | null>(null)
  const run = ref<ReviewRun | null>(null)
  const restoring = ref(false)
  const submitting = ref(false)
  const cancelling = ref(false)
  const error = ref('')
  const notice = ref('')
  const busy = computed(() => restoring.value || submitting.value || Boolean(run.value && !TERMINAL_STATUSES.has(run.value.status)))
  const storageKey = `contoso:${username}:review-conversation`
  let generation = 0
  let pollVersion = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let pending: { key: string; clientId: string; version: number } | undefined

  function storedId() { try { return localStorage.getItem(storageKey) } catch { return null } }
  function remember(id: string) { try { localStorage.setItem(storageKey, id) } catch { /* Server state remains available this session. */ } }
  function stopTimer() { clearTimeout(timer); timer = undefined }
  async function poll(id: string, currentGeneration: number, failures = 0) {
    if (currentGeneration !== generation) return
    const currentPoll = ++pollVersion
    try {
      const received = await reportsApi.run(id)
      if (currentGeneration !== generation || currentPoll !== pollVersion) return
      run.value = { ...received, run_id: received.run_id || received.id || id }
      notice.value = ''
      if (TERMINAL_STATUSES.has(received.status)) {
        cancelling.value = false
        if (received.status === 'failed') error.value = received.message || '本次复盘未完成，请重试'
        if (conversation.value) {
          try {
            const latest = await api.conversation(conversation.value.id)
            if (currentGeneration === generation && currentPoll === pollVersion) conversation.value = latest
          } catch { /* The next submission can reconcile a version conflict. */ }
        }
        return
      }
      timer = setTimeout(() => void poll(id, currentGeneration), 700)
    } catch (cause) {
      if (currentGeneration !== generation || currentPoll !== pollVersion) return
      if (cause instanceof ApiError && [401, 403, 404, 410].includes(cause.status)) {
        run.value = null; cancelling.value = false
        error.value = cause.status === 401 ? '登录已过期，请退出后重新登录' : '本次复盘已过期或当前账号无法读取，请按当前授权范围重新分析'
        return
      }
      notice.value = '连接暂时中断，正在重新获取服务端状态。刷新后可继续恢复，不会重新发起复盘。'
      timer = setTimeout(() => void poll(id, currentGeneration, failures + 1), Math.min(1500 * (failures + 1), 10000))
    }
  }
  async function restore() {
    if (busy.value) return
    const id = storedId()
    if (!id) return
    const currentGeneration = ++generation
    restoring.value = true; error.value = ''; notice.value = ''
    try {
      const restored = await api.conversation(id)
      if (currentGeneration !== generation) return
      conversation.value = restored
      const runId = restored.latest_run_id || restored.current_run_id
      if (runId) await poll(runId, currentGeneration)
    } catch (cause) {
      if (currentGeneration !== generation) return
      conversation.value = null; run.value = null
      error.value = cause instanceof Error ? cause.message : '无法恢复上次复盘，请重试'
    } finally { if (currentGeneration === generation) restoring.value = false }
  }
  async function start(request: ReviewRequest) {
    if (busy.value) return
    const currentGeneration = ++generation
    stopTimer(); submitting.value = true; error.value = ''; notice.value = ''; run.value = null
    try {
      if (!conversation.value) {
        const created = await api.createConversation()
        if (currentGeneration !== generation) return
        conversation.value = { ...created, id: created.id || created.conversation_id! }
        remember(conversation.value.id)
      }
      const key = JSON.stringify(request)
      if (!pending || pending.key !== key) pending = { key, clientId: crypto.randomUUID(), version: conversation.value.state_version }
      const started = await reportsApi.startReview(conversation.value.id, reviewMessage(request), pending.version, pending.clientId, request)
      if (currentGeneration !== generation) return
      pending = undefined
      run.value = { ...started, run_id: started.run_id || started.id! }
      if (started.state_version !== undefined) conversation.value.state_version = started.state_version
      await poll(run.value.run_id, currentGeneration)
    } catch (cause) {
      if (currentGeneration !== generation) return
      if (cause instanceof ApiError && cause.status === 409 && conversation.value) {
        pending = undefined
        try {
          const latest = await api.conversation(conversation.value.id)
          if (currentGeneration !== generation) return
          conversation.value = latest
          const id = latest.latest_run_id || latest.current_run_id
          if (id) await poll(id, currentGeneration)
        } catch { /* Keep the explicit conflict rather than inventing a successful retry. */ }
      }
      if (currentGeneration === generation) error.value = cause instanceof Error ? cause.message : '复盘提交未完成，请重试'
    } finally { if (currentGeneration === generation) submitting.value = false }
  }
  async function cancel() {
    if (!run.value || TERMINAL_STATUSES.has(run.value.status) || cancelling.value) return
    cancelling.value = true; error.value = ''
    const currentGeneration = generation
    const id = run.value.run_id
    try {
      await api.cancel(id)
      if (currentGeneration !== generation) return
      stopTimer(); await poll(id, currentGeneration)
    } catch (cause) {
      if (currentGeneration !== generation) return
      cancelling.value = false; error.value = cause instanceof Error ? cause.message : '停止请求未确认，请重试'
    }
  }
  onBeforeUnmount(() => { generation++; stopTimer() })
  return { run, restoring, submitting, cancelling, busy, error, notice, start, cancel, restore }
}
