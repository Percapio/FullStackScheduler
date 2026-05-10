import { apiClient } from './client'
import type { JobReadExpanded, RestoreConflictPreview, StagingRestoreAction } from './staging'
export type { JobReadExpanded }

export async function fetchShippingJobs(
  limit = 500,
): Promise<{ rows: JobReadExpanded[]; total: number }> {
  const resp = await apiClient.get<JobReadExpanded[]>('/api/jobs/shipping', {
    params: { limit, offset: 0 },
  })
  const total = Number(resp.headers['x-total-count'] ?? resp.data.length)
  return { rows: resp.data, total }
}

export async function discardShippingJob(jobId: number, reason: string): Promise<JobReadExpanded> {
  const resp = await apiClient.post<JobReadExpanded>(`/api/jobs/${jobId}/discard`, { reason })
  return resp.data
}

export async function fetchDiscardedJobs(
  limit = 50,
  offset = 0,
  search: string | null = null,
): Promise<{ rows: JobReadExpanded[]; total: number }> {
  const params: Record<string, unknown> = { limit, offset }
  if (search) params.search = search
  const resp = await apiClient.get<JobReadExpanded[]>('/api/jobs/discarded', { params })
  const total = Number(resp.headers['x-total-count'] ?? resp.data.length)
  return { rows: resp.data, total }
}

export async function fetchJobRestorePreview(
  jobId: number,
): Promise<RestoreConflictPreview> {
  const resp = await apiClient.get<RestoreConflictPreview>(
    `/api/jobs/${jobId}/restore-preview`,
  )
  return resp.data
}

export async function postRestoreJob(
  jobId: number,
  actions: StagingRestoreAction[] = [],
): Promise<JobReadExpanded> {
  const resp = await apiClient.post<JobReadExpanded>(
    `/api/jobs/${jobId}/restore`,
    { actions },
  )
  return resp.data
}
