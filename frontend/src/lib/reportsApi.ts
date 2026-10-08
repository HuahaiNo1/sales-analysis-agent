import type { CreateReportRequest, ReportAnnotations, ReportList, ReviewRequest, ReviewRun, SalesReport } from '../types/reviews'
import { ApiError, request } from './api'

const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) })
const reportPath = (id: string) => `/reports/${encodeURIComponent(id)}`

export const reportsApi = {
  startReview: (conversationId: string, message: string, stateVersion: number, clientId: string, review: ReviewRequest) => post<ReviewRun>(`/conversations/${encodeURIComponent(conversationId)}/runs`, { message, expected_state_version: stateVersion, client_request_id: clientId, review }),
  run: (id: string) => request<ReviewRun>(`/runs/${encodeURIComponent(id)}`),
  create: (body: CreateReportRequest) => post<SalesReport>('/reports', body),
  list: (deleted = false, offset = 0, signal?: AbortSignal) => request<ReportList>(`/reports?deleted=${deleted}&limit=30&offset=${offset}`, { signal }),
  get: (id: string, signal?: AbortSignal) => request<SalesReport>(reportPath(id), { signal }),
  update: (id: string, revision: number, annotations: ReportAnnotations) => request<SalesReport>(reportPath(id), { method: 'PATCH', body: JSON.stringify({ expected_revision: revision, ...annotations }) }),
  trash: (id: string, revision: number) => request<SalesReport>(`${reportPath(id)}?expected_revision=${revision}`, { method: 'DELETE' }),
  restore: (id: string, revision: number) => post<SalesReport>(`${reportPath(id)}/restore`, { expected_revision: revision }),
  async download(id: string, format: 'markdown' | 'html') {
    let response: Response
    try { response = await fetch(`/api${reportPath(id)}/export?format=${format}`, { credentials: 'same-origin' }) }
    catch { throw new ApiError('暂时无法连接服务，报告导出未完成，请重试', 0, 'NETWORK_ERROR') }
    if (!response.ok) {
      let message = '报告导出失败，请确认登录、报告状态与当前数据权限'
      try {
        const payload = await response.json()
        if (typeof payload.detail === 'string') message = payload.detail
        else if (typeof payload.detail?.message === 'string') message = payload.detail.message
      } catch { /* A non-JSON response still has an actionable fallback. */ }
      throw new ApiError(message, response.status)
    }
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a')
    anchor.href = url; anchor.download = `contoso-report-${id.slice(0, 12)}.${format === 'markdown' ? 'md' : 'html'}`
    document.body.append(anchor); anchor.click(); anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 3000)
  },
}
