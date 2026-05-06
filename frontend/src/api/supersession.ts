import { apiClient } from './client'
import type { components } from './types.gen'

export type SupersessionCandidate =
  components['schemas']['JobSupersessionCandidateRead']
export type SupersessionCandidatePage =
  components['schemas']['JobSupersessionCandidatePage']
export type BulkApprovalResult =
  components['schemas']['BulkApprovalResultRead']

export type CandidateResolution =
  components['schemas']['CandidateResolution']
export type CandidateReason =
  components['schemas']['CandidateReason']

export interface FetchCandidatesParams {
  status?: 'pending' | 'resolved' | 'all'
  resolution?: CandidateResolution | null
  limit?: number
  offset?: number
}

export async function fetchSupersessionCandidates(
  params: FetchCandidatesParams = {},
): Promise<SupersessionCandidatePage> {
  const query: Record<string, string | number> = {}
  if (params.status)     query.status     = params.status
  if (params.resolution) query.resolution = params.resolution
  if (params.limit  != null) query.limit  = params.limit
  if (params.offset != null) query.offset = params.offset

  const resp = await apiClient.get<SupersessionCandidatePage>(
    '/api/staging/supersession-candidates',
    { params: query },
  )
  return resp.data
}

export async function approveSupersessionCandidate(
  id: number,
): Promise<SupersessionCandidate> {
  const resp = await apiClient.post<SupersessionCandidate>(
    `/api/staging/supersession-candidates/${id}/approve`,
  )
  return resp.data
}

export async function rejectSupersessionCandidate(
  id: number,
): Promise<SupersessionCandidate> {
  const resp = await apiClient.post<SupersessionCandidate>(
    `/api/staging/supersession-candidates/${id}/reject`,
  )
  return resp.data
}

export async function bulkApproveSupersessionCandidates(
  ids: number[],
): Promise<BulkApprovalResult> {
  const resp = await apiClient.post<BulkApprovalResult>(
    '/api/staging/supersession-candidates/bulk-approve',
    { ids },
  )
  return resp.data
}
