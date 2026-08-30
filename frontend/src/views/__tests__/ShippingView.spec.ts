import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ShippingView from '../ShippingView.vue'
import type { JobReadExpanded } from '@/api/shipping'
import { useShippingStore } from '@/stores/shipping'

const ts = '2026-04-19T00:00:00'

function makeJob(overrides: Partial<JobReadExpanded> & { _pn?: string; _cid?: number; _cname?: string } = {}): JobReadExpanded {
  const { _pn = '100000', _cid = 1, _cname = 'Acme', ...rest } = overrides
  return {
    id: 1,
    assembly_id: 1,
    customer_id: _cid,
    status: 'planned',
    quantity: 10,
    line_1: false,
    line_2: false,
    line_3: false,
    created_at: ts,
    updated_at: ts,
    assembly: { id: 1, part_number: _pn, created_at: ts, updated_at: ts },
    customer: { id: _cid, name: _cname, created_at: ts, updated_at: ts },
    ...rest,
  } as JobReadExpanded
}

const mockFetch = vi.fn()
vi.mock('@/api/shipping', () => ({
  fetchShippingJobs: (...args: unknown[]) => mockFetch(...args),
  fetchDiscardedJobs: vi.fn().mockResolvedValue({ rows: [], total: 0 }),
  discardShippingJob: vi.fn().mockResolvedValue(undefined),
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

function mountView() {
  return mount(ShippingView, {
    global: { plugins: [createPinia()] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  mockFetch.mockReset()
})

describe('ShippingView', () => {
  it('renders one row per job (flat, no grouping)', async () => {
    mockFetch.mockResolvedValue({
      rows: [
        makeJob({ id: 1, _pn: 'A', _cid: 1, _cname: 'C1', resolved_ship_date: '2026-05-01' }),
        makeJob({ id: 2, _pn: 'A', _cid: 1, _cname: 'C1', resolved_ship_date: '2026-06-01' }),
        makeJob({ id: 3, _pn: 'B', _cid: 2, _cname: 'C2', resolved_ship_date: '2026-04-01' }),
      ],
      total: 3,
    })
    const w = mountView()
    await flushPromises()
    const rows = w.findAll('tbody tr')
    expect(rows.length).toBe(3)
  })

  it('renders the empty-state when the API returns []', async () => {
    mockFetch.mockResolvedValue({ rows: [], total: 0 })
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('No open jobs')
  })

  it('sorts rows when a column header is clicked', async () => {
    mockFetch.mockResolvedValue({
      rows: [
        makeJob({ id: 1, _pn: 'Zebra', _cid: 1, _cname: 'C1', resolved_ship_date: '2026-06-01' }),
        makeJob({ id: 2, _pn: 'Alpha', _cid: 2, _cname: 'C2', resolved_ship_date: '2026-05-01' }),
      ],
      total: 2,
    })
    const w = mountView()
    await flushPromises()

    const jobHeader = w.findAll('th').find(th => th.text().includes('Job'))!
    await jobHeader.trigger('click')
    const rows = w.findAll('tbody tr')
    expect(rows[0].text()).toContain('Alpha')
  })

  it('renders "—" for null resolved_ship_date', async () => {
    mockFetch.mockResolvedValue({
      rows: [makeJob({ id: 1, _pn: 'A', _cid: 1, resolved_ship_date: null })],
      total: 1,
    })
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('—')
  })

  it('shows the error banner with a Retry button when the fetch rejects', async () => {
    mockFetch.mockRejectedValue(new Error('Network error'))
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('Could not load open jobs')
    const retryBtn = w.findAll('button').find(b => b.text().includes('Retry'))!
    expect(retryBtn).toBeTruthy()

    mockFetch.mockResolvedValue({
      rows: [makeJob({ id: 1, _pn: 'A', _cid: 1, resolved_ship_date: '2026-05-01' })],
      total: 1,
    })
    await retryBtn.trigger('click')
    await flushPromises()
    expect(w.text()).not.toContain('Could not load open jobs')
    expect(w.text()).toContain('A')
  })

  it('renders Markdown in the MFG NOTES column as HTML', async () => {
    mockFetch.mockResolvedValue({
      rows: [makeJob({
        id: 1, _pn: 'A', _cid: 1,
        resolved_ship_date: '2026-05-01',
        assembly: { id: 1, part_number: 'A', base_mfg_notes: '**bold text**', created_at: ts, updated_at: ts } as JobReadExpanded['assembly'],
      })],
      total: 1,
    })
    const w = mountView()
    await flushPromises()
    expect(w.html()).toContain('<strong>bold text</strong>')
    expect(w.html()).toContain('<li><strong>bold text</strong></li>')
  })

  it('renders strikethrough in MFG NOTES', async () => {
    mockFetch.mockResolvedValue({
      rows: [makeJob({
        id: 1, _pn: 'A', _cid: 1,
        resolved_ship_date: '2026-05-01',
        assembly: { id: 1, part_number: 'A', base_mfg_notes: '~~removed~~', created_at: ts, updated_at: ts } as JobReadExpanded['assembly'],
      })],
      total: 1,
    })
    const w = mountView()
    await flushPromises()
    expect(w.html()).not.toContain('removed')
  })

  it('renders newlines in MFG NOTES as <br>', async () => {
    mockFetch.mockResolvedValue({
      rows: [makeJob({
        id: 1, _pn: 'A', _cid: 1,
        resolved_ship_date: '2026-05-01',
        assembly: { id: 1, part_number: 'A', base_mfg_notes: 'line one\nline two', created_at: ts, updated_at: ts } as JobReadExpanded['assembly'],
      })],
      total: 1,
    })
    const w = mountView()
    await flushPromises()
    expect(w.html()).toContain('<li>line one</li>')
    expect(w.html()).toContain('<li>line two</li>')
  })

  it('displays build_type uppercased, blanks for new', async () => {
    mockFetch.mockResolvedValue({
      rows: [
        makeJob({ id: 1, _pn: 'A', _cid: 1, build_type: 'rowc', resolved_ship_date: '2026-05-01' }),
        makeJob({ id: 2, _pn: 'B', _cid: 1, build_type: 'new', resolved_ship_date: '2026-05-02' }),
      ],
      total: 2,
    })
    const w = mountView()
    await flushPromises()
    const rows = w.findAll('tbody tr')
    expect(rows[0].text()).toContain('ROWC')
    const buildCells = rows[1].findAll('td')
    expect(buildCells[3].text().trim()).toBe('')
  })

  it('zebra-stripes alternate rows', async () => {
    mockFetch.mockResolvedValue({
      rows: [
        makeJob({ id: 1, _pn: 'A', _cid: 1, resolved_ship_date: '2026-05-01' }),
        makeJob({ id: 2, _pn: 'B', _cid: 2, resolved_ship_date: '2026-05-02' }),
      ],
      total: 2,
    })
    const w = mountView()
    await flushPromises()
    const rows = w.findAll('tbody tr')
    expect(rows[0].classes()).toContain('odd:bg-white')
    expect(rows[0].classes()).toContain('dark:odd:bg-slate-800')
    expect(rows[1].classes()).toContain('even:bg-slate-100')
    expect(rows[1].classes()).toContain('dark:even:bg-slate-820')
  })

  it('un-shipped job (status=planned) appears in the view after re-fetch', async () => {
    // Regression for Phase 16 un-ship: when a previously-shipped job transitions
    // back to planned, it must appear in ShippingView on the next poll.
    mockFetch.mockResolvedValueOnce({ rows: [], total: 0 })
    const w = mountView()
    await flushPromises()
    expect(w.findAll('tbody tr').length).toBe(0)

    // Job was un-shipped server-side; next fetch returns it with status=planned
    mockFetch.mockResolvedValueOnce({
      rows: [makeJob({ id: 42, _pn: 'RESHIP', status: 'planned', shipped_at: null, resolved_ship_date: '2026-05-15' })],
      total: 1,
    })
    await useShippingStore().load()
    await flushPromises()

    expect(w.findAll('tbody tr').length).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// 2nd OPS column (Phase 22)
// ---------------------------------------------------------------------------

const mockFetchSecondOps = vi.fn()
const mockPutSecondOps = vi.fn()

vi.mock('@/api/secondOps', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/secondOps')>()),
  fetchSecondOps: (...args: unknown[]) => mockFetchSecondOps(...args),
  putSecondOps: (...args: unknown[]) => mockPutSecondOps(...args),
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

function unauditedRecord(jobId: number) {
  return {
    job_id: jobId,
    state: 'unaudited',
    reviewed_at: null,
    unexpected_inclusions: null,
    lines: [],
    limits: { max_lines: 500, note_max_chars: 4000 },
  }
}

describe('ShippingView — 2nd OPS column', () => {
  beforeEach(() => {
    mockFetchSecondOps.mockReset()
    mockPutSecondOps.mockReset()
    document.body.innerHTML = ''
  })

  it('renders a plain 2nd OPS header that is not a SortHeader', async () => {
    mockFetch.mockResolvedValue({ rows: [makeJob({ id: 1 })], total: 1 })
    const w = mountView()
    await flushPromises()

    const headers = w.findAll('thead th').map((th) => th.text())
    expect(headers).toContain('2nd OPS')
    const sortHeaders = w.findAllComponents({ name: 'SortHeader' }).map((c) => c.props('label'))
    expect(sortHeaders).not.toContain('2nd OPS')
  })

  it('opens the entry modal from the Audit button on an unaudited job', async () => {
    mockFetch.mockResolvedValue({
      rows: [
        makeJob({
          id: 7,
          second_ops: {
            state: 'unaudited',
            line_count: 0,
            reviewed_at: null,
            has_unexpected_inclusions: false,
            preview: [],
          },
        } as never),
      ],
      total: 1,
    })
    mockFetchSecondOps.mockResolvedValue(unauditedRecord(7))
    const w = mountView()
    await flushPromises()

    await w.find('[data-testid="second-ops-audit-btn"]').trigger('click')
    await flushPromises()

    expect(document.body.querySelector('[data-testid="second-ops-entry-modal"]')).not.toBeNull()
    expect(document.body.querySelector('[data-testid="second-ops-paste-area"]')).not.toBeNull()
    expect(mockFetchSecondOps).toHaveBeenCalledWith(7)
    w.unmount()
  })

  it('opens the item modal from a preview line with no network request', async () => {
    mockFetch.mockResolvedValue({
      rows: [
        makeJob({
          id: 8,
          second_ops: {
            state: 'recorded',
            line_count: 1,
            reviewed_at: '2026-08-28T10:00:00',
            has_unexpected_inclusions: false,
            preview: [makeSecondOpsLine()],
          },
        } as never),
      ],
      total: 1,
    })
    const w = mountView()
    await flushPromises()

    await w.find('[data-testid="second-ops-preview-line"]').trigger('click')
    await flushPromises()

    expect(document.body.querySelector('[data-testid="second-ops-item-modal"]')).not.toBeNull()
    expect(
      document.body.querySelector('[data-testid="second-ops-item-ref_des"]')?.textContent,
    ).toBe('C1, C2')
    expect(mockFetchSecondOps).not.toHaveBeenCalled()
    w.unmount()
  })

  it('renders nothing in the cell when second_ops is null', async () => {
    mockFetch.mockResolvedValue({ rows: [makeJob({ id: 1 })], total: 1 })
    const w = mountView()
    await flushPromises()

    expect(w.find('[data-testid="second-ops-cell"]').exists()).toBe(false)
    expect(w.find('[data-testid="second-ops-audit-btn"]').exists()).toBe(false)
  })

  describe('ship-today signal', () => {
    // Fake timers drive `new Date()` inside the shop clock, so the Pacific date
    // the formatter derives is deterministic. 18:00Z is mid-morning PDT, well
    // clear of either midnight boundary.
    afterEach(() => { vi.useRealTimers() })

    it('marks only the job whose resolved ship date is today in shop time', async () => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-04-19T18:00:00Z'))
      mockFetch.mockResolvedValue({
        rows: [
          makeJob({ id: 1, _pn: 'A', _cid: 1, resolved_ship_date: '2026-04-19' }),
          makeJob({ id: 2, _pn: 'B', _cid: 2, resolved_ship_date: '2026-04-20' }),
          makeJob({ id: 3, _pn: 'C', _cid: 3, resolved_ship_date: null }),
        ],
        total: 3,
      })
      const w = mountView()
      await flushPromises()

      const cells = w.findAll('[data-testid="ship-date-cell"]')
      expect(cells.length).toBe(3)
      expect(cells[0].classes()).toContain('text-ship-today')
      expect(cells[1].classes()).not.toContain('text-ship-today')
      expect(cells[2].classes()).not.toContain('text-ship-today')
      expect(cells[1].classes()).toContain('text-slate-700')
    })

    it('resolves today in Pacific time, not the viewer local zone', async () => {
      // 05:00Z on the 20th is still 22:00 PDT on the 19th. A viewer in UTC would
      // see the 20th; the shop floor has not turned over yet.
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-04-20T05:00:00Z'))
      mockFetch.mockResolvedValue({
        rows: [makeJob({ id: 1, _pn: 'A', _cid: 1, resolved_ship_date: '2026-04-19' })],
        total: 1,
      })
      const w = mountView()
      await flushPromises()

      expect(w.get('[data-testid="ship-date-cell"]').classes()).toContain('text-ship-today')
    })
  })
})
