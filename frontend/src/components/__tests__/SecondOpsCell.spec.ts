import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SecondOpsCell from '../SecondOpsCell.vue'
import type { SecondOpsLine, SecondOpsSummary } from '@/api/secondOps'

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

function makeSummary(overrides: Partial<SecondOpsSummary> = {}): SecondOpsSummary {
  return {
    state: 'recorded',
    line_count: 1,
    reviewed_at: '2026-08-28T10:00:00',
    has_unexpected_inclusions: false,
    preview: [makeLine()],
    ...overrides,
  }
}

function mountCell(props: { summary: SecondOpsSummary | null; readonly?: boolean }) {
  return mount(SecondOpsCell, { props })
}

describe('SecondOpsCell', () => {
  it('renders nothing at all when the summary is null', () => {
    // null means "this endpoint does not carry it", NOT "unaudited".
    const wrapper = mountCell({ summary: null })

    expect(wrapper.find('[data-testid="second-ops-cell"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="second-ops-audit-btn"]').exists()).toBe(false)
    expect(wrapper.text()).toBe('')
  })

  it('offers Audit on an unaudited Shipping cell', () => {
    const wrapper = mountCell({
      summary: makeSummary({ state: 'unaudited', line_count: 0, preview: [], reviewed_at: null }),
    })

    expect(wrapper.find('[data-testid="second-ops-audit-btn"]').exists()).toBe(true)
  })

  it('renders an em-dash on an unaudited History cell', () => {
    const wrapper = mountCell({
      summary: makeSummary({ state: 'unaudited', line_count: 0, preview: [], reviewed_at: null }),
      readonly: true,
    })

    expect(wrapper.find('[data-testid="second-ops-audit-btn"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('—')
  })

  it('renders N/A plus EDIT on a Shipping not_applicable cell', () => {
    const wrapper = mountCell({
      summary: makeSummary({ state: 'not_applicable', line_count: 0, preview: [] }),
    })

    expect(wrapper.find('[data-testid="second-ops-na"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="second-ops-edit-btn"]').exists()).toBe(true)
  })

  it('renders N/A with no EDIT on a History not_applicable cell', () => {
    const wrapper = mountCell({
      summary: makeSummary({ state: 'not_applicable', line_count: 0, preview: [] }),
      readonly: true,
    })

    expect(wrapper.find('[data-testid="second-ops-na"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="second-ops-edit-btn"]').exists()).toBe(false)
  })

  it('renders find number, description and quantity for each preview line', () => {
    const wrapper = mountCell({ summary: makeSummary() })

    const line = wrapper.find('[data-testid="second-ops-preview-line"]')
    expect(line.text()).toContain('1')
    expect(line.text()).toContain('CAP 0.1uF')
    expect(line.text()).toContain('40')
  })

  it('emits inspect with the whole line, not a three-field projection', () => {
    const wrapper = mountCell({ summary: makeSummary() })

    wrapper.find('[data-testid="second-ops-preview-line"]').trigger('click')

    const emitted = wrapper.emitted('inspect')
    expect(emitted).toHaveLength(1)
    expect(emitted?.[0][0]).toMatchObject({ ref_des: 'C1, C2', mount_type: 'SMT' })
  })

  it('offers View all when line_count exceeds the preview length', () => {
    const wrapper = mountCell({
      summary: makeSummary({ line_count: 56, preview: [makeLine()] }),
      readonly: true,
    })

    const viewAll = wrapper.find('[data-testid="second-ops-view-all-btn"]')
    expect(viewAll.exists()).toBe(true)
    expect(viewAll.text()).toContain('56')
  })

  it('offers View all for a note with no extra lines', () => {
    // has_unexpected_inclusions is a boolean on the summary, so the note is
    // otherwise unreachable from the cell.
    const wrapper = mountCell({
      summary: makeSummary({ line_count: 1, preview: [makeLine()], has_unexpected_inclusions: true }),
      readonly: true,
    })

    expect(wrapper.find('[data-testid="second-ops-view-all-btn"]').exists()).toBe(true)
  })

  it('omits View all when the preview already holds everything', () => {
    const wrapper = mountCell({
      summary: makeSummary({ line_count: 1, preview: [makeLine()] }),
    })

    expect(wrapper.find('[data-testid="second-ops-view-all-btn"]').exists()).toBe(false)
  })

  it('renders no Audit and no EDIT at any state when readonly', () => {
    for (const state of ['unaudited', 'not_applicable', 'recorded'] as const) {
      const wrapper = mountCell({ summary: makeSummary({ state }), readonly: true })
      expect(wrapper.find('[data-testid="second-ops-audit-btn"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="second-ops-edit-btn"]').exists()).toBe(false)
    }
  })

  it('renders a script tag in a description as literal text', () => {
    const wrapper = mountCell({
      summary: makeSummary({
        preview: [makeLine({ description: '<script>alert(1)</script>' })],
      }),
    })

    expect(wrapper.element.querySelector('script')).toBeNull()
    expect(wrapper.text()).toContain('<script>alert(1)</script>')
  })

  it('stops click propagation so a History row does not also toggle lineage', async () => {
    let rowClicks = 0
    const wrapper = mount(
      {
        components: { SecondOpsCell },
        setup: () => ({ summary: makeSummary() }),
        template: `<div @click="$emit('row')"><SecondOpsCell :summary="summary" readonly /></div>`,
      },
      { attrs: { onRow: () => { rowClicks += 1 } } },
    )

    await wrapper.find('[data-testid="second-ops-preview-line"]').trigger('click')

    expect(rowClicks).toBe(0)
  })
})
