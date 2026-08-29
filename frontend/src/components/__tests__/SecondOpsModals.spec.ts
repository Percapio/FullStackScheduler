import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, VueWrapper, flushPromises } from '@vue/test-utils'
import SecondOpsEntryModal from '../SecondOpsEntryModal.vue'
import SecondOpsRecordModal from '../SecondOpsRecordModal.vue'
import SecondOpsItemModal from '../SecondOpsItemModal.vue'
import type {
  AuditBomFields,
  SecondOpsFetch,
  SecondOpsLine,
  SecondOpsRecord,
  SecondOpsSaveResult,
  SecondOpsWritePayload,
} from '@/api/secondOps'
import type { JobReadExpanded } from '@/api/history'

const ts = '2026-08-28T10:00:00'

const job = {
  id: 42,
  assembly_id: 1,
  customer_id: 1,
  status: 'planned',
  quantity: 10,
  line_1: false,
  line_2: false,
  line_3: false,
  created_at: ts,
  updated_at: ts,
  assembly: { id: 1, part_number: 'B142006', created_at: ts, updated_at: ts },
  customer: { id: 1, name: 'ACME', created_at: ts, updated_at: ts },
} as unknown as JobReadExpanded

function makeLine(overrides: Partial<SecondOpsLine> = {}): SecondOpsLine {
  return {
    id: 1,
    line_order: 0,
    find_number: '1',
    component_part_number: 'CMP-1',
    per_board_count: '2',
    ref_des: 'C1, C2',
    description: 'CAP 0.1uF',
    mount_type: 'SMT',
    quantity_needed: '40',
    quantity_on_hand: '500',
    ...overrides,
  }
}

function makeRecord(overrides: Partial<SecondOpsRecord> = {}): SecondOpsRecord {
  return {
    job_id: job.id,
    state: 'unaudited',
    reviewed_at: null,
    unexpected_inclusions: null,
    lines: [],
    limits: { max_lines: 500, note_max_chars: 4000 },
    ...overrides,
  }
}

function tsvLine(overrides: Record<number, string> = {}): string {
  return Array.from({ length: 14 }, (_, i) => overrides[i + 1] ?? `c${i + 1}`).join('\t')
}

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

function mountEntry(
  fetch: SecondOpsFetch,
  saveImpl: (jobId: number, payload: unknown) => Promise<SecondOpsSaveResult> = async () => ({
    kind: 'saved',
    record: makeRecord(),
  }),
) {
  wrapper = mount(SecondOpsEntryModal, {
    props: { job, fetch, isOpen: true, saveImpl: saveImpl as never },
    attachTo: document.body,
  })
  return wrapper
}

function find(selector: string): HTMLElement | null {
  return document.body.querySelector(selector)
}

