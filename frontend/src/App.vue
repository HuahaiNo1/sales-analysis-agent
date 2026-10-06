<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Activity, ArrowDownToLine, ArrowRight, BarChart3, BookOpen, Check, ChevronRight, Database, History, LayoutDashboard, LoaderCircle, LogOut, MessageSquareText, PanelLeftClose, Plus, RefreshCw, Send, ShieldCheck, Sparkles, Square, X, CircleAlert, CircleHelp, ArrowUpRight, LockKeyhole, FileText } from '@lucide/vue'
import type { AnalysisResult, Catalog, ChartType, Conversation, ConversationMessage, Dashboard, Run, User } from './types'
import { api, ApiError, unwrapUser } from './lib/api'
import { exclusiveEnd, formatValue, METRICS, periodLabel, statusLabel, TERMINAL_STATUSES } from './lib/format'
import MetricCard from './components/MetricCard.vue'
import QueryResult from './components/QueryResult.vue'
const user = ref<User | null>(null)
const booting = ref(true)
const loginBusy = ref(false)
const loginError = ref('')
const username = ref('admin')
const password = ref('demo123')
const catalog = ref<Catalog>({})
const page = ref<'overview' | 'history' | 'definitions' | 'dataset'>('overview')
const sidebarOpen = ref(false)
const dashboard = ref<Dashboard | null>(null)
const dashboardBusy = ref(false)
const dashboardError = ref('')
const dateStart = ref('2025-01-01')
const dateEnd = ref('2025-12-31')
const appliedPeriod = ref({ start: '2025-01-01', end: '2026-01-01' })
const conversation = ref<Conversation | null>(null)
const messages = ref<ConversationMessage[]>([])
const currentResult = ref<AnalysisResult | null>(null)
const currentPresentation = ref<ChartType | null>(null)
const activeRun = ref<Run | null>(null)
const submitting = ref(false)
const cancelling = ref(false)
const prompt = ref('')
const queryError = ref('')
const queryNotice = ref('')
const liveAnnouncement = ref('')
const input = ref<HTMLTextAreaElement>()
const chatBody = ref<HTMLElement>()
const querySection = ref<HTMLElement>()
const showResetConfirm = ref(false)
const recent = ref<Array<{ id: string; title: string; date: string }>>([])
let pollTimer: ReturnType<typeof setTimeout> | undefined
let generation = 0
let dashboardController: AbortController | undefined
let dashboardGeneration = 0
const busy = computed(() => submitting.value || Boolean(activeRun.value && !TERMINAL_STATUSES.has(activeRun.value.status)))
const modelMode = computed(() => catalog.value.model_mode || catalog.value.mode || 'unknown')
const isMock = computed(() => modelMode.value === 'mock')
const isLive = computed(() => modelMode.value === 'live' || modelMode.value === 'deepagents')
const scopeLabel = computed(() => user.value?.scope_label || catalog.value.scope_label || '当前授权门店')
const totals = computed(() => dashboard.value?.totals || dashboard.value?.metrics || dashboard.value?.result?.totals || {})
const budget = computed(() => activeRun.value?.budget || currentResult.value?.budget || catalog.value.budget)
const budgetExposure = computed(() => Number(budget.value?.exposure_rmb ?? (Number(budget.value?.spent_rmb || 0) + Number(budget.value?.reserved_rmb || 0))))
const messagesWithRun = computed(() => messages.value.filter(message => message.role === 'user' || message.role === 'assistant'))
const welcome = computed(() => !messagesWithRun.value.length)
const defaultSamples = [
  { icon: BarChart3, title: '看销售趋势', text: '2025年按月查看销售额和订单数', tag: '趋势' },
  { icon: Activity, title: '比较两个期间', text: '2025年9月与8月的销售额相比怎么样？', tag: '比较' },
  { icon: ArrowDownToLine, title: '拆解变化贡献', text: '2025年9月比8月销售额的变化，按类别拆解贡献，用瀑布图展示', tag: '贡献' },
]
const followups = ['按门店看', '只看前十', '换成柱状图']
function makeDashboardResult(value: Dashboard['trend'], title: 'trend' | 'categories'): AnalysisResult | null {
  if (!value) return null
  if (!Array.isArray(value)) return value
  if (!value.length) return null
  const dimension = title === 'trend' ? 'month' : 'category'
  const first = value[0]!
  const x = Object.keys(first).find(key => !METRICS.some(metric => metric.id === key)) || dimension
  return { id: String(dashboard.value?.result_id || `dashboard-${title}`), columns: [{ key: x, label: title === 'trend' ? '月份' : '类别', kind: 'string' }, { key: 'sales_amount', label: '销售额', kind: 'currency' }], rows: value, metrics: ['sales_amount'], totals: totals.value, period: appliedPeriod.value, chart: { type: title === 'trend' ? 'line' : 'bar', x }, metadata: { ...dashboard.value?.metadata, simulated: true, currency: 'USD', scope_label: scopeLabel.value } }
}
const dashboardTrend = computed(() => makeDashboardResult(dashboard.value?.trend || dashboard.value?.monthly, 'trend') || dashboard.value?.result || null)
function safeStorageGet(key: string): string | null { try { return localStorage.getItem(key) } catch { return null } }
function safeStorageSet(key: string, value: string) { try { localStorage.setItem(key, value) } catch { /* In private mode, server-side state still works during this session. */ } }
function storageKey(suffix: string) { return `contoso:${user.value?.username}:${suffix}` }
function hydrateRecent() {
  try { const parsed: unknown = JSON.parse(safeStorageGet(storageKey('recent')) || '[]'); recent.value = Array.isArray(parsed) ? parsed.filter(item => item && typeof item.id === 'string' && typeof item.title === 'string').slice(0, 15) : [] } catch { recent.value = [] }
}
function rememberConversation(title: string) {
  if (!conversation.value) return
  const item = { id: conversation.value.id, title: title.slice(0, 60), date: new Date().toISOString() }
  recent.value = [item, ...recent.value.filter(entry => entry.id !== item.id)].slice(0, 15)
  safeStorageSet(storageKey('recent'), JSON.stringify(recent.value))
  safeStorageSet(storageKey('conversation'), item.id)
}
function scrollChat() { void nextTick(() => { if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight }) }
function setPage(target: typeof page.value) { page.value = target; sidebarOpen.value = false }
function selectAccount(value: 'admin' | 'analyst') { username.value = value; password.value = 'demo123'; loginError.value = '' }
async function login() {
  if (loginBusy.value) return
  loginBusy.value = true; loginError.value = ''
  try { await api.login(username.value.trim(), password.value); user.value = unwrapUser(await api.me()); password.value = ''; await initialize() } catch (error) { loginError.value = error instanceof Error ? error.message : '登录失败，请重试' } finally { loginBusy.value = false }
}
async function initialize() {
  queryError.value = ''; queryNotice.value = ''; page.value = 'overview'; hydrateRecent()
  try { catalog.value = await api.catalog() } catch (error) { queryNotice.value = error instanceof Error ? error.message : '无法读取数据目录' }
  void loadDashboard()
  const saved = safeStorageGet(storageKey('conversation'))
  if (saved) {
    try { await loadConversation(saved, false); return } catch { /* An expired or inaccessible conversation is replaced explicitly. */ }
  }
  await newConversation(false)
}
async function loadDashboard() {
  if (!dateStart.value || !dateEnd.value || dateStart.value > dateEnd.value) { dashboardError.value = '请选择有效的起止日期，开始日期不能晚于结束日期'; return }
  if (new Date(dateEnd.value).getTime() - new Date(dateStart.value).getTime() >= 366 * 86400000) { dashboardError.value = '每次查询最多覆盖 366 天，请缩小日期范围'; return }
  dashboardController?.abort(); dashboardController = new AbortController()
  const requestGeneration = ++dashboardGeneration
  dashboardBusy.value = true; dashboardError.value = ''
  const requestedPeriod = { start: dateStart.value, end: exclusiveEnd(dateEnd.value) }
  try {
    const data = await api.dashboard(requestedPeriod.start, requestedPeriod.end, dashboardController.signal)
    if (requestGeneration !== dashboardGeneration) return
    dashboard.value = data; appliedPeriod.value = requestedPeriod
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return
    if (requestGeneration !== dashboardGeneration) return
    dashboardError.value = error instanceof Error ? error.message : '概览加载失败'; dashboard.value = null
  } finally { if (requestGeneration === dashboardGeneration) dashboardBusy.value = false }
}
function selectYear(year: string) { dateStart.value = `${year}-01-01`; dateEnd.value = `${year}-12-31`; void loadDashboard() }
async function newConversation(ask = true) {
  if (busy.value) return
  if (ask && messages.value.length) { showResetConfirm.value = true; return }
  showResetConfirm.value = false; generation++; clearTimeout(pollTimer)
  try { conversation.value = await api.createConversation(); conversation.value.id ||= conversation.value.conversation_id!; messages.value = []; currentResult.value = null; currentPresentation.value = null; activeRun.value = null; queryError.value = ''; queryNotice.value = ''; prompt.value = ''; safeStorageSet(storageKey('conversation'), conversation.value.id); void nextTick(() => input.value?.focus()) } catch (error) { queryError.value = error instanceof Error ? error.message : '新建会话失败' }
}
async function loadConversation(id: string, changePage = true) {
  if (busy.value) return
  generation++; clearTimeout(pollTimer)
  const data = await api.conversation(id)
  conversation.value = { ...data, id: data.id || data.conversation_id || id }
  messages.value = data.messages || data.history || []
  currentResult.value = null; currentPresentation.value = null; activeRun.value = null; queryError.value = ''; queryNotice.value = ''
  safeStorageSet(storageKey('conversation'), id)
  if (changePage) setPage('overview')
  const runId = data.current_run_id || data.latest_run_id
  if (runId) {
    activeRun.value = await api.run(runId)
    if (!TERMINAL_STATUSES.has(activeRun.value.status)) void pollRun(runId, generation)
    else if (['succeeded', 'no_data'].includes(activeRun.value.status)) {
      if (activeRun.value.result) currentResult.value = activeRun.value.result
      else if (activeRun.value.result_id) currentResult.value = await api.result(activeRun.value.result_id)
      applyRunPresentation(activeRun.value)
    }
  } else if (data.last_result_id) {
    try { currentResult.value = await api.result(data.last_result_id) } catch (error) { queryNotice.value = error instanceof Error ? error.message : '历史结果无法读取' }
  }
  scrollChat()
}
async function openConversation(id: string) { try { await loadConversation(id) } catch (error) { queryError.value = error instanceof Error ? error.message : '读取会话失败' } }
async function submitQuery(text = prompt.value) {
  const message = text.trim()
  if (!message || busy.value) return
  if (message.length > 2000) { queryError.value = '问题最多 2,000 个字符，请简化后再试'; return }
  queryError.value = ''; queryNotice.value = ''; currentResult.value = null; currentPresentation.value = null; activeRun.value = null
  submitting.value = true
  const currentGeneration = ++generation
  clearTimeout(pollTimer)
  try {
    if (!conversation.value) { conversation.value = await api.createConversation(); conversation.value.id ||= conversation.value.conversation_id! }
    const result = await api.startRun(conversation.value.id, message, conversation.value.state_version, crypto.randomUUID())
    if (currentGeneration !== generation) return
    activeRun.value = { ...result, run_id: result.run_id || result.id! }
    if (result.state_version !== undefined) conversation.value.state_version = result.state_version
    messages.value.push({ role: 'user', content: message, run_id: activeRun.value.run_id, created_at: new Date().toISOString() })
    rememberConversation(messages.value.find(item => item.role === 'user')?.content || message)
    prompt.value = ''; scrollChat(); liveAnnouncement.value = '问题已提交，正在查询'
    if (TERMINAL_STATUSES.has(result.status)) await finishRun(activeRun.value, currentGeneration)
    else void pollRun(activeRun.value.run_id, currentGeneration)
  } catch (error) {
    if (error instanceof ApiError && error.status === 409 && conversation.value) { try { const updated = await api.conversation(conversation.value.id); conversation.value.state_version = updated.state_version } catch { /* The next retry will surface connection errors. */ } }
    queryError.value = error instanceof Error ? error.message : '请求未完成，请重试'
  } finally { submitting.value = false }
}
async function pollRun(runId: string, currentGeneration: number, failures = 0) {
  if (currentGeneration !== generation) return
  try {
    const run = await api.run(runId)
    if (currentGeneration !== generation) return
    activeRun.value = { ...run, run_id: run.run_id || run.id || runId }; queryNotice.value = ''
    if (TERMINAL_STATUSES.has(run.status)) { await finishRun(activeRun.value, currentGeneration); return }
    pollTimer = setTimeout(() => void pollRun(runId, currentGeneration), 700)
  } catch (error) {
    if (currentGeneration !== generation) return
    if (error instanceof ApiError && [401, 403, 404, 410].includes(error.status)) { queryError.value = error.message; activeRun.value = { run_id: runId, status: 'failed', message: error.message }; cancelling.value = false; return }
    queryNotice.value = '连接暂时中断，正在重新获取服务端状态。你可以稍后刷新恢复。'
    pollTimer = setTimeout(() => void pollRun(runId, currentGeneration, failures + 1), Math.min(1500 * (failures + 1), 10000))
  }
}
function applyRunPresentation(run: Run) {
  currentPresentation.value = run.presentation?.reused_result_id === currentResult.value?.id ? run.presentation?.chart_type || null : null
}
async function finishRun(run: Run, currentGeneration: number) {
  if (currentGeneration !== generation) return
  cancelling.value = false; liveAnnouncement.value = statusLabel(run.status)
  if (run.result) currentResult.value = run.result
  else if (run.result_id && ['succeeded', 'no_data'].includes(run.status)) { try { const result = await api.result(run.result_id); if (currentGeneration === generation) currentResult.value = result } catch (error) { queryError.value = error instanceof Error ? error.message : '结果读取失败' } }
  if (currentGeneration !== generation) return
  applyRunPresentation(run)
  if (conversation.value) {
    try { const updated = await api.conversation(conversation.value.id); if (currentGeneration !== generation) return; conversation.value.state_version = updated.state_version; if (updated.messages || updated.history) messages.value = updated.messages || updated.history || [] } catch { if (run.state_version !== undefined) conversation.value.state_version = run.state_version }
  }
  if (!messages.value.some(message => message.role === 'assistant' && message.run_id === run.run_id)) messages.value.push({ role: 'assistant', content: run.message || run.clarification || (run.status === 'succeeded' ? '分析已完成，可以查看图表和数据明细。你也可以继续追问。' : run.status === 'cancelled' ? '已停止本次分析，原有会话仍保留。' : run.status === 'no_data' ? '这个范围没有匹配的数据，请调整日期或筛选条件。' : '请补充问题后重试。'), run_id: run.run_id, result_id: run.result_id, status: run.status })
  if (run.status === 'failed') queryError.value = run.message || '本次分析未完成，请重试'
  scrollChat()
}
async function cancelRun() {
  if (!activeRun.value || cancelling.value) return
  cancelling.value = true; queryError.value = ''
  try { const runId = activeRun.value.run_id; const run = await api.cancel(runId); if (run.status && TERMINAL_STATUSES.has(run.status)) { activeRun.value = await api.run(runId); await finishRun(activeRun.value, generation) } } catch (error) { cancelling.value = false; queryError.value = error instanceof Error ? error.message : '停止请求未确认，请稍后重试' }
}
async function showHistoryResult(message: ConversationMessage) {
  if (!message.result_id || busy.value) return
  try {
    const result = await api.result(message.result_id)
    const run = message.run_id ? await api.run(message.run_id) : null
    currentResult.value = result
    currentPresentation.value = null
    if (run) applyRunPresentation(run)
    setPage('overview'); void nextTick(() => querySection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  } catch (error) { queryError.value = error instanceof Error ? error.message : '结果读取失败' }
}
async function logout() {
  if (busy.value) return
  try { await api.logout(); generation++; clearTimeout(pollTimer); dashboardController?.abort(); dashboardGeneration++; user.value = null; conversation.value = null; currentResult.value = null; currentPresentation.value = null; messages.value = []; dashboard.value = null; catalog.value = {}; username.value = 'admin'; password.value = 'demo123' } catch (error) { queryError.value = error instanceof Error ? error.message : '退出失败' }
}
function useSample(text: string) { prompt.value = text; void nextTick(() => input.value?.focus()) }
function handleKey(event: KeyboardEvent) { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submitQuery() } }
onMounted(async () => {
  try { user.value = unwrapUser(await api.me()); await initialize() } catch (error) { if (!(error instanceof ApiError && error.status === 401)) loginError.value = error instanceof Error ? error.message : '服务连接失败' } finally { booting.value = false }
})
onBeforeUnmount(() => { generation++; clearTimeout(pollTimer); dashboardController?.abort() })
</script>

