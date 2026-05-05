import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  fetchErrored, fetchDetail, submitCorrection,
  fetchDiscarded, deleteStagingRow, postRestoreStagingRow,
  fetchConflicts,
  type StagingRowSummary, type StagingRowDetail, type CorrectionPayload,
  type ConflictGroup,
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
  | { kind: 'stale' }
  | { kind: 'conflict'; message: string }
  | { kind: 'network'; message: string }

/** Discriminated union for the sidebar's two render modes.
 *  A tagged union forces every render path to pattern-match; two independent
 *  booleans produce "panel open with no contents" bugs on state-drift. */
type ConflictMode =
  | { kind: 'single' }
  | { kind: 'group'; batchId: number; groupKey: string }

export const useStagingStore = defineStore('staging', () => {
  const rows             = ref<StagingRowSummary[]>([])
  const total            = ref(0)
  const loading          = ref(false)
  const details          = ref<Record<number, StagingRowDetail>>({})
  const activeErrorRowId = ref<number | null>(null)

  const discardedRows       = ref<StagingRowSummary[]>([])
  const discardedTotal      = ref(0)
  const discardedLoading    = ref(false)
  const discardedDrawerOpen = ref(false)

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
    try {
      const { rows: r, total: t } = await fetchErrored(100, 0)
      rows.value = r
      total.value = t
    } finally {
      loading.value = false
    }
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
      const { rows: r, total: t } = await fetchDiscarded(100, 0)
      discardedRows.value = r
      discardedTotal.value = t
    } finally {
      discardedLoading.value = false
    }
  }

  async function openDiscardedDrawer() {
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

  async function restoreRow(rowId: number): Promise<RestoreOutcome> {
    const idx = discardedRows.value.findIndex(r => r.id === rowId)
    if (idx < 0) return { kind: 'stale' }
    const removed = discardedRows.value.splice(idx, 1)[0]

    try {
      const restored = await postRestoreStagingRow(rowId)
      discardedTotal.value = Math.max(0, discardedTotal.value - 1)
      // Re-injection into rows[] requires a list refresh — the restored row's
      // place in pagination order is not derivable from the response alone.
      await loadErrored()
      return { kind: 'ok', row: restored }
    } catch (err) {
      discardedRows.value.splice(idx, 0, removed)
      if (isApiError(err) && err.response?.status === 409) {
        // Server view disagrees with local cache; resync before returning so
        // the next user action operates on truth, not on the stale rollback.
        await loadDiscarded()
        return {
          kind: 'conflict',
          message: String(err.response.data?.detail ?? 'Row is not discarded'),
        }
      }
      return { kind: 'network', message: 'Could not reach the API' }
    }
  }

  return {
    rows, visibleRows, total, loading, details,
    activeErrorRowId,
    discardedRows, discardedTotal, discardedLoading, discardedDrawerOpen,
    conflictGroups, reconciledConflictGroups, conflictsLoading, sidebarMode, groupBusy,
    loadErrored, openError, closeError, loadDetail, correct,
    loadConflicts, openConflictGroup,
    loadDiscarded, openDiscardedDrawer, closeDiscardedDrawer,
    discardRow, restoreRow,
    correctConflictRow, discardConflictRow,
  }
})
