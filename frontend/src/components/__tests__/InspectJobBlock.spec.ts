import { describe, it, expect, afterEach, vi } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import InspectJobBlock from '../InspectJobBlock.vue'
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
    discarded_at: null,
    ...overrides,
  } as JobReadExpanded
}

const mockEditJob = vi.fn()
const mockDiscardJob = vi.fn()

vi.mock('@/composables/useJobActions', () => ({
  useJobActions: () => ({
    canEdit: (job: JobReadExpanded) => job.status === 'shipped',
    canDiscard: () => true,
    editJob: mockEditJob,
    discardJob: mockDiscardJob,
  }),
}))

vi.mock('@/composables/useToast', () => ({
  useToast: () => ({
    show: vi.fn(),
  }),
}))

let wrapper: VueWrapper | null = null

function mountBlock(
  job: JobReadExpanded,
  extraProps: Record<string, unknown> = {},
) {
  wrapper = mount(InspectJobBlock, {
    props: {
      job,
      isAnchor: true,
      showAllData: false,
      editLocked: false,
      photoFolders: [],
      photoStatus: 'unknown',
      openPhotosCallback: vi.fn(),
      openGalleryCallback: vi.fn(),
      ...extraProps
    },
    attachTo: document.body,
  })
  return wrapper
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  mockEditJob.mockReset()
  mockDiscardJob.mockReset()
})

function bodyHtml() {
  return document.body.innerHTML
}

