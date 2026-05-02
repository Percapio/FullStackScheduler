import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SortHeader from '../SortHeader.vue'
import type { FlatSortKey, SortState } from '@/composables/useShippingSort'

function mountHeader(label: string, sortKey: FlatSortKey, current: SortState) {
  const table = {
    components: { SortHeader },
    props: ['label', 'sortKey', 'current'],
    emits: ['sort'],
    template: `<table><thead><tr>
      <SortHeader :label="label" :sort-key="sortKey" :current="current"
                  @sort="(k) => $emit('sort', k)" />
    </tr></thead></table>`,
  }
  return mount(table, { props: { label, sortKey, current } })
}

describe('SortHeader', () => {
  it('renders the label and an inactive ↕ arrow when not sorted on this column', () => {
    const w = mountHeader('Ship Date', 'resolved_ship_date',
      { key: 'part_number', direction: 'asc' })
    expect(w.text()).toContain('Ship Date')
    expect(w.text()).toContain('↕')
  })

  it('renders the active ↑/↓ arrow with darker weight when current.key === sortKey', () => {
    const asc = mountHeader('Ship Date', 'resolved_ship_date',
      { key: 'resolved_ship_date', direction: 'asc' })
    expect(asc.text()).toContain('↑')
    const arrowSpan = asc.find('span.inline-block')
    expect(arrowSpan.classes()).toContain('text-slate-700')
    expect(arrowSpan.classes()).not.toContain('text-slate-400')

    const desc = mountHeader('Ship Date', 'resolved_ship_date',
      { key: 'resolved_ship_date', direction: 'desc' })
    expect(desc.text()).toContain('↓')
  })

  it('arrow renders LEFT of the label (regression for wrap bug)', () => {
    const w = mountHeader('Customer', 'customer_name',
      { key: 'customer_name', direction: 'asc' })
    const innerText = w.find('span.inline-flex').text()
    const arrowIdx = innerText.indexOf('↑')
    const labelIdx = innerText.indexOf('Customer')
    expect(arrowIdx).toBeGreaterThanOrEqual(0)
    expect(arrowIdx).toBeLessThan(labelIdx)
  })

  it('clicking the header emits sort with the column key', async () => {
    const w = mountHeader('Qty', 'quantity',
      { key: 'part_number', direction: 'asc' })
    await w.find('th').trigger('click')
    expect(w.emitted('sort')?.[0]).toEqual(['quantity'])
  })
})
