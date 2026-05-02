import { apiClient } from './client'
import type { components } from './types.gen'

// Schema names below MUST match the keys produced by `npm run gen:types` in
// `types.gen.ts` under `components["schemas"]`. Run `npm run build` after
// every regen to catch drift at vue-tsc time.
export type StagingRowSummary  = components['schemas']['ImportStagingRowRead']
export type StagingRowDetail   = components['schemas']['StagingRowDetailRead']
export type CorrectionPayload  = components['schemas']['StagingRowCorrectionRequest']
export type JobReadExpanded    = components['schemas']['JobReadExpanded']

export async function fetchErrored(
  limit = 100, offset = 0,
): Promise<{ rows: StagingRowSummary[]; total: number }> {
  const resp = await apiClient.get<StagingRowSummary[]>('/api/staging/errored', {
    params: { limit, offset },
  })
  const total = Number(resp.headers['x-total-count'] ?? resp.data.length)
  return { rows: resp.data, total }
}

export async function fetchDetail(rowId: number): Promise<StagingRowDetail> {
  const resp = await apiClient.get<StagingRowDetail>(`/api/staging/${rowId}`)
  return resp.data
}

export async function submitCorrection(
  rowId: number,
  payload: Partial<CorrectionPayload>,
): Promise<JobReadExpanded> {
  // Footgun guard (audit A3): callers must NOT spread defaults into `payload`.
  // The `useCorrectionDraft` composable builds this object by *only assigning*
  // changed keys, satisfying the server's `exclude_unset` contract.
  const resp = await apiClient.post<JobReadExpanded>(
    `/api/staging/${rowId}/correct`, payload,
  )
  return resp.data
}

export async function fetchDiscarded(
  limit = 100, offset = 0,
): Promise<{ rows: StagingRowSummary[]; total: number }> {
  const resp = await apiClient.get<StagingRowSummary[]>('/api/staging/discarded', {
    params: { limit, offset },
  })
  const total = Number(resp.headers['x-total-count'] ?? resp.data.length)
  return { rows: resp.data, total }
}

export async function deleteStagingRow(rowId: number): Promise<void> {
  await apiClient.delete(`/api/staging/${rowId}`)
}

export async function postRestoreStagingRow(
  rowId: number,
): Promise<StagingRowSummary> {
  const resp = await apiClient.post<StagingRowSummary>(
    `/api/staging/${rowId}/restore`,
  )
  return resp.data
}
