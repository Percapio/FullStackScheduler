import { computed, type Ref } from 'vue'
import type { JobReadExpanded } from '@/api/shipping'

export type FlatSortKey =
  | 'resolved_ship_date'
  | 'part_number'
  | 'quantity'
  | 'build_type'
  | 'base_mfg_notes'
  | 'customer_name'

export type SortDirection = 'asc' | 'desc'
export interface SortState { key: FlatSortKey; direction: SortDirection }

function sortValue(job: JobReadExpanded, key: FlatSortKey): string | number | null {
  switch (key) {
    case 'resolved_ship_date': return job.resolved_ship_date ?? null
    case 'part_number':        return job.assembly.part_number
    case 'quantity':           return job.quantity
    case 'build_type':         return job.build_type ?? null
    case 'base_mfg_notes':     return job.assembly.base_mfg_notes ?? null
    case 'customer_name':      return job.customer.name
  }
}

function makeComparator(sort: SortState) {
  const sign = sort.direction === 'asc' ? 1 : -1
  // Null-valued sort keys always sort last, regardless of direction.
  return (a: JobReadExpanded, b: JobReadExpanded) => {
    const av = sortValue(a, sort.key)
    const bv = sortValue(b, sort.key)
    if (av === bv) return a.id - b.id
    if (av === null) return 1
    if (bv === null) return -1
    return av < bv ? -sign : sign
  }
}

export function useShippingSort(
  jobs: Ref<JobReadExpanded[]>,
  sort: Ref<SortState>,
) {
  const sorted = computed(() => [...jobs.value].sort(makeComparator(sort.value)))
  return { sorted }
}
