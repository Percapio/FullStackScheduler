import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ReconciliationView from '../../views/ReconciliationView.vue'
import { useStagingStore } from '@/stores/staging'
const mockFetchErrored   = vi.fn()
const mockFetchDetail    = vi.fn()
const mockFetchDiscarded = vi.fn()
const mockFetchConflicts = vi.fn()
const mockDeleteStagingRow = vi.fn()
const mockPostRestoreStagingRow = vi.fn()

vi.mock('@/api/staging', () => ({
  fetchErrored:           (...a: unknown[]) => mockFetchErrored(...a),
  fetchDetail:            (...a: unknown[]) => mockFetchDetail(...a),
  fetchDiscarded:         (...a: unknown[]) => mockFetchDiscarded(...a),
  fetchConflicts:         (...a: unknown[]) => mockFetchConflicts(...a),
  deleteStagingRow:       (...a: unknown[]) => mockDeleteStagingRow(...a),
  postRestoreStagingRow:  (...a: unknown[]) => mockPostRestoreStagingRow(...a),
}))

vi.mock('@/composables/useToast', () => ({
  useToast: () => ({ toasts: { value: [] }, show: vi.fn(), dismiss: vi.fn() }),
}))

let wrapper: VueWrapper | null = null

function mountView() {
  setActivePinia(createPinia())
  mockFetchErrored.mockResolvedValue({ rows: [], total: 0 })
  mockFetchDiscarded.mockResolvedValue({ rows: [], total: 0 })
  mockFetchConflicts.mockResolvedValue([])
  wrapper = mount(ReconciliationView, { attachTo: document.body })
  const store = useStagingStore()
  return { wrapper, store }
}

beforeEach(() => {
  mockFetchErrored.mockReset()
  mockFetchDetail.mockReset()
  mockFetchDiscarded.mockReset()
  mockFetchConflicts.mockReset()
  mockDeleteStagingRow.mockReset()
  mockPostRestoreStagingRow.mockReset()
  mockFetchConflicts.mockResolvedValue([])
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

  it('single-page invariant: loadErrored passes (100, 0) to fetchErrored', async () => {
    // Arrange: mount, drain mount-time async work, then isolate the assertion
    // from the mount-time fetch by resetting the API mock.
    const { store } = mountView()
    await flushPromises()
    mockFetchErrored.mockReset()
    mockFetchErrored.mockResolvedValue({ rows: [], total: 0 })

    // Act: real store.loadErrored runs end-to-end — no spy interposed.
    await store.loadErrored()

    // Assert: exactly one call with positional args (100, 0).
    expect(mockFetchErrored).toHaveBeenCalledTimes(1)
    expect(mockFetchErrored).toHaveBeenCalledWith(100, 0)
  })
})
