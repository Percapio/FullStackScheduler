import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import DiscardedRowsDrawer from '../DiscardedRowsDrawer.vue'
import { useStagingStore } from '@/stores/staging'
import type { StagingRowSummary } from '@/api/staging'

const mockFetchErrored    = vi.fn()
const mockFetchDetail     = vi.fn()
const mockFetchDiscarded  = vi.fn()
const mockDeleteStagingRow = vi.fn()
const mockPostRestoreStagingRow = vi.fn()

vi.mock('@/api/staging', () => ({
  fetchErrored:           (...a: unknown[]) => mockFetchErrored(...a),
  fetchDetail:            (...a: unknown[]) => mockFetchDetail(...a),
  fetchDiscarded:         (...a: unknown[]) => mockFetchDiscarded(...a),
  deleteStagingRow:       (...a: unknown[]) => mockDeleteStagingRow(...a),
  postRestoreStagingRow:  (...a: unknown[]) => mockPostRestoreStagingRow(...a),
}))

const mockShowToast = vi.fn()
vi.mock('@/composables/useToast', () => ({
  useToast: () => ({ toasts: { value: [] }, show: mockShowToast, dismiss: vi.fn() }),
}))

const TS = '2026-05-01T12:00:00Z'

function summaryFixture(id: number): StagingRowSummary {
  return {
    id,
    batch_id: 1,
    source_row_number: id,
    processing_status: 'error',
    processing_error: `Error on row ${id}`,
    suggested_correction: null,
    resolved_job_id: null,
    processed_at: null,
    discarded_at: TS,
    created_at: TS,
    updated_at: TS,
  } as StagingRowSummary
}

let wrapper: VueWrapper | null = null

function mountDrawer(state: {
  discardedDrawerOpen?: boolean
  discardedRows?: StagingRowSummary[]
  discardedTotal?: number
  discardedLoading?: boolean
} = {}) {
  setActivePinia(createPinia())
  const store = useStagingStore()
  if (state.discardedDrawerOpen !== undefined) store.discardedDrawerOpen = state.discardedDrawerOpen
  if (state.discardedRows !== undefined) store.discardedRows = state.discardedRows
  if (state.discardedTotal !== undefined) store.discardedTotal = state.discardedTotal
  if (state.discardedLoading !== undefined) store.discardedLoading = state.discardedLoading

  wrapper = mount(DiscardedRowsDrawer, { attachTo: document.body })
  return { wrapper, store }
}

