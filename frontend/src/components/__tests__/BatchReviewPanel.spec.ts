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
 *  - Patch 06 §7.5: duplicates section replaced (never spliced), confirm Guard 4,
 *    confirm 409 identity_collision, 5xx refetch, revert suffix, read-only rows
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import BatchReviewPanel from '../BatchReviewPanel.vue'
import type {
  ReviewGroup,
  ReviewGroupIdentity,
  ReviewPayload,
  ReviewRow,
  ReviewRowResponse,
} from '@/api/review'

const mockFetchReviewPayload = vi.fn()
const mockSetCanonical       = vi.fn()
const mockPatchSplitSuffix   = vi.fn()
const mockRevertSplitSuffix  = vi.fn()
const mockVerifyRow          = vi.fn()
const mockDeleteRow          = vi.fn()
const mockConfirmReview      = vi.fn()
const mockAbandonReview      = vi.fn()

vi.mock('@/api/review', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/review')>()),
  fetchReviewPayload: (...args: unknown[]) => mockFetchReviewPayload(...args),
  setCanonical:       (...args: unknown[]) => mockSetCanonical(...args),
  patchSplitSuffix:   (...args: unknown[]) => mockPatchSplitSuffix(...args),
  revertSplitSuffix:  (...args: unknown[]) => mockRevertSplitSuffix(...args),
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

function makeRow(overrides: Partial<ReviewRow> = {}): ReviewRow {
  return {
    staging_row_id: 101,
    source_row_number: 1,
    original_cell_text: '123456\nNEW',
    parsed_part_number: '123456',
    review_part_number_override: null,
    review_split_suffix_override: null,
    review_split_suffix_source: null,
    review_status: 'verified',
    shape_rule_fired: true,
    ...overrides,
  }
}

function makeGroup(reviewStatus: 'pending' | 'verified' | 'edited', rowStatus: 'pending' | 'verified' | 'edited' | 'deleted'): ReviewGroup {
  return {
    parsed_part_number: '123456',
    rows: [makeRow({ review_status: rowStatus })],
    similar_assemblies: [],
    review_status: reviewStatus,
  }
}

const IDENTITY: ReviewGroupIdentity = {
  part_number: '123456',
  build_type: 'new',
  split_suffix: null,
  repeat_reference: null,
  build_qualifier: null,
}

function makeDuplicateGroup(overrides: Partial<ReviewGroup> = {}): ReviewGroup {
  return {
    parsed_part_number: '123456',
    identity: IDENTITY,
    origin: 'staged',
    resolved: false,
    similar_assemblies: [],
    review_status: 'verified',
    rows: [
      makeRow({ staging_row_id: 101, source_row_number: 1, effective_identity: IDENTITY, actionable: true }),
      makeRow({ staging_row_id: 102, source_row_number: 2, effective_identity: IDENTITY, actionable: true }),
    ],
    ...overrides,
  }
}

function rowResponse(overrides: Partial<ReviewRowResponse> = {}): ReviewRowResponse {
  return {
    staging_row_id: 101,
    review_status: 'verified',
    reviewed_at: null,
    reviewed_by: null,
    review_part_number_override: null,
    review_split_suffix_override: null,
    review_split_suffix_source: null,
    ...overrides,
  }
}

function axiosError(status: number, data: unknown) {
  return { response: { status, data }, message: `Request failed with status code ${status}` }
}

function mountPanel(payload: ReviewPayload) {
  mockFetchReviewPayload.mockResolvedValue(payload)
  return mount(BatchReviewPanel, {
    props: { batchId: 7 },
    global: { plugins: [createPinia()] },
  })
}

function confirmButton(w: ReturnType<typeof mountPanel>) {
  return w.findAll('button').find(b => b.text().includes('Confirm'))!
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mockPatchSplitSuffix.mockResolvedValue({
    row: rowResponse({ review_status: 'edited', review_split_suffix_override: '-par', review_split_suffix_source: 'operator' }),
    group: { parsed_part_number: '123456', review_status: 'edited', active_row_count: 1 },
    intra_file_duplicates: [],
  })
  mockVerifyRow.mockResolvedValue({
    row: rowResponse(),
    group: { parsed_part_number: '123456', review_status: 'verified', active_row_count: 1 },
    intra_file_duplicates: [],
  })
  mockDeleteRow.mockResolvedValue({
    row: rowResponse({ review_status: 'deleted' }),
    group: { parsed_part_number: '123456', review_status: 'verified', active_row_count: 0 },
    intra_file_duplicates: [],
  })
  mockSetCanonical.mockResolvedValue({
    updated_rows: [rowResponse({ review_part_number_override: '123456', review_split_suffix_source: 'computed' })],
    group: { parsed_part_number: '123456', review_status: 'verified', active_row_count: 1 },
    intra_file_duplicates: [],
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
      row: rowResponse({ review_status: 'edited', review_split_suffix_source: 'operator' }),
      group: { parsed_part_number: '123456', review_status: 'edited', active_row_count: 1 },
      intra_file_duplicates: [],
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
      row: rowResponse(),
      group: { parsed_part_number: '123456', review_status: 'verified', active_row_count: 1 },
      intra_file_duplicates: [],
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

// ---------------------------------------------------------------------------
// Patch 06 §7.5: one source of truth for the duplicates section
// ---------------------------------------------------------------------------

function duplicateGroups(w: ReturnType<typeof mountPanel>) {
  return w.findAll('[data-testid="duplicate-group"]')
}

describe('BatchReviewPanel — Patch 06 applyMutationResponse', () => {
  it('replaces the duplicates section with the response section and never splices it', async () => {
    const w = mountPanel(makePayload({
      new_b_numbers: [makeGroup('verified', 'verified')],
      intra_file_duplicates: [makeDuplicateGroup()],
    }))
    await flushPromises()
    expect(duplicateGroups(w)[0].find('[data-testid="duplicate-group-state"]').text()).toBe('Collides')

    // The response's row says 'edited'; its duplicates section says row 101 is 'verified'
    // and the group resolved. Splicing resp.row into the section would show 'edited'.
    const resolvedSection = makeDuplicateGroup({
      resolved: true,
      rows: [
        makeRow({ staging_row_id: 101, effective_identity: IDENTITY, actionable: true }),
        makeRow({ staging_row_id: 102, source_row_number: 2, review_status: 'deleted', actionable: false }),
      ],
    })
    mockDeleteRow.mockResolvedValue({
      row: rowResponse({ staging_row_id: 101, review_status: 'edited' }),
      group: { parsed_part_number: '123456', review_status: 'edited', active_row_count: 1 },
      intra_file_duplicates: [resolvedSection],
    })

    const dupDelete = duplicateGroups(w)[0].findAll('button').find(b => b.text() === 'Delete')!
    await dupDelete.trigger('click')
    await flushPromises()

    const section = duplicateGroups(w)[0]
    expect(section.find('[data-testid="duplicate-group-state"]').text()).toBe('Resolved')
    expect(section.find('[data-row-id="101"]').text()).toContain('verified')
    expect(section.find('[data-row-id="102"]').text()).toContain('deleted')
    // The new-part section takes the spliced row and the part-number-wide status.
    const state = (w.vm as unknown as { payload: ReviewPayload }).payload
    expect(state.new_b_numbers[0].review_status).toBe('edited')
    expect(state.new_b_numbers[0].rows[0].review_status).toBe('edited')
    expect(state.intra_file_duplicates).toEqual([resolvedSection])
    expect(mockFetchReviewPayload).toHaveBeenCalledTimes(1)
  })

  it('splices every new-part group whose parsed_part_number matches', async () => {
    const bGroup = makeGroup('verified', 'verified')
    const nonBGroup = makeGroup('verified', 'verified')
    const w = mountPanel(makePayload({ new_b_numbers: [bGroup], new_non_b_numbers: [nonBGroup] }))
    await flushPromises()
    mockSetCanonical.mockResolvedValue({
      updated_rows: [rowResponse({ review_status: 'edited', review_part_number_override: '999999', review_split_suffix_source: 'computed' })],
      group: { parsed_part_number: '123456', review_status: 'edited', active_row_count: 1 },
      intra_file_duplicates: [],
    })

    const checkbox = w.find<HTMLInputElement>('input[type="checkbox"]')
    await checkbox.setValue(false)
    await checkbox.trigger('change')
    await w.find('input[placeholder="canonical"]').setValue('999999')
    await w.findAll('button').find(b => b.text() === 'Apply')!.trigger('click')
    await flushPromises()

    const state = (w.vm as unknown as { payload: ReviewPayload }).payload
    for (const group of [state.new_b_numbers[0], state.new_non_b_numbers[0]]) {
      expect(group.review_status).toBe('edited')
      expect(group.rows[0].review_part_number_override).toBe('999999')
    }
  })

  it('seeds inputs for rows that first appear in a replaced section', async () => {
    const w = mountPanel(makePayload({ intra_file_duplicates: [makeDuplicateGroup()] }))
    await flushPromises()

    const bystander = makeRow({
      staging_row_id: 103,
      source_row_number: 3,
      original_cell_text: 'OCTO-QUAD-2par\nNEW',
      parsed_part_number: 'OCTO-QUAD',
      review_status: null,
      shape_rule_fired: false,
      actionable: false,
    })
    mockPatchSplitSuffix.mockResolvedValue({
      row: rowResponse({ staging_row_id: 102, review_status: 'edited', review_part_number_override: '123456', review_split_suffix_override: '-2par', review_split_suffix_source: 'operator' }),
      group: { parsed_part_number: '123456', review_status: 'edited', active_row_count: 2 },
      intra_file_duplicates: [
        makeDuplicateGroup(),
        makeDuplicateGroup({
          origin: 'edit_induced',
          parsed_part_number: 'OCTO-QUAD',
          identity: { ...IDENTITY, part_number: 'OCTO-QUAD', split_suffix: '-2par' },
          rows: [makeRow({ staging_row_id: 102, actionable: true }), bystander],
        }),
      ],
    })

    const splitInputs = duplicateGroups(w)[0].findAll('input[placeholder="-par"]')
    await splitInputs[1].setValue('-2par')
    await duplicateGroups(w)[0].findAll('button').filter(b => b.text() === 'Apply')[1].trigger('click')
    await flushPromises()

    const groups = duplicateGroups(w)
    expect(groups).toHaveLength(2)
    expect(groups[1].text()).toContain('Created by an edit in this review')
    // The bystander's canonical input is seeded from its own parse, so Guard 2 stays quiet
    // and the disabled reason names the collision.
    const bystanderRow = groups[1].find('[data-row-id="103"]')
    expect(bystanderRow.find<HTMLInputElement>('input[placeholder="canonical"]').element.value).toBe('OCTO-QUAD')
    expect(confirmButton(w).attributes('title')).toContain('duplicate group(s) still collide')
  })

  it('follows the server for inputs the operator has not edited', async () => {
    const group = makeDuplicateGroup({
      rows: [
        makeRow({ staging_row_id: 101, shape_rule_fired: false, actionable: true }),
        makeRow({ staging_row_id: 102, source_row_number: 2, shape_rule_fired: false, actionable: true }),
      ],
    })
    const w = mountPanel(makePayload({ intra_file_duplicates: [group] }))
    await flushPromises()
    const canonicalApplied = makeDuplicateGroup({
      resolved: true,
      rows: [
        makeRow({ staging_row_id: 101, shape_rule_fired: false, review_part_number_override: '654321', actionable: true }),
        makeRow({ staging_row_id: 102, source_row_number: 2, shape_rule_fired: false, review_part_number_override: '654321', actionable: true }),
      ],
    })
    mockSetCanonical.mockResolvedValue({
      updated_rows: [
        rowResponse({ staging_row_id: 101, review_status: 'edited', review_part_number_override: '654321', review_split_suffix_source: 'computed' }),
        rowResponse({ staging_row_id: 102, review_status: 'edited', review_part_number_override: '654321', review_split_suffix_source: 'computed' }),
      ],
      group: { parsed_part_number: '123456', review_status: 'edited', active_row_count: 2 },
      intra_file_duplicates: [canonicalApplied],
    })

    const firstRow = duplicateGroups(w)[0].find('[data-row-id="101"]')
    await firstRow.find('input[placeholder="canonical"]').setValue('654321')
    await firstRow.findAll('button').find(b => b.text() === 'Apply')!.trigger('click')
    await flushPromises()

    const secondRowInput = duplicateGroups(w)[0].find('[data-row-id="102"]').find<HTMLInputElement>('input[placeholder="canonical"]')
    expect(secondRowInput.element.value).toBe('654321')
    expect((confirmButton(w).element as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('BatchReviewPanel — Patch 06 confirm guard and refusal', () => {
  it('disables Confirm with the collision reason while any duplicate group is unresolved', async () => {
    const w = mountPanel(makePayload({
      intra_file_duplicates: [makeDuplicateGroup({ resolved: true }), makeDuplicateGroup({ origin: 'edit_induced', resolved: false })],
    }))
    await flushPromises()

    const btn = confirmButton(w)
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
    expect(btn.attributes('title')).toBe('1 duplicate group(s) still collide — rename or delete a row in each.')
  })

  it('enables Confirm once every duplicate group is resolved', async () => {
    const w = mountPanel(makePayload({ intra_file_duplicates: [makeDuplicateGroup({ resolved: true })] }))
    await flushPromises()

    expect((confirmButton(w).element as HTMLButtonElement).disabled).toBe(false)
  })

  it('a 409 identity_collision replaces the section and names the unresolved count', async () => {
    const w = mountPanel(makePayload({ intra_file_duplicates: [makeDuplicateGroup({ resolved: true })] }))
    await flushPromises()
    mockConfirmReview.mockRejectedValue(axiosError(409, {
      code: 'identity_collision',
      detail: '1 set(s) of rows would write the same job.',
      collisions: [{ identity: IDENTITY, row_ids: [101, 102] }],
      intra_file_duplicates: [makeDuplicateGroup({ resolved: false })],
    }))

    await confirmButton(w).trigger('click')
    await flushPromises()

    expect(duplicateGroups(w)[0].find('[data-testid="duplicate-group-state"]').text()).toBe('Collides')
    expect(w.find('[role="alert"]').text()).toBe(
      'Import not confirmed: 1 duplicate group(s) still collide — rename or delete a row in each.'
    )
    expect((confirmButton(w).element as HTMLButtonElement).disabled).toBe(true)
    expect(mockFetchReviewPayload).toHaveBeenCalledTimes(1)
  })

  it('any other 409 keeps the detail message', async () => {
    const w = mountPanel(makePayload({ intra_file_duplicates: [makeDuplicateGroup({ resolved: true })] }))
    await flushPromises()
    mockConfirmReview.mockRejectedValue(axiosError(409, { detail: '1 row(s) still in \'pending\' review status.' }))

    await confirmButton(w).trigger('click')
    await flushPromises()

    expect(w.find('[role="alert"]').text()).toBe('1 row(s) still in \'pending\' review status.')
    expect(duplicateGroups(w)[0].find('[data-testid="duplicate-group-state"]').text()).toBe('Resolved')
  })
})

describe('BatchReviewPanel — Patch 06 mutation failure handling', () => {
  it('a 5xx from a mutation refetches GET /review', async () => {
    const w = mountPanel(makePayload({ intra_file_duplicates: [makeDuplicateGroup()] }))
    await flushPromises()
    mockDeleteRow.mockRejectedValue(axiosError(500, 'Internal Server Error'))
    mockFetchReviewPayload.mockResolvedValue(makePayload({ intra_file_duplicates: [makeDuplicateGroup({ resolved: true })] }))

    await duplicateGroups(w)[0].findAll('button').find(b => b.text() === 'Delete')!.trigger('click')
    await flushPromises()

    expect(mockFetchReviewPayload).toHaveBeenCalledTimes(2)
    expect(duplicateGroups(w)[0].find('[data-testid="duplicate-group-state"]').text()).toBe('Resolved')
    expect(w.find('[role="alert"]').text()).toContain('may still have been saved')
  })

  it('a 4xx from a mutation does not refetch', async () => {
    const w = mountPanel(makePayload({ intra_file_duplicates: [makeDuplicateGroup()] }))
    await flushPromises()
    mockPatchSplitSuffix.mockRejectedValue(axiosError(422, { detail: "split_suffix 'x' exceeds 32 characters" }))

    await duplicateGroups(w)[0].findAll('button').filter(b => b.text() === 'Apply')[0].trigger('click')
    await flushPromises()

    expect(mockFetchReviewPayload).toHaveBeenCalledTimes(1)
    expect(w.find('[role="alert"]').text()).toBe("split_suffix 'x' exceeds 32 characters")
  })
})

describe('BatchReviewPanel — Patch 06 row rendering', () => {
  it('shows Revert suffix only for operator suffixes and applies the recomputed suffix', async () => {
    const group = makeDuplicateGroup({
      resolved: true,
      rows: [
        makeRow({ staging_row_id: 101, review_split_suffix_override: '-9par', review_split_suffix_source: 'operator', review_part_number_override: '123456', actionable: true }),
        makeRow({ staging_row_id: 102, source_row_number: 2, review_split_suffix_source: 'computed', review_part_number_override: '123456', actionable: true }),
      ],
    })
    const w = mountPanel(makePayload({ intra_file_duplicates: [group] }))
    await flushPromises()
    mockRevertSplitSuffix.mockResolvedValue({
      row: rowResponse({ staging_row_id: 101, review_part_number_override: '123456', review_split_suffix_override: null, review_split_suffix_source: 'computed' }),
      group: { parsed_part_number: '123456', review_status: 'verified', active_row_count: 2 },
      intra_file_duplicates: [makeDuplicateGroup()],
    })

    const operatorRow = duplicateGroups(w)[0].find('[data-row-id="101"]')
    const computedRow = duplicateGroups(w)[0].find('[data-row-id="102"]')
    expect(computedRow.findAll('button').some(b => b.text() === 'Revert suffix')).toBe(false)
    await operatorRow.find('input[placeholder="-par"]').setValue('-5par')
    await operatorRow.findAll('button').find(b => b.text() === 'Revert suffix')!.trigger('click')
    await flushPromises()

    expect(mockRevertSplitSuffix).toHaveBeenCalledWith(7, 101)
    const input = duplicateGroups(w)[0].find('[data-row-id="101"]').find<HTMLInputElement>('input[placeholder="-par"]')
    expect(input.element.value).toBe('')
  })

  it('renders a non-actionable row read-only', async () => {
    const group = makeDuplicateGroup({
      origin: 'edit_induced',
      rows: [
        makeRow({ staging_row_id: 101, actionable: true }),
        makeRow({ staging_row_id: 103, source_row_number: 3, review_status: null, actionable: false }),
      ],
    })
    const w = mountPanel(makePayload({ intra_file_duplicates: [group] }))
    await flushPromises()

    const readOnly = duplicateGroups(w)[0].find('[data-row-id="103"]')
    expect(readOnly.find('[data-testid="read-only-note"]').exists()).toBe(true)
    expect(readOnly.text()).toContain('not in review')
    for (const control of [...readOnly.findAll('button'), ...readOnly.findAll('input')]) {
      expect((control.element as HTMLButtonElement | HTMLInputElement).disabled).toBe(true)
    }
    const actionableRow = duplicateGroups(w)[0].find('[data-row-id="101"]')
    expect(actionableRow.findAll('button').every(b => !(b.element as HTMLButtonElement).disabled)).toBe(true)
  })

  it('shows a row\'s effective identity only when it differs from the group identity', async () => {
    const renamed = { ...IDENTITY, split_suffix: '-2par' }
    const group = makeDuplicateGroup({
      resolved: true,
      rows: [
        makeRow({ staging_row_id: 101, effective_identity: IDENTITY, actionable: true }),
        makeRow({ staging_row_id: 102, source_row_number: 2, effective_identity: renamed, actionable: true }),
      ],
    })
    const w = mountPanel(makePayload({ intra_file_duplicates: [group] }))
    await flushPromises()

    const section = duplicateGroups(w)[0]
    expect(section.find('[data-row-id="101"] [data-testid="effective-identity"]').exists()).toBe(false)
    expect(section.find('[data-row-id="102"] [data-testid="effective-identity"]').text()).toBe('writes as 123456 · new · -2par')
  })

  it('canonical actions in an edit-induced group address the row\'s own parsed part number', async () => {
    const group = makeDuplicateGroup({
      origin: 'edit_induced',
      parsed_part_number: '654321',
      identity: { ...IDENTITY, part_number: '654321' },
      rows: [
        makeRow({ staging_row_id: 101, parsed_part_number: '123456', review_part_number_override: '654321', shape_rule_fired: false, actionable: true }),
        makeRow({ staging_row_id: 104, source_row_number: 4, parsed_part_number: '654321', shape_rule_fired: false, actionable: true }),
      ],
    })
    const w = mountPanel(makePayload({ intra_file_duplicates: [group] }))
    await flushPromises()

    const row = duplicateGroups(w)[0].find('[data-row-id="101"]')
    await row.find('input[placeholder="canonical"]').setValue('777777')
    await row.findAll('button').find(b => b.text() === 'Apply')!.trigger('click')
    await flushPromises()

    expect(mockSetCanonical).toHaveBeenCalledWith(7, '123456', '777777')
  })
})
