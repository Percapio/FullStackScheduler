import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ReconciliationSidebar from '../ReconciliationSidebar.vue'
import { useStagingStore } from '@/stores/staging'
import type { StagingRowDetail, StagingRowSummary } from '@/api/staging'

const mockFetchErrored      = vi.fn()
const mockFetchDetail       = vi.fn()
const mockSubmitCorrection  = vi.fn()

vi.mock('@/api/staging', () => ({
  fetchErrored:     (...a: unknown[]) => mockFetchErrored(...a),
  fetchDetail:      (...a: unknown[]) => mockFetchDetail(...a),
  submitCorrection: (...a: unknown[]) => mockSubmitCorrection(...a),
}))

const mockShowToast = vi.fn()
vi.mock('@/composables/useToast', () => ({
  useToast: () => ({ toasts: { value: [] }, show: mockShowToast, dismiss: vi.fn() }),
}))

const ts = '2026-04-19T00:00:00'

function summaryFixture(id: number, processing_error = 'Invalid QTY: 0'): StagingRowSummary {
  return {
    id, batch_id: 1, source_row_number: id,
    processing_status: 'error', processing_error,
    suggested_correction: null, resolved_job_id: null, processed_at: null,
    created_at: ts, updated_at: ts,
  } as StagingRowSummary
}

function makeDetail(overrides: Partial<StagingRowDetail> = {}): StagingRowDetail {
  return {
    ...summaryFixture(7),
    raw_job: 'X', raw_qty: '0', raw_customer: 'C',
    raw_shipped: null, raw_pcb_notes: null, raw_kit_notes: null,
    raw_scheduling_notes: null, raw_line_1: null, raw_line_2: null, raw_line_3: null,
    raw_ship_date: null, raw_prog: null, raw_mfg_notes: null,
    raw_smt_lines: null, raw_smt_plcmnts: null, raw_ship_method: null,
    raw_sales_p: null, raw_doc_rel: null, raw_kit_rel: null,
    raw_code: null, raw_bom_compare_photos: null,
    highlight_fields: ['raw_qty'],
    ...overrides,
  } as StagingRowDetail
}

let wrapper: VueWrapper | null = null

function mountWithStore(state: {
  activeErrorRowId?: number | null
  details?: Record<number, StagingRowDetail>
  rows?: StagingRowSummary[]
} = {}) {
  setActivePinia(createPinia())
  const store = useStagingStore()
  if (state.details) Object.assign(store.details, state.details)
  if (state.rows)    store.rows = state.rows
  if (state.activeErrorRowId !== undefined) store.activeErrorRowId = state.activeErrorRowId

  wrapper = mount(ReconciliationSidebar, { attachTo: document.body })
  return { wrapper, store }
}

