import { apiClient } from './client'
import type { JobReadExpanded } from './staging'
export type { JobReadExpanded }

export type HistoryEditDraft = {
  part_number?: string | null
  build_type?: string | null
  split_suffix?: string | null
  repeat_reference?: string | null
  build_qualifier?: string | null
  raw_qty?: string | null
  raw_customer?: string | null
  raw_shipped?: string | null
}

/**
 * Discriminated union of errors from editHistoryJob.
 *
 * - "validation":  server returned 422 with { field, message } body
 *                  (per-field parse failure or Pydantic no-raw-field rejection).
 * - "collision":   server returned 409 with { colliding_job_id } body
 *                  (raw_job edit would collide with another active job).
 * - "conflict":    server returned 409 with { kind } body
 *                  (job not shipped or already discarded).
 * - "transport":   any non-409/422 error (network, 5xx, etc.).
 */
export type HistoryEditError =
  | { kind: 'validation'; field: 'part_number' | 'build_type' | 'split_suffix' | 'repeat_reference' | 'build_qualifier' | 'raw_qty' | 'raw_customer' | 'raw_shipped' | null; message: string }
  | { kind: 'collision'; collidingJobId: number }
  | { kind: 'conflict'; message: string }
  | { kind: 'transport'; message: string }

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

/**
 * Edit reconciliation-style fields of a shipped job.
 *
 * Pre:  edit has at least one non-null raw_* field; reason is non-empty.
 * Post: resolves with the refreshed JobReadExpanded on success.
 *
 * Throws HistoryEditError on:
 *   422 { field, message }     → kind="validation"
 *   409 { colliding_job_id }   → kind="collision"
 *   409 { kind }               → kind="conflict"
 *   any other error            → kind="transport"
 */
export async function editHistoryJob(
  jobId: number,
  edit: HistoryEditDraft,
  reason: string,
): Promise<JobReadExpanded> {
  try {
    const resp = await apiClient.patch<JobReadExpanded>(`/api/jobs/${jobId}/history-edit`, {
      ...edit,
      reason,
    })
    return resp.data
  } catch (err: unknown) {
    const response = (err as { response?: { status?: number; data?: unknown } })?.response
    if (response?.status === 422) {
      const detail = (response.data as { detail?: unknown })?.detail
      if (detail && typeof detail === 'object') {
        const d = detail as Record<string, unknown>
        if (typeof d.field === 'string' && typeof d.message === 'string') {
          throw {
            kind: 'validation',
          field: d.field as Extract<HistoryEditError, { kind: 'validation' }>['field'],
            message: d.message,
          } satisfies HistoryEditError
        }
      }
      throw { kind: 'transport', message: 'Validation failed' } satisfies HistoryEditError
    }
    if (response?.status === 409) {
      const detail = (response.data as { detail?: unknown })?.detail
      if (detail && typeof detail === 'object') {
        const d = detail as Record<string, unknown>
        if (typeof d.colliding_job_id === 'number') {
          throw { kind: 'collision', collidingJobId: d.colliding_job_id } satisfies HistoryEditError
        }
        const msg = typeof d.message === 'string' ? d.message : 'Job not editable'
        throw { kind: 'conflict', message: msg } satisfies HistoryEditError
      }
    }
    const message =
      err instanceof Error ? err.message : 'Could not save edit. Check the backend is running.'
    throw { kind: 'transport', message } satisfies HistoryEditError
  }
}

/**
 * Soft-delete a shipped job from the History tab.
 *
 * Pre:  reason is non-empty.
 * Post: resolves with the discarded JobReadExpanded on success.
 *       Throws the raw axios error on failure (caller normalises for toast).
 */
export async function discardHistoryJob(
  jobId: number,
  reason: string,
): Promise<JobReadExpanded> {
  const resp = await apiClient.post<JobReadExpanded>(`/api/jobs/${jobId}/discard`, { reason })
  return resp.data
}

export interface HistoryExportColumn {
  key: string
  header: string
}

export async function fetchHistoryExportColumns(): Promise<HistoryExportColumn[]> {
  const resp = await apiClient.get<HistoryExportColumn[]>('/api/jobs/history/export-columns')
  return resp.data
}

export function buildHistoryExportUrl(
  search: string | null,
  columnKeys: string[],
  delimiterToken: string,
): string {
  const params = new URLSearchParams()
  if (search) {
    params.set('search', search)
  }
  for (const key of columnKeys) {
    params.append('column', key)
  }
  params.set('delimiter', delimiterToken)
  return `/api/jobs/history/export.csv?${params.toString()}`
}
