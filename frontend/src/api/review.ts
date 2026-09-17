/**
 * API client for the Phase 18a review workflow endpoints.
 * All response types are manually typed because the backend endpoints return
 * plain dicts without Pydantic response models, so openapi-typescript emits `unknown`.
 */

import { apiClient } from './client'

// ---------------------------------------------------------------------------
// Response types
// ---------------------------------------------------------------------------

/** Who authored a row's split-suffix override (Patch 06 §2.2). */
export type SuffixSource = 'computed' | 'operator'

/** Why a duplicate group exists (Patch 06 §1.3). */
export type DuplicateOrigin = 'staged' | 'edit_induced'

/** A single staging row inside a review group. */
export interface ReviewRow {
  staging_row_id: number
  source_row_number: number
  original_cell_text: string
  /** Part number parsed from the raw cell; target of PUT /canonical for this row. */
  parsed_part_number?: string | null
  review_part_number_override: string | null
  review_split_suffix_override: string | null
  /** 'operator' suffixes survive PUT /canonical; DELETE /split-suffix returns them to 'computed'. */
  review_split_suffix_source: SuffixSource | null
  /** null only for a row outside review that an edit collided into (edit_induced groups). */
  review_status: 'pending' | 'verified' | 'edited' | 'deleted' | null
  /** True iff the Phase 18b B# shape rule fired when the row was ingested. */
  shape_rule_fired: boolean
  /** Duplicate groups only: the identity this row will be written under. */
  effective_identity?: ReviewGroupIdentity | null
  /** Duplicate groups only: false when the row endpoints reject this row. */
  actionable?: boolean
}

/** An existing assembly close to a new parsed part number, by edit distance. */
export interface SimilarAssembly {
  part_number: string
  edit_distance: number
}

/** Identity tuple for an intra-file duplicate group (Phase 18c §6.2). */
export interface ReviewGroupIdentity {
  part_number: string
  build_type: string
  split_suffix: string | null
  repeat_reference: string | null
  build_qualifier: string | null
}

/** One parsed canonical and all the staging rows that map to it. */
export interface ReviewGroup {
  parsed_part_number: string
  rows: ReviewRow[]
  similar_assemblies: SimilarAssembly[]
  review_status: 'pending' | 'verified' | 'edited'
  /** Populated only for intra_file_duplicates groups; null/absent for new_b/non_b groups. */
  identity?: ReviewGroupIdentity | null
  /**
   * Optional only because new-part sections share this type; the backend always
   * populates origin and resolved on intra_file_duplicates groups.
   */
  origin?: DuplicateOrigin
  resolved?: boolean
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
  review_split_suffix_source: SuffixSource | null
}

/** Derived group status returned alongside every mutation. */
export interface GroupStatusResponse {
  parsed_part_number: string
  review_status: string
  active_row_count: number
}

/**
 * Returned by verify, delete, patch-split-suffix, and revert-split-suffix.
 * `group` is part-number-wide and applies to new-part sections only;
 * `intra_file_duplicates` is the recomputed section, replaced wholesale (Patch 06 §2.6).
 */
export interface MutationResponse {
  row: ReviewRowResponse
  group: GroupStatusResponse
  intra_file_duplicates: ReviewGroup[]
}

/** Returned by set-canonical (multiple rows updated at once). */
export interface CanonicalMutationResponse {
  updated_rows: ReviewRowResponse[]
  group: GroupStatusResponse
  intra_file_duplicates: ReviewGroup[]
}

/** Surviving rows that would write the same job. */
export interface CollisionSet {
  identity: ReviewGroupIdentity
  row_ids: number[]
}

/** POST /confirm 409 body when surviving rows still collide (Patch 06 §3.1). */
export interface ConfirmCollisionBody {
  code: 'identity_collision'
  detail: string
  collisions: CollisionSet[]
  intra_file_duplicates: ReviewGroup[]
}

export function isConfirmCollisionBody(body: unknown): body is ConfirmCollisionBody {
  return (
    typeof body === 'object' &&
    body !== null &&
    (body as { code?: unknown }).code === 'identity_collision' &&
    Array.isArray((body as { intra_file_duplicates?: unknown }).intra_file_duplicates)
  )
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

/** DELETE /api/ingest/{batch_id}/staging-row/{row_id}/split-suffix */
export async function revertSplitSuffix(
  batchId: number,
  rowId: number,
): Promise<MutationResponse> {
  const resp = await apiClient.delete<MutationResponse>(
    `/api/ingest/${batchId}/staging-row/${rowId}/split-suffix`,
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

/**
 * POST /api/ingest/{batch_id}/confirm
 * Rejects with a 409 whose body is a ConfirmCollisionBody when surviving rows
 * still share an identity; any other 409 carries a `detail` string.
 */
export async function confirmReview(batchId: number): Promise<ConfirmResult> {
  const resp = await apiClient.post<ConfirmResult>(
    `/api/ingest/${batchId}/confirm`,
    undefined,
    { timeout: 60_000 },  // Override global 30 s — Stage 4..6 is the longest sync operation.
  )
  return resp.data
}

/** POST /api/ingest/{batch_id}/abandon */
export async function abandonReview(batchId: number): Promise<{ batch_id: number; status: string }> {
  const resp = await apiClient.post(`/api/ingest/${batchId}/abandon`)
  return resp.data
}
