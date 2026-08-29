import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  discardShippingJob,
  fetchDiscardedJobs,
  fetchJobRestorePreview,
  fetchShippingJobs,
  postRestoreJob,
  type JobReadExpanded,
} from '@/api/shipping'
import type { RestoreConflictPreview, StagingRestoreAction } from '@/api/staging'
import {
  fetchSecondOps,
  putSecondOps,
  type AuditBomFields,
  type SecondOpsFetch,
  type SecondOpsSaveResult,
  type SecondOpsWritePayload,
} from '@/api/secondOps'
import { isApiError } from '@/api/client'
import { useToast } from '@/composables/useToast'

export type ShippingDiscardOutcome =
  | { kind: 'ok' }
  | { kind: 'stale' }
  | { kind: 'conflict'; message: string }
  | { kind: 'network'; message: string }

type RestoreOutcome =
  | { kind: 'ok'; job: JobReadExpanded }
  | { kind: 'preview'; preview: RestoreConflictPreview }
  | { kind: 'stale' }
  | { kind: 'conflict'; message: string; preview: RestoreConflictPreview }
  | { kind: 'invalid-edit'; message: string; action_index: number }
  | { kind: 'network'; message: string }

const DISCARDED_PAGE_SIZE = 50

const SECOND_OPS_FETCH_FAILED =
  'Could not load the 2nd OPS record. Check that the backend is running and retry.'

