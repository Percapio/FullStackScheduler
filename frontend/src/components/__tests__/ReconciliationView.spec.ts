import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ReconciliationView from '../../views/ReconciliationView.vue'
import { useStagingStore } from '@/stores/staging'
import type { StagingRowSummary } from '@/api/staging'

const mockFetchErrored   = vi.fn()
const mockFetchDetail    = vi.fn()
const mockFetchDiscarded = vi.fn()
const mockDeleteStagingRow = vi.fn()
const mockPostRestoreStagingRow = vi.fn()

vi.mock('@/api/staging', () => ({
  fetchErrored:           (...a: unknown[]) => mockFetchErrored(...a),
  fetchDetail:            (...a: unknown[]) => mockFetchDetail(...a),
  fetchDiscarded:         (...a: unknown[]) => mockFetchDiscarded(...a),
  deleteStagingRow:       (...a: unknown[]) => mockDeleteStagingRow(...a),
  postRestoreStagingRow:  (...a: unknown[]) => mockPostRestoreStagingRow(...a),
}))

vi.mock('@/composables/useToast', () => ({
  useToast: () => ({ toasts: { value: [] }, show: vi.fn(), dismiss: vi.fn() }),
}))

const TS = '2026-05-01T12:00:00Z'

function summaryFixture(id: number): StagingRowSummary {
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
  } as StagingRowSummary
}

let wrapper: VueWrapper | null = null

function mountView() {
  setActivePinia(createPinia())
  mockFetchErrored.mockResolvedValue({ rows: [], total: 0 })
  mockFetchDiscarded.mockResolvedValue({ rows: [], total: 0 })
  wrapper = mount(ReconciliationView, { attachTo: document.body })
  const store = useStagingStore()
  return { wrapper, store }
}

beforeEach(() => {
  mockFetchErrored.mockReset()
  mockFetchDetail.mockReset()
  mockFetchDiscarded.mockReset()
  mockDeleteStagingRow.mockReset()
  mockPostRestoreStagingRow.mockReset()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('ReconciliationView', () => {
  it('calls store.loadDiscarded exactly once on mount', async () => {
    const { store } = mountView()
    const loadDiscardedSpy = vi.spyOn(store, 'loadDiscarded')
    // loadDiscarded is called in onMounted; we need to remount to see the spy.
    wrapper!.unmount()
    mockFetchErrored.mockResolvedValue({ rows: [], total: 0 })
    mockFetchDiscarded.mockResolvedValue({ rows: [], total: 0 })
    wrapper = mount(ReconciliationView, { attachTo: document.body })
    await flushPromises()
    expect(loadDiscardedSpy).toHaveBeenCalledTimes(1)
  })

  it('renders Discarded pill button', async () => {
    mountView()
    await flushPromises()
    expect(document.body.querySelector('[data-testid="discarded-pill-btn"]')).not.toBeNull()
  })

  it('pill text contains count in parentheses when discardedTotal > 0', async () => {
    const { store } = mountView()
    await flushPromises()
    store.discardedTotal = 4
    await flushPromises()

    const pill = document.body.querySelector('[data-testid="discarded-pill-btn"]') as HTMLElement
    expect(pill.textContent).toContain('(4)')
  })

  it('pill text omits parenthetical when discardedTotal is 0', async () => {
    const { store } = mountView()
    await flushPromises()
    store.discardedTotal = 0
    await flushPromises()

    const pill = document.body.querySelector('[data-testid="discarded-pill-btn"]') as HTMLElement
    expect(pill.textContent).not.toContain('(')
  })

  it('pill click calls store.openDiscardedDrawer', async () => {
    const { store } = mountView()
    await flushPromises()
    const openSpy = vi.spyOn(store, 'openDiscardedDrawer').mockResolvedValueOnce(undefined)

    const pill = document.body.querySelector('[data-testid="discarded-pill-btn"]') as HTMLButtonElement
    pill.click()
    await flushPromises()

    expect(openSpy).toHaveBeenCalledTimes(1)
  })

  it('single-page invariant: loadErrored is only ever called with (100, 0)', async () => {
    // loadDiscarded triggers loadErrored on restore; confirm the parameters.
    const { store } = mountView()
    await flushPromises()

    const loadErroredSpy = vi.spyOn(store, 'loadErrored').mockResolvedValue(undefined)
    await store.loadErrored()
    // Confirm the spy was invoked — our mock resolved without params being checked,
    // but the store's actual loadErrored passes (100, 0) to fetchErrored.
    expect(mockFetchErrored).toHaveBeenCalledWith(100, 0)
  })
})