<template>
  <div v-if="booting" class="boot-screen"><div class="brand-symbol"><BarChart3 :size="23" /></div><LoaderCircle class="spin" :size="22" /><p>正在连接分析工作台</p></div>
  <main v-else-if="!user" class="login-layout">
    <section class="login-story"><a class="brand" href="#"><span class="brand-symbol"><BarChart3 :size="23" /></span><span>Contoso<span class="brand-sub">INSIGHT</span></span></a><div class="login-story-body"><span class="capsule"><span class="tiny-dot"></span>SALES INTELLIGENCE</span><h1>让每个问题，<br />找到可靠的答案。</h1><p>用自然语言探索销售数据。<br />从趋势，到对比，再到变化的每一份贡献。</p><div class="login-chart" aria-hidden="true"><div class="login-chart-heading"><span>从数据，到洞察</span><span>2023 — 2025</span></div><div class="decor-bars"><i v-for="height in [32, 42, 37, 51, 45, 62, 55, 74, 67, 79, 72, 94]" :key="height" :style="{ height: `${height}%` }"></i></div><div class="login-chart-axis"><span>JAN</span><span>JUN</span><span>DEC</span></div><small>装饰示意图，不代表实际查询结果</small></div></div><div class="login-disclaimer"><ShieldCheck :size="16" />受控指标 · 服务端权限 · 可追溯结果</div></section>
    <section class="login-form-section"><div class="login-form-wrap"><span class="eyebrow">WELCOME TO CONTOSO INSIGHT</span><h2>进入分析工作台</h2><p class="login-subtitle">选择一个演示身份，开始探索模拟数据</p><div class="demo-account-picker"><button :class="{ selected: username === 'admin' }" @click="selectAccount('admin')"><ShieldCheck :size="19" /><span><strong>管理员</strong><small>全部授权门店</small></span><Check v-if="username === 'admin'" :size="16" /></button><button :class="{ selected: username === 'analyst' }" @click="selectAccount('analyst')"><BarChart3 :size="19" /><span><strong>区域分析师</strong><small>限定门店范围</small></span><Check v-if="username === 'analyst'" :size="16" /></button></div><form @submit.prevent="login"><label class="form-label" for="username">账号</label><input id="username" v-model="username" autocomplete="username" required placeholder="admin 或 analyst" /><label class="form-label" for="password">密码</label><input id="password" v-model="password" type="password" autocomplete="current-password" required /><p class="field-hint">演示账号 admin / analyst，密码均为 demo123</p><div v-if="loginError" class="alert alert-error" role="alert"><CircleAlert :size="16" />{{ loginError }}</div><button class="button button-primary login-submit" :disabled="loginBusy" :aria-busy="loginBusy"><LoaderCircle v-if="loginBusy" class="spin" :size="17" />{{ loginBusy ? '正在登录' : '进入工作台' }}<ArrowRight v-if="!loginBusy" :size="17" /></button></form><div class="login-data-note"><Database :size="18" /><p>仅使用 Contoso 合成零售数据<br /><span>所有金额以 USD 展示，不代表真实企业经营情况</span></p></div></div><div class="login-version">Contoso Insight <span>Portfolio Edition · v1.0</span></div></section>
  </main>
  <div v-else class="app-shell">
    <div v-if="sidebarOpen" class="sidebar-backdrop" @click="sidebarOpen = false"></div>
    <aside class="sidebar" :class="{ open: sidebarOpen }"><a class="brand" href="#" @click.prevent="setPage('overview')"><span class="brand-symbol"><BarChart3 :size="21" /></span><span>Contoso<span class="brand-sub">INSIGHT</span></span></a><div class="workspace-label">分析空间 <span>DEMO</span></div><nav aria-label="主导航"><button :class="{ active: page === 'overview' }" @click="setPage('overview')"><LayoutDashboard :size="18" />销售概览<span class="nav-active-dot" v-if="page === 'overview'"></span></button><button :class="{ active: page === 'history' }" @click="setPage('history')"><History :size="18" />分析记录<span v-if="recent.length" class="nav-count">{{ recent.length }}</span></button><button :class="{ active: page === 'definitions' }" @click="setPage('definitions')"><BookOpen :size="18" />指标口径</button><button :class="{ active: page === 'dataset' }" @click="setPage('dataset')"><Database :size="18" />数据与权限</button></nav><div class="sidebar-session"><div class="sidebar-section-title">最近的分析<button class="icon-button" :disabled="busy" @click="newConversation()" aria-label="新建分析"><Plus :size="15" /></button></div><button v-for="item in recent.slice(0, 4)" :key="item.id" class="recent-link" :title="item.title" :disabled="busy" @click="openConversation(item.id)"><MessageSquareText :size="14" /><span>{{ item.title }}</span></button><span v-if="!recent.length" class="sidebar-empty">还没有分析记录</span></div><div class="sidebar-bottom"><div class="dataset-card"><span class="tiny-dot"></span><div><strong>Contoso 模拟数据</strong><small>2023–2025 · USD</small></div><Database :size="16" /></div><div class="profile"><div class="avatar">{{ username === 'analyst' || user.username === 'analyst' ? 'A' : 'C' }}</div><div><strong>{{ user.display_name || (user.username === 'admin' ? '演示管理员' : '区域分析师') }}</strong><span>{{ scopeLabel }}</span></div><button class="icon-button" @click="logout" :disabled="busy" :title="busy ? '请先停止或等待当前分析完成' : '退出登录'" aria-label="退出登录"><LogOut :size="16" /></button></div></div></aside>
    <div class="main-shell"><header class="topbar"><div class="breadcrumb"><button class="icon-button menu-toggle" aria-label="打开菜单" @click="sidebarOpen = !sidebarOpen"><PanelLeftClose :size="21" /></button><span>工作台</span><ChevronRight :size="13" /><strong>{{ ({ overview: '销售概览', history: '分析记录', definitions: '指标口径', dataset: '数据与权限' })[page] }}</strong></div><div class="topbar-status"><span class="mode-badge" :class="{ live: isLive }"><span class="tiny-dot"></span>{{ isLive ? 'LIVE · 模型模式' : modelMode === 'unknown' ? '模式待确认' : 'MOCK · 离线模型' }}</span><span class="topbar-divider"></span><span class="currency-label">USD</span><button class="icon-button" aria-label="查看数据与权限说明" @click="setPage('dataset')"><CircleHelp :size="18" /></button></div></header>
    <main class="main-content">
      <template v-if="page === 'overview'">
        <div class="page-heading"><div><div class="eyebrow">SALES OVERVIEW</div><h1>销售分析工作台<span class="demo-pill">模拟数据</span></h1><p>看清销售表现，让下一步分析有据可循</p></div><div class="scope-chip"><ShieldCheck :size="15" />{{ scopeLabel }}</div></div>
        <section class="filters-panel" aria-label="概览日期筛选"><div class="year-tabs" role="group" aria-label="选择年份"><button v-for="year in ['2023', '2024', '2025']" :key="year" :class="{ selected: dateStart === `${year}-01-01` && dateEnd === `${year}-12-31` }" @click="selectYear(year)">{{ year }}年</button></div><div class="date-inputs"><label><span class="sr-only">开始日期</span><input v-model="dateStart" type="date" min="2023-01-01" max="2025-12-31" aria-label="开始日期" /></label><span>至</span><label><span class="sr-only">结束日期（含当日）</span><input v-model="dateEnd" type="date" min="2023-01-01" max="2025-12-31" aria-label="结束日期" /></label><button class="button button-outline button-small" :disabled="dashboardBusy" @click="loadDashboard"><RefreshCw :size="13" :class="{ spin: dashboardBusy }" />应用</button></div></section>
        <div v-if="dashboardError" class="alert alert-error" role="alert"><CircleAlert :size="16" /><span>{{ dashboardError }}</span><button class="text-button" @click="loadDashboard">重试</button></div>
        <section class="metrics-grid" aria-label="关键销售指标"><MetricCard v-for="metric in METRICS" :key="metric.id" :metric="metric.id" :value="totals[metric.id]" :loading="dashboardBusy" /></section>
        <div class="section-caption"><span>概览期间：{{ periodLabel(appliedPeriod) }}</span><span><LockKeyhole :size="11" />仅汇总当前账号获授权的数据</span></div>
        <div class="workspace-grid"><div class="analysis-column">
          <QueryResult v-if="dashboardTrend && !dashboardBusy" :result="dashboardTrend" title="销售趋势" compact :exportable="false" />
          <section v-else class="panel dashboard-placeholder"><div class="panel-heading"><div><h2>销售趋势</h2><p>{{ dashboardBusy ? '正在读取授权范围内的汇总数据' : '等待后端查询结果' }}</p></div><span class="subtle-tag">按月</span></div><div v-if="dashboardBusy" class="chart-loading"><LoaderCircle class="spin" :size="25" /><span>正在加载销售概览</span></div><div v-else class="empty-state"><BarChart3 :size="32" /><h3>销售概览尚未就绪</h3><p>连接后端后会显示实际聚合结果，不使用示例数字填充。</p><button class="button button-outline button-small" @click="loadDashboard">重新加载</button></div></section>
          <section class="panel quick-analysis"><div class="panel-heading"><div><h2>从一个好问题开始</h2><p>选一个方向，继续深入你的数据</p></div><Sparkles :size="18" class="teal" /></div><div class="sample-grid"><button v-for="sample in defaultSamples" :key="sample.title" @click="useSample(sample.text)" :disabled="busy"><span class="sample-icon"><component :is="sample.icon" :size="18" /></span><strong>{{ sample.title }}</strong><span>{{ sample.text }}</span><span class="sample-footer">{{ sample.tag }}分析<ArrowUpRight :size="14" /></span></button></div></section>
          <div ref="querySection" class="query-result-anchor"><QueryResult v-if="currentResult" :result="currentResult" :presentation-chart="currentPresentation" title="本次查询结果" /><div v-else-if="busy" class="panel analyzing-result" role="status"><div class="analyzing-orbit"><Sparkles :size="24" /></div><h3>{{ statusLabel(activeRun?.status || 'queued') }}</h3><p>先校验口径和权限，再计算并解释结果</p><div class="loading-line"></div></div></div>
          <div class="integrity-note"><ShieldCheck :size="15" /><span>结果来自受控查询。模拟数据中的变化不代表真实市场趋势，也不能单独证明业务原因。</span></div>
        </div>
        <aside class="assistant-panel panel"><div class="assistant-heading"><span class="assistant-logo"><Sparkles :size="18" /></span><div><h2>销售分析助手</h2><span>{{ isLive ? 'Deep Agents · 受控查询' : isMock ? 'Deep Agents · 离线确定性模型' : '正在确认执行模式' }}</span></div><button class="icon-button" title="新建会话并重置上下文" aria-label="新建会话并重置上下文" :disabled="busy" @click="newConversation()"><Plus :size="18" /></button></div><div class="assistant-context"><span class="tiny-dot"></span>{{ busy ? statusLabel(activeRun?.status || 'queued') : '可连续追问' }}<span>{{ conversation ? `会话 ${conversation.id.slice(0, 6)}` : '连接中' }}</span></div><div ref="chatBody" class="chat-body" role="log" aria-label="分析对话"><div v-if="welcome" class="assistant-welcome"><span class="welcome-mark"><Sparkles :size="23" /></span><h3>你好，想了解哪些销售表现？</h3><p>你可以问趋势、期间对比和变化贡献。<br />我会展示采用的口径与数据来源。</p><button v-for="sample in defaultSamples" :key="sample.title" @click="useSample(sample.text)" :disabled="busy">{{ sample.title }}<ChevronRight :size="14" /></button><div class="welcome-tip"><CircleHelp :size="15" /><span>当前快照覆盖 2023–2025 年<br />建议在问题里写明日期</span></div></div><template v-for="(message, index) in messagesWithRun" :key="`${message.run_id || index}-${message.role}`"><div class="chat-message" :class="message.role"><div class="message-avatar" v-if="message.role === 'assistant'"><Sparkles :size="13" /></div><div class="message-content"><p>{{ message.content }}</p><button v-if="message.result_id" class="message-result-link" @click="showHistoryResult(message)" :disabled="busy"><BarChart3 :size="13" />查看这次结果<ArrowUpRight :size="12" /></button><span v-if="message.status" class="message-status">{{ statusLabel(message.status) }}</span></div></div></template><div v-if="busy" class="chat-message assistant"><div class="message-avatar"><Sparkles :size="13" /></div><div class="message-content working-message"><LoaderCircle :size="14" class="spin" />{{ cancelling ? '正在请求停止…' : statusLabel(activeRun?.status || 'queued') + '…' }}</div></div></div><div v-if="queryNotice" class="assistant-notice" role="status">{{ queryNotice }}</div><div v-if="queryError" class="assistant-error" role="alert"><CircleAlert :size="14" /><span>{{ queryError }}</span><button class="icon-button" aria-label="关闭错误提示" @click="queryError = ''"><X :size="13" /></button></div><div class="composer-wrap"><div v-if="!welcome && !busy" class="followup-chips"><button v-for="text in followups" :key="text" @click="useSample(text)">{{ text }}</button></div><form class="composer" @submit.prevent="submitQuery()"><label class="sr-only" for="query-input">向销售分析助手提问</label><textarea id="query-input" ref="input" v-model="prompt" placeholder="例如：2025 年销售额按月怎么变化？" rows="3" maxlength="2000" :disabled="busy" @keydown="handleKey"></textarea><div class="composer-bottom"><span>{{ prompt.length ? `${prompt.length}/2000` : 'Enter 发送 · Shift+Enter 换行' }}</span><button v-if="busy" type="button" class="stop-button" @click="cancelRun" :disabled="cancelling || !activeRun"><Square :size="12" fill="currentColor" />{{ cancelling ? '停止中' : '停止' }}</button><button v-else type="submit" class="send-button" :disabled="!prompt.trim() || !conversation" aria-label="发送问题"><Send :size="15" /></button></div></form><div class="assistant-footer"><LockKeyhole :size="11" />权限由服务端校验<span v-if="budget">占用 ¥{{ budgetExposure.toFixed(2) }} / ¥{{ Number(budget.cap_rmb || 0).toFixed(2) }}</span><span v-else>{{ isMock ? '外部模型费用 $0' : '费用以服务端记录为准' }}</span></div></div></aside></div>
      </template>
      <template v-else-if="page === 'history'"><div class="page-heading"><div><div class="eyebrow">ANALYSIS HISTORY</div><h1>分析记录</h1><p>回到一个问题，接着把它想清楚</p></div><button class="button button-primary" :disabled="busy" @click="setPage('overview'); newConversation()"><Plus :size="16" />新建分析</button></div><section class="panel history-panel"><div class="panel-heading"><div><h2>最近会话</h2><p>本浏览器保存会话索引；消息、权限和结果以服务端记录为准</p></div><History :size="19" class="muted" /></div><div v-if="!recent.length" class="empty-state"><MessageSquareText :size="30" /><h3>还没有分析记录</h3><p>问一个销售问题后，就能在这里继续会话</p></div><button v-for="item in recent" :key="item.id" class="history-row" :disabled="busy" @click="openConversation(item.id)"><span class="history-icon"><MessageSquareText :size="18" /></span><span class="history-row-title"><strong>{{ item.title }}</strong><small>{{ new Date(item.date).toLocaleString('zh-CN') }} · {{ item.id.slice(0, 8) }}</small></span><ChevronRight :size="17" /></button></section></template>
      <template v-else-if="page === 'definitions'"><div class="page-heading"><div><div class="eyebrow">METRIC DICTIONARY</div><h1>统一口径，可信分析</h1><p>固定五个指标。计算由程序执行，模型不能改写公式。</p></div><span class="scope-chip">USD · 订单日期</span></div><div class="definitions-grid"><article v-for="(metric, index) in METRICS" :key="metric.id" class="panel definition-card"><div><span class="definition-index">0{{ index + 1 }}</span><span class="subtle-tag">{{ metric.unit }}</span></div><h2>{{ metric.label }}</h2><span class="definition-key">{{ metric.id }}</span><p>{{ metric.definition }}</p></article><article class="panel definition-card definition-boundary"><div class="section-label"><ShieldCheck :size="18" />分析边界</div><h2>知道数字，也知道边界</h2><ul><li>仅分析获授权的模拟销售订单</li><li>不提供支付、实际退款或取消率</li><li>金额变化贡献不能证明业务因果</li><li>零分母变化率与平均值显示为不适用</li><li>每次查询最多两个指标、366 天</li></ul></article></div></template>
      <template v-else><div class="page-heading"><div><div class="eyebrow">DATA & ACCESS</div><h1>数据与权限</h1><p>透明展示数据来源、执行模式和访问范围</p></div><span class="demo-pill">模拟数据</span></div><div class="data-grid"><section class="panel data-section"><div class="panel-heading"><div><h2>Contoso 数据快照</h2><p>合成零售数据 · 非真实经营记录</p></div><Database :size="21" class="teal" /></div><dl><div><dt>数据来源</dt><dd>{{ catalog.dataset?.source || catalog.source || '以服务端发布的数据来源为准' }}</dd></div><div><dt>数据版本</dt><dd>{{ catalog.dataset?.version || catalog.dataset_version || '目录未提供' }}</dd></div><div><dt>覆盖期间</dt><dd>{{ catalog.coverage ? periodLabel(catalog.coverage) : catalog.date_coverage ? `${catalog.date_coverage.start} 至 ${catalog.date_coverage.end}` : '2023–2025（以查询校验为准）' }}</dd></div><div><dt>校验状态</dt><dd>{{ catalog.verified ? '服务端已发布校验通过的数据版本' : '未提供已验证状态' }}</dd></div><div><dt>币种</dt><dd>USD，不进行隐式换汇</dd></div><div v-if="catalog.orders || catalog.dataset?.order_count"><dt>快照订单数</dt><dd>{{ formatValue(catalog.orders || catalog.dataset?.order_count, 'order_count') }}</dd></div><div><dt>真实性说明</dt><dd>所有业务规律由生成配置形成，不能外推为真实市场结论。</dd></div></dl></section><section class="panel data-section"><div class="panel-heading"><div><h2>当前访问范围</h2><p>身份与权限由后端认证决定</p></div><ShieldCheck :size="21" class="teal" /></div><dl><div><dt>登录身份</dt><dd>{{ user.display_name || user.username }}（{{ user.username }}）</dd></div><div><dt>可见范围</dt><dd>{{ scopeLabel }}</dd></div><div><dt>可见门店</dt><dd>{{ catalog.stores?.map(store => store.label || store.name).join('、') || '由服务端应用账号映射后强制过滤' }}</dd></div><div><dt>CSV 下载</dt><dd>只导出本次获授权的聚合结果，不开放原始客户明细。</dd></div><div><dt>会话隔离</dt><dd>读取历史结果与下载时重新校验身份；知道结果 ID 不代表有权访问。</dd></div></dl></section><section class="panel data-section"><div class="panel-heading"><div><h2>执行模式与费用</h2><p>演示模式始终显式标示</p></div><Sparkles :size="21" class="teal" /></div><dl><div><dt>当前模式</dt><dd>{{ isLive ? 'LIVE：模型解析与受控工具执行' : modelMode === 'unknown' ? '尚未确认，请检查后端连接' : 'MOCK：Deep Agents 使用本地确定性模型，不调用外部模型' }}</dd></div><div><dt>查询约束</dt><dd>QuerySpec → 校验 → 固定模板 → 确定性聚合 → 图表与解释</dd></div><div><dt>模型费用</dt><dd>{{ budget ? `用量估算 ¥${Number(budget.spent_rmb || 0).toFixed(4)}；未决预留 ¥${Number(budget.reserved_rmb || 0).toFixed(4)}；合计占用 ¥${budgetExposure.toFixed(4)} / 上限 ¥${Number(budget.cap_rmb || 0).toFixed(2)}；剩余 ¥${Number(budget.remaining_rmb || 0).toFixed(4)}` : isMock ? '$0（离线模式）' : '以服务端预算记录为准' }}</dd></div><div><dt>密钥管理</dt><dd>前端不持有任何模型 API 密钥</dd></div></dl></section><section class="panel data-section data-note"><FileText :size="26" /><h2>一份可以复核的结果</h2><p>每个结果都保留期间、指标口径、数据版本和权限范围。切换图表复用原结果，CSV 使用相同的授权聚合。</p><p>正式展示前，请在后端确认数据生成与校验记录；未验证的数据不会在前端伪装成已验证统计。</p></section></div></template>
      <footer class="page-footer"><span>Contoso Insight · Portfolio Edition</span><span>模拟数据，仅用于分析演示</span></footer>
    </main></div>
    <div v-if="showResetConfirm" class="modal-backdrop" @click.self="showResetConfirm = false"><section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="reset-title"><span class="modal-icon"><MessageSquareText :size="25" /></span><h2 id="reset-title">开始一个新分析？</h2><p>新会话会清空当前业务筛选和追问上下文。已有会话仍可在分析记录中打开，账号权限不会改变。</p><div class="modal-actions"><button class="button button-outline" @click="showResetConfirm = false">保留当前会话</button><button class="button button-primary" @click="newConversation(false)">新建会话</button></div></section></div>
    <div class="sr-only" aria-live="polite">{{ liveAnnouncement }}</div>
  </div>
</template>
