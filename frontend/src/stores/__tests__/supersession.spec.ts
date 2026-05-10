import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { AxiosError, AxiosHeaders } from 'axios'
import { useSupersessionStore } from '../supersession'
import type { SupersessionCandidate, BulkApprovalResult } from '@/api/supersession'

const mockFetch        = vi.fn()
const mockApprove      = vi.fn()
const mockReject       = vi.fn()
const mockBulkApprove  = vi.fn()

vi.mock('@/api/supersession', () => ({
  fetchSupersessionCandidates:       (...a: unknown[]) => mockFetch(...a),
  approveSupersessionCandidate:      (...a: unknown[]) => mockApprove(...a),
  rejectSupersessionCandidate:       (...a: unknown[]) => mockReject(...a),
  bulkApproveSupersessionCandidates: (...a: unknown[]) => mockBulkApprove(...a),
}))

vi.mock('@/api/client', () => ({
  isApiError: (err: unknown) =>
    typeof err === 'object' && err !== null && 'response' in err,
}))

const TS = '2026-05-05T10:00:00'

function candidateFixture(id: number): SupersessionCandidate {
  return {
    id,
    job_id: id * 10,
    detected_in_batch_id: 1,
    reason: 'orphan_other',
    detected_at: TS,
    resolved_at: null,
    resolution: null,
    closed_by_shield_reason: null,
    created_at: TS,
    updated_at: TS,
  }
}

function axiosError(status: number, detail: unknown): AxiosError {
  const err = new AxiosError('mock', String(status))
  err.response = {
    status,
    statusText: '',
    data: { detail },
    headers: {},
    config: { headers: new AxiosHeaders() } as never,
  }
  return err
}

beforeEach(() => {
  setActivePinia(createPinia())
  mockFetch.mockReset()
  mockApprove.mockReset()
  mockReject.mockReset()
  mockBulkApprove.mockReset()
})

// ---- loadPending ------------------------------------------------------------

describe('loadPending', () => {
  it('populates candidates and total on success', async () => {
    const store = useSupersessionStore()
    const rows = [candidateFixture(1), candidateFixture(2)]
    mockFetch.mockResolvedValueOnce({ items: rows, total: 2 })

    await store.loadPending()

    expect(store.candidates).toEqual(rows)
    expect(store.total).toBe(2)
    expect(store.loading).toBe(false)
    expect(store.lastError).toBeNull()
  })

  it('sets lastError on network failure', async () => {
    const store = useSupersessionStore()
    mockFetch.mockRejectedValueOnce(new Error('network'))

    await store.loadPending()

    expect(store.lastError).not.toBeNull()
    expect(store.candidates).toHaveLength(0)
  })
})

// ---- approve ----------------------------------------------------------------

describe('approve', () => {
  it('removes the row from candidates on 200', async () => {
    const store = useSupersessionStore()
    const cand = candidateFixture(1)
    store.candidates = [cand]
    store.total = 1
    mockApprove.mockResolvedValueOnce({
      ...cand, resolved_at: TS, resolution: 'approve', closed_by_shield_reason: null,
    })

    await store.approve(1)

    expect(store.candidates).toHaveLength(0)
    expect(store.total).toBe(0)
  })

  it('removes the row even on shield-trip 200 (resolution=reject)', async () => {
    const store = useSupersessionStore()
    const cand = candidateFixture(2)
    store.candidates = [cand]
    store.total = 1
    mockApprove.mockResolvedValueOnce({
      ...cand, resolved_at: TS, resolution: 'reject', closed_by_shield_reason: 'ever_shipped',
    })

    await store.approve(2)

    expect(store.candidates).toHaveLength(0)
  })

  it('refetches on 409 rather than removing the row', async () => {
    const store = useSupersessionStore()
    const cand = candidateFixture(3)
    store.candidates = [cand]
    store.total = 1
    mockApprove.mockRejectedValueOnce(axiosError(409, 'already_closed'))
    // loadPending will be called — mock it to return the same row.
    mockFetch.mockResolvedValueOnce({ items: [cand], total: 1 })

    await store.approve(3)

    // Row was NOT optimistically removed; loadPending refetched it.
    expect(store.candidates).toHaveLength(1)
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })
})

// ---- reject -----------------------------------------------------------------

describe('reject', () => {
  it('removes the row from candidates on 200', async () => {
    const store = useSupersessionStore()
    const cand = candidateFixture(4)
    store.candidates = [cand]
    store.total = 1
    mockReject.mockResolvedValueOnce({
      ...cand, resolved_at: TS, resolution: 'reject',
    })

    await store.reject(4)

    expect(store.candidates).toHaveLength(0)
    expect(store.total).toBe(0)
  })

  it('refetches on 409', async () => {
    const store = useSupersessionStore()
    const cand = candidateFixture(5)
    store.candidates = [cand]
    mockReject.mockRejectedValueOnce(axiosError(409, 'already_closed'))
    mockFetch.mockResolvedValueOnce({ items: [cand], total: 1 })

    await store.reject(5)

    expect(mockFetch).toHaveBeenCalledTimes(1)
  })
})

// ---- bulkApprove ------------------------------------------------------------

describe('bulkApprove', () => {
  it('stores lastBulkSummary and reloads pending on success', async () => {
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1), candidateFixture(2)]
    const result: BulkApprovalResult = {
      approved: [1, 2], shield_rejected: [], already_closed: [], not_found: [],
    }
    mockBulkApprove.mockResolvedValueOnce(result)
    mockFetch.mockResolvedValueOnce({ items: [], total: 0 })

    await store.bulkApprove([1, 2])

    expect(store.lastBulkSummary).toEqual(result)
    // loadPending was called after bulk.
    expect(mockFetch).toHaveBeenCalledTimes(1)
    expect(store.candidates).toHaveLength(0)
  })
})

// ---- toggleSelection / clearSelection ---------------------------------------

describe('toggleSelection', () => {
  it('adds id on first toggle, removes on second', () => {
    const store = useSupersessionStore()
    store.toggleSelection(7)
    expect(store.selectedIds.has(7)).toBe(true)
    store.toggleSelection(7)
    expect(store.selectedIds.has(7)).toBe(false)
  })
})

describe('clearSelection', () => {
  it('empties selectedIds', () => {
    const store = useSupersessionStore()
    store.selectedIds.add(1)
    store.selectedIds.add(2)
    store.clearSelection()
    expect(store.selectedIds.size).toBe(0)
  })
})
