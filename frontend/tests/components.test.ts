// @vitest-environment jsdom
// These isolated fixtures exist only in tests; the running app always uses the backend API.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { AnalysisResult } from '../src/types'
import App from '../src/App.vue'
import QueryResult from '../src/components/QueryResult.vue'
const mocks = vi.hoisted(() => ({ api: { me: vi.fn(), login: vi.fn(), logout: vi.fn(), catalog: vi.fn(), dashboard: vi.fn(), createConversation: vi.fn(), conversation: vi.fn(), startRun: vi.fn(), run: vi.fn(), cancel: vi.fn(), result: vi.fn(), download: vi.fn() } }))
vi.mock('../src/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('../src/lib/api')>(), api: mocks.api }))
vi.mock('../src/components/ChartView.vue', () => ({ __esModule: true, default: { template: '<div data-testid="test-only-chart-stub"></div>' } }))
const fixture: AnalysisResult = { id: 'test-only-result', query: { metrics: ['sales_amount'], period: { start: '2025-01-01', end: '2026-01-01' }, group_by: ['month'] }, metrics: ['sales_amount'], columns: [{ key: 'month', label: '月份', kind: 'dimension' }, { key: 'sales_amount', label: '销售额', kind: 'money' }], rows: [{ month: '2025-01', sales_amount: '100.00' }], totals: { sales_amount: '100.00' }, metadata: { simulated: true, currency: 'USD', scope_label: '隔离测试夹具' }, chart: { type: 'table' } }
let wrapper: VueWrapper | undefined
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear()
  mocks.api.me.mockResolvedValue({ username: 'admin', scope_label: '隔离测试范围' })
  mocks.api.catalog.mockResolvedValue({ model_mode: 'mock', budget: { reserved_rmb: 0, cap_rmb: 150, remaining_rmb: 150 } })
  mocks.api.dashboard.mockResolvedValue({ totals: { sales_amount: '100', order_count: '2', units_sold: '3', avg_order_value: '50', gross_profit: '20' } })
  mocks.api.createConversation.mockResolvedValue({ id: 'test-conversation', state_version: 0 })
  mocks.api.conversation.mockResolvedValue({ id: 'test-conversation', state_version: 1, history: [] })
  mocks.api.startRun.mockResolvedValue({ run_id: 'test-run', status: 'queued', state_version: 1 })
  mocks.api.result.mockResolvedValue(fixture)
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers(); document.body.innerHTML = '' })
function button(text: string) { return wrapper!.findAll('button').find(item => item.text() === text)! }

