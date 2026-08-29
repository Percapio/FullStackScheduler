import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import { useShippingStore } from '@/stores/shipping'
import { useHistoryStore } from '@/stores/history'
import type { JobReadExpanded } from '@/api/history'
import type { SecondOpsRecord } from '@/api/secondOps'

const ts = '2026-08-28T10:00:00'

function makeJob(id: number, partNumber = `PN-${id}`): JobReadExpanded {
  return {
    id,
    assembly_id: id,
    customer_id: 1,
    status: 'planned',
    quantity: 10,
    line_1: false,
    line_2: false,
    line_3: false,
    created_at: ts,
    updated_at: ts,
    assembly: { id, part_number: partNumber, created_at: ts, updated_at: ts },
    customer: { id: 1, name: 'ACME', created_at: ts, updated_at: ts },
  } as unknown as JobReadExpanded
}

function makeRecord(jobId: number): SecondOpsRecord {
  return {
    job_id: jobId,
    state: 'unaudited',
    reviewed_at: null,
    unexpected_inclusions: null,
    lines: [],
    limits: { max_lines: 500, note_max_chars: 4000 },
  }
}

const mockFetchSecondOps = vi.fn()
const mockPutSecondOps = vi.fn()

vi.mock('@/api/secondOps', () => ({
  fetchSecondOps: (...args: unknown[]) => mockFetchSecondOps(...args),
  putSecondOps: (...args: unknown[]) => mockPutSecondOps(...args),
}))

const mockFetchShippingJobs = vi.fn()

vi.mock('@/api/shipping', () => ({
  fetchShippingJobs: (...args: unknown[]) => mockFetchShippingJobs(...args),
  discardShippingJob: vi.fn(),
  fetchDiscardedJobs: vi.fn(),
  fetchJobRestorePreview: vi.fn(),
  postRestoreJob: vi.fn(),
}))

vi.mock('@/api/history', () => ({
  fetchJobHistory: vi.fn(),
  fetchJobLineage: vi.fn(),
  editHistoryJob: vi.fn(),
  discardHistoryJob: vi.fn(),
}))

vi.mock('@/composables/useToast', () => ({
  useToast: () => ({ toasts: { value: [] }, show: vi.fn(), dismiss: vi.fn() }),
}))

/** A promise plus its resolver, so resolution order can be inverted explicitly. */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

beforeEach(() => {
  setActivePinia(createPinia())
  mockFetchSecondOps.mockReset()
  mockPutSecondOps.mockReset()
  mockFetchShippingJobs.mockReset()
  mockFetchShippingJobs.mockResolvedValue({ rows: [], total: 0 })
})

describe('useShippingStore — 2nd OPS read lifecycle', () => {
  it('is loading before the request resolves and loaded after', async () => {
    const pending = deferred<SecondOpsRecord>()
    mockFetchSecondOps.mockReturnValue(pending.promise)
    const store = useShippingStore()

    const open = store.openSecondOps(makeJob(1))
    expect(store.secondOpsFetch).toEqual({ status: 'loading' })
    expect(store.secondOpsOpen).toBe(true)

    pending.resolve(makeRecord(1))
    await open

    expect(store.secondOpsFetch).toEqual({ status: 'loaded', record: makeRecord(1) })
  })

  it('is loading for job B immediately after closing job A', async () => {
    const a = deferred<SecondOpsRecord>()
    const b = deferred<SecondOpsRecord>()
    mockFetchSecondOps.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    const store = useShippingStore()

    store.openSecondOps(makeJob(1))
    store.closeSecondOps()
    store.openSecondOps(makeJob(2))

    expect(store.secondOpsFetch).toEqual({ status: 'loading' })

    a.resolve(makeRecord(1))
    await flushPromises()

    expect(store.secondOpsFetch).toEqual({ status: 'loading' })
    b.resolve(makeRecord(2))
    await flushPromises()
    expect(store.secondOpsFetch).toEqual({ status: 'loaded', record: makeRecord(2) })
  })

  it("discards job A's response when it resolves AFTER job B's modal opened", async () => {
    // The sequential close-then-open test alone does not catch this: the
    // resolution order has to be inverted explicitly.
    const a = deferred<SecondOpsRecord>()
    const b = deferred<SecondOpsRecord>()
    mockFetchSecondOps.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    const store = useShippingStore()

    store.openSecondOps(makeJob(1))
    store.openSecondOps(makeJob(2))

    b.resolve(makeRecord(2))
    await flushPromises()
    a.resolve(makeRecord(1))
    await flushPromises()

    expect(store.secondOpsFetch).toEqual({ status: 'loaded', record: makeRecord(2) })
  })

  it("discards job A's FAILURE when it rejects after job B opened", async () => {
    // A stale error arm must not surface over a healthy modal.
    const a = deferred<SecondOpsRecord>()
    const b = deferred<SecondOpsRecord>()
    mockFetchSecondOps.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    const store = useShippingStore()

    store.openSecondOps(makeJob(1))
    store.openSecondOps(makeJob(2))

    b.resolve(makeRecord(2))
    await flushPromises()
    a.reject(new Error('network'))
    await flushPromises()

    expect(store.secondOpsFetch).toEqual({ status: 'loaded', record: makeRecord(2) })
  })

  it('discards a resolution that lands after close, and refetches on reopen', async () => {
    const first = deferred<SecondOpsRecord>()
    const second = deferred<SecondOpsRecord>()
    mockFetchSecondOps.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const store = useShippingStore()

    store.openSecondOps(makeJob(1))
    store.closeSecondOps()
    first.resolve(makeRecord(1))
    await flushPromises()

    expect(store.secondOpsFetch).toEqual({ status: 'loading' })
    expect(store.secondOpsJob).toBeNull()

    store.openSecondOps(makeJob(1))
    expect(mockFetchSecondOps).toHaveBeenCalledTimes(2)
    second.resolve(makeRecord(1))
    await flushPromises()
    expect(store.secondOpsFetch.status).toBe('loaded')
  })

  it('surfaces a failed arm when the fetch rejects', async () => {
    mockFetchSecondOps.mockRejectedValue(new Error('network'))
    const store = useShippingStore()

    await store.openSecondOps(makeJob(1))

    expect(store.secondOpsFetch.status).toBe('failed')
  })
})