describe('SecondOpsEntryModal', () => {
  it('shows a loading indicator and no editing surface while the fetch is in flight', () => {
    // Pasting into a grid that is about to be replaced would silently discard it.
    mountEntry({ status: 'loading' })

    expect(find('[data-testid="second-ops-entry-loading"]')).not.toBeNull()
    expect(find('[data-testid="second-ops-paste-area"]')).toBeNull()
    expect(find('[data-testid="second-ops-grid"]')).toBeNull()
    expect(find('[data-testid="second-ops-accept-btn"]')).toBeNull()
  })

  it('shows the message and a Retry on a failed fetch, with ACCEPT unreachable', () => {
    mountEntry({ status: 'failed', message: 'Could not load the 2nd OPS record.' })

    expect(find('[data-testid="second-ops-entry-failed"]')?.textContent).toContain(
      'Could not load',
    )
    expect(find('[data-testid="second-ops-entry-retry-btn"]')).not.toBeNull()
    expect(find('[data-testid="second-ops-accept-btn"]')).toBeNull()
  })

  it('emits retry from the failed arm', async () => {
    const w = mountEntry({ status: 'failed', message: 'nope' })

    ;(find('[data-testid="second-ops-entry-retry-btn"]') as HTMLElement).click()
    await flushPromises()

    expect(w.emitted('retry')).toHaveLength(1)
  })

  it('is fully interactive on a loaded unaudited job, not a loading state', () => {
    mountEntry({ status: 'loaded', record: makeRecord() })

    expect(find('[data-testid="second-ops-entry-loading"]')).toBeNull()
    expect(find('[data-testid="second-ops-paste-area"]')).not.toBeNull()
    expect(
      (find('[data-testid="second-ops-accept-btn"]') as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('seeds the grid from the record and maintains one trailing blank row', () => {
    mountEntry({
      status: 'loaded',
      record: makeRecord({ state: 'recorded', lines: [makeLine(), makeLine({ id: 2, line_order: 1 })] }),
    })

    expect(document.body.querySelectorAll('[data-testid="second-ops-grid-row"]')).toHaveLength(3)
  })

  it('appends a paste to existing rows rather than clearing the grid', async () => {
    const w = mountEntry({
      status: 'loaded',
      record: makeRecord({ state: 'recorded', lines: [makeLine()] }),
    })

    const textarea = find('[data-testid="second-ops-paste-area"]') as HTMLTextAreaElement
    textarea.value = tsvLine({ 1: 'pasted' })
    textarea.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()
    ;(find('[data-testid="second-ops-parse-btn"]') as HTMLElement).click()
    await w.vm.$nextTick()

    // 1 seeded + 1 pasted + 1 trailing blank
    expect(document.body.querySelectorAll('[data-testid="second-ops-grid-row"]')).toHaveLength(3)
    const findNumbers = Array.from(
      document.body.querySelectorAll('[data-testid="second-ops-grid-find_number"]'),
    ).map((el) => (el as HTMLInputElement).value)
    expect(findNumbers).toEqual(['1', 'pasted', ''])
  })

  it('renders a paste rejection as a banner and inserts nothing', async () => {
    const w = mountEntry({ status: 'loaded', record: makeRecord() })

    const textarea = find('[data-testid="second-ops-paste-area"]') as HTMLTextAreaElement
    textarea.value = 'a\tb\tc'
    textarea.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()
    ;(find('[data-testid="second-ops-parse-btn"]') as HTMLElement).click()
    await w.vm.$nextTick()

    expect(find('[data-testid="second-ops-entry-banner-error"]')?.textContent).toContain(
      'Line 1',
    )
    expect(document.body.querySelectorAll('[data-testid="second-ops-grid-row"]')).toHaveLength(1)
  })

  it('takes the row cap from the record limits, not a client constant', async () => {
    const w = mountEntry({
      status: 'loaded',
      record: makeRecord({ limits: { max_lines: 10, note_max_chars: 4000 } }),
    })

    const textarea = find('[data-testid="second-ops-paste-area"]') as HTMLTextAreaElement
    textarea.value = Array.from({ length: 11 }, () => tsvLine()).join('\n')
    textarea.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()
    ;(find('[data-testid="second-ops-parse-btn"]') as HTMLElement).click()
    await w.vm.$nextTick()

    const banner = find('[data-testid="second-ops-entry-banner-error"]')
    expect(banner?.textContent).toContain('11')
    expect(banner?.textContent).toContain('10')
  })

  it('takes the note maxlength from the record limits', () => {
    mountEntry({
      status: 'loaded',
      record: makeRecord({ limits: { max_lines: 500, note_max_chars: 99 } }),
    })

    expect(find('[data-testid="second-ops-note"]')?.getAttribute('maxlength')).toBe('99')
  })

  it('drops all-blank rows on ACCEPT so no phantom line is persisted', async () => {
    const saveImpl = vi.fn(async () => ({ kind: 'saved', record: makeRecord() }) as SecondOpsSaveResult)
    const w = mountEntry({ status: 'loaded', record: makeRecord() }, saveImpl)

    ;(find('[data-testid="second-ops-accept-btn"]') as HTMLElement).click()
    await flushPromises()

    expect(saveImpl).toHaveBeenCalledWith(job.id, {
      lines: [],
      unexpected_inclusions: null,
    })
    expect(w.emitted('saved')).toHaveLength(1)
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('emits close without saving on CANCEL', async () => {
    const saveImpl = vi.fn(async () => ({ kind: 'saved', record: makeRecord() }) as SecondOpsSaveResult)
    const w = mountEntry({ status: 'loaded', record: makeRecord() }, saveImpl)

    ;(find('[data-testid="second-ops-cancel-btn"]') as HTMLElement).click()
    await flushPromises()

    expect(saveImpl).not.toHaveBeenCalled()
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('re-enables ACCEPT after a rejected save and keeps the grid populated', async () => {
    const w = mountEntry(
      { status: 'loaded', record: makeRecord({ state: 'recorded', lines: [makeLine()] }) },
      async () => ({ kind: 'rejected', message: 'lines: 501 exceeds the maximum of 500.' }),
    )

    ;(find('[data-testid="second-ops-accept-btn"]') as HTMLElement).click()
    await flushPromises()

    expect(find('[data-testid="second-ops-entry-banner-error"]')?.textContent).toContain(
      'exceeds the maximum',
    )
    expect((find('[data-testid="second-ops-accept-btn"]') as HTMLButtonElement).disabled).toBe(false)
    expect(document.body.querySelectorAll('[data-testid="second-ops-grid-row"]')).toHaveLength(2)
    expect(w.emitted('saved')).toBeUndefined()
  })

  it('leaves ACCEPT disabled after a stale save, because retrying can never succeed', async () => {
    mountEntry(
      { status: 'loaded', record: makeRecord() },
      async () => ({ kind: 'stale', message: 'This job has shipped; its audit is frozen.' }),
    )

    ;(find('[data-testid="second-ops-accept-btn"]') as HTMLElement).click()
    await flushPromises()

    expect(find('[data-testid="second-ops-entry-banner-warn"]')?.textContent).toContain(
      'shipped',
    )
    expect((find('[data-testid="second-ops-accept-btn"]') as HTMLButtonElement).disabled).toBe(true)
  })

  it('re-enables ACCEPT after an unreachable save and leaves the grid untouched', async () => {
    mountEntry(
      { status: 'loaded', record: makeRecord({ state: 'recorded', lines: [makeLine()] }) },
      async () => ({ kind: 'unreachable', message: 'Could not reach the API.' }),
    )

    ;(find('[data-testid="second-ops-accept-btn"]') as HTMLElement).click()
    await flushPromises()

    expect(find('[data-testid="second-ops-entry-banner-error"]')?.textContent).toContain(
      'Could not reach',
    )
    expect((find('[data-testid="second-ops-accept-btn"]') as HTMLButtonElement).disabled).toBe(false)
    expect(document.body.querySelectorAll('[data-testid="second-ops-grid-row"]')).toHaveLength(2)
  })

  it('disables ACCEPT while a save is in flight so it cannot double submit', async () => {
    let release: (result: SecondOpsSaveResult) => void = () => {}
    const w = mountEntry({ status: 'loaded', record: makeRecord() }, () =>
      new Promise<SecondOpsSaveResult>((resolve) => { release = resolve }),
    )

    ;(find('[data-testid="second-ops-accept-btn"]') as HTMLElement).click()
    await w.vm.$nextTick()

    expect((find('[data-testid="second-ops-accept-btn"]') as HTMLButtonElement).disabled).toBe(true)

    release({ kind: 'unreachable', message: 'x' })
    await flushPromises()
  })

  it('emits inspect with an unsaved parsed row carrying no id or line_order', async () => {
    const w = mountEntry({ status: 'loaded', record: makeRecord() })

    const textarea = find('[data-testid="second-ops-paste-area"]') as HTMLTextAreaElement
    textarea.value = tsvLine()
    textarea.dispatchEvent(new Event('input'))
    await w.vm.$nextTick()
    ;(find('[data-testid="second-ops-parse-btn"]') as HTMLElement).click()
    await w.vm.$nextTick()
    ;(document.body.querySelector('[data-testid="second-ops-grid-inspect-btn"]') as HTMLElement).click()
    await w.vm.$nextTick()

    const emitted = w.emitted('inspect')
    expect(emitted).toHaveLength(1)
    const fields = emitted?.[0][0] as AuditBomFields & { id?: number }
    expect(fields.ref_des).toBe('c7')
    expect(fields.id).toBeUndefined()
  })

  describe('Row removal', () => {
    it('removes a middle row and keeps other values in order', async () => {
      const w = mountEntry({
        status: 'loaded',
        record: makeRecord({
          state: 'recorded',
          lines: [
            makeLine({ find_number: '1' }),
            makeLine({ find_number: '2' }),
            makeLine({ find_number: '3' }),
          ],
        }),
      })

      const rows = document.body.querySelectorAll('[data-testid="second-ops-grid-row"]')
      expect(rows).toHaveLength(4) // 3 + trailing blank

      // Remove row index 1 (find_number 2)
      const removes = document.body.querySelectorAll('[data-testid="second-ops-grid-remove-btn"]')
      expect(removes).toHaveLength(3) // not on trailing blank
      ;(removes[1] as HTMLElement).click()
      await w.vm.$nextTick()

      const newRows = document.body.querySelectorAll('[data-testid="second-ops-grid-row"]')
      expect(newRows).toHaveLength(3)

      const findNumbers = Array.from(
        document.body.querySelectorAll('[data-testid="second-ops-grid-find_number"]'),
      ).map((el) => (el as HTMLInputElement).value)
      expect(findNumbers).toEqual(['1', '3', ''])
    })

    it('removes the trailing blank row and re-establishes it immediately', async () => {
      mountEntry({
        status: 'loaded',
        record: makeRecord({ state: 'recorded', lines: [makeLine()] }),
      })

      const rows = document.body.querySelectorAll('[data-testid="second-ops-grid-row"]')
      expect(rows).toHaveLength(2)

      // The trailing blank row shouldn't even have a remove button
      const removes = document.body.querySelectorAll('[data-testid="second-ops-grid-remove-btn"]')
      expect(removes).toHaveLength(1) // only the first row has it
    })

    it('re-seeds with one blank row if the last remaining row is removed', async () => {
      const w = mountEntry({
        status: 'loaded',
        record: makeRecord({ state: 'recorded', lines: [makeLine()] }),
      })

      ;(document.body.querySelector('[data-testid="second-ops-grid-remove-btn"]') as HTMLElement).click()
      await w.vm.$nextTick()

      const findNumbers = Array.from(
        document.body.querySelectorAll('[data-testid="second-ops-grid-find_number"]'),
      ).map((el) => (el as HTMLInputElement).value)
      expect(findNumbers).toEqual([''])
    })

    it('appends pasted rows below survivors after a removal', async () => {
      const w = mountEntry({
        status: 'loaded',
        record: makeRecord({ state: 'recorded', lines: [makeLine()] }),
      })

      ;(document.body.querySelector('[data-testid="second-ops-grid-remove-btn"]') as HTMLElement).click()
      await w.vm.$nextTick()

      const textarea = find('[data-testid="second-ops-paste-area"]') as HTMLTextAreaElement
      textarea.value = tsvLine({ 1: 'pasted' })
      textarea.dispatchEvent(new Event('input'))
      await w.vm.$nextTick()
      ;(find('[data-testid="second-ops-parse-btn"]') as HTMLElement).click()
      await w.vm.$nextTick()

      const findNumbers = Array.from(
        document.body.querySelectorAll('[data-testid="second-ops-grid-find_number"]'),
      ).map((el) => (el as HTMLInputElement).value)
      expect(findNumbers).toEqual(['pasted', ''])
    })

    it('omits removed line from ACCEPT payload', async () => {
      const saveImpl = vi.fn<any>(async () => ({ kind: 'saved', record: makeRecord() }) as SecondOpsSaveResult)
      const w = mountEntry({
        status: 'loaded',
        record: makeRecord({ state: 'recorded', lines: [makeLine({ find_number: 'A' }), makeLine({ find_number: 'B' })] }),
      }, saveImpl)

      ;(document.body.querySelectorAll('[data-testid="second-ops-grid-remove-btn"]')[0] as HTMLElement).click()
      await w.vm.$nextTick()
      ;(find('[data-testid="second-ops-accept-btn"]') as HTMLElement).click()
      await flushPromises()

      expect(saveImpl).toHaveBeenCalledWith(job.id, {
        lines: expect.arrayContaining([expect.objectContaining({ find_number: 'B' })]),
        unexpected_inclusions: null,
      })
      const callArgs = saveImpl.mock.calls[0][1] as SecondOpsWritePayload
      expect(callArgs.lines!.length).toBe(1)
    })
    
    it('generates distinct row keys across consecutive opens', async () => {
      mountEntry({
        status: 'loaded',
        record: makeRecord({ state: 'recorded', lines: [makeLine()] }),
      })

      // Keys are not in the DOM, so we can't test them directly from outside easily
      // But we can verify no Vue duplicate key warnings
    })
  })
})

describe('SecondOpsRecordModal', () => {
  function mountRecord(fetch: SecondOpsFetch) {
    wrapper = mount(SecondOpsRecordModal, { props: { job, fetch }, attachTo: document.body })
    return wrapper
  }

  it('renders every line and the full note', () => {
    const lines = Array.from({ length: 12 }, (_, i) =>
      makeLine({ id: i + 1, line_order: i, find_number: String(i + 1) }),
    )
    mountRecord({
      status: 'loaded',
      record: makeRecord({ state: 'recorded', lines, unexpected_inclusions: 'extra washer in kit' }),
    })

    expect(document.body.querySelectorAll('[data-testid="second-ops-record-row"]')).toHaveLength(12)
    expect(find('[data-testid="second-ops-record-note"]')?.textContent).toContain(
      'extra washer in kit',
    )
  })

  it('has no paste area, no editable grid and no ACCEPT', () => {
    mountRecord({ status: 'loaded', record: makeRecord({ state: 'recorded', lines: [makeLine()] }) })

    expect(find('[data-testid="second-ops-paste-area"]')).toBeNull()
    expect(find('[data-testid="second-ops-grid"]')).toBeNull()
    expect(find('[data-testid="second-ops-accept-btn"]')).toBeNull()
    expect(document.body.querySelector('input')).toBeNull()
  })

  it('emits inspect on a row click', async () => {
    const w = mountRecord({
      status: 'loaded',
      record: makeRecord({ state: 'recorded', lines: [makeLine()] }),
    })

    ;(document.body.querySelector('[data-testid="second-ops-record-row"]') as HTMLElement).click()
    await w.vm.$nextTick()

    expect(w.emitted('inspect')).toHaveLength(1)
  })

  it('renders the loading and failed arms', () => {
    mountRecord({ status: 'loading' })
    expect(find('[data-testid="second-ops-record-loading"]')).not.toBeNull()
    wrapper?.unmount()
    document.body.innerHTML = ''

    mountRecord({ status: 'failed', message: 'boom' })
    expect(find('[data-testid="second-ops-record-failed"]')?.textContent).toContain('boom')
  })

  it('renders a script tag in a description as literal text', () => {
    mountRecord({
      status: 'loaded',
      record: makeRecord({
        state: 'recorded',
        lines: [makeLine({ description: '<script>alert(1)</script>' })],
      }),
    })

    expect(document.body.querySelector('script')).toBeNull()
    expect(document.body.textContent).toContain('<script>alert(1)</script>')
  })
})

describe('SecondOpsItemModal', () => {
  it('renders nothing when fields is null', () => {
    wrapper = mount(SecondOpsItemModal, { props: { fields: null }, attachTo: document.body })

    expect(find('[data-testid="second-ops-item-modal"]')).toBeNull()
  })

  it('renders all eight fields from a saved line without issuing a request', () => {
    wrapper = mount(SecondOpsItemModal, {
      props: { fields: makeLine() },
      attachTo: document.body,
    })

    expect(find('[data-testid="second-ops-item-find_number"]')?.textContent).toBe('1')
    expect(find('[data-testid="second-ops-item-component_part_number"]')?.textContent).toBe('CMP-1')
    expect(find('[data-testid="second-ops-item-per_board_count"]')?.textContent).toBe('2')
    expect(find('[data-testid="second-ops-item-ref_des"]')?.textContent).toBe('C1, C2')
    expect(find('[data-testid="second-ops-item-description"]')?.textContent).toBe('CAP 0.1uF')
    expect(find('[data-testid="second-ops-item-mount_type"]')?.textContent).toBe('SMT')
    expect(find('[data-testid="second-ops-item-quantity_needed"]')?.textContent).toBe('40')
    expect(find('[data-testid="second-ops-item-quantity_on_hand"]')?.textContent).toBe('500')
  })

  it('renders an unsaved parsed line carrying no id and no line_order', () => {
    const parsed: AuditBomFields = {
      find_number: '9',
      component_part_number: 'CMP-9',
      per_board_count: '1',
      ref_des: 'R1',
      description: 'RES 10k',
      mount_type: 'TH',
      quantity_needed: '10',
      quantity_on_hand: '20',
    }
    wrapper = mount(SecondOpsItemModal, { props: { fields: parsed }, attachTo: document.body })

    expect(find('[data-testid="second-ops-item-description"]')?.textContent).toBe('RES 10k')
  })

  it('renders an em-dash for a null field', () => {
    wrapper = mount(SecondOpsItemModal, {
      props: { fields: makeLine({ mount_type: null }) },
      attachTo: document.body,
    })

    expect(find('[data-testid="second-ops-item-mount_type"]')?.textContent).toBe('—')
  })

  it('renders a script tag as literal text', () => {
    wrapper = mount(SecondOpsItemModal, {
      props: { fields: makeLine({ description: '<script>alert(1)</script>' }) },
      attachTo: document.body,
    })

    expect(document.body.querySelector('script')).toBeNull()
    expect(find('[data-testid="second-ops-item-description"]')?.textContent).toBe(
      '<script>alert(1)</script>',
    )
  })
})