describe('focused UI state and rendering checks (not a real browser)', () => {
  it('shows no-data explicitly and keeps undefined average value nonnumeric', async () => {
    wrapper = mount(QueryResult, { props: { result: { ...fixture, no_data: true, rows: [], metrics: ['avg_order_value'], totals: { avg_order_value: null } } } })
    await flushPromises()
    expect(wrapper.text()).toContain('这个范围没有匹配数据')
    expect(wrapper.find('.result-summary strong').text()).toBe('—')
    expect(wrapper.find('table').exists()).toBe(false)
  })
  it('renders hostile model/dimension strings only as escaped text', async () => {
    const malicious = '<img src=x onerror="alert(1)">'
    wrapper = mount(QueryResult, { props: { result: { ...fixture, rows: [{ month: malicious, sales_amount: '100' }], observations: [malicious] } } })
    await flushPromises()
    expect(wrapper.text()).toContain(malicious)
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('script').exists()).toBe(false)
  })
  it('CSV failure is visible and releases the download button for retry', async () => {
    mocks.api.download.mockRejectedValue(new Error('隔离测试：授权结果已过期'))
    wrapper = mount(QueryResult, { props: { result: fixture } })
    await button('导出 CSV').trigger('click'); await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toContain('结果已过期')
    expect(button('导出 CSV').attributes('disabled')).toBeUndefined()
  })
  it('stops a running server query and re-enables the composer', async () => {
    mocks.api.run.mockResolvedValueOnce({ run_id: 'test-run', status: 'querying' }).mockResolvedValue({ run_id: 'test-run', status: 'cancelled', message: '运行已取消，未发布新的结果' })
    mocks.api.cancel.mockResolvedValue({ run_id: 'test-run', status: 'cancelled' })
    wrapper = mount(App, { attachTo: document.body }); await flushPromises()
    await wrapper.find('#query-input').setValue('2025年销售额按月看')
    await wrapper.find('.composer').trigger('submit'); await flushPromises()
    expect(wrapper.find('#query-input').attributes('disabled')).toBeDefined()
    await button('停止').trigger('click'); await flushPromises()
    expect(mocks.api.cancel).toHaveBeenCalledWith('test-run')
    expect(wrapper.find('#query-input').attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('运行已取消')
    expect(wrapper.find('.query-result-anchor .result-card').exists()).toBe(false)
  })
  it('refreshing a failed/refused latest turn does not present an older result as current', async () => {
    localStorage.setItem('contoso:admin:conversation', 'test-conversation')
    mocks.api.conversation.mockResolvedValue({ id: 'test-conversation', state_version: 2, latest_run_id: 'refused-run', history: [{ role: 'assistant', content: '旧结果', result_id: fixture.id, run_id: 'old-run' }, { role: 'user', content: '退款', run_id: 'refused-run' }, { role: 'assistant', content: '不支持退款分析', status: 'unsupported', run_id: 'refused-run' }] })
    mocks.api.run.mockResolvedValue({ run_id: 'refused-run', status: 'unsupported', message: '不支持退款分析' })
    wrapper = mount(App, { attachTo: document.body }); await flushPromises()
    expect(wrapper.text()).toContain('不支持退款分析')
    expect(wrapper.find('.query-result-anchor .result-card').exists()).toBe(false)
    expect(wrapper.find('#query-input').attributes('disabled')).toBeUndefined()
    expect(mocks.api.result).not.toHaveBeenCalled()
  })
  it('reset cancellation preserves the current chat; confirmation creates a new server conversation', async () => {
    mocks.api.conversation.mockResolvedValue({ id: 'test-conversation', state_version: 1, history: [{ role: 'user', content: '2025年销售额按月看', run_id: 'test-run' }, { role: 'assistant', content: '隔离测试完成', run_id: 'test-run', result_id: fixture.id }] })
    mocks.api.run.mockResolvedValue({ run_id: 'test-run', status: 'succeeded', message: '隔离测试完成', result: fixture })
    wrapper = mount(App, { attachTo: document.body }); await flushPromises()
    await wrapper.find('#query-input').setValue('2025年销售额按月看')
    await wrapper.find('.composer').trigger('submit'); await flushPromises()
    await wrapper.find('[aria-label="新建会话并重置上下文"]').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    await button('保留当前会话').trigger('click')
    expect(wrapper.find('.chat-message.user').text()).toContain('2025年销售额')
    await wrapper.find('[aria-label="新建会话并重置上下文"]').trigger('click')
    await button('新建会话').trigger('click'); await flushPromises()
    expect(mocks.api.createConversation).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('.chat-message.user')).toHaveLength(0)
  })
  it('presentation props switch the same immutable result without mutating its payload', async () => {
    const original = JSON.stringify(fixture)
    wrapper = mount(QueryResult, { props: { result: fixture, presentationChart: 'bar' } })
    await flushPromises()
    expect(button('柱状').attributes('aria-pressed')).toBe('true')
    await wrapper.setProps({ presentationChart: 'table' }); await flushPromises()
    expect(button('表格').attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('table').exists()).toBe(true)
    await wrapper.setProps({ presentationChart: 'waterfall' }); await flushPromises()
    expect(button('瀑布').attributes('aria-pressed')).toBe('false')
    expect(JSON.stringify(fixture)).toBe(original)
  })
  it('restores run presentation on refresh and accepts a same-result presentation-only follow-up', async () => {
    const original = JSON.stringify(fixture)
    localStorage.setItem('contoso:admin:conversation', 'test-conversation')
    mocks.api.conversation.mockResolvedValue({ id: 'test-conversation', state_version: 1, latest_run_id: 'test-run', history: [{ role: 'user', content: '换成柱状图', run_id: 'test-run' }] })
    mocks.api.run.mockResolvedValue({ run_id: 'test-run', status: 'succeeded', result: fixture, result_id: fixture.id, presentation: { chart_type: 'bar', reused_result_id: fixture.id } })
    wrapper = mount(App, { attachTo: document.body }); await flushPromises()
    let result = wrapper.find('.query-result-anchor .result-card')
    expect(result.findAll('button').find(item => item.text() === '柱状')!.attributes('aria-pressed')).toBe('true')
    mocks.api.run.mockResolvedValue({ run_id: 'display-run', status: 'succeeded', result: fixture, result_id: fixture.id, presentation: { chart_type: 'table', reused_result_id: fixture.id } })
    mocks.api.startRun.mockResolvedValue({ run_id: 'display-run', status: 'queued', state_version: 2 })
    await wrapper.find('#query-input').setValue('换成表格')
    await wrapper.find('.composer').trigger('submit'); await flushPromises()
    result = wrapper.find('.query-result-anchor .result-card')
    expect(result.findAll('button').find(item => item.text() === '表格')!.attributes('aria-pressed')).toBe('true')
    expect(JSON.stringify(fixture)).toBe(original)
  })

})
