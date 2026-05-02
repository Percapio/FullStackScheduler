import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import HistoryView from '../HistoryView.vue'
import { useHistoryStore } from '@/stores/history'
import type { JobReadExpanded } from '@/api/history'

const ts = '2026-04-19T00:00:00'

function makeJob(overrides: Partial<JobReadExpanded> & { _pn?: string; _cname?: string } = {}): JobReadExpanded {
  const { _pn = 'TEST-001', _cname = 'Acme', ...rest } = overrides
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
    assembly: { id: 1, part_number: _pn, created_at: ts, updated_at: ts },
    customer: { id: 1, name: _cname, created_at: ts, updated_at: ts },
    ...rest,
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

vi.mock('@/composables/useFontSize', () => ({
  useFontSize: () => ({
    fontClass: { value: 'text-base' },
    canDecrease: { value: true },
    canIncrease: { value: true },
    decrease: vi.fn(),
    increase: vi.fn(),
  }),
}))

function mountView(opts: { stubDrawer?: boolean; stubLineage?: boolean } = {}) {
  const { stubDrawer = true, stubLineage = false } = opts
  const stubs: Record<string, boolean> = {}
  if (stubDrawer) stubs.InspectDrawer = true
  if (stubLineage) stubs.LineageAccordion = true
  return mount(HistoryView, {
    global: {
      plugins: [createPinia()],
      stubs,
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  mockFetchHistory.mockReset()
  mockFetchLineage.mockReset()
})

describe('HistoryView', () => {
  it('renders a row per history entry after load', async () => {
    mockFetchHistory.mockResolvedValue({
      rows: [
        makeJob({ id: 1, _pn: 'A', shipped_at: '2026-04-01' }),
        makeJob({ id: 2, _pn: 'B', shipped_at: '2026-04-02' }),
        makeJob({ id: 3, _pn: 'C', shipped_at: '2026-04-03' }),
      ],
      total: 3,
    })
    const w = mountView()
    await flushPromises()
    const rows = w.findAll('tbody tr')
    expect(rows.length).toBe(3)
  })

  it('clicking a row toggles the accordion', async () => {
    mockFetchHistory.mockResolvedValue({
      rows: [makeJob({ id: 10 })],
      total: 1,
    })
    mockFetchLineage.mockResolvedValue([makeJob({ id: 10 })])
    const w = mountView()
    await flushPromises()

    const row = w.find('tbody tr')
    await row.trigger('click')
    await flushPromises()
    expect(w.findComponent({ name: 'LineageAccordion' }).exists()).toBe(true)
  })

  it('clicking the eye icon opens the drawer but does not toggle the accordion (Q5)', async () => {
    mockFetchHistory.mockResolvedValue({
      rows: [makeJob({ id: 20 })],
      total: 1,
    })
    const w = mountView()
    await flushPromises()

    const eyeBtn = w.find('button[aria-label="Inspect job 20"]')
    await eyeBtn.trigger('click')
    await flushPromises()

    expect(w.findComponent({ name: 'LineageAccordion' }).exists()).toBe(false)
  })

  it('empty state card renders "No shipped jobs" when total=0 and no active search', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [], total: 0 })
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('No shipped jobs')
  })

  it('empty state differentiates when searchQuery is active', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [], total: 0 })
    const w = mountView()
    await flushPromises()
    const store = useHistoryStore()
    store.searchQuery = 'xyz'
    await flushPromises()
    expect(w.text()).toContain('No matches for')
    expect(w.text()).toContain('xyz')
  })

  it('error banner renders with a working Retry button', async () => {
    mockFetchHistory.mockRejectedValueOnce(new Error('fail'))
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('Could not load shipped jobs')

    mockFetchHistory.mockResolvedValue({ rows: [makeJob()], total: 1 })
    await w.find('button').trigger('click')
    await flushPromises()
    expect(w.text()).not.toContain('Could not load shipped jobs')
  })

  it('does NOT render the old <footer> pagination block (moved to AppNav)', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [makeJob({ id: 1 })], total: 1 })
    const w = mountView()
    await flushPromises()
    expect(w.find('footer').exists()).toBe(false)
    expect(w.text()).not.toContain('Showing 1–1 of 1')
  })

  it('@inspect from a LineageAccordion Eye button forwards to store.inspect', async () => {
    mockFetchHistory.mockResolvedValue({
      rows: [makeJob({ id: 100, _pn: 'PARENT' })],
      total: 1,
    })
    mockFetchLineage.mockResolvedValue([
      makeJob({ id: 100, _pn: 'PARENT' }),
      makeJob({ id: 201, _pn: 'CHILD-A' }),
    ])
    const w = mountView()
    await flushPromises()

    await w.find('tbody tr').trigger('click')
    await flushPromises()

    await w.find('button[aria-label="Inspect job 201"]').trigger('click')
    await flushPromises()

    const store = useHistoryStore()
    expect(store.inspected?.id).toBe(201)
  })
})
