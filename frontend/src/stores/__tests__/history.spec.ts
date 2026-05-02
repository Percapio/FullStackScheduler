import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useHistoryStore } from '../history'
import type { JobReadExpanded } from '@/api/history'

const ts = '2026-04-19T00:00:00'

function makeJob(overrides: Partial<JobReadExpanded> = {}): JobReadExpanded {
  return {
    id: 1,
    assembly_id: 1,
    customer_id: 1,
    status: 'shipped',
    quantity: 10,
    shipped_at: '2026-04-01',
    line_1: false,
    line_2: false,
    line_3: false,
    created_at: ts,
    updated_at: ts,
    assembly: { id: 1, part_number: 'TEST-001', created_at: ts, updated_at: ts },
    customer: { id: 1, name: 'TestCo', created_at: ts, updated_at: ts },
    ...overrides,
  } as JobReadExpanded
}

const mockFetchHistory = vi.fn()
const mockFetchLineage = vi.fn()

vi.mock('@/api/history', () => ({
  fetchJobHistory: (...args: unknown[]) => mockFetchHistory(...args),
  fetchJobLineage: (...args: unknown[]) => mockFetchLineage(...args),
}))

vi.mock('@/composables/useToast', () => ({
  useToast: () => ({ toasts: { value: [] }, show: vi.fn(), dismiss: vi.fn() }),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  mockFetchHistory.mockReset()
  mockFetchLineage.mockReset()
})

describe('useHistoryStore', () => {
  it('load() populates rows + total and resets expanded/lineage', async () => {
    const jobs = [makeJob({ id: 1 }), makeJob({ id: 2 })]
    mockFetchHistory.mockResolvedValue({ rows: jobs, total: 2 })

    const store = useHistoryStore()
    await store.load()

    expect(store.rows).toHaveLength(2)
    expect(store.total).toBe(2)
    expect(store.expanded.size).toBe(0)
    expect(store.lineage.size).toBe(0)
  })

  it('next() increments offset by limit and reloads', async () => {
    mockFetchHistory.mockResolvedValue({ rows: Array.from({ length: 50 }, (_, i) => makeJob({ id: i })), total: 100 })

    const store = useHistoryStore()
    await store.load()
    expect(store.offset).toBe(0)

    await store.next()
    expect(store.offset).toBe(50)
    expect(mockFetchHistory).toHaveBeenCalledWith(50, 50, null)
  })

  it('prev() clamps offset to 0', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [makeJob()], total: 1 })

    const store = useHistoryStore()
    store.offset = 10
    await store.prev()
    expect(store.offset).toBe(0)
  })

  it('page change wipes expanded set, lineage cache, AND inspected drawer (Q7 + V1)', async () => {
    const jobs = Array.from({ length: 50 }, (_, i) => makeJob({ id: i + 1 }))
    mockFetchHistory.mockResolvedValue({ rows: jobs, total: 100 })
    mockFetchLineage.mockResolvedValue([makeJob({ id: 99 })])

    const store = useHistoryStore()
    await store.load()

    await store.toggleExpand(1)
    store.inspect(jobs[0])
    expect(store.expanded.has(1)).toBe(true)
    expect(store.lineage.has(1)).toBe(true)
    expect(store.inspected).not.toBeNull()

    await store.next()
    expect(store.expanded.size).toBe(0)
    expect(store.lineage.size).toBe(0)
    expect(store.inspected).toBeNull()
  })

  it('toggleExpand triggers fetchJobLineage on first expand only (cache hit on reopen)', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [makeJob({ id: 5 })], total: 1 })
    mockFetchLineage.mockResolvedValue([makeJob({ id: 5 }), makeJob({ id: 6 })])

    const store = useHistoryStore()
    await store.load()

    await store.toggleExpand(5)
    expect(mockFetchLineage).toHaveBeenCalledTimes(1)
    expect(store.expanded.has(5)).toBe(true)

    await store.toggleExpand(5)
    expect(store.expanded.has(5)).toBe(false)

    await store.toggleExpand(5)
    expect(mockFetchLineage).toHaveBeenCalledTimes(1)
  })

  it('toggleExpand handles fetch rejection by storing {status:"error"} in cache', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [makeJob({ id: 7 })], total: 1 })
    mockFetchLineage.mockRejectedValue(new Error('Network'))

    const store = useHistoryStore()
    await store.load()
    await store.toggleExpand(7)

    const state = store.lineage.get(7)
    expect(state?.status).toBe('error')
  })

  it('inspect/closeInspect drive inspected ref', async () => {
    const store = useHistoryStore()
    const job = makeJob({ id: 42 })

    store.inspect(job)
    expect(store.inspected).toStrictEqual(job)

    store.closeInspect()
    expect(store.inspected).toBeNull()
  })

  it('setSearch updates searchQuery, resets offset to 0, and reloads', async () => {
    const first = Array.from({ length: 50 }, (_, i) => makeJob({ id: i + 1 }))
    const second = Array.from({ length: 50 }, (_, i) => makeJob({ id: i + 51 }))
    mockFetchHistory.mockResolvedValueOnce({ rows: first, total: 100 })
    const store = useHistoryStore()
    await store.load()

    mockFetchHistory.mockResolvedValueOnce({ rows: second, total: 100 })
    await store.next()
    expect(store.offset).toBe(50)

    mockFetchHistory.mockResolvedValueOnce({ rows: [makeJob({ id: 1 })], total: 1 })
    await store.setSearch('acme')

    expect(store.searchQuery).toBe('acme')
    expect(store.offset).toBe(0)
    expect(store.rows).toHaveLength(1)
  })

  it('setSearch passes the trimmed search string through to fetchJobHistory', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [], total: 0 })
    const store = useHistoryStore()
    await store.setSearch('  Acme  ')
    expect(mockFetchHistory).toHaveBeenLastCalledWith(50, 0, 'Acme')

    await store.setSearch('')
    expect(mockFetchHistory).toHaveBeenLastCalledWith(50, 0, null)
  })

  it('hasPrev/hasNext reflect offset vs total correctly', async () => {
    mockFetchHistory.mockResolvedValue({ rows: Array.from({ length: 50 }, (_, i) => makeJob({ id: i })), total: 75 })

    const store = useHistoryStore()
    await store.load()

    expect(store.hasPrev).toBe(false)
    expect(store.hasNext).toBe(true)

    mockFetchHistory.mockResolvedValue({ rows: Array.from({ length: 25 }, (_, i) => makeJob({ id: i + 50 })), total: 75 })
    await store.next()

    expect(store.hasPrev).toBe(true)
    expect(store.hasNext).toBe(false)
  })
})
