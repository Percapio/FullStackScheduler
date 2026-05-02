import { apiClient } from './client'
import type { JobReadExpanded } from './staging'
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
