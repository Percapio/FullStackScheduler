import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  fetchJobHistory,
  type JobReadExpanded,
} from '@/api/history'
// Read-only by construction: fetchSecondOps ONLY. putSecondOps is deliberately
// absent from this module and from HistoryView's import graph — a write action
// sitting unused here is how the write guard gets circumvented six months from
// now. History is the audit trail; a shipped record does not change after the
// fact (Decision 10).
import { fetchSecondOps, type AuditBomFields, type SecondOpsFetch } from '@/api/secondOps'

export type { HistoryEditError, HistoryEditDraft } from '@/api/history'

const PAGE_SIZE = 50

const SECOND_OPS_FETCH_FAILED =
  'Could not load the 2nd OPS record. Check that the backend is running and retry.'

export const useHistoryStore = defineStore('history', () => {
  const rows        = ref<JobReadExpanded[]>([])
  const total       = ref(0)
  const offset      = ref(0)
  const limit       = ref(PAGE_SIZE)
  const loading     = ref(false)
  const error       = ref<string | null>(null)
  const searchQuery = ref('')

  const inspected = ref<JobReadExpanded | null>(null)

  // ---- 2nd OPS (Phase 22) — read-only ---------------------------------------
  const secondOpsRecordJob   = ref<JobReadExpanded | null>(null)
  const secondOpsRecordFetch = ref<SecondOpsFetch>({ status: 'loading' })
  const secondOpsItem        = ref<AuditBomFields | null>(null)

  // Monotonic request sequence; see the shipping store for why clearing on close
  // alone does not close the concurrent case.
  let secondOpsRequestSeq = 0

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

  function inspect(row: JobReadExpanded) { inspected.value = row }
  function closeInspect() { inspected.value = null }

  function applyEdited(job: JobReadExpanded): void {
    const idx = rows.value.findIndex(r => r.id === job.id)
    if (idx >= 0) {
      const updated = [...rows.value]
      updated[idx] = job
      rows.value = updated
    }
    if (inspected.value !== null && inspected.value.id === job.id) {
      inspected.value = job
    }
  }

  function applyDiscarded(job_id: number): void {
    const idx = rows.value.findIndex(r => r.id === job_id)
    if (idx >= 0) {
      rows.value = rows.value.filter(r => r.id !== job_id)
      total.value = Math.max(0, total.value - 1)
    }
  }

  // ---------------------------------------------------------------------------
  // 2nd OPS (Phase 22) — read-only record and item views
  // ---------------------------------------------------------------------------

  async function _loadRecord(jobId: number): Promise<void> {
    const seq = ++secondOpsRequestSeq
    secondOpsRecordFetch.value = { status: 'loading' }
    try {
      const record = await fetchSecondOps(jobId)
      if (seq !== secondOpsRequestSeq) return
      secondOpsRecordFetch.value = { status: 'loaded', record }
    } catch {
      if (seq !== secondOpsRequestSeq) return
      secondOpsRecordFetch.value = { status: 'failed', message: SECOND_OPS_FETCH_FAILED }
    }
  }

  /**
   * Open the read-only whole-record view for `job`.
   *
   * Post: secondOpsRecordFetch reflects the MOST RECENTLY REQUESTED job and no
   *       other; a stale resolution is discarded without touching state.
   */
  async function openSecondOpsRecord(job: JobReadExpanded): Promise<void> {
    secondOpsRecordJob.value = job
    await _loadRecord(job.id)
  }

  function closeSecondOpsRecord(): void {
    secondOpsRequestSeq += 1
    secondOpsRecordJob.value = null
    secondOpsRecordFetch.value = { status: 'loading' }
  }

  async function retrySecondOpsRecord(): Promise<void> {
    const job = secondOpsRecordJob.value
    if (job === null) return
    await _loadRecord(job.id)
  }

  function openSecondOpsItem(fields: AuditBomFields): void {
    secondOpsItem.value = fields
  }

  function closeSecondOpsItem(): void {
    secondOpsItem.value = null
  }

  return {
    rows, total, offset, limit, loading, error, searchQuery,
    secondOpsRecordJob, secondOpsRecordFetch, secondOpsItem,
    openSecondOpsRecord, closeSecondOpsRecord, retrySecondOpsRecord,
    openSecondOpsItem, closeSecondOpsItem,
    inspected,
    hasPrev, hasNext, pageStart, pageEnd,
    load, next, prev, setSearch, inspect, closeInspect,
    applyEdited, applyDiscarded,
  }
})
