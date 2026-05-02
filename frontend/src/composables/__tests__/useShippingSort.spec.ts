import { describe, it, expect } from 'vitest'
import { ref } from 'vue'
import { useShippingSort, type SortState } from '../useShippingSort'
import type { JobReadExpanded } from '@/api/shipping'

const ts = '2026-04-19T00:00:00'

function makeJob(
  overrides: Partial<JobReadExpanded> & { _pn?: string; _cid?: number; _cname?: string } = {},
): JobReadExpanded {
  const { _pn = '100000', _cid = 1, _cname = 'Acme', ...rest } = overrides
  return {
    id: 1,
    assembly_id: 1,
    customer_id: _cid,
    status: 'planned',
    quantity: 10,
    line_1: false,
    line_2: false,
    line_3: false,
    created_at: ts,
    updated_at: ts,
    assembly: { id: 1, part_number: _pn, created_at: ts, updated_at: ts },
    customer: { id: _cid, name: _cname, created_at: ts, updated_at: ts },
    ...rest,
  } as JobReadExpanded
}

describe('useShippingSort', () => {
  it('sorts by resolved_ship_date asc with nulls last', () => {
    const jobs = ref([
      makeJob({ id: 1, resolved_ship_date: null }),
      makeJob({ id: 2, resolved_ship_date: '2026-06-01' }),
      makeJob({ id: 3, resolved_ship_date: '2026-04-01' }),
    ])
    const sort = ref<SortState>({ key: 'resolved_ship_date', direction: 'asc' })
    const { sorted } = useShippingSort(jobs, sort)
    expect(sorted.value.map(j => j.id)).toEqual([3, 2, 1])
  })

  it('reverses order when direction is desc, nulls still last', () => {
    const jobs = ref([
      makeJob({ id: 1, resolved_ship_date: null }),
      makeJob({ id: 2, resolved_ship_date: '2026-06-01' }),
      makeJob({ id: 3, resolved_ship_date: '2026-04-01' }),
    ])
    const sort = ref<SortState>({ key: 'resolved_ship_date', direction: 'desc' })
    const { sorted } = useShippingSort(jobs, sort)
    expect(sorted.value.map(j => j.id)).toEqual([2, 3, 1])
  })

  it('sorts by part_number alphabetically', () => {
    const jobs = ref([
      makeJob({ id: 1, _pn: 'Zebra' }),
      makeJob({ id: 2, _pn: 'Alpha' }),
    ])
    const sort = ref<SortState>({ key: 'part_number', direction: 'asc' })
    const { sorted } = useShippingSort(jobs, sort)
    expect(sorted.value.map(j => j.id)).toEqual([2, 1])
  })

  it('sorts by quantity numerically', () => {
    const jobs = ref([
      makeJob({ id: 1, quantity: 100 }),
      makeJob({ id: 2, quantity: 5 }),
      makeJob({ id: 3, quantity: 50 }),
    ])
    const sort = ref<SortState>({ key: 'quantity', direction: 'asc' })
    const { sorted } = useShippingSort(jobs, sort)
    expect(sorted.value.map(j => j.id)).toEqual([2, 3, 1])
  })

  it('sorts by build_type with nulls last', () => {
    const jobs = ref([
      makeJob({ id: 1, build_type: null }),
      makeJob({ id: 2, build_type: 'rowc' }),
      makeJob({ id: 3, build_type: 'ronc' }),
    ])
    const sort = ref<SortState>({ key: 'build_type', direction: 'asc' })
    const { sorted } = useShippingSort(jobs, sort)
    expect(sorted.value.map(j => j.id)).toEqual([3, 2, 1])
  })

  it('sorts by customer_name alphabetically', () => {
    const jobs = ref([
      makeJob({ id: 1, _cname: 'Zeta Corp' }),
      makeJob({ id: 2, _cname: 'Alpha Inc' }),
    ])
    const sort = ref<SortState>({ key: 'customer_name', direction: 'asc' })
    const { sorted } = useShippingSort(jobs, sort)
    expect(sorted.value.map(j => j.id)).toEqual([2, 1])
  })

  it('uses stable tiebreak by job.id', () => {
    const jobs = ref([
      makeJob({ id: 3, _pn: 'Same', quantity: 10 }),
      makeJob({ id: 1, _pn: 'Same', quantity: 10 }),
      makeJob({ id: 2, _pn: 'Same', quantity: 10 }),
    ])
    const sort = ref<SortState>({ key: 'quantity', direction: 'asc' })
    const { sorted } = useShippingSort(jobs, sort)
    expect(sorted.value.map(j => j.id)).toEqual([1, 2, 3])
  })
})
