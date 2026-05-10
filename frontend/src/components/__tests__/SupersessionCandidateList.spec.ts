import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SupersessionCandidateList from '../../components/SupersessionCandidateList.vue'
import { useSupersessionStore } from '@/stores/supersession'
import type { SupersessionCandidate } from '@/api/supersession'

const mockApprove     = vi.fn()
const mockReject      = vi.fn()
const mockBulkApprove = vi.fn()
const mockFetch       = vi.fn()
const mockShowToast   = vi.fn()

vi.mock('@/api/supersession', () => ({
  fetchSupersessionCandidates:       (...a: unknown[]) => mockFetch(...a),
  approveSupersessionCandidate:      (...a: unknown[]) => mockApprove(...a),
  rejectSupersessionCandidate:       (...a: unknown[]) => mockReject(...a),
  bulkApproveSupersessionCandidates: (...a: unknown[]) => mockBulkApprove(...a),
}))

vi.mock('@/api/client', () => ({
  isApiError: (err: unknown) =>
    typeof err === 'object' && err !== null && 'response' in err,
}))

vi.mock('@/composables/useToast', () => ({
  useToast: () => ({ show: mockShowToast, toasts: { value: [] }, dismiss: vi.fn() }),
}))

const TS = '2026-05-05T10:00:00'

function candidateFixture(
  id: number,
  overrides: Partial<SupersessionCandidate> = {},
): SupersessionCandidate {
  return {
    id,
    job_id: id * 10,
    detected_in_batch_id: 1,
    reason: 'orphan_other',
    detected_at: TS,
    resolved_at: null,
    resolution: null,
    closed_by_shield_reason: null,
    created_at: TS,
    updated_at: TS,
    ...overrides,
  }
}

function mountComponent() {
  setActivePinia(createPinia())
  return mount(SupersessionCandidateList, { attachTo: document.body })
}

afterEach(() => {
  vi.clearAllMocks()
})

// ---- empty state -----------------------------------------------------------

describe('SupersessionCandidateList — empty state', () => {
  it('renders empty-state message when no candidates', async () => {
    const wrapper = mountComponent()
    await flushPromises()
    expect(wrapper.text()).toContain('No supersession candidates')
    wrapper.unmount()
  })
})

// ---- render with candidates ------------------------------------------------

describe('SupersessionCandidateList — with candidates', () => {
  it('renders one row per candidate', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1), candidateFixture(2)]
    await flushPromises()

    const rows = wrapper.findAll('[data-testid="candidate-row"]')
    expect(rows).toHaveLength(2)
    wrapper.unmount()
  })

  it('shows heading with candidate count', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1)]
    await flushPromises()

    const heading = wrapper.find('[data-testid="supersession-heading"]')
    expect(heading.text()).toContain('1 supersession candidate')
    wrapper.unmount()
  })

  it('reason badge displays correct label for orphan_after_split', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1, { reason: 'orphan_after_split' })]
    await flushPromises()

    expect(wrapper.text()).toContain('Split detected')
    wrapper.unmount()
  })

  it('reason badge displays correct label for orphan_after_recombine', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1, { reason: 'orphan_after_recombine' })]
    await flushPromises()

    expect(wrapper.text()).toContain('Recombined')
    wrapper.unmount()
  })
})

// ---- approve ---------------------------------------------------------------

describe('SupersessionCandidateList — approve', () => {
  it('calls store.approve with the correct id on Approve click', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1)]
    await flushPromises()

    const approveSpy = vi.spyOn(store, 'approve').mockResolvedValueOnce(null)
    const btn = wrapper.find('[data-testid="approve-btn"]')
    await btn.trigger('click')

    expect(approveSpy).toHaveBeenCalledWith(1)
    wrapper.unmount()
  })

  it('shows toast when approve returns shield-closed result', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1)]
    await flushPromises()

    vi.spyOn(store, 'approve').mockResolvedValueOnce({
      ...candidateFixture(1),
      resolution: 'reject',
      closed_by_shield_reason: 'ever_shipped',
    })
    const btn = wrapper.find('[data-testid="approve-btn"]')
    await btn.trigger('click')
    await flushPromises()

    expect(mockShowToast).toHaveBeenCalledTimes(1)
    expect(mockShowToast.mock.calls[0][0]).toContain('ever_shipped')
    wrapper.unmount()
  })

  it('does NOT show toast when approve succeeds normally', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1)]
    await flushPromises()

    vi.spyOn(store, 'approve').mockResolvedValueOnce({
      ...candidateFixture(1),
      resolution: 'approve',
      closed_by_shield_reason: null,
    })
    await wrapper.find('[data-testid="approve-btn"]').trigger('click')
    await flushPromises()

    expect(mockShowToast).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})

// ---- reject ----------------------------------------------------------------

describe('SupersessionCandidateList — reject', () => {
  it('calls store.reject with the correct id on Reject click', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1)]
    await flushPromises()

    const rejectSpy = vi.spyOn(store, 'reject').mockResolvedValueOnce(null)
    await wrapper.find('[data-testid="reject-btn"]').trigger('click')

    expect(rejectSpy).toHaveBeenCalledWith(1)
    wrapper.unmount()
  })
})

// ---- bulk approve ----------------------------------------------------------

describe('SupersessionCandidateList — bulk approve', () => {
  it('shows bulk footer when rows are selected', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1), candidateFixture(2)]
    store.selectedIds.add(1)
    await flushPromises()

    expect(wrapper.find('[data-testid="bulk-footer"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('hides bulk footer when nothing is selected', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1)]
    await flushPromises()

    expect(wrapper.find('[data-testid="bulk-footer"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('calls store.bulkApprove with selected ids on footer button click', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1), candidateFixture(2)]
    store.selectedIds.add(1)
    store.selectedIds.add(2)
    await flushPromises()

    const bulkSpy = vi.spyOn(store, 'bulkApprove').mockResolvedValueOnce(null)
    await wrapper.find('[data-testid="bulk-approve-btn"]').trigger('click')

    expect(bulkSpy).toHaveBeenCalledWith([1, 2])
    wrapper.unmount()
  })

  it('shows toast for shield-rejected candidates after bulk approve', async () => {
    const wrapper = mountComponent()
    const store = useSupersessionStore()
    store.candidates = [candidateFixture(1), candidateFixture(2)]
    store.selectedIds.add(1)
    store.selectedIds.add(2)
    await flushPromises()

    vi.spyOn(store, 'bulkApprove').mockImplementationOnce(async () => {
      store.lastBulkSummary = {
        approved: [1], shield_rejected: [2], already_closed: [], not_found: [],
      }
      return store.lastBulkSummary
    })
    await wrapper.find('[data-testid="bulk-approve-btn"]').trigger('click')
    await flushPromises()

    expect(mockShowToast).toHaveBeenCalledTimes(1)
    expect(mockShowToast.mock.calls[0][0]).toContain('1 candidate')
    wrapper.unmount()
  })
})
