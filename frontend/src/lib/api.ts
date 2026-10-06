import type { AnalysisResult, Catalog, Conversation, Dashboard, Run, User } from '../types'
export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) { super(message); this.name = 'ApiError' }
}
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, { ...options, credentials: 'same-origin', headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('暂时无法连接服务，请检查后端是否已启动，然后重试', 0, 'NETWORK_ERROR')
  }
  if (!response.ok) {
    let payload: { detail?: unknown; message?: string; error_code?: string } = {}
    try { payload = await response.json() } catch { /* HTTP errors can have no JSON body. */ }
    const detail = typeof payload.detail === 'object' && payload.detail !== null ? payload.detail as Record<string, unknown> : null
    const message = payload.message || (typeof payload.detail === 'string' ? payload.detail : typeof detail?.message === 'string' ? detail.message : response.status === 401 ? '登录已过期，请重新登录' : `请求未完成（${response.status}），请重试`)
    throw new ApiError(message, response.status, payload.error_code || (typeof detail?.code === 'string' ? detail.code : undefined))
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
const post = <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
export const api = {
  login: (username: string, password: string) => post<User | { user: User }>('/auth/login', { username, password }),
  logout: () => post<void>('/auth/logout'),
  me: () => request<User | { user: User }>('/me'),
  catalog: () => request<Catalog>('/catalog'),
  dashboard: (start: string, end: string, signal?: AbortSignal) => request<Dashboard>(`/dashboard?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`, { signal }),
  createConversation: () => post<Conversation>('/conversations'),
  conversation: (id: string) => request<Conversation>(`/conversations/${encodeURIComponent(id)}`),
  startRun: (id: string, message: string, stateVersion: number, clientId: string) => post<Run>(`/conversations/${encodeURIComponent(id)}/runs`, { message, client_request_id: clientId, expected_state_version: stateVersion }),
  run: (id: string) => request<Run>(`/runs/${encodeURIComponent(id)}`),
  cancel: (id: string) => post<Run>(`/runs/${encodeURIComponent(id)}/cancel`),
  result: (id: string) => request<AnalysisResult>(`/results/${encodeURIComponent(id)}`),
  async download(id: string): Promise<void> {
    const response = await fetch(`/api/results/${encodeURIComponent(id)}/csv`, { credentials: 'same-origin' })
    if (!response.ok) throw new ApiError('CSV 下载失败，请检查当前登录和结果是否仍有效', response.status)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `contoso-${id.slice(0, 12)}.csv`
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 3000)
  },
}
export function unwrapUser(value: User | { user: User }): User { return 'user' in value ? value.user : value }
