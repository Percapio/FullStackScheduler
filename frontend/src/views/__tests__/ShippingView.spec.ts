import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ShippingView from '../ShippingView.vue'
import type { JobReadExpanded } from '@/api/shipping'

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
    expect(w.find('button').text()).toContain('Retry')

    mockFetch.mockResolvedValue({
      rows: [makeJob({ id: 1, _pn: 'A', _cid: 1, resolved_ship_date: '2026-05-01' })],
      total: 1,
    })
    await w.find('button').trigger('click')
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
    expect(w.html()).toContain('<del>removed</del>')
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
    expect(w.html()).toContain('<br>')
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
})
