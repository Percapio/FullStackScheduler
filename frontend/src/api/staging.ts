import { apiClient } from './client'
import type { components } from './types.gen'

// Schema names below MUST match the keys produced by `npm run gen:types` in
// `types.gen.ts` under `components["schemas"]`. Run `npm run build` after
// every regen to catch drift at vue-tsc time.
export type StagingRowSummary  = components['schemas']['ImportStagingRowRead']
export type StagingRowDetail   = components['schemas']['StagingRowDetailRead']
export type CorrectionPayload  = components['schemas']['StagingRowCorrectionRequest']
export type JobReadExpanded    = components['schemas']['JobReadExpanded']
export type ConflictGroup      = components['schemas']['ConflictGroup']

// ---------- Epoch 2: restore-preview types -----------------------------------

export type RestoreSourceKind = 'staging' | 'job'

export interface IncomingRestoreCandidate {
  kind: RestoreSourceKind
  staging: StagingRowDetail | null
  job: JobReadExpanded | null
}

export interface RestoreConflictPreview {
  incoming: IncomingRestoreCandidate
  colliding_staging_errored_rows: StagingRowDetail[]
  colliding_staging_discarded_rows: StagingRowDetail[]
  colliding_live_jobs: JobReadExpanded[]
  group_key: string
}

export interface StagingRestoreAction {
  kind: 'edit' | 'discard'
  row_id: number
  payload?: Record<string, unknown> | null
}

export async function fetchErrored(
  limit = 50, offset = 0, search: string | null = null,
): Promise<{ rows: StagingRowSummary[]; total: number }> {
  const params: Record<string, unknown> = { limit, offset }
  if (search) params.search = search
  const resp = await apiClient.get<StagingRowSummary[]>('/api/staging/errored', { params })
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
  limit = 50, offset = 0, search: string | null = null,
): Promise<{ rows: StagingRowSummary[]; total: number }> {
  const resp = await apiClient.get<StagingRowSummary[]>('/api/staging/discarded', {
    params: { limit, offset, ...(search ? { search } : {}) },
  })
  const total = Number(resp.headers['x-total-count'] ?? resp.data.length)
  return { rows: resp.data, total }
}

export async function deleteStagingRow(rowId: number): Promise<void> {
  await apiClient.delete(`/api/staging/${rowId}`)
}

export async function postRestoreStagingRow(
  rowId: number,
  actions: StagingRestoreAction[] = [],
): Promise<StagingRowSummary> {
  const resp = await apiClient.post<StagingRowSummary>(
    `/api/staging/${rowId}/restore`,
    { actions },
  )
  return resp.data
}

export async function fetchStagingRestorePreview(
  rowId: number,
): Promise<RestoreConflictPreview> {
  const resp = await apiClient.get<RestoreConflictPreview>(
    `/api/staging/${rowId}/restore-preview`,
  )
  return resp.data
}

export async function fetchConflicts(): Promise<ConflictGroup[]> {
  const resp = await apiClient.get<ConflictGroup[]>('/api/staging/conflicts')
  return resp.data
}