describe('InspectJobBlock', () => {
  it('flattens nested objects with dotted keys', async () => {
    mountBlock(makeJob(), { showAllData: true })
    const html = bodyHtml()
    expect(html).toContain('assembly.part_number')
    expect(html).toContain('customer.name')
    expect(html).toContain('Acme Corp')
  })

  it('joins all-scalar arrays into comma-separated strings', async () => {
    const job = makeJob()
    ;(job as Record<string, unknown>).tags = ['fast', 'urgent']
    mountBlock(job, { showAllData: true })
    expect(bodyHtml()).toContain('fast, urgent')
  })

  it('recurses into object arrays with bracket indices', async () => {
    mountBlock(makeJob(), { showAllData: true })
    const html = bodyHtml()
    expect(html).toContain('assembly.classifications[0].code')
    expect(html).toContain('AS9100')
  })

  it('does not render Edit button when canEdit is false', () => {
    mountBlock(makeJob({ status: 'planned' }))
    expect(bodyHtml()).not.toContain('inspect-edit-btn')
  })

  it('renders Edit button when canEdit is true', () => {
    mountBlock(makeJob())
    expect(bodyHtml()).toContain('inspect-edit-btn')
  })

  it('enters edit mode on Edit button click', async () => {
    const w = mountBlock(makeJob())
    const editBtn = document.body.querySelector('[data-testid="inspect-edit-btn"]') as HTMLElement
    editBtn.click()
    await w.vm.$nextTick()
    expect(bodyHtml()).toContain('edit-part-number')
    expect(w.emitted('editStarted')).toBeTruthy()
  })

  it('Save button is disabled until reason is filled', async () => {
    const w = mountBlock(makeJob())
    const editBtn = document.body.querySelector('[data-testid="inspect-edit-btn"]') as HTMLElement
    editBtn.click()
    await w.vm.$nextTick()
    const saveBtn = document.body.querySelector('[data-testid="edit-save-btn"]') as HTMLButtonElement
    expect(saveBtn.disabled).toBe(true)
  })

  it('Cancel in edit mode returns to read mode', async () => {
    const w = mountBlock(makeJob())
    const editBtn = document.body.querySelector('[data-testid="inspect-edit-btn"]') as HTMLElement
    editBtn.click()
    await w.vm.$nextTick()
    const cancelBtn = document.body.querySelector('[data-testid="edit-cancel-btn"]') as HTMLElement
    cancelBtn.click()
    await w.vm.$nextTick()
    expect(bodyHtml()).not.toContain('edit-part-number')
    expect(w.emitted('editEnded')).toBeTruthy()
  })

  it('calls editJob on Save', async () => {
    mockEditJob.mockResolvedValue({ kind: 'ok', job: makeJob({ assembly: { ...makeJob().assembly, part_number: 'UPDATED-001' } }) })
    const w = mountBlock(makeJob())
    const editBtn = document.body.querySelector('[data-testid="inspect-edit-btn"]') as HTMLElement
    editBtn.click()
    await w.vm.$nextTick()

    const reasonEl = document.body.querySelector('[data-testid="edit-reason-textarea"]') as HTMLTextAreaElement
    reasonEl.value = 'Correcting part number'
    reasonEl.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()

    const partNumberEl = document.body.querySelector('[data-testid="edit-part-number"]') as HTMLInputElement
    partNumberEl.value = 'UPDATED-001'
    partNumberEl.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()

    const saveBtn = document.body.querySelector('[data-testid="edit-save-btn"]') as HTMLButtonElement
    saveBtn.click()
    await w.vm.$nextTick()
    await flushPromises() // ensure async function completes
    
    expect(mockEditJob).toHaveBeenCalledWith(
      42,
      expect.objectContaining({ part_number: 'UPDATED-001' }),
      'Correcting part number',
    )
  })

  it('Save payload omits unchanged fields', async () => {
    mockEditJob.mockResolvedValue({ kind: 'ok', job: makeJob() })
    const w = mountBlock(makeJob())
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
    await flushPromises() // ensure async function completes

    const [, payload] = mockEditJob.mock.calls[0] as [number, Record<string, unknown>, string]
    expect(payload).toHaveProperty('raw_qty', '99')
    expect(payload).not.toHaveProperty('part_number')
    expect(payload).not.toHaveProperty('build_type')
  })

  it('Save payload sends empty string to clear a clearable field', async () => {
    mockEditJob.mockResolvedValue({ kind: 'ok', job: makeJob() })
    const job = makeJob({ split_suffix: '-1a' } as Partial<JobReadExpanded>)
    const w = mountBlock(job)
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
    await flushPromises() // ensure async function completes

    const [, payload] = mockEditJob.mock.calls[0] as [number, Record<string, unknown>, string]
    expect(payload).toHaveProperty('split_suffix', '')
  })

  it('opens confirm modal on Discard button click', async () => {
    const w = mountBlock(makeJob())
    const discardBtn = document.body.querySelector('[data-testid="inspect-discard-btn"]') as HTMLElement
    discardBtn.click()
    await w.vm.$nextTick()
    expect(bodyHtml()).toContain('confirm-discard-modal')
  })

  it('calls discardJob after confirm', async () => {
    mockDiscardJob.mockResolvedValue({ kind: 'ok', job_id: 42 })
    const w = mountBlock(makeJob())
    const discardBtn = document.body.querySelector('[data-testid="inspect-discard-btn"]') as HTMLElement
    discardBtn.click()
    await w.vm.$nextTick()

    const reasonEl = document.body.querySelector('[data-testid="confirm-discard-reason"]') as HTMLTextAreaElement
    reasonEl.value = 'Duplicate entry'
    reasonEl.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()

    const confirmBtn = document.body.querySelector('[data-testid="confirm-discard-confirm-btn"]') as HTMLButtonElement
    confirmBtn.click()
    await w.vm.$nextTick()
    await flushPromises() // ensure async function completes

    expect(mockDiscardJob).toHaveBeenCalledWith(42, 'Duplicate entry')
  })

  describe('photos integration', () => {
    it('disables photos button with empty folders array', () => {
      mountBlock(makeJob({ shipped_at: '2023-07-24' }), {
        photoStatus: 'ok',
        photoFolders: []
      })
      const btn = document.body.querySelector('[data-testid="inspect-photos-btn"]') as HTMLButtonElement
      expect(btn.disabled).toBe(true)
      expect(btn.title).toContain('No photos folder')
    })

    it('enables photos button when available and calls open callback', async () => {
      const openPhotosCallback = vi.fn().mockResolvedValue({ kind: 'ok' })
      const w = mountBlock(makeJob({ shipped_at: '2023-07-24' }), {
        photoStatus: 'ok',
        photoFolders: ['2023_07_24'],
        openPhotosCallback
      })
      
      const btn = document.body.querySelector('[data-testid="inspect-photos-btn"]') as HTMLButtonElement
      expect(btn.disabled).toBe(false)
      
      btn.click()
      await w.vm.$nextTick()
      expect(openPhotosCallback).toHaveBeenCalledWith('2023_07_24')
    })
  })
})

async function flushPromises() {
  return new Promise(resolve => setTimeout(resolve, 0))
}
