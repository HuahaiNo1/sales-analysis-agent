// @vitest-environment jsdom
// Isolated UI fixtures. Production views only display authorized backend snapshots.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { AnalysisResult, Conversation } from '../src/types'
import type { ReviewPayload, ReviewRequest, ReviewRun, SalesReport } from '../src/types/reviews'
import { ApiError } from '../src/lib/api'
import { reviewValidation } from '../src/lib/reviews'
import ReviewSetup from '../src/components/ReviewSetup.vue'
import ReviewWorkspace from '../src/views/ReviewWorkspace.vue'
import ReportsWorkspace from '../src/views/ReportsWorkspace.vue'
import ConfirmDialog from '../src/components/ConfirmDialog.vue'
import App from '../src/App.vue'

const mocks = vi.hoisted(() => ({
  api: { me: vi.fn(), catalog: vi.fn(), dashboard: vi.fn(), logout: vi.fn(), createConversation: vi.fn(), conversation: vi.fn(), cancel: vi.fn() },
  reportsApi: { startReview: vi.fn(), run: vi.fn(), create: vi.fn(), list: vi.fn(), get: vi.fn(), update: vi.fn(), trash: vi.fn(), restore: vi.fn(), download: vi.fn() },
}))
vi.mock('../src/lib/api', async original => ({ ...await original<typeof import('../src/lib/api')>(), api: mocks.api }))
vi.mock('../src/lib/reportsApi', () => ({ reportsApi: mocks.reportsApi }))
vi.mock('../src/components/ChartView.vue', () => ({ __esModule: true, default: { template: '<div data-testid="test-only-chart"></div>' } }))

const request: ReviewRequest = { period: { start: '2025-09-01', end: '2025-10-01' }, comparison: { start: '2025-08-01', end: '2025-09-01' }, metric: 'sales_amount', filters: [], drill_down: true }
const result: AnalysisResult = { id: 'frozen-result', period: request.period, comparison: request.comparison, query: { metrics: ['sales_amount'], period: request.period, comparison: request.comparison, analysis: 'compare' }, metrics: ['sales_amount'], columns: [{ key: 'sales_amount', label: '销售额', kind: 'money' }], rows: [{ sales_amount: '100.00' }], totals: { sales_amount: '100.00' }, comparison_totals: { sales_amount: '80.00' }, deltas: { sales_amount: '20.00' }, chart: { type: 'table' }, metadata: { scope_label: '隔离测试门店', dataset_version: 'frozen-data' } }
const review: ReviewPayload = { template_id: 'sales_review_v1', template_version: '1', request, evidence: [{ evidence_id: 'E1', role: 'overall', title: '整体比较', result }], findings: [{ text: '隔离测试：销售额增加 20.00 USD', evidence_ids: ['E1'] }], flags: [], assumptions: ['模拟数据不能确认真实业务原因'], suggestions: ['先核实相关业务记录'], rule_config: { version: 'rules-v1', relative_change_threshold_pct: '20', concentration_threshold_pct: '50' }, query_count: 1, max_queries: 4, stop_reason: 'drill_disabled' }
const completed: ReviewRun = { run_id: 'review-run', kind: 'review', status: 'succeeded', result, review, state_version: 1 }
const saved: SalesReport = { id: 'report-one', title: '已保存复盘', kind: 'review', revision: 1, created_at: '2026-10-07T10:00:00Z', updated_at: '2026-10-07T10:00:00Z', deleted_at: null, source_run_id: 'review-run', scope_label: '隔离测试门店', comment: '原备注', suggestions: '原补充建议', facts: { schema_version: 'report_v1', kind: 'review', generated_at: '2026-10-07T09:00:00Z', dataset_version: 'frozen-data', metric_version: 'metrics_v1', metric_definitions: [{ id: 'sales_amount', label: '冻结销售额口径', formula: 'SUM(Quantity × NetPrice)', unit: 'USD', description: '冻结定义' }], scope: { allowed_store_ids: [1], scope_label: '隔离测试门店', scope_version: 'scope-v1' }, result, review } }
let wrapper: VueWrapper | undefined
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (cause: Error) => void; const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail }); return { promise, resolve, reject } }
function button(text: string) { return wrapper!.findAll('button').find(item => item.text() === text)! }
async function mountReview() { wrapper = mount(ReviewWorkspace, { props: { user: { username: 'admin' }, catalog: { stores: [{ key: 1, label: '测试门店' }] }, scopeLabel: '隔离测试门店' }, attachTo: document.body }); await flushPromises() }
async function mountReport() { wrapper = mount(ReportsWorkspace, { props: { selectedId: saved.id, active: true }, attachTo: document.body }); await flushPromises() }
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear(); sessionStorage.clear(); history.replaceState(null, '', '/')
  mocks.api.me.mockResolvedValue({ username: 'admin', scope_label: '隔离测试门店' })
  mocks.api.catalog.mockResolvedValue({ model_mode: 'mock' })
  mocks.api.dashboard.mockResolvedValue({ totals: {} })
  mocks.api.createConversation.mockResolvedValue({ id: 'review-conversation', state_version: 0 })
  mocks.api.conversation.mockResolvedValue({ id: 'review-conversation', state_version: 1, latest_run_id: 'review-run' })
  mocks.reportsApi.startReview.mockResolvedValue({ run_id: 'review-run', status: 'queued', state_version: 1 })
  mocks.reportsApi.run.mockResolvedValue(completed)
  mocks.reportsApi.get.mockResolvedValue(saved)
  mocks.reportsApi.list.mockResolvedValue({ items: [saved], limit: 30, offset: 0 })
  mocks.reportsApi.download.mockResolvedValue(undefined)
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers(); document.body.innerHTML = '' })

