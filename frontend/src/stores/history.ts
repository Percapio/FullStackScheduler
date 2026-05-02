import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'
import { fetchJobHistory, fetchJobLineage, type JobReadExpanded } from '@/api/history'
import { useToast } from '@/composables/useToast'

const PAGE_SIZE = 50

export type LineageState =
  | { status: 'loading' }
  | { status: 'ready'; rows: JobReadExpanded[] }
  | { status: 'error'; message: string }

export const useHistoryStore = defineStore('history', () => {
  const rows        = ref<JobReadExpanded[]>([])
  const total       = ref(0)
  const offset      = ref(0)
  const limit       = ref(PAGE_SIZE)
  const loading     = ref(false)
  const error       = ref<string | null>(null)
  const searchQuery = ref('')

  const lineage   = shallowRef<Map<number, LineageState>>(new Map())
  const expanded  = shallowRef<Set<number>>(new Set())
  const inspected = ref<JobReadExpanded | null>(null)

  const hasPrev   = computed(() => offset.value > 0)
  const hasNext   = computed(() => offset.value + rows.value.length < total.value)
  const pageStart = computed(() => total.value === 0 ? 0 : offset.value + 1)
  const pageEnd   = computed(() => offset.value + rows.value.length)

  async function load() {
    loading.value = true
    error.value = null
    try {
      const res = await fetchJobHistory(
        limit.value,
        offset.value,
        searchQuery.value.trim() || null,
      )
      rows.value  = res.rows
      total.value = res.total
      expanded.value  = new Set()
      lineage.value   = new Map()
      inspected.value = null
    } catch {
      error.value = 'Could not load shipped jobs. Check that the backend is running and retry.'
    } finally {
      loading.value = false
    }
  }

  async function next() {
    if (hasNext.value) {
      offset.value += limit.value
      await load()
    }
  }

  async function prev() {
    if (hasPrev.value) {
      offset.value = Math.max(0, offset.value - limit.value)
      await load()
    }
  }

  async function setSearch(q: string) {
    searchQuery.value = q
    offset.value = 0
    await load()
  }

  async function toggleExpand(jobId: number) {
    if (expanded.value.has(jobId)) {
      expanded.value.delete(jobId)
      expanded.value = new Set(expanded.value)
      return
    }
    expanded.value.add(jobId)
    expanded.value = new Set(expanded.value)

    if (lineage.value.get(jobId)?.status === 'ready') return

    lineage.value.set(jobId, { status: 'loading' })
    lineage.value = new Map(lineage.value)
    try {
      const lineageRows = await fetchJobLineage(jobId)
      lineage.value.set(jobId, { status: 'ready', rows: lineageRows })
    } catch {
      lineage.value.set(jobId, {
        status: 'error',
        message: 'Could not load lineage for this job.',
      })
      useToast().show('Lineage request failed. Try reopening the row.', 'error', 6000)
    } finally {
      lineage.value = new Map(lineage.value)
    }
  }

  function inspect(row: JobReadExpanded) { inspected.value = row }
  function closeInspect() { inspected.value = null }

  return {
    rows, total, offset, limit, loading, error, searchQuery,
    lineage, expanded, inspected,
    hasPrev, hasNext, pageStart, pageEnd,
    load, next, prev, setSearch, toggleExpand, inspect, closeInspect,
  }
})