beforeEach(() => {
  mockFetchErrored.mockReset()
  mockFetchDetail.mockReset()
  mockSubmitCorrection.mockReset()
  mockShowToast.mockReset()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('ReconciliationSidebar', () => {
  it('renders no overlay markup when activeErrorRowId is null', () => {
    mountWithStore({ activeErrorRowId: null })
    expect(document.body.innerHTML).not.toContain('drawer-overlay')
  })

  it('renders processing_error and suggested_correction when both present', async () => {
    mountWithStore({
      activeErrorRowId: 7,
      details: {
        7: makeDetail({
          processing_error: 'Invalid QTY: 0',
          suggested_correction: 'Set raw_qty to a positive integer',
        }),
      },
      rows: [summaryFixture(7)],
    })
    await flushPromises()
    const html = document.body.innerHTML
    expect(html).toContain('Invalid QTY: 0')
    expect(html).toContain('Set raw_qty to a positive integer')
    expect(document.body.querySelector('[data-testid="how-callout"]')).not.toBeNull()
  })

  it('hides How callout when suggested_correction is null', async () => {
    mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail({ suggested_correction: null }) },
      rows: [summaryFixture(7)],
    })
    await flushPromises()
    expect(document.body.querySelector('[data-testid="how-callout"]')).toBeNull()
  })

  it('renders amber ring on highlighted field, not on others', async () => {
    mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail({ highlight_fields: ['raw_qty'] }) },
      rows: [summaryFixture(7)],
    })
    await flushPromises()

    const rawQty = document.body.querySelector('textarea[name="raw_qty"]') as HTMLElement
    const rawJob = document.body.querySelector('textarea[name="raw_job"]') as HTMLElement
    expect(rawQty.classList.contains('ring-2')).toBe(true)
    expect(rawQty.classList.contains('ring-amber-400')).toBe(true)
    expect(rawQty.classList.contains('highlight-pulse-once')).toBe(true)
    expect(rawJob.classList.contains('ring-2')).toBe(false)
    expect(rawJob.classList.contains('ring-amber-400')).toBe(false)
  })

  it('submits via store.correct exactly once with the changed payload', async () => {
    const { store } = mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail() },
      rows: [summaryFixture(7)],
    })
    await flushPromises()
    const correctSpy = vi.spyOn(store, 'correct').mockResolvedValueOnce({ kind: 'ok' })

    const rawQty = document.body.querySelector('textarea[name="raw_qty"]') as HTMLTextAreaElement
    rawQty.value = '5'
    rawQty.dispatchEvent(new Event('input'))
    await flushPromises()

    const submitBtn = document.body.querySelector('[data-testid="sidebar-submit-btn"]') as HTMLButtonElement
    submitBtn.click()
    await flushPromises()

    expect(correctSpy).toHaveBeenCalledTimes(1)
    expect(correctSpy).toHaveBeenCalledWith(7, { raw_qty: '5' })
  })

  it('auto-closes after 200 via the stale-row watcher', async () => {
    const { store } = mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail() },
      rows: [summaryFixture(7)],
    })
    await flushPromises()
    const closeSpy = vi.spyOn(store, 'closeError')

    store.rows = []
    await flushPromises()

    expect(closeSpy).toHaveBeenCalled()
  })

  it('422 keeps the panel open and calls store.loadDetail', async () => {
    const { store } = mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail() },
      rows: [summaryFixture(7)],
    })
    await flushPromises()
    vi.spyOn(store, 'correct').mockResolvedValueOnce({
      kind: 'transform-failed', processingError: 'Invalid JOB cell',
    })
    const loadDetailSpy = vi.spyOn(store, 'loadDetail').mockResolvedValueOnce(undefined)
    const closeSpy = vi.spyOn(store, 'closeError')

    const rawQty = document.body.querySelector('textarea[name="raw_qty"]') as HTMLTextAreaElement
    rawQty.value = '5'
    rawQty.dispatchEvent(new Event('input'))
    await flushPromises()

    const submitBtn = document.body.querySelector('[data-testid="sidebar-submit-btn"]') as HTMLButtonElement
    submitBtn.click()
    await flushPromises()

    expect(loadDetailSpy).toHaveBeenCalledTimes(1)
    expect(loadDetailSpy).toHaveBeenCalledWith(7)
    expect(closeSpy).not.toHaveBeenCalled()
  })

  it('409 closes via explicit closeError and shows a toast', async () => {
    const { store } = mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail() },
      rows: [summaryFixture(7)],
    })
    await flushPromises()
    vi.spyOn(store, 'correct').mockResolvedValueOnce({
      kind: 'conflict', message: 'Row no longer in error state',
    })
    const closeSpy = vi.spyOn(store, 'closeError')

    const rawQty = document.body.querySelector('textarea[name="raw_qty"]') as HTMLTextAreaElement
    rawQty.value = '5'
    rawQty.dispatchEvent(new Event('input'))
    await flushPromises()

    const submitBtn = document.body.querySelector('[data-testid="sidebar-submit-btn"]') as HTMLButtonElement
    submitBtn.click()
    await flushPromises()

    expect(mockShowToast).toHaveBeenCalledWith('Row no longer in error state', 'error')
    expect(closeSpy).toHaveBeenCalledTimes(1)
  })

  it('network outcome keeps the panel open and shows a toast', async () => {
    const { store } = mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail() },
      rows: [summaryFixture(7)],
    })
    await flushPromises()
    vi.spyOn(store, 'correct').mockResolvedValueOnce({
      kind: 'network', message: 'Could not reach the API',
    })
    const closeSpy = vi.spyOn(store, 'closeError')

    const rawQty = document.body.querySelector('textarea[name="raw_qty"]') as HTMLTextAreaElement
    rawQty.value = '5'
    rawQty.dispatchEvent(new Event('input'))
    await flushPromises()

    const submitBtn = document.body.querySelector('[data-testid="sidebar-submit-btn"]') as HTMLButtonElement
    submitBtn.click()
    await flushPromises()

    expect(mockShowToast).toHaveBeenCalledWith('Could not reach the API', 'error')
    expect(closeSpy).not.toHaveBeenCalled()
  })

  // ---- Discard button (Phase 2) ----

  it('discard button is present in the DOM when activeErrorRowId is set', async () => {
    mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail() },
      rows: [summaryFixture(7)],
    })
    await flushPromises()
    expect(document.body.querySelector('[data-testid="sidebar-discard-btn"]')).not.toBeNull()
  })

  it('discard button is disabled when hasChanges is true', async () => {
    mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail() },
      rows: [summaryFixture(7)],
    })
    await flushPromises()

    // Edit a field to make hasChanges true.
    const rawQty = document.body.querySelector('textarea[name="raw_qty"]') as HTMLTextAreaElement
    rawQty.value = '5'
    rawQty.dispatchEvent(new Event('input'))
    await flushPromises()

    const discardBtn = document.body.querySelector('[data-testid="sidebar-discard-btn"]') as HTMLButtonElement
    expect(discardBtn.disabled).toBe(true)
    expect(discardBtn.title).toContain('Reset edits')
  })

  it('discard click with ok result shows success toast and closes panel', async () => {
    const { store } = mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail() },
      rows: [summaryFixture(7)],
    })
    await flushPromises()

    const discardSpy = vi.spyOn(store, 'discardRow').mockResolvedValueOnce({ kind: 'ok' })
    const discardBtn = document.body.querySelector('[data-testid="sidebar-discard-btn"]') as HTMLButtonElement
    discardBtn.click()
    await flushPromises()

    expect(discardSpy).toHaveBeenCalledTimes(1)
    expect(discardSpy).toHaveBeenCalledWith(7)
    expect(mockShowToast).toHaveBeenCalledWith('Row discarded', 'success')
  })

  it('discard click with stale result closes quietly with no toast', async () => {
    const { store } = mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail() },
      rows: [summaryFixture(7)],
    })
    await flushPromises()

    vi.spyOn(store, 'discardRow').mockResolvedValueOnce({ kind: 'stale' })
    const closeSpy = vi.spyOn(store, 'closeError')
    const discardBtn = document.body.querySelector('[data-testid="sidebar-discard-btn"]') as HTMLButtonElement
    discardBtn.click()
    await flushPromises()

    expect(mockShowToast).not.toHaveBeenCalled()
    expect(closeSpy).toHaveBeenCalledTimes(1)
  })

  it('discard click with conflict result shows error toast, reloads, and closes', async () => {
    const { store } = mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail() },
      rows: [summaryFixture(7)],
    })
    await flushPromises()

    vi.spyOn(store, 'discardRow').mockResolvedValueOnce({
      kind: 'conflict',
      message: 'Row already resolved',
    })
    const loadErroredSpy = vi.spyOn(store, 'loadErrored').mockResolvedValueOnce(undefined)
    const closeSpy = vi.spyOn(store, 'closeError')
    const discardBtn = document.body.querySelector('[data-testid="sidebar-discard-btn"]') as HTMLButtonElement
    discardBtn.click()
    await flushPromises()

    expect(mockShowToast).toHaveBeenCalledWith('Row already resolved', 'error')
    expect(loadErroredSpy).toHaveBeenCalledTimes(1)
    expect(closeSpy).toHaveBeenCalledTimes(1)
  })

  it('discard click with network result shows error toast and keeps panel open', async () => {
    const { store } = mountWithStore({
      activeErrorRowId: 7,
      details: { 7: makeDetail() },
      rows: [summaryFixture(7)],
    })
    await flushPromises()

    vi.spyOn(store, 'discardRow').mockResolvedValueOnce({
      kind: 'network',
      message: 'Could not reach the API',
    })
    const closeSpy = vi.spyOn(store, 'closeError')
    const discardBtn = document.body.querySelector('[data-testid="sidebar-discard-btn"]') as HTMLButtonElement
    discardBtn.click()
    await flushPromises()

    expect(mockShowToast).toHaveBeenCalledWith('Could not reach the API', 'error')
    expect(closeSpy).not.toHaveBeenCalled()
  })
})