export const useShippingStore = defineStore('shipping', () => {
  const jobs      = ref<JobReadExpanded[]>([])
  const loading   = ref(false)
  const error     = ref<string | null>(null)
  const inspected = ref<JobReadExpanded | null>(null)

  // Discarded jobs state (Epoch 4)
  const discardedJobs        = ref<JobReadExpanded[]>([])
  const discardedTotal       = ref(0)
  const discardedLoading     = ref(false)
  const discardedDrawerOpen  = ref(false)
  const discardedOffset      = ref(0)
  const discardedLimit       = ref(DISCARDED_PAGE_SIZE)
  const discardedSearchQuery = ref('')

  // ---- 2nd OPS (Phase 22) --------------------------------------------------
  const secondOpsJob         = ref<JobReadExpanded | null>(null)
  const secondOpsOpen        = ref(false)
  const secondOpsFetch       = ref<SecondOpsFetch>({ status: 'loading' })
  const secondOpsRecordJob   = ref<JobReadExpanded | null>(null)
  const secondOpsRecordFetch = ref<SecondOpsFetch>({ status: 'loading' })
  const secondOpsItem        = ref<AuditBomFields | null>(null)

  // Monotonic request sequence. Not reactive: it is a guard, not rendered state.
  // Clearing on close alone is NOT sufficient — close job A, immediately open
  // job B, and A's still-in-flight GET resolves after B's Loading was assigned,
  // overwriting it with A's record. The operator would then edit what looks like
  // B's audit and PUT it to B.
  let secondOpsRequestSeq = 0

  const discardedHasPrev  = computed(() => discardedOffset.value > 0)
  const discardedHasNext  = computed(
    () => discardedOffset.value + discardedJobs.value.length < discardedTotal.value,
  )
  const discardedPageStart = computed(() =>
    discardedTotal.value === 0 ? 0 : discardedOffset.value + 1,
  )
  const discardedPageEnd = computed(
    () => discardedOffset.value + discardedJobs.value.length,
  )

  async function load() {
    loading.value = true
    error.value = null
    try {
      const { rows, total } = await fetchShippingJobs(500)
      jobs.value = rows
      if (total > rows.length) {
        useToast().show(
          `Showing ${rows.length} of ${total} open jobs. Contact admin if the full list is needed.`,
          'error',
          8000,
        )
      }
    } catch {
      error.value = 'Could not load open jobs. Check that the backend is running and retry.'
    } finally {
      loading.value = false
    }
  }

  /**
   * Optimistic soft-delete: splices jobId from jobs[] immediately, rolls back
   * on rejection. Mirrors useStagingStore.discardRow.
   *
   * Pre:  jobId is rendered in jobs[]; reason is non-empty.
   * Post: kind='ok'       — job spliced from jobs[]; success toast shown.
   *       kind='stale'    — jobId was not in jobs[] on entry; no-op.
   *       kind='conflict' — already discarded; toast shown; list reloaded.
   *       kind='network'  — non-409 transport error; error toast shown.
   */
  async function discardJob(jobId: number, reason: string): Promise<ShippingDiscardOutcome> {
    const idx = jobs.value.findIndex(j => j.id === jobId)
    if (idx === -1) return { kind: 'stale' }

    const snapshot = jobs.value[idx]
    jobs.value = jobs.value.filter(j => j.id !== jobId)

    try {
      await discardShippingJob(jobId, reason)
      useToast().show('Job discarded.', 'success', 4000)
      // Update discarded count for the pill without reloading the full list.
      discardedTotal.value += 1
      return { kind: 'ok' }
    } catch (err: unknown) {
      // Roll back optimistic splice.
      jobs.value = [...jobs.value.slice(0, idx), snapshot, ...jobs.value.slice(idx)]

      const status = (err as { response?: { status?: number; data?: { detail?: string } } })
        ?.response?.status
      if (status === 409) {
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Cannot discard this job.'
        useToast().show(typeof detail === 'string' ? detail : 'Cannot discard this job.', 'error', 6000)
        await load()
        return { kind: 'conflict', message: typeof detail === 'string' ? detail : 'Cannot discard this job.' }
      }
      const msg = 'Could not discard job. Check that the backend is running and retry.'
      useToast().show(msg, 'error', 6000)
      return { kind: 'network', message: msg }
    }
  }

  function inspect(row: JobReadExpanded): void {
    inspected.value = row
  }

  function closeInspect(): void {
    inspected.value = null
  }

  // ---------------------------------------------------------------------------
  // Discarded jobs drawer actions (Epoch 4)
  // ---------------------------------------------------------------------------

  async function loadDiscardedJobs(): Promise<void> {
    discardedLoading.value = true
    try {
      const { rows, total } = await fetchDiscardedJobs(
        discardedLimit.value,
        discardedOffset.value,
        discardedSearchQuery.value.trim() || null,
      )
      discardedJobs.value = rows
      discardedTotal.value = total
    } finally {
      discardedLoading.value = false
    }
  }

  async function nextDiscardedJobsPage(): Promise<void> {
    if (discardedHasNext.value) {
      discardedOffset.value += discardedLimit.value
      await loadDiscardedJobs()
    }
  }

  async function prevDiscardedJobsPage(): Promise<void> {
    if (discardedHasPrev.value) {
      discardedOffset.value = Math.max(0, discardedOffset.value - discardedLimit.value)
      await loadDiscardedJobs()
    }
  }

  async function setDiscardedJobsSearch(q: string): Promise<void> {
    discardedSearchQuery.value = q
    discardedOffset.value = 0
    await loadDiscardedJobs()
  }

  async function openDiscardedJobsDrawer(): Promise<void> {
    discardedOffset.value = 0
    discardedSearchQuery.value = ''
    await loadDiscardedJobs()
    discardedDrawerOpen.value = true
  }

  function closeDiscardedJobsDrawer(): void {
    discardedDrawerOpen.value = false
  }

  // ---------------------------------------------------------------------------
  // Job restore two-phase flow (Epoch 4)
  // ---------------------------------------------------------------------------

  /**
   * Phase 1: fetch the restore preview. If no blockers exist, restore immediately
   * with empty actions (fast path). Otherwise return kind='preview' so the caller
   * can open the RestoreConflictPreview modal.
   */
  async function beginJobRestore(jobId: number): Promise<RestoreOutcome> {
    const idx = discardedJobs.value.findIndex(j => j.id === jobId)
    if (idx < 0) return { kind: 'stale' }

    let preview: RestoreConflictPreview
    try {
      preview = await fetchJobRestorePreview(jobId)
    } catch {
      return { kind: 'network', message: 'Could not fetch restore preview' }
    }

    const hasBlocker =
      preview.colliding_staging_errored_rows.length > 0 ||
      preview.colliding_live_jobs.length > 0

    if (hasBlocker) {
      return { kind: 'preview', preview }
    }

    // No blockers — restore immediately.
    return _commitJobRestoreRequest(jobId, idx, [])
  }

  /**
   * Phase 2: send the operator's actions and restore atomically. On 409 returns
   * a fresh preview so the modal can re-render without an extra round-trip.
   */
  async function commitJobRestore(
    jobId: number,
    actions: StagingRestoreAction[],
  ): Promise<RestoreOutcome> {
    const idx = discardedJobs.value.findIndex(j => j.id === jobId)
    if (idx < 0) return { kind: 'stale' }
    return _commitJobRestoreRequest(jobId, idx, actions)
  }

  async function _commitJobRestoreRequest(
    jobId: number,
    idx: number,
    actions: StagingRestoreAction[],
  ): Promise<RestoreOutcome> {
    const removed = discardedJobs.value.splice(idx, 1)[0]
    try {
      const restored = await postRestoreJob(jobId, actions)
      discardedTotal.value = Math.max(0, discardedTotal.value - 1)
      // The restored job is now active; prepend it to the shipping list.
      jobs.value = [restored, ...jobs.value]
      return { kind: 'ok', job: restored }
    } catch (err) {
      discardedJobs.value.splice(idx, 0, removed)
      if (isApiError(err) && err.response) {
        const status = err.response.status
        const detail = err.response.data?.detail
        if (status === 422 && detail && typeof detail === 'object') {
          const d = detail as Record<string, unknown>
          return {
            kind: 'invalid-edit',
            message: String(d.message ?? 'Action validation failed'),
            action_index: Number(d.action_index ?? 0),
          }
        }
        if (status === 409 && detail && typeof detail === 'object') {
          const d = detail as Record<string, unknown>
          if (d.preview) {
            await loadDiscardedJobs()
            return {
              kind: 'conflict',
              message: String(d.message ?? 'Residual collision after actions'),
              preview: d.preview as RestoreConflictPreview,
            }
          }
        }
        if (status === 409) {
          await loadDiscardedJobs()
          return { kind: 'network', message: String(detail ?? 'Job is not discarded') }
        }
      }
      return { kind: 'network', message: 'Could not reach the API' }
    }
  }

  // ---------------------------------------------------------------------------
  // 2nd OPS (Phase 22)
  // ---------------------------------------------------------------------------

  async function _loadRecordInto(
    target: typeof secondOpsFetch,
    jobId: number,
  ): Promise<void> {
    const seq = ++secondOpsRequestSeq
    target.value = { status: 'loading' }
    try {
      const record = await fetchSecondOps(jobId)
      if (seq !== secondOpsRequestSeq) return
      target.value = { status: 'loaded', record }
    } catch {
      if (seq !== secondOpsRequestSeq) return
      target.value = { status: 'failed', message: SECOND_OPS_FETCH_FAILED }
    }
  }

  /**
   * Open the entry modal for `job` and fetch its record.
   *
   * Post: secondOpsFetch reflects the MOST RECENTLY REQUESTED job and no other.
   *       A response whose captured sequence is stale is discarded without
   *       touching state — including the failed arm, so job A's network error
   *       cannot surface over job B's open modal.
   */
  async function openSecondOps(job: JobReadExpanded): Promise<void> {
    secondOpsJob.value = job
    secondOpsOpen.value = true
    await _loadRecordInto(secondOpsFetch, job.id)
  }

  function closeSecondOps(): void {
    // Incrementing here orphans any in-flight request by itself.
    secondOpsRequestSeq += 1
    secondOpsOpen.value = false
    secondOpsJob.value = null
    secondOpsFetch.value = { status: 'loading' }
  }

  async function retrySecondOps(): Promise<void> {
    const job = secondOpsJob.value
    if (job === null) return
    await _loadRecordInto(secondOpsFetch, job.id)
  }

  async function openSecondOpsRecord(job: JobReadExpanded): Promise<void> {
    secondOpsRecordJob.value = job
    await _loadRecordInto(secondOpsRecordFetch, job.id)
  }

  function closeSecondOpsRecord(): void {
    secondOpsRequestSeq += 1
    secondOpsRecordJob.value = null
    secondOpsRecordFetch.value = { status: 'loading' }
  }

  async function retrySecondOpsRecord(): Promise<void> {
    const job = secondOpsRecordJob.value
    if (job === null) return
    await _loadRecordInto(secondOpsRecordFetch, job.id)
  }

  function openSecondOpsItem(fields: AuditBomFields): void {
    secondOpsItem.value = fields
  }

  function closeSecondOpsItem(): void {
    secondOpsItem.value = null
  }

  /**
   * Whole-set replace of a job's 2nd OPS record.
   *
   * Post: on `saved` the grid is reloaded so the cell reflects the SERVER's
   *       preview cap rather than a locally guessed slice. Audits are
   *       infrequent; a stale cell after every save is not worth avoiding one
   *       request. The three failure arms are returned untouched — the modal
   *       renders each differently and must not see them collapsed.
   */
  async function saveSecondOps(
    jobId: number,
    payload: SecondOpsWritePayload,
  ): Promise<SecondOpsSaveResult> {
    const result = await putSecondOps(jobId, payload)
    if (result.kind === 'saved') {
      await load()
    }
    return result
  }

  return {
    jobs, loading, error, inspected,
    secondOpsJob, secondOpsOpen, secondOpsFetch,
    secondOpsRecordJob, secondOpsRecordFetch, secondOpsItem,
    openSecondOps, closeSecondOps, retrySecondOps,
    openSecondOpsRecord, closeSecondOpsRecord, retrySecondOpsRecord,
    openSecondOpsItem, closeSecondOpsItem, saveSecondOps,
    discardedJobs, discardedTotal, discardedLoading, discardedDrawerOpen,
    discardedOffset, discardedLimit, discardedSearchQuery,
    discardedHasPrev, discardedHasNext, discardedPageStart, discardedPageEnd,
    load, discardJob, inspect, closeInspect,
    loadDiscardedJobs, nextDiscardedJobsPage, prevDiscardedJobsPage,
    setDiscardedJobsSearch, openDiscardedJobsDrawer, closeDiscardedJobsDrawer,
    beginJobRestore, commitJobRestore,
  }
})

