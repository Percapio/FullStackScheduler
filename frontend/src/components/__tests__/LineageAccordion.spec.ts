import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import LineageAccordion from '../LineageAccordion.vue'
import type { JobReadExpanded } from '@/api/history'
import type { LineageState } from '@/stores/history'

const ts = '2026-04-19T00:00:00'

function makeJob(overrides: Partial<JobReadExpanded> = {}): JobReadExpanded {
  return {
    id: 1,
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
    assembly: { id: 1, part_number: 'TEST-001', created_at: ts, updated_at: ts },
    customer: { id: 1, name: 'TestCo', created_at: ts, updated_at: ts },
    ...overrides,
  } as JobReadExpanded
}

function mountAccordion(state: LineageState | undefined, jobId = 1) {
  return mount(LineageAccordion, {
    props: { jobId, state },
  })
}

describe('LineageAccordion', () => {
  it('renders skeleton table while state.status === "loading"', () => {
    const w = mountAccordion({ status: 'loading' })
    expect(w.find('table[aria-busy="true"]').exists()).toBe(true)
    expect(w.findAll('tbody tr')).toHaveLength(3)
    expect(w.find('svg.animate-spin').exists()).toBe(false)
  })

  it('renders sub-table rows when state.status === "ready"', () => {
    const state: LineageState = {
      status: 'ready',
      rows: [makeJob({ id: 1 }), makeJob({ id: 2 }), makeJob({ id: 3 })],
    }
    const w = mountAccordion(state)
    expect(w.findAll('tbody tr')).toHaveLength(3)
  })

  it('renders error banner with Retry emit when state.status === "error"', async () => {
    const state: LineageState = { status: 'error', message: 'Network failure' }
    const w = mountAccordion(state)
    expect(w.text()).toContain('Network failure')

    await w.find('button').trigger('click')
    expect(w.emitted('retry')).toHaveLength(1)
  })

  it('highlights the anchor row via lineage-anchor class', () => {
    const state: LineageState = {
      status: 'ready',
      rows: [makeJob({ id: 5 }), makeJob({ id: 6 })],
    }
    const w = mountAccordion(state, 5)
    const rows = w.findAll('tbody tr')
    expect(rows[0].classes()).toContain('lineage-anchor')
    expect(rows[1].classes()).not.toContain('lineage-anchor')
  })

  it('renders the status badge colored by JobStatus value', () => {
    const state: LineageState = {
      status: 'ready',
      rows: [
        makeJob({ id: 1, status: 'shipped' }),
        makeJob({ id: 2, status: 'planned' }),
      ],
    }
    const w = mountAccordion(state)
    const statusBadges = w.findAll('tbody tr').map(row =>
      row.findAll('td').at(-2)!.find('span.inline-flex'),
    )
    expect(statusBadges[0].classes()).toContain('bg-emerald-100')
    expect(statusBadges[1].classes()).toContain('bg-slate-200')
  })

  it('renders a "Job" column header (not "Identity")', () => {
    const state: LineageState = { status: 'ready', rows: [makeJob()] }
    const w = mountAccordion(state)
    const firstTh = w.find('thead th')
    expect(firstTh.text()).toBe('Job')
    expect(w.text()).not.toContain('Identity')
  })

  it('clicking the Eye button in a lineage row emits inspect with that job', async () => {
    const jobs = [
      makeJob({ id: 11 }),
      makeJob({ id: 22 }),
    ]
    const state: LineageState = { status: 'ready', rows: jobs }
    const w = mountAccordion(state, 11)

    await w.find('button[aria-label="Inspect job 22"]').trigger('click')
    const events = w.emitted('inspect')
    expect(events).toHaveLength(1)
    expect((events![0][0] as JobReadExpanded).id).toBe(22)
  })
})
