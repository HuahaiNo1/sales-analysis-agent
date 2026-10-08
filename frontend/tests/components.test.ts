// @vitest-environment jsdom
// These isolated fixtures exist only in tests; the running app always uses the backend API.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { AnalysisResult, Conversation, Run } from '../src/types'
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
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
async function mountExistingConversation() {
  localStorage.setItem('contoso:admin:conversation', 'old-conversation')
  localStorage.setItem('contoso:admin:recent', JSON.stringify([{ id: 'old-conversation', title: '原有分析', date: '2026-10-07' }]))
  mocks.api.conversation.mockResolvedValue({ id: 'old-conversation', state_version: 4, latest_run_id: 'old-run', history: [{ role: 'user', content: '原有分析', run_id: 'old-run' }, { role: 'assistant', content: '原有结果', run_id: 'old-run', result_id: fixture.id }] })
  mocks.api.run.mockResolvedValue({ run_id: 'old-run', status: 'succeeded', result: fixture })
  wrapper = mount(App, { attachTo: document.body }); await flushPromises()
}
async function confirmReset() {
  await wrapper!.find('[aria-label="新建会话并重置上下文"]').trigger('click')
  await button('新建会话').trigger('click'); await flushPromises()
}

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
  it.each(
    (['cancel', 'run'] as const).flatMap(stage =>
      (['success', 'failure'] as const).flatMap(outcome =>
        (['new-run', 'new-conversation', 'history', 'logout'] as const).map(boundary => ({ stage, outcome, boundary })))),
  )('ignores late $stage $outcome after $boundary while preserving the new cancellation', async ({ stage, outcome, boundary }) => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const delayed = deferred<Run>()
    mocks.api.run.mockResolvedValue({ run_id: 'test-run', status: 'querying' })
    mocks.api.cancel.mockImplementationOnce(() => stage === 'cancel' ? delayed.promise : Promise.resolve({ run_id: 'test-run', status: 'cancelled' }))
    wrapper = mount(App, { attachTo: document.body }); await flushPromises()
    await wrapper.find('#query-input').setValue('旧问题')
    await wrapper.find('.composer').trigger('submit'); await flushPromises()
    if (stage === 'run') mocks.api.run.mockReturnValueOnce(delayed.promise)
    await button('停止').trigger('click'); await flushPromises()
    expect(mocks.api.cancel).toHaveBeenCalledWith('test-run')

    // Polling can finish the old run and unlock navigation before cancellation returns.
    mocks.api.run.mockResolvedValue({ run_id: 'test-run', status: 'cancelled', message: '旧运行已结束' })
    await vi.advanceTimersByTimeAsync(700); await flushPromises()
    expect(wrapper.find('#query-input').attributes('disabled')).toBeUndefined()
    if (boundary === 'new-conversation') {
      mocks.api.createConversation.mockResolvedValueOnce({ id: 'new-conversation', state_version: 0 })
      await confirmReset()
    } else if (boundary === 'history') {
      mocks.api.conversation.mockResolvedValueOnce({ id: 'history-conversation', state_version: 8, history: [] })
      await wrapper.find('.recent-link').trigger('click'); await flushPromises()
    } else if (boundary === 'logout') {
      mocks.api.logout.mockResolvedValueOnce({})
      await wrapper.find('[aria-label="退出登录"]').trigger('click'); await flushPromises()
      expect(wrapper.find('.login-layout').exists()).toBe(true)
      localStorage.removeItem('contoso:admin:conversation')
      mocks.api.createConversation.mockResolvedValueOnce({ id: 'login-conversation', state_version: 0 })
      await wrapper.find('.login-form-wrap form').trigger('submit'); await flushPromises()
    }
    mocks.api.startRun.mockResolvedValueOnce({ run_id: 'new-run', status: 'queued' })
    mocks.api.run.mockResolvedValue({ run_id: 'new-run', status: 'querying' })
    await wrapper.find('#query-input').setValue('新问题')
    await wrapper.find('.composer').trigger('submit'); await flushPromises()
    const newCancellation = deferred<Run>()
    mocks.api.cancel.mockReturnValueOnce(newCancellation.promise)
    await button('停止').trigger('click'); await flushPromises()
    expect(mocks.api.cancel).toHaveBeenLastCalledWith('new-run')
    expect(button('停止中').attributes('disabled')).toBeDefined()
    const currentHtml = wrapper.html()
    const runReads = mocks.api.run.mock.calls.length
    const conversationReads = mocks.api.conversation.mock.calls.length

    if (outcome === 'success') delayed.resolve({ run_id: 'test-run', status: 'succeeded', result: fixture, message: '过期的旧结果' })
    else delayed.reject(new Error('不应显示的旧停止错误'))
    await flushPromises()
    expect(wrapper.html()).toBe(currentHtml)
    expect(mocks.api.run).toHaveBeenCalledTimes(runReads)
    expect(mocks.api.conversation).toHaveBeenCalledTimes(conversationReads)
    expect(wrapper.find('.query-result-anchor .result-card').exists()).toBe(false)
    expect(wrapper.find('.assistant-error').exists()).toBe(false)
    expect(button('停止中').attributes('disabled')).toBeDefined()
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
  it('blocks rapid send, reset, history and logout during delayed creation, then sends both turns to the new conversation', async () => {
    await mountExistingConversation()
    const created = deferred<Conversation>()
    mocks.api.createConversation.mockReturnValueOnce(created.promise)
    await wrapper!.find('#query-input').setValue('2025年销售额按月看')
    await confirmReset()
    expect(wrapper!.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper!.find('.working-message').text()).toContain('正在新建会话')
    for (const selector of ['#query-input', '.send-button', '[aria-label="新建会话并重置上下文"]', '.recent-link', '[aria-label="退出登录"]']) {
      expect(wrapper!.find(selector).attributes('disabled')).toBeDefined()
    }
    expect(wrapper!.find('.stop-button').exists()).toBe(false)
    // Dispatch directly as well: stale/queued events must be gated by handlers, not just the DOM.
    await wrapper!.find('.composer').trigger('submit')
    wrapper!.find('#query-input').element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    for (const selector of ['[aria-label="新建会话并重置上下文"]', '.recent-link', '[aria-label="退出登录"]']) {
      wrapper!.find(selector).element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    }
    await flushPromises()
    expect(mocks.api.startRun).not.toHaveBeenCalled()
    expect(mocks.api.createConversation).toHaveBeenCalledTimes(1)
    expect(mocks.api.conversation).toHaveBeenCalledTimes(1)
    expect(mocks.api.logout).not.toHaveBeenCalled()
    expect(wrapper!.find('.chat-message.user').text()).toBe('原有分析')
    expect(localStorage.getItem('contoso:admin:conversation')).toBe('old-conversation')

    created.resolve({ id: 'new-conversation', state_version: 0 }); await flushPromises()
    expect(wrapper!.find('#query-input').attributes('disabled')).toBeUndefined()
    expect(wrapper!.findAll('.chat-message.user')).toHaveLength(0)
    expect(wrapper!.find('.query-result-anchor .result-card').exists()).toBe(false)
    expect(localStorage.getItem('contoso:admin:conversation')).toBe('new-conversation')
    for (const [index, text] of ['2025年销售额按月看', '按门店看'].entries()) {
      mocks.api.startRun.mockResolvedValueOnce({ run_id: `new-run-${index}`, status: 'succeeded', state_version: index + 1 })
      mocks.api.run.mockResolvedValueOnce({ run_id: `new-run-${index}`, status: 'succeeded', result: fixture })
      mocks.api.conversation.mockResolvedValueOnce({ id: 'new-conversation', state_version: index + 1 })
      await wrapper!.find('#query-input').setValue(text)
      await wrapper!.find('.composer').trigger('submit'); await flushPromises()
      expect(mocks.api.startRun).toHaveBeenNthCalledWith(index + 1, 'new-conversation', text, index, expect.any(String))
      expect(wrapper!.find('#query-input').attributes('disabled')).toBeUndefined()
    }
    expect(wrapper!.findAll('.chat-message.user').map(item => item.text())).toEqual(['2025年销售额按月看', '按门店看'])
  })
  it('failed creation releases the gate while preserving the original context and draft, and permits retry', async () => {
    await mountExistingConversation()
    const created = deferred<Conversation>()
    mocks.api.createConversation.mockReturnValueOnce(created.promise)
    await wrapper!.find('#query-input').setValue('待发送问题')
    await confirmReset()
    created.reject(new Error('隔离测试：新建会话失败')); await flushPromises()
    expect(wrapper!.find('.assistant-error').text()).toContain('新建会话失败')
    expect(wrapper!.find('#query-input').attributes('disabled')).toBeUndefined()
    expect((wrapper!.find('#query-input').element as HTMLTextAreaElement).value).toBe('待发送问题')
    expect(wrapper!.find('.chat-message.user').text()).toBe('原有分析')
    expect(wrapper!.find('.query-result-anchor .result-card').exists()).toBe(true)
    expect(localStorage.getItem('contoso:admin:conversation')).toBe('old-conversation')
    expect(mocks.api.startRun).not.toHaveBeenCalled()
    mocks.api.createConversation.mockResolvedValueOnce({ id: 'retry-conversation', state_version: 0 })
    await confirmReset()
    expect(mocks.api.createConversation).toHaveBeenCalledTimes(2)
    expect(localStorage.getItem('contoso:admin:conversation')).toBe('retry-conversation')
    expect(wrapper!.find('.assistant-error').exists()).toBe(false)
    expect(wrapper!.find('#query-input').attributes('disabled')).toBeUndefined()
  })
  it('holds the history-switch gate through result hydration and keeps the old context if hydration fails', async () => {
    await mountExistingConversation()
    const loaded = deferred<Conversation>()
    const result = deferred<AnalysisResult>()
    mocks.api.conversation.mockReturnValueOnce(loaded.promise)
    mocks.api.run.mockResolvedValueOnce({ run_id: 'history-run', status: 'succeeded', result_id: 'history-result' })
    mocks.api.result.mockReturnValueOnce(result.promise)
    await wrapper!.find('#query-input').setValue('不能发到旧会话')
    await wrapper!.find('.recent-link').trigger('click'); await flushPromises()
    expect(wrapper!.find('.working-message').text()).toContain('正在读取会话')
    await wrapper!.find('.composer').trigger('submit')
    expect(mocks.api.startRun).not.toHaveBeenCalled()
    loaded.resolve({ id: 'history-conversation', state_version: 7, latest_run_id: 'history-run', history: [{ role: 'user', content: '历史会话' }] })
    await flushPromises()
    expect(wrapper!.find('#query-input').attributes('disabled')).toBeDefined()
    expect(wrapper!.find('.chat-message.user').text()).toBe('原有分析')
    result.reject(new Error('隔离测试：历史结果读取失败')); await flushPromises()
    expect(wrapper!.find('#query-input').attributes('disabled')).toBeUndefined()
    expect(wrapper!.find('.assistant-error').text()).toContain('历史结果读取失败')
    expect(wrapper!.find('.chat-message.user').text()).toBe('原有分析')
    expect(localStorage.getItem('contoso:admin:conversation')).toBe('old-conversation')
    expect(wrapper!.find('.query-result-anchor .result-card').exists()).toBe(true)
  })
  it.each(['success', 'failure'])('ignores a stale historical result %s after reset switches generation', async outcome => {
    await mountExistingConversation()
    const result = deferred<AnalysisResult>()
    mocks.api.result.mockReturnValueOnce(result.promise)
    await wrapper!.find('.message-result-link').trigger('click'); await flushPromises()
    mocks.api.createConversation.mockResolvedValueOnce({ id: 'new-conversation', state_version: 0 })
    await confirmReset()
    if (outcome === 'success') result.resolve(fixture)
    else result.reject(new Error('不应显示的旧错误'))
    await flushPromises()
    expect(wrapper!.find('.query-result-anchor .result-card').exists()).toBe(false)
    expect(wrapper!.find('.assistant-error').exists()).toBe(false)
    expect(wrapper!.findAll('.chat-message.user')).toHaveLength(0)
    expect(localStorage.getItem('contoso:admin:conversation')).toBe('new-conversation')
    expect(mocks.api.run).toHaveBeenCalledTimes(1)
  })
  it.each(
    (['result', 'run'] as const).flatMap(stage =>
      (['success', 'failure'] as const).map(outcome => ({ stage, outcome }))),
  )('keeps the latest history selection when an older $stage returns $outcome', async ({ stage, outcome }) => {
    localStorage.setItem('contoso:admin:conversation', 'test-conversation')
    mocks.api.conversation.mockResolvedValue({ id: 'test-conversation', state_version: 2, history: [
      { role: 'assistant', content: '第一份历史结果', run_id: 'first-run', result_id: fixture.id },
      { role: 'assistant', content: '第二份历史结果', run_id: 'second-run', result_id: 'latest-result' },
    ] })
    const oldResult = deferred<AnalysisResult>()
    const oldRun = deferred<Run>()
    const latestResult = { ...fixture, id: 'latest-result', rows: [{ month: '最新选择', sales_amount: '200.00' }] }
    mocks.api.result.mockImplementation((id: string) => id === 'latest-result' ? Promise.resolve(latestResult) : stage === 'result' ? oldResult.promise : Promise.resolve(fixture))
    mocks.api.run.mockImplementation((id: string) => id === 'first-run' ? oldRun.promise : Promise.resolve({ run_id: id, status: 'succeeded' }))
    wrapper = mount(App, { attachTo: document.body }); await flushPromises()
    // jsdom has no scrolling implementation; the selection itself is rendered normally.
    Object.defineProperty(wrapper.find('.query-result-anchor').element, 'scrollIntoView', { value: vi.fn() })
    const links = wrapper.findAll('.message-result-link')
    await links[0]!.trigger('click'); await flushPromises()
    await links[1]!.trigger('click'); await flushPromises()
    expect(wrapper.find('.query-result-anchor').text()).toContain('最新选择')
    const currentHtml = wrapper.html()
    const runReads = mocks.api.run.mock.calls.length
    if (stage === 'result') {
      if (outcome === 'success') oldResult.resolve(fixture)
      else oldResult.reject(new Error('不应显示的旧结果错误'))
    } else {
      if (outcome === 'success') oldRun.resolve({ run_id: 'first-run', status: 'succeeded' })
      else oldRun.reject(new Error('不应显示的旧运行错误'))
    }
    await flushPromises()
    expect(wrapper.html()).toBe(currentHtml)
    expect(mocks.api.run).toHaveBeenCalledTimes(runReads)
    expect(wrapper.find('.assistant-error').exists()).toBe(false)
  })
  it('does not publish a late creation response after unmount', async () => {
    await mountExistingConversation()
    const created = deferred<Conversation>()
    mocks.api.createConversation.mockReturnValueOnce(created.promise)
    await confirmReset()
    wrapper!.unmount(); wrapper = undefined
    created.resolve({ id: 'stale-conversation', state_version: 0 }); await flushPromises()
    expect(localStorage.getItem('contoso:admin:conversation')).toBe('old-conversation')
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
  it('shows a visible reason beside a disabled waterfall button, including compact charts', async () => {
    wrapper = mount(QueryResult, { props: { result: fixture, compact: true } })
    await flushPromises()
    const waterfall = button('瀑布')
    expect(waterfall.attributes('disabled')).toBeDefined()
    expect(waterfall.attributes('aria-label')).toBe('瀑布')
    const reason = wrapper.find('.waterfall-hint')
    expect(reason.exists()).toBe(true)
    expect(reason.text()).toContain('没有对比期数据')
    expect(waterfall.attributes('aria-describedby')).toBe(reason.attributes('id'))
    await waterfall.trigger('click')
    expect(button('表格').attributes('aria-pressed')).toBe('true')
    await wrapper.setProps({ result: { ...fixture, metrics: ['sales_amount', 'order_count'] } })
    expect(wrapper.find('.waterfall-hint').text()).toContain('单一指标')
  })
  it('enables waterfall for a contribution result and preserves it through table and bar switches', async () => {
    const contribution: AnalysisResult = {
      ...fixture, query: { ...fixture.query, analysis: 'contribution', group_by: ['category'] },
      columns: [{ key: 'category', label: '类别', kind: 'dimension' }, { key: 'sales_amount', label: '销售额', kind: 'money' }],
      rows: [{ category: 'A', sales_amount: '100', delta_sales_amount: '-20' }],
      comparison_totals: { sales_amount: '120' }, chart: { type: 'bar', x: 'category' },
    }
    const original = JSON.stringify(contribution)
    wrapper = mount(QueryResult, { props: { result: contribution } })
    await flushPromises()
    expect(button('瀑布').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.waterfall-hint').exists()).toBe(false)
    for (const label of ['瀑布', '表格', '柱状', '瀑布']) {
      await button(label).trigger('click'); await flushPromises()
      expect(button(label).attributes('aria-pressed')).toBe('true')
      expect(wrapper.find('table').exists()).toBe(label === '表格')
    }
    await wrapper.setProps({ presentationChart: 'table' })
    await wrapper.setProps({ presentationChart: 'waterfall' }); await flushPromises()
    expect(button('瀑布').attributes('aria-pressed')).toBe('true')
    expect(JSON.stringify(contribution)).toBe(original)
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
  it('hydrates a sparse synchronous display-only response before showing its result, CSV and budget', async () => {
    const monthly: AnalysisResult = {
      ...fixture,
      query: { ...fixture.query, metrics: ['sales_amount', 'order_count'] },
      metrics: ['sales_amount', 'order_count'],
      columns: [...fixture.columns, { key: 'order_count', label: '订单数', kind: 'count' }],
      rows: Array.from({ length: 12 }, (_, index) => ({ month: `2025-${String(index + 1).padStart(2, '0')}`, sales_amount: '100.00', order_count: '2' })),
      totals: { sales_amount: '1200.00', order_count: '24' },
      chart: { type: 'line', x: 'month' },
    }
    const original = JSON.stringify(monthly)
    const runBudget = { spent_rmb: '0.037976', reserved_rmb: '2.113536', exposure_rmb: '2.151512', cap_rmb: '4.8', remaining_rmb: '2.648488' }
    mocks.api.run.mockResolvedValue({ run_id: 'test-run', status: 'succeeded', result: monthly, result_id: monthly.id, budget: runBudget })
    wrapper = mount(App, { attachTo: document.body }); await flushPromises()
    await wrapper.find('#query-input').setValue('2025年销售额和订单数按月看')
    await wrapper.find('.composer').trigger('submit'); await flushPromises()
    expect(wrapper.find('.query-result-anchor .result-card').exists()).toBe(true)
    expect(wrapper.find('.assistant-footer').text()).toContain('占用 ¥2.15 / ¥4.80')

    // The POST response is only an acknowledgement, even when the run already succeeded.
    mocks.api.startRun.mockResolvedValue({ run_id: 'display-run', status: 'succeeded', state_version: 2 })
    let finishHydration!: (run: Run) => void
    mocks.api.run.mockImplementationOnce(() => new Promise<Run>(resolve => { finishHydration = resolve }))
    mocks.api.download.mockResolvedValue(undefined)
    await wrapper.find('#query-input').setValue('换成表格')
    await wrapper.find('.composer').trigger('submit'); await flushPromises()

    expect(mocks.api.run).toHaveBeenLastCalledWith('display-run')
    expect(wrapper.find('#query-input').attributes('disabled')).toBeDefined()
    finishHydration({ run_id: 'display-run', status: 'succeeded', result: monthly, result_id: monthly.id, budget: runBudget, presentation: { chart_type: 'table', reused_result_id: monthly.id } })
    await flushPromises()
    const result = wrapper.find('.query-result-anchor .result-card')
    expect(result.findAll('button').find(item => item.text() === '表格')!.attributes('aria-pressed')).toBe('true')
    expect(result.findAll('tbody tr')).toHaveLength(12)
    expect(result.find('thead').text()).toContain('订单数')
    expect(wrapper.findComponent(QueryResult).props('result')).toEqual(monthly)
    expect(wrapper.find('.assistant-footer').text()).toContain('占用 ¥2.15 / ¥4.80')
    expect(wrapper.find('#query-input').attributes('disabled')).toBeUndefined()
    expect(mocks.api.startRun).toHaveBeenCalledTimes(2)
    await result.findAll('button').find(item => item.text() === '导出 CSV')!.trigger('click'); await flushPromises()
    expect(mocks.api.download).toHaveBeenCalledWith(monthly.id)
    expect(JSON.stringify(monthly)).toBe(original)
  })

})
