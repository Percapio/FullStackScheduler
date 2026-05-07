import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  fetchErrored, fetchDetail, submitCorrection,
  fetchDiscarded, deleteStagingRow, postRestoreStagingRow,
  fetchStagingRestorePreview,
  fetchConflicts,
  type StagingRowSummary, type StagingRowDetail, type CorrectionPayload,
  type ConflictGroup, type RestoreConflictPreview, type StagingRestoreAction,
} from '@/api/staging'
import { isApiError } from '@/api/client'

export type SubmitOutcome =
  | { kind: 'ok' }
  | { kind: 'transform-failed'; processingError: string }
  | { kind: 'conflict'; message: string }
  | { kind: 'network'; message: string }

export type DiscardOutcome =
  | { kind: 'ok' }
  | { kind: 'stale' }
  | { kind: 'conflict'; message: string }
  | { kind: 'network'; message: string }

type RestoreOutcome =
  | { kind: 'ok'; row: StagingRowSummary }
  | { kind: 'preview'; preview: RestoreConflictPreview }
  | { kind: 'stale' }
  | { kind: 'conflict'; message: string; preview: RestoreConflictPreview }
  | { kind: 'invalid-edit'; message: string; action_index: number }
  | { kind: 'network'; message: string }

/** Discriminated union for the sidebar's two render modes.
 *  A tagged union forces every render path to pattern-match; two independent
 *  booleans produce "panel open with no contents" bugs on state-drift. */
type ConflictMode =
  | { kind: 'single' }
  | { kind: 'group'; batchId: number; groupKey: string }

const ERRORED_PAGE_SIZE = 50