describe('bounded review and independent reports', () => {
  it('submits explicit quarter boundaries and an authorized store without a natural-language parser', async () => {
    wrapper = mount(ReviewSetup, { props: { catalog: { stores: [{ key: 1, label: '测试门店' }] }, scopeLabel: '隔离测试', busy: false } })
    await button('2025 年 Q3 vs Q2').trigger('click')
    await wrapper.findAll('select')[1]!.setValue('1')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('start')?.[0]?.[0]).toEqual({ ...request, period: { start: '2025-07-01', end: '2025-10-01' }, comparison: { start: '2025-04-01', end: '2025-07-01' }, filters: [{ dimension: 'store', op: 'eq', values: ['1'] }] })
    expect(reviewValidation({ ...request, comparison: request.period })).toContain('相同')
    expect(reviewValidation({ ...request, period: { start: '2022-12-01', end: '2023-01-01' } })).toContain('2023–2025')
  })
  it('gates rapid starts through delayed conversation creation and hydrates the actual review result', async () => {
    const creating = deferred<Conversation>(); mocks.api.createConversation.mockReturnValueOnce(creating.promise)
    await mountReview()
    await wrapper!.find('form[aria-label="销售复盘设置"]').trigger('submit')
    await wrapper!.find('form[aria-label="销售复盘设置"]').trigger('submit')
    expect(mocks.api.createConversation).toHaveBeenCalledTimes(1)
    expect(mocks.reportsApi.startReview).not.toHaveBeenCalled()
    expect(wrapper!.find('fieldset').attributes('disabled')).toBeDefined()
    creating.resolve({ id: 'new-review-conversation', state_version: 0 }); await flushPromises()
    expect(mocks.reportsApi.startReview).toHaveBeenCalledWith('new-review-conversation', expect.stringContaining('销售复盘'), 0, expect.any(String), request)
    expect(wrapper!.text()).toContain('本次复盘已完成')
    expect(wrapper!.text()).toContain('数据事实')
    expect(wrapper!.text()).toContain('假设与局限')
    expect(wrapper!.text()).toContain('本次未启用进一步下钻')
    expect(localStorage.getItem('contoso:admin:review-conversation')).toBe('new-review-conversation')
  })
  it('retries an uncertain save with the same idempotency key and emits only the persisted report id', async () => {
    localStorage.setItem('contoso:admin:review-conversation', 'review-conversation')
    mocks.reportsApi.create.mockRejectedValueOnce(new ApiError('网络中断', 0)).mockResolvedValueOnce(saved)
    await mountReview()
    expect(mocks.reportsApi.startReview).not.toHaveBeenCalled()
    await button('保存报告').trigger('click')
    await wrapper!.find('.report-save-form').trigger('submit'); await flushPromises()
    expect(wrapper!.find('.report-save-form [role="alert"]').text()).toContain('网络中断')
    const first = mocks.reportsApi.create.mock.calls[0]![0]
    await wrapper!.find('.report-save-form').trigger('submit'); await flushPromises()
    expect(mocks.reportsApi.create.mock.calls[1]![0]).toEqual(first)
    expect(wrapper!.emitted('openReport')).toEqual([[saved.id]])
    expect(mocks.reportsApi.run).toHaveBeenCalledTimes(1)
  })
  it('cancels a pending review, publishes no old evidence, and releases controls', async () => {
    vi.useFakeTimers()
    mocks.reportsApi.run.mockResolvedValueOnce({ run_id: 'review-run', status: 'querying', progress_stage: 'review_category' }).mockResolvedValue({ run_id: 'review-run', status: 'cancelled', message: '已取消，未发布结果' })
    mocks.api.cancel.mockResolvedValue({ run_id: 'review-run', status: 'cancelling' })
    await mountReview(); await wrapper!.find('form[aria-label="销售复盘设置"]').trigger('submit'); await flushPromises()
    await button('停止复盘').trigger('click'); await flushPromises()
    expect(mocks.api.cancel).toHaveBeenCalledWith('review-run')
    expect(wrapper!.text()).toContain('已取消，未发布结果')
    expect(wrapper!.find('.review-evidence').exists()).toBe(false)
    expect(wrapper!.find('fieldset').attributes('disabled')).toBeUndefined()
  })
  it('reopens and exports only the frozen report, then edits annotations without sending facts', async () => {
    const original = JSON.stringify(saved.facts)
    mocks.reportsApi.update.mockImplementation(async (_id, _revision, annotations) => ({ ...saved, ...annotations, revision: 2 }))
    await mountReport()
    expect(wrapper!.text()).toContain('冻结销售额口径')
    expect(mocks.reportsApi.run).not.toHaveBeenCalled()
    expect(mocks.reportsApi.startReview).not.toHaveBeenCalled()
    await button('导出 HTML').trigger('click'); await flushPromises()
    expect(mocks.reportsApi.download).toHaveBeenCalledWith(saved.id, 'html')
    await button('编辑批注').trigger('click')
    await wrapper!.find('.report-editor input').setValue('修改后的标题')
    await wrapper!.find('.report-editor textarea').setValue('<img src=x onerror=alert(1)>')
    await wrapper!.find('.report-editor').trigger('submit'); await flushPromises()
    expect(mocks.reportsApi.update).toHaveBeenCalledWith(saved.id, 1, { title: '修改后的标题', comment: '<img src=x onerror=alert(1)>', suggestions: '原补充建议' })
    expect(wrapper!.find('img').exists()).toBe(false)
    expect(JSON.stringify(saved.facts)).toBe(original)
    expect(wrapper!.text()).toContain('冻结数据事实未改变')
  })
  it('keeps dirty annotations on cancel and version conflict, and asks before discarding for navigation', async () => {
    mocks.reportsApi.update.mockRejectedValue(new ApiError('版本冲突', 409))
    await mountReport(); await button('编辑批注').trigger('click')
    await wrapper!.find('.report-editor input').setValue('尚未保存的标题')
    await wrapper!.find('.report-editor').trigger('submit'); await flushPromises()
    expect(wrapper!.text()).toContain('你的输入仍在这里')
    expect((wrapper!.find('.report-editor input').element as HTMLInputElement).value).toBe('尚未保存的标题')
    const next = vi.fn()
    ;(wrapper!.vm as unknown as { requestNavigation: (next: () => void) => void }).requestNavigation(next)
    await flushPromises()
    expect(next).not.toHaveBeenCalled()
    wrapper!.findComponent(ConfirmDialog).vm.$emit('cancel'); await flushPromises()
    expect(wrapper!.find('.report-editor').exists()).toBe(true)
    ;(wrapper!.vm as unknown as { requestNavigation: (next: () => void) => void }).requestNavigation(next)
    await flushPromises(); wrapper!.findComponent(ConfirmDialog).vm.$emit('confirm'); await flushPromises()
    expect(next).toHaveBeenCalledOnce()
    expect(wrapper!.find('.report-editor').exists()).toBe(false)
  })
  it('removes all report facts after export authorization is denied, rather than showing stale numbers', async () => {
    mocks.reportsApi.download.mockRejectedValue(new ApiError('权限变化', 404))
    await mountReport()
    expect(wrapper!.find('.review-evidence').exists()).toBe(true)
    await button('导出 Markdown').trigger('click'); await flushPromises()
    expect(wrapper!.find('.review-evidence').exists()).toBe(false)
    expect(wrapper!.find('.report-summary').exists()).toBe(false)
    expect(wrapper!.text()).toContain('当前账号已无权查看完整范围')
  })
  it('soft-deletes only after confirmation and restores without losing the snapshot', async () => {
    mocks.reportsApi.trash.mockResolvedValue({ ...saved, revision: 2, deleted_at: '2026-10-07T11:00:00Z' })
    mocks.reportsApi.restore.mockResolvedValue({ ...saved, revision: 3 })
    await mountReport(); await button('移入回收站').trigger('click')
    expect(mocks.reportsApi.trash).not.toHaveBeenCalled()
    wrapper!.findComponent(ConfirmDialog).vm.$emit('confirm'); await flushPromises()
    expect(mocks.reportsApi.trash).toHaveBeenCalledWith(saved.id, 1)
    expect(wrapper!.findAll('button').some(item => item.text() === '导出 HTML')).toBe(false)
    expect(wrapper!.find('.review-evidence').exists()).toBe(true)
    await button('恢复报告').trigger('click'); await flushPromises()
    expect(mocks.reportsApi.restore).toHaveBeenCalledWith(saved.id, 2)
    expect(button('导出 HTML').exists()).toBe(true)
  })
  it('opens a report from a refreshable URL and guards main navigation without losing dirty edits', async () => {
    history.replaceState(null, '', `/#reports/${saved.id}`)
    wrapper = mount(App, { attachTo: document.body }); await flushPromises()
    expect(wrapper.find('.report-title-row').text()).toContain(saved.title)
    expect(mocks.reportsApi.get).toHaveBeenCalledWith(saved.id, expect.any(AbortSignal))
    expect(mocks.api.dashboard).not.toHaveBeenCalled()
    expect(mocks.api.createConversation).not.toHaveBeenCalled()
    await button('编辑批注').trigger('click')
    await wrapper.find('.report-editor input').setValue('尚未保存')
    await wrapper.find('nav').findAll('button').find(item => item.text() === '销售概览')!.trigger('click')
    expect(location.hash).toBe(`#reports/${saved.id}`)
    wrapper.findComponent(ConfirmDialog).vm.$emit('cancel'); await flushPromises()
    expect(wrapper.find('.report-editor').exists()).toBe(true)
    await wrapper.find('nav').findAll('button').find(item => item.text() === '销售概览')!.trigger('click')
    wrapper.findComponent(ConfirmDialog).vm.$emit('confirm'); await flushPromises()
    expect(location.hash).toBe('#overview')
    // Simulate browser Back arriving at the saved deep link. It must reauthorize, not rerun analysis.
    history.replaceState(null, '', `/#reports/${saved.id}`)
    window.dispatchEvent(new PopStateEvent('popstate')); await flushPromises()
    expect(wrapper.find('.report-title-row').text()).toContain(saved.title)
    expect(mocks.reportsApi.get).toHaveBeenCalledTimes(2)
    expect(mocks.reportsApi.startReview).not.toHaveBeenCalled()
  })
  it('clears an expired or unauthorized review preview when save is denied', async () => {
    localStorage.setItem('contoso:admin:review-conversation', 'review-conversation')
    mocks.reportsApi.create.mockRejectedValue(new ApiError('来源已过期', 410))
    await mountReview(); await button('保存报告').trigger('click')
    await wrapper!.find('.report-save-form').trigger('submit'); await flushPromises()
    expect(wrapper!.find('.review-evidence').exists()).toBe(false)
    expect(wrapper!.text()).toContain('来源证据已过期或当前权限无法保存')
  })
})
