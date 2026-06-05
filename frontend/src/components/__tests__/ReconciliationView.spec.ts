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

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/api/review', () => ({
  fetchAwaitingReview: vi.fn(() => Promise.resolve([])),
}))

vi.mock('@/api/staging', () => ({
  fetchErrored:           (...a: unknown[]) => mockFetchErrored(...a),
  fetchDetail:            (...a: unknown[]) => mockFetchDetail(...a),
  fetchDiscarded:         (...a: unknown[]) => mockFetchDiscarded(...a),
  fetchConflicts:         (...a: unknown[]) => mockFetchConflicts(...a),
  deleteStagingRow:       (...a: unknown[]) => mockDeleteStagingRow(...a),
  postRestoreStagingRow:  (...a: unknown[]) => mockPostRestoreStagingRow(...a),
}))

vi.mock('@/api/client', () => ({
  isApiError: (err: unknown) =>
    typeof err === 'object' && err !== null && 'response' in err,
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

  // NOTE: "single-page invariant: loadErrored passes (100, 0) to fetchErrored" retired in
  // Phase 15 Epoch 1. The errored table is now paginated (50-per-page) with server-side search.

  it('renders SearchPaginatorBar with errored-search placeholder', async () => {
    mountView()
    await flushPromises()
    const bar = document.body.querySelector('[data-testid="errored-search-bar"]')
    expect(bar).not.toBeNull()
    const input = bar?.querySelector('input[aria-label="Search"]') as HTMLInputElement | null
    expect(input).not.toBeNull()
    expect(input?.placeholder).toContain('row')
  })

  it('typing in the search bar calls store.setErroredSearch after 300ms debounce', async () => {
    vi.useFakeTimers()
    const { store } = mountView()
    await flushPromises()

    const spy = vi.spyOn(store, 'setErroredSearch').mockResolvedValue()
    const input = document.body.querySelector('[data-testid="errored-search-bar"] input') as HTMLInputElement
    input.value = 'myquery'
    input.dispatchEvent(new Event('input'))

    expect(spy).not.toHaveBeenCalled()
    vi.advanceTimersByTime(300)
    await flushPromises()
    expect(spy).toHaveBeenCalledWith('myquery')
    vi.useRealTimers()
  })

  it('Prev button fires store.prevErroredPage', async () => {
    const { store } = mountView()
    await flushPromises()
    // Give the store some state so hasPrev=true
    store.erroredOffset = 50
    store.rows = []
    store.total = 60
    await flushPromises()

    const spy = vi.spyOn(store, 'prevErroredPage').mockResolvedValue()
    const prevBtn = document.body.querySelector('button[aria-label="Previous page"]') as HTMLButtonElement
    prevBtn.click()
    await flushPromises()
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('Next button fires store.nextErroredPage', async () => {
    const { store } = mountView()
    await flushPromises()
    // Give the store some state so hasNext=true
    store.erroredOffset = 0
    store.rows = Array.from({ length: 50 }, () => ({ id: 1 } as never))
    store.total = 60
    await flushPromises()

    const spy = vi.spyOn(store, 'nextErroredPage').mockResolvedValue()
    const nextBtn = document.body.querySelector('button[aria-label="Next page"]') as HTMLButtonElement
    nextBtn.click()
    await flushPromises()
    expect(spy).toHaveBeenCalledTimes(1)
  })
})