beforeEach(() => {
  mockFetchErrored.mockReset()
  mockFetchDetail.mockReset()
  mockFetchDiscarded.mockReset()
  mockDeleteStagingRow.mockReset()
  mockPostRestoreStagingRow.mockReset()
  mockShowToast.mockReset()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('DiscardedRowsDrawer', () => {
  it('renders no overlay markup when discardedDrawerOpen is false', () => {
    mountDrawer({ discardedDrawerOpen: false })
    expect(document.body.innerHTML).not.toContain('drawer-overlay')
  })

  it('renders rows with source row, batch, error, and Restore button when open', async () => {
    mountDrawer({
      discardedDrawerOpen: true,
      discardedRows: [summaryFixture(3), summaryFixture(7)],
      discardedTotal: 2,
    })
    await flushPromises()
    const html = document.body.innerHTML
    expect(html).toContain('Error on row 3')
    expect(html).toContain('Error on row 7')
    expect(document.body.querySelectorAll('[data-testid^="restore-btn-"]')).toHaveLength(2)
  })

  it('renders empty state when discardedTotal is 0 and not loading', async () => {
    mountDrawer({
      discardedDrawerOpen: true,
      discardedRows: [],
      discardedTotal: 0,
      discardedLoading: false,
    })
    await flushPromises()
    expect(document.body.innerHTML).toContain('No discarded rows.')
  })

  it('calls store.restoreRow exactly once when Restore is clicked', async () => {
    const { store } = mountDrawer({
      discardedDrawerOpen: true,
      discardedRows: [summaryFixture(5)],
      discardedTotal: 1,
    })
    await flushPromises()

    const restoreSpy = vi.spyOn(store, 'restoreRow').mockResolvedValueOnce({ kind: 'ok', row: summaryFixture(5) })
    const btn = document.body.querySelector('[data-testid="restore-btn-5"]') as HTMLButtonElement
    btn.click()
    await flushPromises()

    expect(restoreSpy).toHaveBeenCalledTimes(1)
    expect(restoreSpy).toHaveBeenCalledWith(5)
  })

  it('shows success toast on successful restore', async () => {
    const { store } = mountDrawer({
      discardedDrawerOpen: true,
      discardedRows: [summaryFixture(5)],
      discardedTotal: 1,
    })
    await flushPromises()

    vi.spyOn(store, 'restoreRow').mockResolvedValueOnce({ kind: 'ok', row: summaryFixture(5) })
    const btn = document.body.querySelector('[data-testid="restore-btn-5"]') as HTMLButtonElement
    btn.click()
    await flushPromises()

    expect(mockShowToast).toHaveBeenCalledWith('Row 5 restored', 'success')
  })

  it('shows error toast on conflict restore', async () => {
    const { store } = mountDrawer({
      discardedDrawerOpen: true,
      discardedRows: [summaryFixture(5)],
      discardedTotal: 1,
    })
    await flushPromises()

    vi.spyOn(store, 'restoreRow').mockResolvedValueOnce({
      kind: 'conflict',
      message: 'Row is not discarded',
    })
    const btn = document.body.querySelector('[data-testid="restore-btn-5"]') as HTMLButtonElement
    btn.click()
    await flushPromises()

    expect(mockShowToast).toHaveBeenCalledWith('Row is not discarded', 'error')
  })

  it('does not show a toast on stale restore outcome', async () => {
    const { store } = mountDrawer({
      discardedDrawerOpen: true,
      discardedRows: [summaryFixture(5)],
      discardedTotal: 1,
    })
    await flushPromises()

    vi.spyOn(store, 'restoreRow').mockResolvedValueOnce({ kind: 'stale' })
    const btn = document.body.querySelector('[data-testid="restore-btn-5"]') as HTMLButtonElement
    btn.click()
    await flushPromises()

    expect(mockShowToast).not.toHaveBeenCalled()
  })

  it('sets discardedDrawerOpen to false when close button is clicked', async () => {
    const { store } = mountDrawer({
      discardedDrawerOpen: true,
      discardedRows: [],
      discardedTotal: 0,
    })
    await flushPromises()

    const closeBtn = document.body.querySelector('[data-testid="discarded-drawer-close-btn"]') as HTMLButtonElement
    closeBtn.click()
    await flushPromises()

    expect(store.discardedDrawerOpen).toBe(false)
  })

  it('renders truncation footer when discardedTotal > discardedRows.length', async () => {
    const rows = Array.from({ length: 3 }, (_, i) => summaryFixture(i + 1))
    mountDrawer({
      discardedDrawerOpen: true,
      discardedRows: rows,
      discardedTotal: 10,
    })
    await flushPromises()

    const footer = document.body.querySelector('[data-testid="discarded-truncation-footer"]')
    expect(footer).not.toBeNull()
    expect(footer!.textContent).toContain('Showing 3 of 10')
  })

  it('does not render truncation footer when total equals row count', async () => {
    const rows = Array.from({ length: 3 }, (_, i) => summaryFixture(i + 1))
    mountDrawer({
      discardedDrawerOpen: true,
      discardedRows: rows,
      discardedTotal: 3,
    })
    await flushPromises()

    expect(document.body.querySelector('[data-testid="discarded-truncation-footer"]')).toBeNull()
  })

  it('header text uses parenthetical format with count when nonzero', async () => {
    mountDrawer({
      discardedDrawerOpen: true,
      discardedRows: [summaryFixture(1), summaryFixture(2), summaryFixture(3), summaryFixture(4)],
      discardedTotal: 4,
    })
    await flushPromises()

    const h2 = document.body.querySelector('h2')!
    expect(h2.textContent).toContain('(4)')
  })

  it('header text omits parenthetical when total is 0', async () => {
    mountDrawer({
      discardedDrawerOpen: true,
      discardedRows: [],
      discardedTotal: 0,
    })
    await flushPromises()

    const h2 = document.body.querySelector('h2')!
    expect(h2.textContent).not.toContain('(')
  })
})
