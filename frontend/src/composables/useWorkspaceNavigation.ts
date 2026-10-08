import { onBeforeUnmount, ref } from 'vue'

export type WorkspacePage = 'overview' | 'review' | 'reports' | 'history' | 'definitions' | 'dataset'
interface Destination { page: WorkspacePage; reportId: string }
type Guard = (next: () => void, cancelled: () => void) => void
const PAGES = new Set<WorkspacePage>(['overview', 'review', 'reports', 'history', 'definitions', 'dataset'])
function parseHash(): Destination {
  const [rawPage, rawId] = location.hash.slice(1).split('/')
  const page = PAGES.has(rawPage as WorkspacePage) ? rawPage as WorkspacePage : 'overview'
  let reportId = ''
  try { reportId = page === 'reports' && rawId ? decodeURIComponent(rawId) : '' } catch { /* Ignore malformed navigation fragments. */ }
  return { page, reportId }
}
function fragment(destination: Destination) { return `#${destination.page}${destination.page === 'reports' && destination.reportId ? `/${encodeURIComponent(destination.reportId)}` : ''}` }

export function useWorkspaceNavigation(guard: Guard) {
  const initial = parseHash()
  const page = ref<WorkspacePage>(initial.page)
  const reportId = ref(initial.reportId)
  function apply(destination: Destination) { page.value = destination.page; reportId.value = destination.reportId }
  function navigate(target: WorkspacePage, id = '') {
    const destination = { page: target, reportId: target === 'reports' ? id : '' }
    if (page.value === destination.page && reportId.value === destination.reportId) return
    guard(() => { history.pushState(null, '', fragment(destination)); apply(destination) }, () => {})
  }
  function onHistory() {
    const destination = parseHash()
    if (destination.page === page.value && destination.reportId === reportId.value) return
    const previous = { page: page.value, reportId: reportId.value }
    guard(() => apply(destination), () => history.replaceState(null, '', fragment(previous)))
  }
  window.addEventListener('popstate', onHistory)
  window.addEventListener('hashchange', onHistory)
  onBeforeUnmount(() => { window.removeEventListener('popstate', onHistory); window.removeEventListener('hashchange', onHistory) })
  return { page, reportId, navigate }
}
