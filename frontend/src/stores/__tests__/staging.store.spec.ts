import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useStagingStore } from '../staging'
import type { StagingRowSummary } from '@/api/staging'

const mockFetchErrored          = vi.fn()
const mockFetchDetail           = vi.fn()
const mockFetchDiscarded        = vi.fn()
const mockDeleteStagingRow      = vi.fn()
const mockPostRestoreStagingRow = vi.fn()
const mockFetchStagingRestorePreview = vi.fn()

vi.mock('@/api/staging', () => ({
  fetchErrored:                (...a: unknown[]) => mockFetchErrored(...a),
  fetchDetail:                 (...a: unknown[]) => mockFetchDetail(...a),
  fetchDiscarded:              (...a: unknown[]) => mockFetchDiscarded(...a),
  deleteStagingRow:            (...a: unknown[]) => mockDeleteStagingRow(...a),
  postRestoreStagingRow:       (...a: unknown[]) => mockPostRestoreStagingRow(...a),
  fetchStagingRestorePreview:  (...a: unknown[]) => mockFetchStagingRestorePreview(...a),
  fetchConflicts:              () => Promise.resolve([]),
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
  mockFetchStagingRestorePreview.mockReset()
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

// ---- beginRestore + commitRestore -------------------------------------------

const emptyPreview = {
  incoming: { kind: 'staging' as const, staging: null, job: null },
  colliding_staging_errored_rows: [],
  colliding_staging_discarded_rows: [],
  colliding_live_jobs: [],
  group_key: 'X|new|||',
}

describe('beginRestore', () => {
  it('returns stale and makes no HTTP call when row not in discardedRows[]', async () => {
    const store = useStagingStore()
    store.discardedRows = [summaryFixture(3)]

    const result = await store.beginRestore(99)

    expect(result).toEqual({ kind: 'stale' })
    expect(mockFetchStagingRestorePreview).not.toHaveBeenCalled()
    expect(store.discardedRows).toHaveLength(1)
  })

  it('short-circuits to ok when preview has no blockers', async () => {
    const store = useStagingStore()
    const restoredRow = summaryFixture(5, { discarded_at: null })
    store.discardedRows = [summaryFixture(5)]
    store.discardedTotal = 1

    mockFetchStagingRestorePreview.mockResolvedValueOnce(emptyPreview)
    mockPostRestoreStagingRow.mockResolvedValueOnce(restoredRow)
    mockFetchErrored.mockResolvedValueOnce({ rows: [restoredRow], total: 1 })

    const result = await store.beginRestore(5)

    expect(result).toEqual({ kind: 'ok', row: restoredRow })
    expect(mockPostRestoreStagingRow).toHaveBeenCalledWith(5, [])
    expect(store.discardedTotal).toBe(0)
  })

  it('returns preview outcome when errored colliders exist', async () => {
    const store = useStagingStore()
    store.discardedRows = [summaryFixture(5)]
    store.discardedTotal = 1

    const previewWithCollider = {
      ...emptyPreview,
      colliding_staging_errored_rows: [summaryFixture(9) as never],
    }
    mockFetchStagingRestorePreview.mockResolvedValueOnce(previewWithCollider)

    const result = await store.beginRestore(5)

    expect(result).toEqual({ kind: 'preview', preview: previewWithCollider })
    expect(mockPostRestoreStagingRow).not.toHaveBeenCalled()
  })

  it('returns preview outcome when live-job colliders exist', async () => {
    const store = useStagingStore()
    store.discardedRows = [summaryFixture(5)]

    const previewWithJob = {
      ...emptyPreview,
      colliding_live_jobs: [{ id: 42 } as never],
    }
    mockFetchStagingRestorePreview.mockResolvedValueOnce(previewWithJob)

    const result = await store.beginRestore(5)

    expect(result).toEqual({ kind: 'preview', preview: previewWithJob })
  })

  it('returns network outcome when preview fetch throws', async () => {
    const store = useStagingStore()
    store.discardedRows = [summaryFixture(5)]

    mockFetchStagingRestorePreview.mockRejectedValueOnce(new Error('network'))

    const result = await store.beginRestore(5)

    expect(result.kind).toBe('network')
  })
})

describe('commitRestore', () => {
  it('returns stale when row not in discardedRows[]', async () => {
    const store = useStagingStore()
    store.discardedRows = []

    const result = await store.commitRestore(5, [])

    expect(result).toEqual({ kind: 'stale' })
    expect(mockPostRestoreStagingRow).not.toHaveBeenCalled()
  })

  it('calls postRestoreStagingRow with provided actions and returns ok on 200', async () => {
    const store = useStagingStore()
    const restoredRow = summaryFixture(5, { discarded_at: null })
    store.discardedRows = [summaryFixture(5)]
    store.discardedTotal = 1

    mockPostRestoreStagingRow.mockResolvedValueOnce(restoredRow)
    mockFetchErrored.mockResolvedValueOnce({ rows: [restoredRow], total: 1 })

    const actions = [{ kind: 'discard' as const, row_id: 9 }]
    const result = await store.commitRestore(5, actions)

    expect(result).toEqual({ kind: 'ok', row: restoredRow })
    expect(mockPostRestoreStagingRow).toHaveBeenCalledWith(5, actions)
    expect(store.discardedTotal).toBe(0)
  })

  it('returns conflict with fresh preview on 409 with preview body', async () => {
    const store = useStagingStore()
    store.discardedRows = [summaryFixture(5)]
    store.discardedTotal = 1

    const freshPreview = { ...emptyPreview, group_key: 'Y|new|||' }
    mockPostRestoreStagingRow.mockRejectedValueOnce({
      response: {
        status: 409,
        data: { detail: { message: 'Residual collision', preview: freshPreview } },
      },
    })
    mockFetchDiscarded.mockResolvedValueOnce({ rows: [summaryFixture(5)], total: 1 })

    const result = await store.commitRestore(5, [])

    expect(result.kind).toBe('conflict')
    if (result.kind === 'conflict') {
      expect(result.preview).toEqual(freshPreview)
    }
  })

  it('returns invalid-edit with action_index on 422', async () => {
    const store = useStagingStore()
    store.discardedRows = [summaryFixture(5)]

    mockPostRestoreStagingRow.mockRejectedValueOnce({
      response: {
        status: 422,
        data: { detail: { message: 'Action 1: row not found', action_index: 1 } },
      },
    })

    const result = await store.commitRestore(5, [])

    expect(result.kind).toBe('invalid-edit')
    if (result.kind === 'invalid-edit') {
      expect(result.action_index).toBe(1)
    }
  })
})

// ---- errored pagination (Epoch 1) -------------------------------------------

describe('errored pagination', () => {
  it('initial state has offset=0, limit=50, searchQuery=""', () => {
    const store = useStagingStore()
    expect(store.erroredOffset).toBe(0)
    expect(store.erroredLimit).toBe(50)
    expect(store.erroredSearchQuery).toBe('')
  })

  it('loadErrored forwards limit/offset/search to fetchErrored', async () => {
    const store = useStagingStore()
    store.erroredOffset = 50
    store.erroredLimit = 50
    store.erroredSearchQuery = 'needle'
    mockFetchErrored.mockResolvedValueOnce({ rows: [], total: 0 })

    await store.loadErrored()

    expect(mockFetchErrored).toHaveBeenCalledWith(50, 50, 'needle')
  })

  it('loadErrored passes null for search when query is blank', async () => {
    const store = useStagingStore()
    store.erroredSearchQuery = '   '
    mockFetchErrored.mockResolvedValueOnce({ rows: [], total: 0 })

    await store.loadErrored()

    expect(mockFetchErrored).toHaveBeenCalledWith(50, 0, null)
  })

  it('loadErrored populates error ref on rejection', async () => {
    const store = useStagingStore()
    mockFetchErrored.mockRejectedValueOnce(new Error('network down'))

    await store.loadErrored()

    expect(store.error).not.toBeNull()
    expect(store.loading).toBe(false)
  })

  it('loadErrored clears error ref on success after prior failure', async () => {
    const store = useStagingStore()
    store.error = 'previous error'
    mockFetchErrored.mockResolvedValueOnce({ rows: [], total: 0 })

    await store.loadErrored()

    expect(store.error).toBeNull()
  })

  it('nextErroredPage advances offset and calls loadErrored when hasNext', async () => {
    const store = useStagingStore()
    // Simulate 60 total rows; first page has 50, offset=0 → hasNext=true
    store.rows = Array.from({ length: 50 }, (_, i) => summaryFixture(i + 1))
    store.total = 60
    store.erroredOffset = 0
    mockFetchErrored.mockResolvedValueOnce({ rows: [], total: 60 })

    await store.nextErroredPage()

    expect(store.erroredOffset).toBe(50)
    expect(mockFetchErrored).toHaveBeenCalledTimes(1)
  })

  it('nextErroredPage is no-op when hasNext is false', async () => {
    const store = useStagingStore()
    // Simulate 30 total rows; first page has 30 rows → hasNext=false
    store.rows = Array.from({ length: 30 }, (_, i) => summaryFixture(i + 1))
    store.total = 30
    store.erroredOffset = 0

    await store.nextErroredPage()

    expect(store.erroredOffset).toBe(0)
    expect(mockFetchErrored).not.toHaveBeenCalled()
  })

  it('prevErroredPage retreats offset and calls loadErrored when hasPrev', async () => {
    const store = useStagingStore()
    store.erroredOffset = 50
    store.rows = Array.from({ length: 10 }, (_, i) => summaryFixture(i + 1))
    store.total = 60
    mockFetchErrored.mockResolvedValueOnce({ rows: [], total: 60 })

    await store.prevErroredPage()

    expect(store.erroredOffset).toBe(0)
    expect(mockFetchErrored).toHaveBeenCalledTimes(1)
  })

  it('prevErroredPage is no-op when hasPrev is false (offset=0)', async () => {
    const store = useStagingStore()
    store.erroredOffset = 0

    await store.prevErroredPage()

    expect(store.erroredOffset).toBe(0)
    expect(mockFetchErrored).not.toHaveBeenCalled()
  })

  it('setErroredSearch resets offset to 0 and calls loadErrored', async () => {
    const store = useStagingStore()
    store.erroredOffset = 50
    mockFetchErrored.mockResolvedValueOnce({ rows: [], total: 0 })

    await store.setErroredSearch('abc')

    expect(store.erroredSearchQuery).toBe('abc')
    expect(store.erroredOffset).toBe(0)
    expect(mockFetchErrored).toHaveBeenCalledWith(50, 0, 'abc')
  })

  it('erroredPageStart is 1-indexed (offset+1) when total > 0', () => {
    const store = useStagingStore()
    store.total = 100
    store.erroredOffset = 0
    store.rows = Array.from({ length: 50 }, (_, i) => summaryFixture(i + 1))
    expect(store.erroredPageStart).toBe(1)
  })

  it('erroredPageEnd equals offset + rows.length', () => {
    const store = useStagingStore()
    store.erroredOffset = 50
    store.rows = Array.from({ length: 10 }, (_, i) => summaryFixture(i + 1))
    expect(store.erroredPageEnd).toBe(60)
  })

  it('erroredHasPrev is false at offset=0', () => {
    const store = useStagingStore()
    store.erroredOffset = 0
    expect(store.erroredHasPrev).toBe(false)
  })

  it('erroredHasPrev is true when offset > 0', () => {
    const store = useStagingStore()
    store.erroredOffset = 50
    expect(store.erroredHasPrev).toBe(true)
  })

  it('erroredHasNext is false when all rows are on this page', () => {
    const store = useStagingStore()
    store.erroredOffset = 0
    store.total = 30
    store.rows = Array.from({ length: 30 }, (_, i) => summaryFixture(i + 1))
    expect(store.erroredHasNext).toBe(false)
  })

  it('erroredHasNext is true when more rows exist beyond current page', () => {
    const store = useStagingStore()
    store.erroredOffset = 0
    store.total = 60
    store.rows = Array.from({ length: 50 }, (_, i) => summaryFixture(i + 1))
    expect(store.erroredHasNext).toBe(true)
  })
})