describe('useShippingStore — saveSecondOps', () => {
  it('reloads the grid on a saved result so the cell reflects the server preview cap', async () => {
    mockPutSecondOps.mockResolvedValue({ kind: 'saved', record: makeRecord(1) })
    const store = useShippingStore()

    const result = await store.saveSecondOps(1, { lines: [], unexpected_inclusions: null })

    expect(result.kind).toBe('saved')
    expect(mockFetchShippingJobs).toHaveBeenCalledTimes(1)
  })

  it.each(['rejected', 'stale', 'unreachable'] as const)(
    'returns a %s result untouched and does not reload',
    async (kind) => {
      mockPutSecondOps.mockResolvedValue({ kind, message: 'nope' })
      const store = useShippingStore()

      const result = await store.saveSecondOps(1, { lines: [], unexpected_inclusions: null })

      expect(result).toEqual({ kind, message: 'nope' })
      expect(mockFetchShippingJobs).not.toHaveBeenCalled()
    },
  )
})

describe('useHistoryStore — 2nd OPS read-only surfaces', () => {
  it('exposes a read-only record fetch and no write action', () => {
    const store = useHistoryStore()

    expect(typeof store.openSecondOpsRecord).toBe('function')
    expect(typeof store.openSecondOpsItem).toBe('function')
    const surface = store as unknown as Record<string, unknown>
    expect(surface.saveSecondOps).toBeUndefined()
    expect(surface.openSecondOps).toBeUndefined()
  })

  it('guards a stale record resolution with the same sequence discipline', async () => {
    const a = deferred<SecondOpsRecord>()
    const b = deferred<SecondOpsRecord>()
    mockFetchSecondOps.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    const store = useHistoryStore()

    store.openSecondOpsRecord(makeJob(1))
    store.openSecondOpsRecord(makeJob(2))

    b.resolve(makeRecord(2))
    await flushPromises()
    a.resolve(makeRecord(1))
    await flushPromises()

    expect(store.secondOpsRecordFetch).toEqual({ status: 'loaded', record: makeRecord(2) })
  })

  it('clears the record modal state on close', async () => {
    mockFetchSecondOps.mockResolvedValue(makeRecord(1))
    const store = useHistoryStore()

    await store.openSecondOpsRecord(makeJob(1))
    store.closeSecondOpsRecord()

    expect(store.secondOpsRecordJob).toBeNull()
    expect(store.secondOpsRecordFetch).toEqual({ status: 'loading' })
  })

  it('holds and clears the item-modal fields without any request', () => {
    const store = useHistoryStore()
    const fields = { description: 'CAP' }

    store.openSecondOpsItem(fields)
    expect(store.secondOpsItem).toEqual(fields)
    expect(mockFetchSecondOps).not.toHaveBeenCalled()

    store.closeSecondOpsItem()
    expect(store.secondOpsItem).toBeNull()
  })
})
