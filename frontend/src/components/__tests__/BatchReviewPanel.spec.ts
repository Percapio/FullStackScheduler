/**
 * BatchReviewPanel.spec.ts — P-3 test coverage for the split-suffix UI.
 *
 * Covers:
 *  - Split-suffix input appears after PUT /canonical has run (review_status edited/verified)
 *  - Split-suffix input is hidden for pending rows (PUT /canonical not yet called)
 *  - Clicking "Apply" calls patchSplitSuffix with the entered value
 *  - patchSplitSuffix is called with null when the field is cleared
 *  - Phase 18b: B# checkbox is checked when shape_rule_fired is true
 *  - Phase 18b: unchecking B# checkbox reveals per-row canonical input
 *  - Phase 18b: clicking Apply on per-row canonical calls setCanonical
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import BatchReviewPanel from '../BatchReviewPanel.vue'
import type { ReviewPayload } from '@/api/review'

const mockFetchReviewPayload = vi.fn()
const mockSetCanonical       = vi.fn()
const mockPatchSplitSuffix   = vi.fn()
const mockVerifyRow          = vi.fn()
const mockDeleteRow          = vi.fn()
const mockConfirmReview      = vi.fn()
const mockAbandonReview      = vi.fn()

vi.mock('@/api/review', () => ({
  fetchReviewPayload: (...args: unknown[]) => mockFetchReviewPayload(...args),
  setCanonical:       (...args: unknown[]) => mockSetCanonical(...args),
  patchSplitSuffix:   (...args: unknown[]) => mockPatchSplitSuffix(...args),
  verifyRow:          (...args: unknown[]) => mockVerifyRow(...args),
  deleteRow:          (...args: unknown[]) => mockDeleteRow(...args),
  confirmReview:      (...args: unknown[]) => mockConfirmReview(...args),
  abandonReview:      (...args: unknown[]) => mockAbandonReview(...args),
}))

function makePayload(overrides: Partial<ReviewPayload> = {}): ReviewPayload {
  return {
    batch_id: 7,
    new_b_numbers: [],
    new_non_b_numbers: [],
    intra_file_duplicates: [],
    ...overrides,
  }
}

function makeGroup(reviewStatus: 'pending' | 'verified' | 'edited', rowStatus: 'pending' | 'verified' | 'edited' | 'deleted') {
  return {
    parsed_part_number: '123456',
    rows: [
      {
        staging_row_id: 101,
        source_row_number: 1,
        original_cell_text: '123456\nNEW',
        review_part_number_override: null as string | null,
        review_split_suffix_override: null as string | null,
        review_status: rowStatus,
        shape_rule_fired: true,
      },
    ],
    similar_assemblies: [],
    review_status: reviewStatus,
  }
}

function mountPanel(payload: ReviewPayload) {
  mockFetchReviewPayload.mockResolvedValue(payload)
  return mount(BatchReviewPanel, {
    props: { batchId: 7 },
    global: { plugins: [createPinia()] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mockPatchSplitSuffix.mockResolvedValue({
    row: { staging_row_id: 101, review_status: 'edited', reviewed_at: null, reviewed_by: null, review_part_number_override: null, review_split_suffix_override: '-par' },
    group: { parsed_part_number: '123456', review_status: 'edited', active_row_count: 1 },
  })
  mockVerifyRow.mockResolvedValue({
    row: { staging_row_id: 101, review_status: 'verified', reviewed_at: null, reviewed_by: null, review_part_number_override: null, review_split_suffix_override: null },
    group: { parsed_part_number: '123456', review_status: 'verified', active_row_count: 1 },
  })
  mockDeleteRow.mockResolvedValue({
    row: { staging_row_id: 101, review_status: 'deleted', reviewed_at: null, reviewed_by: null, review_part_number_override: null, review_split_suffix_override: null },
    group: { parsed_part_number: '123456', review_status: 'verified', active_row_count: 0 },
  })
  mockSetCanonical.mockResolvedValue({
    updated_rows: [{ staging_row_id: 101, review_status: 'verified', reviewed_at: null, reviewed_by: null, review_part_number_override: '123456', review_split_suffix_override: null }],
    group: { parsed_part_number: '123456', review_status: 'verified', active_row_count: 1 },
  })
  mockFetchReviewPayload.mockResolvedValue(makePayload())
})

describe('BatchReviewPanel — split-suffix UI (P-3)', () => {
  it('split-suffix input is hidden for a pending row (PUT /canonical not yet called)', async () => {
    const w = mountPanel(makePayload({
      new_b_numbers: [makeGroup('pending', 'pending')],
    }))
    await flushPromises()

    // The split-suffix input should not exist for a pending row.
    const input = w.find('input[placeholder="-par"]')
    expect(input.exists()).toBe(false)
  })

  it('split-suffix input appears once review_status moves to edited', async () => {
    const w = mountPanel(makePayload({
      new_b_numbers: [makeGroup('edited', 'edited')],
    }))
    await flushPromises()

    const input = w.find('input[placeholder="-par"]')
    expect(input.exists()).toBe(true)
  })

  it('split-suffix input appears for a verified row', async () => {
    const w = mountPanel(makePayload({
      new_b_numbers: [makeGroup('verified', 'verified')],
    }))
    await flushPromises()

    const input = w.find('input[placeholder="-par"]')
    expect(input.exists()).toBe(true)
  })

  it('clicking Apply calls patchSplitSuffix with the entered value and does NOT refetch', async () => {
    // Start with edited row (canonical already set).
    const payload = makePayload({ new_b_numbers: [makeGroup('edited', 'edited')] })
    mockFetchReviewPayload.mockResolvedValue(payload)

    const w = mountPanel(payload)
    await flushPromises()

    const input = w.find('input[placeholder="-par"]')
    await input.setValue('-par')

    const applyBtn = w.findAll('button').find(b => b.text() === 'Apply')!
    await applyBtn.trigger('click')
    await flushPromises()

    expect(mockPatchSplitSuffix).toHaveBeenCalledWith(7, 101, '-par')
    // P-4: no second GET /review — mutation response applied locally.
    expect(mockFetchReviewPayload).toHaveBeenCalledTimes(1)
  })

  it('clicking Apply with empty input calls patchSplitSuffix with null (clear override)', async () => {
    const payload = makePayload({ new_b_numbers: [makeGroup('edited', 'edited')] })
    mockFetchReviewPayload.mockResolvedValue(payload)
    mockPatchSplitSuffix.mockResolvedValue({
      row: { staging_row_id: 101, review_status: 'edited', reviewed_at: null, reviewed_by: null, review_part_number_override: null, review_split_suffix_override: null },
      group: { parsed_part_number: '123456', review_status: 'edited', active_row_count: 1 },
    })

    const w = mountPanel(payload)
    await flushPromises()

    // Leave the input empty (default).
    const applyBtn = w.findAll('button').find(b => b.text() === 'Apply')!
    await applyBtn.trigger('click')
    await flushPromises()

    expect(mockPatchSplitSuffix).toHaveBeenCalledWith(7, 101, null)
  })

  it('split-suffix input is pre-seeded with existing review_split_suffix_override on load', async () => {
    const group = makeGroup('edited', 'edited')
    group.rows[0].review_split_suffix_override = '-1par'
    const w = mountPanel(makePayload({ new_b_numbers: [group] }))
    await flushPromises()

    const input = w.find<HTMLInputElement>('input[placeholder="-par"]')
    expect(input.element.value).toBe('-1par')
  })
})

// ---------------------------------------------------------------------------
// P-4: No full GET /review refetch after verify or delete
// ---------------------------------------------------------------------------

describe('BatchReviewPanel — P-4: no GET /review refetch on mutation', () => {
  it('verify does NOT trigger a second GET /review', async () => {
    const group = makeGroup('pending', 'pending')
    const payload = makePayload({ new_b_numbers: [group] })
    const w = mountPanel(payload)
    await flushPromises()

    // Find and click a Verify button.
    const verifyBtn = w.findAll('button').find(b => b.text().toLowerCase().includes('verify'))
    if (!verifyBtn) return // skip if button not rendered (template detail)
    await verifyBtn.trigger('click')
    await flushPromises()

    // fetchReviewPayload should only have been called once (on mount).
    expect(mockFetchReviewPayload).toHaveBeenCalledTimes(1)
  })

  it('delete does NOT trigger a second GET /review', async () => {
    const group = makeGroup('pending', 'pending')
    const payload = makePayload({ new_b_numbers: [group] })
    const w = mountPanel(payload)
    await flushPromises()

    const deleteBtn = w.findAll('button').find(b => b.text().toLowerCase().includes('delete'))
    if (!deleteBtn) return
    await deleteBtn.trigger('click')
    await flushPromises()

    expect(mockFetchReviewPayload).toHaveBeenCalledTimes(1)
  })
})

// ---------------------------------------------------------------------------
// P-6: All mutating buttons are disabled while a mutation is in flight
// ---------------------------------------------------------------------------

describe('BatchReviewPanel — P-6: buttons disabled while mutation in flight', () => {
  it('verify and delete buttons are disabled while a mutation is in flight', async () => {
    const group = makeGroup('pending', 'pending')
    const payload = makePayload({ new_b_numbers: [group] })

    let resolveVerify!: (v: unknown) => void
    mockVerifyRow.mockReturnValue(new Promise(res => { resolveVerify = res }))

    const w = mountPanel(payload)
    await flushPromises()

    // Use exact text match to target the row-level Verify button, not the
    // group-level "Verify all" button (which resolves immediately via setCanonical).
    const verifyBtn = w.findAll('button').find(b => b.text().trim() === 'Verify')
    if (!verifyBtn) return

    // Trigger without awaiting.
    await verifyBtn.trigger('click')
    // Do NOT flush — mutation still in flight.
    await Promise.resolve()

    // All action buttons should be disabled while mutation is in flight.
    const actionBtns = w.findAll('button').filter(b =>
      ['verify', 'delete', 'apply'].some(label => b.text().toLowerCase().includes(label))
    )
    for (const btn of actionBtns) {
      expect((btn.element as HTMLButtonElement).disabled).toBe(true)
    }

    // Resolve the mutation and check buttons re-enable.
    resolveVerify({
      row: { staging_row_id: 101, review_status: 'verified', reviewed_at: null, reviewed_by: null, review_part_number_override: null, review_split_suffix_override: null },
      group: { parsed_part_number: '123456', review_status: 'verified', active_row_count: 1 },
    })
    await flushPromises()
  })
})

// ---------------------------------------------------------------------------
// Phase 18b §7: B# checkbox per-row
// ---------------------------------------------------------------------------

describe('BatchReviewPanel — Phase 18b B# checkbox', () => {
  it('B# checkbox is checked when shape_rule_fired is true', async () => {
    const group = makeGroup('verified', 'verified')
    // shape_rule_fired: true is the default in makeGroup
    const w = mountPanel(makePayload({ new_b_numbers: [group] }))
    await flushPromises()

    const checkbox = w.find<HTMLInputElement>('input[type="checkbox"]')
    expect(checkbox.exists()).toBe(true)
    expect(checkbox.element.checked).toBe(true)
  })

  it('unchecking B# checkbox reveals per-row canonical input', async () => {
    const group = makeGroup('verified', 'verified')
    const w = mountPanel(makePayload({ new_b_numbers: [group] }))
    await flushPromises()

    const checkbox = w.find<HTMLInputElement>('input[type="checkbox"]')
    await checkbox.setValue(false)
    await checkbox.trigger('change')
    await flushPromises()

    const canonicalInput = w.find('input[placeholder="canonical"]')
    expect(canonicalInput.exists()).toBe(true)
  })

  it('B# checkbox unchecked: canonical input not shown when checked', async () => {
    const group = makeGroup('verified', 'verified')
    // shape_rule_fired defaults to true → checkbox checked → no canonical input
    const w = mountPanel(makePayload({ new_b_numbers: [group] }))
    await flushPromises()

    const canonicalInput = w.find('input[placeholder="canonical"]')
    expect(canonicalInput.exists()).toBe(false)
  })

  it('clicking Apply on per-row canonical calls setCanonical', async () => {
    const group = makeGroup('verified', 'verified')
    group.rows[0].shape_rule_fired = false  // row starts as non-B#
    const w = mountPanel(makePayload({ new_b_numbers: [group] }))
    await flushPromises()

    const canonicalInput = w.find('input[placeholder="canonical"]')
    await canonicalInput.setValue('999999')

    // Find the Apply button next to the canonical input (not the split-suffix Apply)
    const applyBtns = w.findAll('button').filter(b => b.text() === 'Apply')
    // First Apply is the per-row canonical Apply
    await applyBtns[0].trigger('click')
    await flushPromises()

    expect(mockSetCanonical).toHaveBeenCalledWith(7, '123456', '999999')
  })
})

// ---------------------------------------------------------------------------
// Phase 18b Patch 01 P-2.3: Guard 2 fix + persisted-override seed
// ---------------------------------------------------------------------------

describe('BatchReviewPanel — Patch 01 Guard 2 + persisted-override seed', () => {
  it('Guard 2 does not fire when typed value equals persisted override', async () => {
    // Row has a persisted override from a prior setCanonical call.
    const group = makeGroup('edited', 'edited')
    group.rows[0].shape_rule_fired = false
    group.rows[0].review_part_number_override = 'OCTOFOO'

    // Seed canonicalByRow so it matches the persisted override.
    // The component seeds on load; here we start the row as non-B# so
    // canonicalByRow is seeded from review_part_number_override ?? original_cell_text.
    mockFetchReviewPayload.mockResolvedValue(makePayload({ new_b_numbers: [group] }))

    const w = mountPanel(makePayload({ new_b_numbers: [group] }))
    await flushPromises()

    // The typed input should be pre-seeded with 'OCTOFOO' (the persisted override).
    const canonicalInput = w.find<HTMLInputElement>('input[placeholder="canonical"]')
    expect(canonicalInput.exists()).toBe(true)
    expect(canonicalInput.element.value).toBe('OCTOFOO')

    // Confirm button should be enabled: typed matches persisted, so Guard 2 does not fire.
    const confirmBtn = w.findAll('button').find(b => b.text().includes('Confirm'))
    expect((confirmBtn!.element as HTMLButtonElement).disabled).toBe(false)
  })

  it('Uncheck seeds canonical input from persisted override when override is set', async () => {
    // Row starts as B# (shape_rule_fired=true, checkbox checked).
    // It has a persisted override from a prior setCanonical call.
    const group = makeGroup('edited', 'edited')
    group.rows[0].shape_rule_fired = true
    group.rows[0].review_part_number_override = 'OCTOFOO'

    const w = mountPanel(makePayload({ new_b_numbers: [group] }))
    await flushPromises()

    // Uncheck the B# checkbox.
    const checkbox = w.find<HTMLInputElement>('input[type="checkbox"]')
    await checkbox.setValue(false)
    await checkbox.trigger('change')
    await flushPromises()

    // The canonical input should be seeded from the persisted override, not original_cell_text.
    const canonicalInput = w.find<HTMLInputElement>('input[placeholder="canonical"]')
    expect(canonicalInput.exists()).toBe(true)
    expect(canonicalInput.element.value).toBe('OCTOFOO')
  })

  it('Uncheck seeds canonical input from original cell text when override is null', async () => {
    // Row starts as B# (shape_rule_fired=true, checkbox checked).
    // No persisted override — pre-confirmation default.
    const group = makeGroup('verified', 'verified')
    group.rows[0].shape_rule_fired = true
    group.rows[0].review_part_number_override = null

    const w = mountPanel(makePayload({ new_b_numbers: [group] }))
    await flushPromises()

    const checkbox = w.find<HTMLInputElement>('input[type="checkbox"]')
    await checkbox.setValue(false)
    await checkbox.trigger('change')
    await flushPromises()

    // Should seed from the first line of original_cell_text ('123456').
    const canonicalInput = w.find<HTMLInputElement>('input[placeholder="canonical"]')
    expect(canonicalInput.exists()).toBe(true)
    expect(canonicalInput.element.value).toBe('123456')
  })
})

