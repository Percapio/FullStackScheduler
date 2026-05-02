import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useStagingStore } from '../staging'
import type { StagingRowSummary } from '@/api/staging'

const mockFetchErrored          = vi.fn()
const mockFetchDetail           = vi.fn()
const mockFetchDiscarded        = vi.fn()
const mockDeleteStagingRow      = vi.fn()
const mockPostRestoreStagingRow = vi.fn()

vi.mock('@/api/staging', () => ({
  fetchErrored:           (...a: unknown[]) => mockFetchErrored(...a),
  fetchDetail:            (...a: unknown[]) => mockFetchDetail(...a),
  fetchDiscarded:         (...a: unknown[]) => mockFetchDiscarded(...a),
  deleteStagingRow:       (...a: unknown[]) => mockDeleteStagingRow(...a),
  postRestoreStagingRow:  (...a: unknown[]) => mockPostRestoreStagingRow(...a),
}))

vi.mock('@/api/client', () => ({
  isApiError: (err: unknown) => {
    return (
      typeof err === 'object' &&
      err !== null &&
      'response' in err
    )
  },
}))

const TS = '2026-05-01T12:00:00Z'

function summaryFixture(id: number, extra: Partial<StagingRowSummary> = {}): StagingRowSummary {
  return {
    id,
    batch_id: 1,
    source_row_number: id,
    processing_status: 'error',
    processing_error: 'err',
    suggested_correction: null,
    resolved_job_id: null,
    processed_at: null,
    discarded_at: null,
    created_at: TS,
    updated_at: TS,
    ...extra,
  } as StagingRowSummary
}

function makeApiError(status: number, detail: string) {
  return { response: { status, data: { detail } } }
}

beforeEach(() => {
  setActivePinia(createPinia())
  mockFetchErrored.mockReset()
  mockFetchDetail.mockReset()
  mockFetchDiscarded.mockReset()
  mockDeleteStagingRow.mockReset()
  mockPostRestoreStagingRow.mockReset()
})

// ---- loadDiscarded -----------------------------------------------------------

describe('loadDiscarded', () => {
  it('populates discardedRows and discardedTotal from the API response', async () => {
    const store = useStagingStore()
    const rows = [summaryFixture(1), summaryFixture(2)]
    mockFetchDiscarded.mockResolvedValueOnce({ rows, total: 2 })

    await store.loadDiscarded()

    expect(store.discardedRows).toEqual(rows)
    expect(store.discardedTotal).toBe(2)
    expect(store.discardedLoading).toBe(false)
  })
})

// ---- discardRow --------------------------------------------------------------

describe('discardRow', () => {
  it('returns stale and makes no HTTP call when row not in rows[]', async () => {
    const store = useStagingStore()
    store.rows = [summaryFixture(3)]

    const result = await store.discardRow(99)

    expect(result).toEqual({ kind: 'stale' })
    expect(mockDeleteStagingRow).not.toHaveBeenCalled()
    expect(store.rows).toHaveLength(1)
    expect(store.total).toBe(0)
    expect(store.discardedTotal).toBe(0)
    expect(store.activeErrorRowId).toBeNull()
  })

  it('removes row, decrements total, increments discardedTotal, and clears activeErrorRowId on 200', async () => {
    const store = useStagingStore()
    store.rows = [summaryFixture(7)]
    store.total = 1
    store.discardedTotal = 0
    store.activeErrorRowId = 7
    store.details[7] = {} as never

    mockDeleteStagingRow.mockResolvedValueOnce(undefined)

    const result = await store.discardRow(7)

    expect(result).toEqual({ kind: 'ok' })
    expect(store.rows).toHaveLength(0)
    expect(store.total).toBe(0)
    expect(store.discardedTotal).toBe(1)
    expect(store.activeErrorRowId).toBeNull()
    expect(store.details[7]).toBeUndefined()
  })

  it('rolls back the row splice on 409', async () => {
    const store = useStagingStore()
    store.rows = [summaryFixture(7)]
    store.total = 1

    mockDeleteStagingRow.mockRejectedValueOnce(makeApiError(409, 'Row already resolved'))

    const result = await store.discardRow(7)

    expect(result).toEqual({ kind: 'conflict', message: 'Row already resolved' })
    expect(store.rows).toHaveLength(1)
    expect(store.rows[0].id).toBe(7)
  })

  it('rolls back the row splice on network error', async () => {
    const store = useStagingStore()
    store.rows = [summaryFixture(7)]

    mockDeleteStagingRow.mockRejectedValueOnce(new Error('fetch failed'))

    const result = await store.discardRow(7)

    expect(result).toEqual({ kind: 'network', message: 'Could not reach the API' })
    expect(store.rows).toHaveLength(1)
  })
})

// ---- restoreRow --------------------------------------------------------------

describe('restoreRow', () => {
  it('returns stale and makes no HTTP call when row not in discardedRows[]', async () => {
    const store = useStagingStore()
    store.discardedRows = [summaryFixture(3)]

    const result = await store.restoreRow(99)

    expect(result).toEqual({ kind: 'stale' })
    expect(mockPostRestoreStagingRow).not.toHaveBeenCalled()
    expect(store.discardedRows).toHaveLength(1)
  })

  it('removes row from discardedRows, decrements discardedTotal, and calls loadErrored on 200', async () => {
    const store = useStagingStore()
    const restoredRow = summaryFixture(5, { discarded_at: null })
    store.discardedRows = [summaryFixture(5)]
    store.discardedTotal = 1

    mockPostRestoreStagingRow.mockResolvedValueOnce(restoredRow)
    mockFetchErrored.mockResolvedValueOnce({ rows: [restoredRow], total: 1 })

    const result = await store.restoreRow(5)

    expect(result).toEqual({ kind: 'ok', row: restoredRow })
    expect(store.discardedRows).toHaveLength(0)
    expect(store.discardedTotal).toBe(0)
    expect(mockFetchErrored).toHaveBeenCalledWith(100, 0)
  })

  it('rolls back the splice on 409 and calls loadDiscarded', async () => {
    const store = useStagingStore()
    store.discardedRows = [summaryFixture(5)]
    store.discardedTotal = 1

    mockPostRestoreStagingRow.mockRejectedValueOnce(makeApiError(409, 'Row is not discarded'))
    mockFetchDiscarded.mockResolvedValueOnce({ rows: [summaryFixture(5)], total: 1 })

    const result = await store.restoreRow(5)

    expect(result).toEqual({ kind: 'conflict', message: 'Row is not discarded' })
    // Splice was rolled back.
    expect(store.discardedRows).toHaveLength(1)
    // loadDiscarded was called to resync after the 409.
    expect(mockFetchDiscarded).toHaveBeenCalledTimes(1)
  })

  it('rolls back splice on network error without calling loadDiscarded', async () => {
    const store = useStagingStore()
    store.discardedRows = [summaryFixture(5)]

    mockPostRestoreStagingRow.mockRejectedValueOnce(new Error('fetch failed'))

    const result = await store.restoreRow(5)

    expect(result).toEqual({ kind: 'network', message: 'Could not reach the API' })
    expect(store.discardedRows).toHaveLength(1)
    expect(mockFetchDiscarded).not.toHaveBeenCalled()
  })
})
