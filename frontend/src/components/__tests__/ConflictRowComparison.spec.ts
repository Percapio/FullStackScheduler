import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ConflictRowComparison from '../ConflictRowComparison.vue'
import { useStagingStore } from '@/stores/staging'
import type { ConflictGroup, StagingRowDetail } from '@/api/staging'

// ---------------------------------------------------------------------------
// API mock — prevent real HTTP calls
// ---------------------------------------------------------------------------
const mockSubmitCorrection = vi.fn()
const mockDeleteStagingRow = vi.fn()
const mockFetchConflicts   = vi.fn()
const mockFetchErrored     = vi.fn()

vi.mock('@/api/staging', () => ({
  fetchErrored:       (...a: unknown[]) => mockFetchErrored(...a),
  fetchDetail:        vi.fn(),
  submitCorrection:   (...a: unknown[]) => mockSubmitCorrection(...a),
  deleteStagingRow:   (...a: unknown[]) => mockDeleteStagingRow(...a),
  fetchConflicts:     (...a: unknown[]) => mockFetchConflicts(...a),
  fetchDiscarded:     vi.fn(),
  postRestoreStagingRow: vi.fn(),
}))

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const ts = '2026-05-03T00:00:00'

function makeRow(id: number, raw_job = `JOB-${id}`, overrides: Partial<StagingRowDetail> = {}): StagingRowDetail {
  return {
    id,
    batch_id: 1,
    source_row_number: id,
    processing_status: 'error',
    processing_error: 'Duplicate group key',
    suggested_correction: null,
    resolved_job_id: null,
    processed_at: null,
    created_at: ts,
    updated_at: ts,
    raw_job,
    raw_qty: '10',
    raw_customer: 'ACME',
    raw_ship_date: null,
    raw_shipped: null,
    raw_sales_p: null,
    raw_prog: null,
    raw_mfg_notes: null,
    raw_pcb_notes: null,
    raw_kit_notes: null,
    raw_scheduling_notes: null,
    raw_smt_lines: null,
    raw_smt_plcmnts: null,
    raw_ship_method: null,
    raw_doc_rel: null,
    raw_kit_rel: null,
    raw_code: null,
    raw_bom_compare_photos: null,
    raw_line_1: null,
    raw_line_2: null,
    raw_line_3: null,
    highlight_fields: [],
    duplicate_group_key: 'JOB/1/a/b',
    ...overrides,
  } as StagingRowDetail
}

function makeGroup(rows: StagingRowDetail[] = [makeRow(10), makeRow(11)]): ConflictGroup {
  return {
    batch_id: 1,
    group_key: 'JOB/1/a/b',
    kind: 'intra_file_duplicate',
    rows,
  } as ConflictGroup
}

// ---------------------------------------------------------------------------
// Mount helper
// ---------------------------------------------------------------------------
let wrapper: VueWrapper | null = null

function mountComponent(group: ConflictGroup | undefined = makeGroup()) {
  setActivePinia(createPinia())
  const store = useStagingStore()
  // Pre-seed reconciledConflictGroups so loadConflicts is mocked at the store
  // level and the component can check presence/absence after reload.
  if (group) store.reconciledConflictGroups = [group]

  wrapper = mount(ConflictRowComparison, {
    props: { group },
    attachTo: document.body,
  })
  return { wrapper, store }
}

