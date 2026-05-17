/**
 * API client for the Phase 18a review workflow endpoints.
 * All response types are manually typed because the backend endpoints return
 * plain dicts without Pydantic response models, so openapi-typescript emits `unknown`.
 */

import { apiClient } from './client'

// ---------------------------------------------------------------------------
// Response types
// ---------------------------------------------------------------------------

/** A single staging row inside a review group. */
export interface ReviewRow {
  staging_row_id: number
  source_row_number: number
  original_cell_text: string
  /** Phase 2: populated from registry-driven decompose. Null in Phase 1. */
  parsed_split_suffix: string | null
  review_split_suffix_override: string | null
  review_status: 'pending' | 'verified' | 'edited' | 'deleted'
}

/** An existing assembly close to a new parsed part number, by edit distance. */
export interface SimilarAssembly {
  part_number: string
  edit_distance: number
}

/** One parsed canonical and all the staging rows that map to it. */
export interface ReviewGroup {
  parsed_part_number: string
  rows: ReviewRow[]
  similar_assemblies: SimilarAssembly[]
  review_status: 'pending' | 'verified' | 'edited'
}

/** Full review payload returned by GET /{batch_id}/review. */
export interface ReviewPayload {
  batch_id: number
  new_b_numbers: ReviewGroup[]
  new_non_b_numbers: ReviewGroup[]
  intra_file_duplicates: ReviewGroup[]
}

/** One entry in the GET /awaiting-review list. */
export interface AwaitingReviewBatch {
  batch_id: number
  source_file: string | null
  created_at: string | null
  new_b_count: number
  new_non_b_count: number
  pending_row_count: number
}

/** POST /confirm response (mirrors IngestResult.processed_or_error). */
export interface ConfirmResult {
  batch_id: number
  source_sha256: string
  rows_total: number
  rows_inserted: number
  rows_updated: number
  rows_errored: number
  duplicate_of_batch_id: number | null
  filename: string
}

// ---------------------------------------------------------------------------
// Ingest upload response shapes (used by UploadModal.vue)
// ---------------------------------------------------------------------------

export interface IngestHeldResponse {
  batch_id: number
  source_sha256: string
  filename: string | null
  requires_review: true
  new_b_numbers: Array<{ parsed_part_number: string; row_count: number }>
  new_non_b_numbers: []
  intra_file_duplicates: []
}

export interface IngestProcessedResponse {
  batch_id: number
  source_sha256: string
  rows_total: number
  rows_inserted: number
  rows_updated: number
  rows_errored: number
  duplicate_of_batch_id: number | null
  filename: string
  requires_review: false
}

export type IngestResponse = IngestHeldResponse | IngestProcessedResponse

// ---------------------------------------------------------------------------
// P-4: Mutation response shapes — returned by verify/delete/patch/canonical.
// Allows the frontend to update local state without a full GET /review refetch.
// ---------------------------------------------------------------------------

/** Serialised staging row returned by verify/delete/patch-split-suffix/set-canonical. */
export interface ReviewRowResponse {
  staging_row_id: number
  review_status: string
  reviewed_at: string | null
  reviewed_by: string | null
  review_part_number_override: string | null
  review_split_suffix_override: string | null
}

/** Derived group status returned alongside every mutation. */
export interface GroupStatusResponse {
  parsed_part_number: string
  review_status: string
  active_row_count: number
}

/** Returned by verify, delete, and patch-split-suffix. */
export interface MutationResponse {
  row: ReviewRowResponse
  group: GroupStatusResponse
}

/** Returned by set-canonical (multiple rows updated at once). */
export interface CanonicalMutationResponse {
  updated_rows: ReviewRowResponse[]
  group: GroupStatusResponse
}

// ---------------------------------------------------------------------------
// Endpoint wrappers
// ---------------------------------------------------------------------------

/** GET /api/ingest/awaiting-review */
export async function fetchAwaitingReview(): Promise<AwaitingReviewBatch[]> {
  const resp = await apiClient.get<AwaitingReviewBatch[]>('/api/ingest/awaiting-review')
  return resp.data
}

/** GET /api/ingest/{batch_id}/review */
export async function fetchReviewPayload(batchId: number): Promise<ReviewPayload> {
  const resp = await apiClient.get<ReviewPayload>(`/api/ingest/${batchId}/review`)
  return resp.data
}

/** PUT /api/ingest/{batch_id}/canonical/{parsed_part_number} */
export async function setCanonical(
  batchId: number,
  parsedPartNumber: string,
  canonicalPartNumber: string,
): Promise<CanonicalMutationResponse> {
  const resp = await apiClient.put<CanonicalMutationResponse>(
    `/api/ingest/${batchId}/canonical/${encodeURIComponent(parsedPartNumber)}`,
    { canonical_part_number: canonicalPartNumber },
  )
  return resp.data
}

/** PATCH /api/ingest/{batch_id}/staging-row/{row_id}/split-suffix */
export async function patchSplitSuffix(
  batchId: number,
  rowId: number,
  splitSuffix: string | null,
): Promise<MutationResponse> {
  const resp = await apiClient.patch<MutationResponse>(
    `/api/ingest/${batchId}/staging-row/${rowId}/split-suffix`,
    { split_suffix: splitSuffix },
  )
  return resp.data
}

/** POST /api/ingest/{batch_id}/staging-row/{row_id}/verify */
export async function verifyRow(
  batchId: number,
  rowId: number,
): Promise<MutationResponse> {
  const resp = await apiClient.post<MutationResponse>(`/api/ingest/${batchId}/staging-row/${rowId}/verify`)
  return resp.data
}

/** DELETE /api/ingest/{batch_id}/staging-row/{row_id} */
export async function deleteRow(
  batchId: number,
  rowId: number,
): Promise<MutationResponse> {
  const resp = await apiClient.delete<MutationResponse>(`/api/ingest/${batchId}/staging-row/${rowId}`)
  return resp.data
}

/** POST /api/ingest/{batch_id}/confirm */
export async function confirmReview(batchId: number): Promise<ConfirmResult> {
  const resp = await apiClient.post<ConfirmResult>(`/api/ingest/${batchId}/confirm`)
  return resp.data
}

/** POST /api/ingest/{batch_id}/abandon */
export async function abandonReview(batchId: number): Promise<{ batch_id: number; status: string }> {
  const resp = await apiClient.post(`/api/ingest/${batchId}/abandon`)
  return resp.data
}
