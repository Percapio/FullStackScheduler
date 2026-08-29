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

  it('un-shipped job is no longer rendered after a re-fetch', async () => {
    // Regression for Phase 16 un-ship: HistoryView must reflect the re-fetched
    // snapshot; a job whose status flips to planned is omitted by the API and
    // must disappear from the view without a page reload.
    mockFetchHistory.mockResolvedValueOnce({
      rows: [makeJob({ id: 42, _pn: 'RESHIP', status: 'shipped', shipped_at: '2026-04-01' })],
      total: 1,
    })
    const w = mountView()
    await flushPromises()
    expect(w.findAll('tbody tr').length).toBe(1)

    // Job was un-shipped server-side; the history endpoint omits it next poll
    mockFetchHistory.mockResolvedValueOnce({ rows: [], total: 0 })
    await useHistoryStore().load()
    await flushPromises()

    expect(w.findAll('tbody tr').length).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// 2nd OPS column (Phase 22)
// ---------------------------------------------------------------------------

const mockFetchSecondOps = vi.fn()

vi.mock('@/api/secondOps', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/secondOps')>()),
  fetchSecondOps: (...args: unknown[]) => mockFetchSecondOps(...args),
  putSecondOps: vi.fn(),
}))

function makeSecondOpsLine(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    line_order: 0,
    find_number: '1',
    component_part_number: 'CMP-1',
    per_board_count: '2',
    ref_des: 'C1, C2',
    description: 'CAP 0.1uF',
    mount_type: 'SMT',
    quantity_needed: '40',
    quantity_on_hand: '500',
    ...overrides,
  }
}

function withSecondOps(id: number, overrides: Record<string, unknown> = {}) {
  return makeJob({
    id,
    second_ops: {
      state: 'recorded',
      line_count: 56,
      reviewed_at: '2026-08-28T10:00:00',
      has_unexpected_inclusions: true,
      preview: [makeSecondOpsLine()],
      ...overrides,
    },
  } as never)
}

describe('HistoryView — 2nd OPS column', () => {
  beforeEach(() => {
    mockFetchSecondOps.mockReset()
    document.body.innerHTML = ''
  })

  it('renders a 2nd OPS header', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [withSecondOps(1)], total: 1 })
    const w = mountView()
    await flushPromises()

    expect(w.findAll('thead th').map((th) => th.text())).toContain('2nd OPS')
  })

  it('gives the lineage row a colspan equal to the rendered th count', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [withSecondOps(10)], total: 1 })
    mockFetchLineage.mockResolvedValue([makeJob({ id: 10 })])
    const w = mountView({ stubLineage: true })
    await flushPromises()

    const thCount = w.findAll('thead th').length
    await w.find('tbody tr').trigger('click')
    await flushPromises()

    const lineageCell = w.findAll('tbody td').find((td) => td.attributes('colspan'))
    expect(thCount).toBe(8)
    expect(lineageCell?.attributes('colspan')).toBe(String(thCount))
  })

  it('renders no Audit and no EDIT — History is read-only', async () => {
    mockFetchHistory.mockResolvedValue({
      rows: [withSecondOps(1, { state: 'unaudited', line_count: 0, preview: [], reviewed_at: null })],
      total: 1,
    })
    const w = mountView()
    await flushPromises()

    expect(w.find('[data-testid="second-ops-audit-btn"]').exists()).toBe(false)
    expect(w.find('[data-testid="second-ops-edit-btn"]').exists()).toBe(false)
  })

  it('offers View all (56) and opens the read-only record modal', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [withSecondOps(1)], total: 1 })
    mockFetchSecondOps.mockResolvedValue({
      job_id: 1,
      state: 'recorded',
      reviewed_at: '2026-08-28T10:00:00',
      unexpected_inclusions: 'extra washer in kit',
      lines: [makeSecondOpsLine()],
      limits: { max_lines: 500, note_max_chars: 4000 },
    })
    const w = mountView()
    await flushPromises()

    const viewAll = w.find('[data-testid="second-ops-view-all-btn"]')
    expect(viewAll.text()).toContain('56')
    await viewAll.trigger('click')
    await flushPromises()

    expect(document.body.querySelector('[data-testid="second-ops-record-modal"]')).not.toBeNull()
    expect(
      document.body.querySelector('[data-testid="second-ops-record-note"]')?.textContent,
    ).toContain('extra washer in kit')
    w.unmount()
  })

  it('opens the item modal on a preview click without toggling the accordion', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [withSecondOps(30)], total: 1 })
    const w = mountView({ stubLineage: true })
    await flushPromises()

    await w.find('[data-testid="second-ops-preview-line"]').trigger('click')
    await flushPromises()

    expect(document.body.querySelector('[data-testid="second-ops-item-modal"]')).not.toBeNull()
    expect(w.findComponent({ name: 'LineageAccordion' }).exists()).toBe(false)
    w.unmount()
  })

  it('renders nothing in the cell when second_ops is null', async () => {
    mockFetchHistory.mockResolvedValue({ rows: [makeJob({ id: 1 })], total: 1 })
    const w = mountView()
    await flushPromises()

    expect(w.find('[data-testid="second-ops-cell"]').exists()).toBe(false)
  })

  it('renders a script tag in a description as literal text', async () => {
    mockFetchHistory.mockResolvedValue({
      rows: [
        withSecondOps(1, {
          preview: [makeSecondOpsLine({ description: '<script>alert(1)</script>' })],
        }),
      ],
      total: 1,
    })
    const w = mountView()
    await flushPromises()

    expect(w.element.querySelector('script')).toBeNull()
    expect(w.text()).toContain('<script>alert(1)</script>')
  })
})