export const useStagingStore = defineStore('staging', () => {
  const rows             = ref<StagingRowSummary[]>([])
  const total            = ref(0)
  const loading          = ref(false)
  const error            = ref<string | null>(null)
  const details          = ref<Record<number, StagingRowDetail>>({})
  const activeErrorRowId = ref<number | null>(null)

  // Errored pagination state (Epoch 1)
  const erroredOffset      = ref(0)
  const erroredLimit       = ref(ERRORED_PAGE_SIZE)
  const erroredSearchQuery = ref('')

  const erroredHasPrev  = computed(() => erroredOffset.value > 0)
  const erroredHasNext  = computed(() => erroredOffset.value + rows.value.length < total.value)
  const erroredPageStart = computed(() => total.value === 0 ? 0 : erroredOffset.value + 1)
  const erroredPageEnd   = computed(() => erroredOffset.value + rows.value.length)

  const discardedRows         = ref<StagingRowSummary[]>([])
  const discardedTotal        = ref(0)
  const discardedLoading      = ref(false)
  const discardedDrawerOpen   = ref(false)

  // Discarded-rows pagination state (mirrors errored pagination)
  const discardedOffset      = ref(0)
  const discardedLimit       = ref(ERRORED_PAGE_SIZE)
  const discardedSearchQuery = ref('')

  const discardedHasPrev   = computed(() => discardedOffset.value > 0)
  const discardedHasNext   = computed(() => discardedOffset.value + discardedRows.value.length < discardedTotal.value)
  const discardedPageStart = computed(() => discardedTotal.value === 0 ? 0 : discardedOffset.value + 1)
  const discardedPageEnd   = computed(() => discardedOffset.value + discardedRows.value.length)

  // Conflict-group state (§3.5.1)
  const conflictGroups        = ref<ConflictGroup[]>([])
  const conflictsLoading      = ref(false)
  const sidebarMode           = ref<ConflictMode>({ kind: 'single' })
  // Reconciled (server-truth) snapshot — read by the disabled-state rule (§3.5.4)
  const reconciledConflictGroups = ref<ConflictGroup[]>([])
  // Per-group busy flag: key = `${batchId}:${groupKey}`
  const groupBusy             = ref<Map<string, boolean>>(new Map())

  const visibleRows = computed(() => rows.value)

  async function loadErrored() {
    loading.value = true
    error.value = null
    try {
      const { rows: r, total: t } = await fetchErrored(
        erroredLimit.value,
        erroredOffset.value,
        erroredSearchQuery.value.trim() || null,
      )
      rows.value = r
      total.value = t
    } catch {
      error.value = 'Could not load errored rows. Check that the backend is running and retry.'
    } finally {
      loading.value = false
    }
  }

  async function nextErroredPage(): Promise<void> {
    if (erroredHasNext.value) {
      erroredOffset.value += erroredLimit.value
      await loadErrored()
    }
  }

  async function prevErroredPage(): Promise<void> {
    if (erroredHasPrev.value) {
      erroredOffset.value = Math.max(0, erroredOffset.value - erroredLimit.value)
      await loadErrored()
    }
  }

  async function setErroredSearch(nextQuery: string): Promise<void> {
    erroredSearchQuery.value = nextQuery
    erroredOffset.value = 0
    error.value = null
    await loadErrored()
  }

  async function loadDetail(rowId: number) {
    details.value[rowId] = await fetchDetail(rowId)
  }

  async function openError(rowId: number) {
    sidebarMode.value = { kind: 'single' }
    activeErrorRowId.value = rowId
    if (!details.value[rowId]) await loadDetail(rowId)
  }

  function closeError() {
    activeErrorRowId.value = null
    sidebarMode.value = { kind: 'single' }
  }

  async function loadConflicts(): Promise<void> {
    conflictsLoading.value = true
    try {
      const groups = await fetchConflicts()
      conflictGroups.value = groups
      reconciledConflictGroups.value = groups
    } finally {
      conflictsLoading.value = false
    }
  }

  async function openConflictGroup(batchId: number, groupKey: string): Promise<void> {
    if (!conflictGroups.value.length) await loadConflicts()
    sidebarMode.value = { kind: 'group', batchId, groupKey }
    activeErrorRowId.value = null
  }

  async function loadDiscarded() {
    discardedLoading.value = true
    try {
      const { rows: r, total: t } = await fetchDiscarded(
        discardedLimit.value,
        discardedOffset.value,
        discardedSearchQuery.value.trim() || null,
      )
      discardedRows.value = r
      discardedTotal.value = t
    } finally {
      discardedLoading.value = false
    }
  }

  async function nextDiscardedPage(): Promise<void> {
    if (discardedHasNext.value) {
      discardedOffset.value += discardedLimit.value
      await loadDiscarded()
    }
  }

  async function prevDiscardedPage(): Promise<void> {
    if (discardedHasPrev.value) {
      discardedOffset.value = Math.max(0, discardedOffset.value - discardedLimit.value)
      await loadDiscarded()
    }
  }

  async function setDiscardedSearch(nextQuery: string): Promise<void> {
    discardedSearchQuery.value = nextQuery
    discardedOffset.value = 0
    await loadDiscarded()
  }

  async function openDiscardedDrawer() {
    discardedOffset.value = 0
    discardedSearchQuery.value = ''
    await loadDiscarded()
    discardedDrawerOpen.value = true
  }

  function closeDiscardedDrawer() {
    discardedDrawerOpen.value = false
  }

  async function correct(
    rowId: number, payload: Partial<CorrectionPayload>,
  ): Promise<SubmitOutcome> {
    const idx = rows.value.findIndex(r => r.id === rowId)
    if (idx < 0) return { kind: 'network', message: 'Row vanished from store' }
    const snapshot = rows.value[idx]      // read without optimistic splice

    try {
      await submitCorrection(rowId, payload)
      delete details.value[rowId]         // 1. cache cleared first
      rows.value.splice(idx, 1)           // 2. then row removed (watcher fires here)
      total.value = Math.max(0, total.value - 1)
      // The sidebar's stale-row watcher closes us on the 200 path; the 409
      // path closes via the sidebar's onSubmit handler explicitly. The store
      // never touches activeErrorRowId on its own.
      return { kind: 'ok' }
    } catch (err) {
      // row was never removed (splice is on the success path only)

      if (isApiError(err) && err.response) {
        const status = err.response.status
        const detail = err.response.data?.detail

        if (status === 422 && typeof detail === 'string') {
          rows.value[idx] = { ...snapshot, processing_error: detail }
          if (details.value[rowId]) {
            details.value[rowId] = { ...details.value[rowId], processing_error: detail }
          }
          return { kind: 'transform-failed', processingError: detail }
        }
        if (status === 409) {
          return { kind: 'conflict', message: String(detail ?? 'Row no longer in error state') }
        }
      }
      return { kind: 'network', message: 'Could not reach the API' }
    }
  }

  async function discardRow(rowId: number): Promise<DiscardOutcome> {
    const idx = rows.value.findIndex(r => r.id === rowId)
    if (idx < 0) return { kind: 'stale' }
    const removed = rows.value.splice(idx, 1)[0]

    try {
      await deleteStagingRow(rowId)
      delete details.value[rowId]
      total.value = Math.max(0, total.value - 1)
      discardedTotal.value += 1
      if (activeErrorRowId.value === rowId) activeErrorRowId.value = null
      return { kind: 'ok' }
    } catch (err) {
      rows.value.splice(idx, 0, removed)
      if (isApiError(err) && err.response?.status === 409) {
        return {
          kind: 'conflict',
          message: String(err.response.data?.detail ?? 'Row cannot be discarded'),
        }
      }
      return { kind: 'network', message: 'Could not reach the API' }
    }
  }

  /**
   * Submit a correction for a row that belongs to a conflict group.
   * Unlike `correct`, this method does NOT splice `rows[]` or `conflictGroups[]`;
   * the caller is responsible for invoking `loadConflicts()` after the batch.
   */
  async function correctConflictRow(
    rowId: number,
    payload: Partial<CorrectionPayload>,
    _batchId: number,
    _groupKey: string,
  ): Promise<SubmitOutcome> {
    try {
      await submitCorrection(rowId, payload)
      return { kind: 'ok' }
    } catch (err) {
      if (isApiError(err) && err.response) {
        const status = err.response.status
        const detail = err.response.data?.detail
        if (status === 422 && typeof detail === 'string') {
          return { kind: 'transform-failed', processingError: detail }
        }
        if (status === 409) {
          return { kind: 'conflict', message: String(detail ?? 'Row no longer in error state') }
        }
      }
      return { kind: 'network', message: 'Could not reach the API' }
    }
  }

  /**
   * Discard a row that belongs to a conflict group.
   * Unlike `discardRow`, this method does NOT splice `rows[]` or `conflictGroups[]`;
   * the caller is responsible for invoking `loadConflicts()` after the batch.
   * Increments `discardedTotal` on success to keep the global tally accurate.
   */
  async function discardConflictRow(
    rowId: number,
    _batchId: number,
    _groupKey: string,
  ): Promise<DiscardOutcome> {
    try {
      await deleteStagingRow(rowId)
      discardedTotal.value += 1
      return { kind: 'ok' }
    } catch (err) {
      if (isApiError(err) && err.response) {
        const status = err.response.status
        const detail = err.response.data?.detail
        if (status === 409) {
          return { kind: 'conflict', message: String(detail ?? 'Row cannot be discarded') }
        }
        if (status === 404) {
          return { kind: 'stale' }
        }
      }
      return { kind: 'network', message: 'Could not reach the API' }
    }
  }

  /**
   * Phase 1 of the two-phase restore flow.
   *
   * Fetches the restore-preview for `rowId` and evaluates whether any genuine
   * blockers exist. If none exist, attempts the restore immediately (empty
   * actions fast-path) and returns `{ kind: 'ok' }`. If blockers exist,
   * returns `{ kind: 'preview', preview }` so the caller can open the
   * `RestoreConflictPreview` modal.
   */
  async function beginRestore(rowId: number): Promise<RestoreOutcome> {
    const idx = discardedRows.value.findIndex(r => r.id === rowId)
    if (idx < 0) return { kind: 'stale' }

    let preview: RestoreConflictPreview
    try {
      preview = await fetchStagingRestorePreview(rowId)
    } catch {
      return { kind: 'network', message: 'Could not fetch restore preview' }
    }

    const hasBlocker =
      preview.colliding_staging_errored_rows.length > 0 ||
      preview.colliding_live_jobs.length > 0

    if (hasBlocker) {
      return { kind: 'preview', preview }
    }

    // No blockers — restore immediately with empty actions.
    return _commitRestoreRequest(rowId, idx, [])
  }

  /**
   * Phase 2 of the two-phase restore flow.
   *
   * Called after the operator has resolved conflicts in the modal and pressed
   * "Restore". Sends the actions list to the backend. On 409, returns a fresh
   * `{ kind: 'conflict', preview }` so the modal can re-render with updated
   * collision state.
   */
  async function commitRestore(
    rowId: number,
    actions: StagingRestoreAction[],
  ): Promise<RestoreOutcome> {
    const idx = discardedRows.value.findIndex(r => r.id === rowId)
    if (idx < 0) return { kind: 'stale' }
    return _commitRestoreRequest(rowId, idx, actions)
  }

  async function _commitRestoreRequest(
    rowId: number,
    idx: number,
    actions: StagingRestoreAction[],
  ): Promise<RestoreOutcome> {
    const removed = discardedRows.value.splice(idx, 1)[0]
    try {
      const restored = await postRestoreStagingRow(rowId, actions)
      discardedTotal.value = Math.max(0, discardedTotal.value - 1)
      await loadErrored()
      return { kind: 'ok', row: restored }
    } catch (err) {
      discardedRows.value.splice(idx, 0, removed)
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
            await loadDiscarded()
            return {
              kind: 'conflict',
              message: String(d.message ?? 'Residual collision after actions'),
              preview: d.preview as RestoreConflictPreview,
            }
          }
        }
        if (status === 409) {
          await loadDiscarded()
          return { kind: 'network', message: String(detail ?? 'Row is not discarded') }
        }
      }
      return { kind: 'network', message: 'Could not reach the API' }
    }
  }

  return {
    rows, visibleRows, total, loading, error, details,
    activeErrorRowId,
    erroredOffset, erroredLimit, erroredSearchQuery,
    erroredHasPrev, erroredHasNext, erroredPageStart, erroredPageEnd,
    discardedRows, discardedTotal, discardedLoading, discardedDrawerOpen,
    discardedOffset, discardedLimit, discardedSearchQuery,
    discardedHasPrev, discardedHasNext, discardedPageStart, discardedPageEnd,
    conflictGroups, reconciledConflictGroups, conflictsLoading, sidebarMode, groupBusy,
    loadErrored, openError, closeError, loadDetail, correct,
    loadConflicts, openConflictGroup,
    loadDiscarded, nextDiscardedPage, prevDiscardedPage, setDiscardedSearch,
    openDiscardedDrawer, closeDiscardedDrawer,
    discardRow, beginRestore, commitRestore,
    correctConflictRow, discardConflictRow,
    nextErroredPage, prevErroredPage, setErroredSearch,
  }
})
