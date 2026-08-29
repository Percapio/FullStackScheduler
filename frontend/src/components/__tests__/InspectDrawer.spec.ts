import { describe, it, expect, afterEach, vi } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import InspectDrawer from '../InspectDrawer.vue'
import type { JobReadExpanded } from '@/api/history'

const ts = '2026-04-19T00:00:00'

function makeJob(overrides: Partial<JobReadExpanded> = {}): JobReadExpanded {
  return {
    id: 42,
    assembly_id: 1,
    customer_id: 1,
    status: 'shipped',
    quantity: 10,
    shipped_at: '2026-04-01',
    line_1: false,
    line_2: false,
    line_3: false,
    created_at: ts,
    updated_at: ts,
    assembly: {
      id: 1,
      part_number: 'TEST-001',
      created_at: ts,
      updated_at: ts,
      classifications: [{ id: 1, code: 'AS9100', description: 'Aero' }],
    },
    customer: { id: 1, name: 'Acme Corp', created_at: ts, updated_at: ts },
    salesperson: null,
    ...overrides,
  } as JobReadExpanded
}

let wrapper: VueWrapper | null = null

function mountDrawer(
  row: JobReadExpanded | null,
  extraProps: Record<string, unknown> = {},
) {
  wrapper = mount(InspectDrawer, {
    props: { row, ...extraProps },
    attachTo: document.body,
  })
  return wrapper
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

function bodyHtml() {
  return document.body.innerHTML
}

describe('InspectDrawer', () => {
  // ── read-mode rendering ───────────────────────────────────────────────────
  it('renders nothing when row is null', () => {
    mountDrawer(null)
    expect(bodyHtml()).not.toContain('drawer-overlay')
  })

  it('flattens nested objects with dotted keys (Q4)', async () => {
    window.localStorage.setItem('inspect-drawer-show-all', 'true')
    mountDrawer(makeJob())
    const html = bodyHtml()
    expect(html).toContain('assembly.part_number')
    expect(html).toContain('customer.name')
    expect(html).toContain('Acme Corp')
    window.localStorage.clear()
  })

  it('joins all-scalar arrays into comma-separated strings', async () => {
    window.localStorage.setItem('inspect-drawer-show-all', 'true')
    const job = makeJob()
    ;(job as Record<string, unknown>).tags = ['fast', 'urgent']
    mountDrawer(job)
    expect(bodyHtml()).toContain('fast, urgent')
    window.localStorage.clear()
  })

  it('recurses into object arrays with bracket indices', async () => {
    window.localStorage.setItem('inspect-drawer-show-all', 'true')
    mountDrawer(makeJob())
    const html = bodyHtml()
    expect(html).toContain('assembly.classifications[0].code')
    expect(html).toContain('AS9100')
    window.localStorage.clear()
  })

  it('renders "—" for null values', async () => {
    window.localStorage.setItem('inspect-drawer-show-all', 'true')
    mountDrawer(makeJob({ salesperson: null }))
    const html = bodyHtml()
    expect(html).toContain('salesperson')
    expect(html).toContain('—')
    window.localStorage.clear()
  })

  it('emits close on X button click', async () => {
    const w = mountDrawer(makeJob())
    const btn = document.body.querySelector('[data-testid="drawer-close-btn"]') as HTMLElement
    btn.click()
    await w.vm.$nextTick()
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('emits close on backdrop click (Q6)', async () => {
    const w = mountDrawer(makeJob())
    const backdrop = document.body.querySelector('[data-testid="drawer-backdrop"]') as HTMLElement
    backdrop.click()
    await w.vm.$nextTick()
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('emits close on ESC keypress (Q6)', () => {
    const w = mountDrawer(makeJob())
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('ESC listener is only active while row is non-null (V2 lifecycle)', () => {
    const w = mountDrawer(null)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(w.emitted('close')).toBeUndefined()
  })

  // ── canEdit / canDiscard gating ───────────────────────────────────────────
  it('does not render Edit button when canEdit is false', () => {
    mountDrawer(makeJob(), { canEdit: false })
    expect(bodyHtml()).not.toContain('inspect-edit-btn')
  })

  it('renders Edit button when canEdit is true', () => {
    mountDrawer(makeJob(), { canEdit: true })
    expect(bodyHtml()).toContain('inspect-edit-btn')
  })

  it('does not render Discard button when canDiscard is false', () => {
    mountDrawer(makeJob(), { canDiscard: false })
    expect(bodyHtml()).not.toContain('inspect-discard-btn')
  })

  it('renders Discard button when canDiscard is true', () => {
    mountDrawer(makeJob(), { canDiscard: true })
    expect(bodyHtml()).toContain('inspect-discard-btn')
  })

  // ── edit mode ─────────────────────────────────────────────────────────────
  it('enters edit mode on Edit button click', async () => {
    const w = mountDrawer(makeJob(), { canEdit: true })
    const editBtn = document.body.querySelector('[data-testid="inspect-edit-btn"]') as HTMLElement
    editBtn.click()
    await w.vm.$nextTick()
    expect(bodyHtml()).toContain('edit-part-number')
  })

  it('Save button is disabled until reason is filled', async () => {
    const editImpl = vi.fn().mockResolvedValue(undefined)
    const w = mountDrawer(makeJob(), { canEdit: true, editImpl })
    const editBtn = document.body.querySelector('[data-testid="inspect-edit-btn"]') as HTMLElement
    editBtn.click()
    await w.vm.$nextTick()
    const saveBtn = document.body.querySelector('[data-testid="edit-save-btn"]') as HTMLButtonElement
    expect(saveBtn.disabled).toBe(true)
  })

  it('Cancel in edit mode returns to read mode', async () => {
    const w = mountDrawer(makeJob(), { canEdit: true })
    const editBtn = document.body.querySelector('[data-testid="inspect-edit-btn"]') as HTMLElement
    editBtn.click()
    await w.vm.$nextTick()
    const cancelBtn = document.body.querySelector('[data-testid="edit-cancel-btn"]') as HTMLElement
    cancelBtn.click()
    await w.vm.$nextTick()
    expect(bodyHtml()).not.toContain('edit-raw-job')
  })

  it('calls editImpl with (jobId, draft, reason) on Save', async () => {
    const editImpl = vi.fn().mockResolvedValue(undefined)
    const w = mountDrawer(makeJob(), { canEdit: true, editImpl })
    const editBtn = document.body.querySelector('[data-testid="inspect-edit-btn"]') as HTMLElement
    editBtn.click()
    await w.vm.$nextTick()

    // Fill reason
    const reasonEl = document.body.querySelector('[data-testid="edit-reason-textarea"]') as HTMLTextAreaElement
    reasonEl.value = 'Correcting part number'
    reasonEl.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()

    // Change a field — part_number differs from prefill 'TEST-001'
    const partNumberEl = document.body.querySelector('[data-testid="edit-part-number"]') as HTMLInputElement
    partNumberEl.value = 'UPDATED-001'
    partNumberEl.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()

    const saveBtn = document.body.querySelector('[data-testid="edit-save-btn"]') as HTMLButtonElement
    saveBtn.click()
    await w.vm.$nextTick()

    expect(editImpl).toHaveBeenCalledWith(
      42,
      expect.objectContaining({ part_number: 'UPDATED-001' }),
      'Correcting part number',
    )
  })

  it('Save payload omits unchanged fields', async () => {
    const editImpl = vi.fn().mockResolvedValue(undefined)
    const w = mountDrawer(makeJob(), { canEdit: true, editImpl })
    const editBtn = document.body.querySelector('[data-testid="inspect-edit-btn"]') as HTMLElement
    editBtn.click()
    await w.vm.$nextTick()

    const reasonEl = document.body.querySelector('[data-testid="edit-reason-textarea"]') as HTMLTextAreaElement
    reasonEl.value = 'fix qty'
    reasonEl.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()

    const qtyEl = document.body.querySelector('[data-testid="edit-raw-qty"]') as HTMLInputElement
    qtyEl.value = '99'
    qtyEl.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()

    const saveBtn = document.body.querySelector('[data-testid="edit-save-btn"]') as HTMLButtonElement
    saveBtn.click()
    await w.vm.$nextTick()

    const [, payload] = editImpl.mock.calls[0] as [number, Record<string, unknown>, string]
    expect(payload).toHaveProperty('raw_qty', '99')
    // unchanged identity fields must not appear in the payload
    expect(payload).not.toHaveProperty('part_number')
    expect(payload).not.toHaveProperty('build_type')
  })

  it('Save payload sends empty string to clear a clearable field', async () => {
    const editImpl = vi.fn().mockResolvedValue(undefined)
    const job = makeJob({ split_suffix: '-1a' } as Partial<JobReadExpanded>)
    const w = mountDrawer(job, { canEdit: true, editImpl })
    const editBtn = document.body.querySelector('[data-testid="inspect-edit-btn"]') as HTMLElement
    editBtn.click()
    await w.vm.$nextTick()

    const reasonEl = document.body.querySelector('[data-testid="edit-reason-textarea"]') as HTMLTextAreaElement
    reasonEl.value = 'remove suffix'
    reasonEl.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()

    const suffixEl = document.body.querySelector('[data-testid="edit-split-suffix"]') as HTMLInputElement
    suffixEl.value = ''
    suffixEl.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()

    const saveBtn = document.body.querySelector('[data-testid="edit-save-btn"]') as HTMLButtonElement
    saveBtn.click()
    await w.vm.$nextTick()

    const [, payload] = editImpl.mock.calls[0] as [number, Record<string, unknown>, string]
    expect(payload).toHaveProperty('split_suffix', '')
  })

  // ── discard mode ──────────────────────────────────────────────────────────
  it('opens confirm modal on Discard button click', async () => {
    const w = mountDrawer(makeJob(), { canDiscard: true, discardImpl: vi.fn() })
    const discardBtn = document.body.querySelector('[data-testid="inspect-discard-btn"]') as HTMLElement
    discardBtn.click()
    await w.vm.$nextTick()
    expect(bodyHtml()).toContain('confirm-discard-modal')
  })

  it('calls discardImpl with (jobId, reason) after confirm', async () => {
    const discardImpl = vi.fn().mockResolvedValue(undefined)
    const w = mountDrawer(makeJob(), { canDiscard: true, discardImpl })
    const discardBtn = document.body.querySelector('[data-testid="inspect-discard-btn"]') as HTMLElement
    discardBtn.click()
    await w.vm.$nextTick()

    // Fill reason in the modal
    const reasonEl = document.body.querySelector('[data-testid="confirm-discard-reason"]') as HTMLTextAreaElement
    reasonEl.value = 'Duplicate entry'
    reasonEl.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()

    const confirmBtn = document.body.querySelector('[data-testid="confirm-discard-confirm-btn"]') as HTMLButtonElement
    confirmBtn.click()
    await w.vm.$nextTick()

    expect(discardImpl).toHaveBeenCalledWith(42, 'Duplicate entry')
  })
})

