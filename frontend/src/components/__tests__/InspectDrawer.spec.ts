import { describe, it, expect, afterEach } from 'vitest'
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

function mountDrawer(row: JobReadExpanded | null) {
  wrapper = mount(InspectDrawer, {
    props: { row },
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
  it('renders nothing when row is null', () => {
    mountDrawer(null)
    expect(bodyHtml()).not.toContain('drawer-overlay')
  })

  it('flattens nested objects with dotted keys (Q4)', () => {
    mountDrawer(makeJob())
    const html = bodyHtml()
    expect(html).toContain('assembly.part_number')
    expect(html).toContain('customer.name')
    expect(html).toContain('Acme Corp')
  })

  it('joins all-scalar arrays into comma-separated strings', () => {
    const job = makeJob()
    ;(job as Record<string, unknown>).tags = ['fast', 'urgent']
    mountDrawer(job)
    expect(bodyHtml()).toContain('fast, urgent')
  })

  it('recurses into object arrays with bracket indices', () => {
    mountDrawer(makeJob())
    const html = bodyHtml()
    expect(html).toContain('assembly.classifications[0].code')
    expect(html).toContain('AS9100')
  })

  it('renders "—" for null values', () => {
    mountDrawer(makeJob({ salesperson: null }))
    const html = bodyHtml()
    expect(html).toContain('salesperson')
    expect(html).toContain('—')
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
})
