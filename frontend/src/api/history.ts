import { apiClient } from './client'
import type { JobReadExpanded } from './staging'
export type { JobReadExpanded }

export async function fetchJobHistory(
  limit: number,
  offset: number,
  search: string | null = null,
): Promise<{ rows: JobReadExpanded[]; total: number }> {
  const params: Record<string, string | number> = { limit, offset }
  if (search) params.search = search
  const resp = await apiClient.get<JobReadExpanded[]>('/api/jobs/history', { params })
  const total = Number(resp.headers['x-total-count'] ?? resp.data.length)
  return { rows: resp.data, total }
}

export async function fetchJobLineage(jobId: number): Promise<JobReadExpanded[]> {
  const resp = await apiClient.get<JobReadExpanded[]>(`/api/jobs/${jobId}/lineage`)
  return resp.data
}