beforeEach(() => {
  mockSubmitCorrection.mockReset()
  mockDeleteStagingRow.mockReset()
  mockFetchConflicts.mockReset()
  mockFetchErrored.mockResolvedValue({ rows: [], total: 0 })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

// ---------------------------------------------------------------------------
// G1 — existing surface: Accept button present and disabled when no changes
// ---------------------------------------------------------------------------
describe('ConflictRowComparison — Accept button', () => {
  it('renders Accept button disabled when no changes are pending', async () => {
    mountComponent()
    await flushPromises()
    const btn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    expect(btn).not.toBeNull()
    expect(btn.disabled).toBe(true)
  })

  it('enables Accept button after editing a cell', async () => {
    const { store } = mountComponent()
    await flushPromises()
    // Spy so loadConflicts doesn't blow up if called
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)

    const cell = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell.value = 'JOB-10-EDITED'
    cell.dispatchEvent(new Event('input'))
    await flushPromises()

    const btn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    expect(btn.disabled).toBe(false)
  })

  it('enables Accept button after marking a row for discard', async () => {
    const { store } = mountComponent()
    await flushPromises()
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)

    const discard = document.body.querySelector('[data-testid="conflict-discard-btn-10"]') as HTMLButtonElement
    discard.click()
    await flushPromises()

    const btn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    expect(btn.disabled).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// G1.5 — new methods wired; existing correct/discardRow NOT called
// ---------------------------------------------------------------------------
describe('ConflictRowComparison — G1.5: new store methods wired', () => {
  it('onAccept calls correctConflictRow for edited rows and discardConflictRow for discarded rows, edits first', async () => {
    const { store } = mountComponent()
    await flushPromises()

    const correctSpy = vi.spyOn(store, 'correctConflictRow')
      .mockResolvedValue({ kind: 'ok' })
    const discardSpy = vi.spyOn(store, 'discardConflictRow')
      .mockResolvedValue({ kind: 'ok' })
    const oldCorrectSpy = vi.spyOn(store, 'correct')
    const oldDiscardSpy = vi.spyOn(store, 'discardRow')
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)
    // Group resolved after reload
    store.reconciledConflictGroups = []

    // Edit row 10
    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'JOB-10-EDITED'
    cell10.dispatchEvent(new Event('input'))
    await flushPromises()

    // Discard row 11
    const discard11 = document.body.querySelector('[data-testid="conflict-discard-btn-11"]') as HTMLButtonElement
    discard11.click()
    await flushPromises()

    const acceptBtn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    acceptBtn.click()
    await flushPromises()

    // Only new methods called, NOT old ones
    expect(oldCorrectSpy).not.toHaveBeenCalled()
    expect(oldDiscardSpy).not.toHaveBeenCalled()

    // correctConflictRow called for row 10 (edit)
    expect(correctSpy).toHaveBeenCalledTimes(1)
    expect(correctSpy.mock.calls[0][0]).toBe(10)

    // discardConflictRow called for row 11 (discard)
    expect(discardSpy).toHaveBeenCalledTimes(1)
    expect(discardSpy.mock.calls[0][0]).toBe(11)

    // Edit before discard — correctSpy called before discardSpy
    const correctOrder = correctSpy.mock.invocationCallOrder[0]
    const discardOrder = discardSpy.mock.invocationCallOrder[0]
    expect(correctOrder).toBeLessThan(discardOrder)
  })

  it('emits resolved when cleanResolved === true', async () => {
    const { store } = mountComponent()
    await flushPromises()

    vi.spyOn(store, 'correctConflictRow').mockResolvedValue({ kind: 'ok' })
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)
    store.reconciledConflictGroups = [] // group gone after reload

    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'JOB-10-EDITED'
    cell10.dispatchEvent(new Event('input'))
    await flushPromises()

    const acceptBtn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    acceptBtn.click()
    await flushPromises()

    expect(wrapper!.emitted('resolved')).toBeTruthy()
  })

  it('does NOT emit resolved when group still present after reload', async () => {
    const group = makeGroup()
    const { store } = mountComponent(group)
    await flushPromises()

    vi.spyOn(store, 'correctConflictRow').mockResolvedValue({ kind: 'transform-failed', processingError: 'bad' })
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)
    // Group still present
    store.reconciledConflictGroups = [group]

    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'JOB-10-EDITED'
    cell10.dispatchEvent(new Event('input'))
    await flushPromises()

    const acceptBtn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    acceptBtn.click()
    await flushPromises()

    expect(wrapper!.emitted('resolved')).toBeFalsy()
  })
})

// ---------------------------------------------------------------------------
// G1.6 — toggleDiscard invariant (§2.6 / G-2.6.A)
// ---------------------------------------------------------------------------
describe('ConflictRowComparison — G1.6: toggleDiscard invariant', () => {
  it('toggleDiscard while edited drops changedFields; toggling back returns pristine', async () => {
    const { store } = mountComponent()
    await flushPromises()
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)

    // Edit row 10
    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'MODIFIED'
    cell10.dispatchEvent(new Event('input'))
    await flushPromises()

    // Accept should be enabled (row is edited)
    let acceptBtn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    expect(acceptBtn.disabled).toBe(false)

    // Discard row 10 (drops the edit)
    const discard10 = document.body.querySelector('[data-testid="conflict-discard-btn-10"]') as HTMLButtonElement
    discard10.click()
    await flushPromises()

    // Row is now discarded, Accept still enabled
    acceptBtn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    expect(acceptBtn.disabled).toBe(false)

    // Cell should be disabled (G-3.1.A)
    const cellAfterDiscard = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    expect(cellAfterDiscard.disabled).toBe(true)

    // Undo discard (toggle off) → pristine
    discard10.click()
    await flushPromises()

    // Cell enabled again
    const cellAfterUndo = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    expect(cellAfterUndo.disabled).toBe(false)

    // Accept should now be disabled (back to pristine — edits were dropped)
    acceptBtn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    expect(acceptBtn.disabled).toBe(true)
  })

  it('discarded cell textarea is disabled (G-3.1.A)', async () => {
    mountComponent()
    await flushPromises()

    const discard10 = document.body.querySelector('[data-testid="conflict-discard-btn-10"]') as HTMLButtonElement
    discard10.click()
    await flushPromises()

    const cell = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    expect(cell.disabled).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// G1.7 — empty diff collapses to pristine (§2.4 / invariant I2)
// ---------------------------------------------------------------------------
describe('ConflictRowComparison — G1.7: empty-diff collapses to pristine', () => {
  it('editing a cell back to its original value returns pristine; Accept becomes disabled', async () => {
    mountComponent()
    await flushPromises()

    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement

    // Edit away from original
    cell10.value = 'CHANGED'
    cell10.dispatchEvent(new Event('input'))
    await flushPromises()

    let acceptBtn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    expect(acceptBtn.disabled).toBe(false)

    // Revert to original value (makeRow uses 'JOB-10' for id=10)
    cell10.value = 'JOB-10'
    cell10.dispatchEvent(new Event('input'))
    await flushPromises()

    // Accept must be disabled — the diff is now empty → pristine
    acceptBtn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    expect(acceptBtn.disabled).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Discard buttons (tfoot)
// ---------------------------------------------------------------------------
describe('ConflictRowComparison — tfoot discard buttons', () => {
  it('renders one Discard button per row in tfoot', async () => {
    mountComponent(makeGroup([makeRow(10), makeRow(11), makeRow(12)]))
    await flushPromises()
    const btns = document.body.querySelectorAll('[data-testid^="conflict-discard-btn-"]')
    expect(btns.length).toBe(3)
  })

  it('Discard button aria-pressed reflects discard state', async () => {
    mountComponent()
    await flushPromises()

    const discard10 = document.body.querySelector('[data-testid="conflict-discard-btn-10"]') as HTMLButtonElement
    expect(discard10.getAttribute('aria-pressed')).toBe('false')

    discard10.click()
    await flushPromises()

    expect(discard10.getAttribute('aria-pressed')).toBe('true')
  })
})

// ---------------------------------------------------------------------------
// §2.3 — draft reset on group identity change
// ---------------------------------------------------------------------------
describe('ConflictRowComparison — §2.3: draft reset on group swap', () => {
  it('clears drafts when the group prop changes to a different key', async () => {
    const { store } = mountComponent()
    await flushPromises()
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)

    // Edit a cell in the original group
    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'EDITED'
    cell10.dispatchEvent(new Event('input'))
    await flushPromises()

    let acceptBtn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    expect(acceptBtn.disabled).toBe(false)

    // Swap to a different group (different batch_id)
    const newGroup = makeGroup([makeRow(20), makeRow(21)])
    newGroup.batch_id = 2
    newGroup.group_key = 'OTHER/key'
    await wrapper!.setProps({ group: newGroup })
    await flushPromises()

    // Drafts cleared — Accept must be disabled again
    acceptBtn = document.body.querySelector('[data-testid="conflict-accept-btn"]') as HTMLButtonElement
    expect(acceptBtn.disabled).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// §3.3 — status line text variants
// ---------------------------------------------------------------------------
describe('ConflictRowComparison — §3.3: status line text', () => {
  function statusText(): string {
    return (document.body.querySelector('[data-testid="conflict-status-line"]') as HTMLElement)?.textContent?.trim() ?? ''
  }

  it('shows "No pending changes." when pristine', async () => {
    mountComponent()
    await flushPromises()
    expect(statusText()).toBe('No pending changes.')
  })

  it('shows "{n} edit{s} pending." after editing cells', async () => {
    mountComponent()
    await flushPromises()

    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'CHANGED'
    cell10.dispatchEvent(new Event('input'))
    await flushPromises()

    expect(statusText()).toBe('1 edit pending.')
  })

  it('shows "{n} discard{s} pending." after toggling discard', async () => {
    mountComponent()
    await flushPromises()

    document.body.querySelector<HTMLButtonElement>('[data-testid="conflict-discard-btn-10"]')!.click()
    document.body.querySelector<HTMLButtonElement>('[data-testid="conflict-discard-btn-11"]')!.click()
    await flushPromises()

    expect(statusText()).toBe('2 discards pending.')
  })

  it('shows combined edits+discards pending text', async () => {
    mountComponent(makeGroup([makeRow(10), makeRow(11), makeRow(12)]))
    await flushPromises()

    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'CHANGED'
    cell10.dispatchEvent(new Event('input'))
    document.body.querySelector<HTMLButtonElement>('[data-testid="conflict-discard-btn-11"]')!.click()
    await flushPromises()

    expect(statusText()).toBe('1 edit, 1 discard pending.')
  })

  it('shows "Group resolved." after clean Accept', async () => {
    const { store } = mountComponent()
    await flushPromises()

    vi.spyOn(store, 'correctConflictRow').mockResolvedValue({ kind: 'ok' })
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)
    store.reconciledConflictGroups = [] // group gone

    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'FIXED'
    cell10.dispatchEvent(new Event('input'))
    await flushPromises()

    document.body.querySelector<HTMLButtonElement>('[data-testid="conflict-accept-btn"]')!.click()
    await flushPromises()

    // After emit('resolved') the parent would close the panel; here we just
    // verify the status text that was set before the emit.
    expect(statusText()).toBe('Group resolved.')
  })

  it('shows partial-failure text when some rows need attention', async () => {
    const group = makeGroup([makeRow(10), makeRow(11)])
    const { store } = mountComponent(group)
    await flushPromises()

    vi.spyOn(store, 'correctConflictRow')
      .mockResolvedValueOnce({ kind: 'ok' })                           // row 10 ok
      .mockResolvedValueOnce({ kind: 'transform-failed', processingError: 'bad transform' }) // row 11 fails
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)
    // Group still present after reload
    store.reconciledConflictGroups = [group]

    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'FIXED-10'
    cell10.dispatchEvent(new Event('input'))
    const cell11 = document.body.querySelector('[data-testid="conflict-cell-11-raw_job"]') as HTMLTextAreaElement
    cell11.value = 'FIXED-11'
    cell11.dispatchEvent(new Event('input'))
    await flushPromises()

    document.body.querySelector<HTMLButtonElement>('[data-testid="conflict-accept-btn"]')!.click()
    await flushPromises()

    expect(statusText()).toContain('1/2 applied')
    expect(statusText()).toContain('need attention')
  })
})

// ---------------------------------------------------------------------------
// §3.3 — per-row outcome annotations in DOM (G5 scenario)
// ---------------------------------------------------------------------------
describe('ConflictRowComparison — §3.3: per-row outcome annotations', () => {
  it('renders transform-failed annotation in column header after Accept', async () => {
    const group = makeGroup()
    const { store } = mountComponent(group)
    await flushPromises()

    vi.spyOn(store, 'correctConflictRow').mockResolvedValue({
      kind: 'transform-failed', processingError: 'Invalid job cell: FOO',
    })
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)
    store.reconciledConflictGroups = [group]

    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'FOO'
    cell10.dispatchEvent(new Event('input'))
    await flushPromises()

    document.body.querySelector<HTMLButtonElement>('[data-testid="conflict-accept-btn"]')!.click()
    await flushPromises()

    const annotation = document.body.querySelector('[data-testid="conflict-row-outcome-10"]')
    expect(annotation).not.toBeNull()
    expect(annotation!.textContent).toContain('Invalid job cell: FOO')
  })

  it('renders asymmetric-trap annotation for discard-conflict after edit-ok (G6.5)', async () => {
    // 2-row group: row 10 edited (key-changing), row 11 discarded.
    // After Accept: edit ok, discard returns conflict, group resolved (lone-survivor logic).
    const group = makeGroup()
    const { store } = mountComponent(group)
    await flushPromises()

    vi.spyOn(store, 'correctConflictRow').mockResolvedValue({ kind: 'ok' })
    vi.spyOn(store, 'discardConflictRow').mockResolvedValue({
      kind: 'conflict', message: 'Row not in error state',
    })
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)
    // Group gone (resolved) even though discard failed — lone-survivor re-eval
    store.reconciledConflictGroups = []

    // Edit row 10
    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'NEW-KEY'
    cell10.dispatchEvent(new Event('input'))
    // Discard row 11
    document.body.querySelector<HTMLButtonElement>('[data-testid="conflict-discard-btn-11"]')!.click()
    await flushPromises()

    document.body.querySelector<HTMLButtonElement>('[data-testid="conflict-accept-btn"]')!.click()
    await flushPromises()

    // Panel stays open (cleanResolved=false: some perRow not ok even though resolved=true)
    expect(wrapper!.emitted('resolved')).toBeFalsy()

    const annotation = document.body.querySelector('[data-testid="conflict-row-outcome-11"]')
    expect(annotation).not.toBeNull()
    expect(annotation!.textContent).toContain('reactivated by sibling re-evaluation')
    expect(annotation!.textContent).toContain('R10')

    // Status line shows "resolved with warnings"
    const statusEl = document.body.querySelector('[data-testid="conflict-status-line"]') as HTMLElement
    expect(statusEl.textContent).toContain('resolved with warnings')
  })
})

// ---------------------------------------------------------------------------
// §4.3 — best-effort: loop never short-circuits on failure
// ---------------------------------------------------------------------------
describe('ConflictRowComparison — §4.3: best-effort submission', () => {
  it('submits all rows even when the first edit returns transform-failed', async () => {
    const group = makeGroup([makeRow(10), makeRow(11), makeRow(12)])
    const { store } = mountComponent(group)
    await flushPromises()

    const correctSpy = vi.spyOn(store, 'correctConflictRow')
      .mockResolvedValueOnce({ kind: 'transform-failed', processingError: 'bad' }) // row 10
      .mockResolvedValueOnce({ kind: 'ok' })                                        // row 11
      .mockResolvedValueOnce({ kind: 'ok' })                                        // row 12
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)
    store.reconciledConflictGroups = [group]

    for (const id of [10, 11, 12]) {
      const cell = document.body.querySelector(`[data-testid="conflict-cell-${id}-raw_job"]`) as HTMLTextAreaElement
      cell.value = `EDITED-${id}`
      cell.dispatchEvent(new Event('input'))
    }
    await flushPromises()

    document.body.querySelector<HTMLButtonElement>('[data-testid="conflict-accept-btn"]')!.click()
    await flushPromises()

    // All three rows submitted — not short-circuited after row 10's failure
    expect(correctSpy).toHaveBeenCalledTimes(3)
  })
})

// ---------------------------------------------------------------------------
// §4.5 — cleanResolved guard: resolved=true but non-ok perRow → no emit
// ---------------------------------------------------------------------------
describe('ConflictRowComparison — §4.5: cleanResolved guard', () => {
  it('does NOT emit resolved when resolved=true but some perRow.kind !== ok', async () => {
    const group = makeGroup()
    const { store } = mountComponent(group)
    await flushPromises()

    vi.spyOn(store, 'correctConflictRow').mockResolvedValue({ kind: 'ok' })
    vi.spyOn(store, 'discardConflictRow').mockResolvedValue({
      kind: 'conflict', message: 'Row already processed',
    })
    vi.spyOn(store, 'loadConflicts').mockResolvedValue(undefined)
    // Group gone (resolved=true)
    store.reconciledConflictGroups = []

    const cell10 = document.body.querySelector('[data-testid="conflict-cell-10-raw_job"]') as HTMLTextAreaElement
    cell10.value = 'NEW'
    cell10.dispatchEvent(new Event('input'))
    document.body.querySelector<HTMLButtonElement>('[data-testid="conflict-discard-btn-11"]')!.click()
    await flushPromises()

    document.body.querySelector<HTMLButtonElement>('[data-testid="conflict-accept-btn"]')!.click()
    await flushPromises()

    expect(wrapper!.emitted('resolved')).toBeFalsy()
  })
})
