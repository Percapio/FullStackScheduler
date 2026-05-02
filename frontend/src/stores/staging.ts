import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  fetchErrored, fetchDetail, submitCorrection,
  fetchDiscarded, deleteStagingRow, postRestoreStagingRow,
  type StagingRowSummary, type StagingRowDetail, type CorrectionPayload,
} from '@/api/staging'
import { isApiError } from '@/api/client'

type SubmitOutcome =
  | { kind: 'ok' }
  | { kind: 'transform-failed'; processingError: string }
  | { kind: 'conflict'; message: string }
  | { kind: 'network'; message: string }

type DiscardOutcome =
  | { kind: 'ok' }
  | { kind: 'stale' }
  | { kind: 'conflict'; message: string }
  | { kind: 'network'; message: string }

type RestoreOutcome =
  | { kind: 'ok'; row: StagingRowSummary }
  | { kind: 'stale' }
  | { kind: 'conflict'; message: string }
  | { kind: 'network'; message: string }

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
    activeErrorRowId.value = rowId
    if (!details.value[rowId]) await loadDetail(rowId)
  }

  function closeError() {
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
    const removed = rows.value.splice(idx, 1)[0]

    try {
      await submitCorrection(rowId, payload)
      delete details.value[rowId]
      total.value = Math.max(0, total.value - 1)
      // The sidebar's stale-row watcher closes us on the 200 path; the 409
      // path closes via the sidebar's onSubmit handler explicitly. The store
      // never touches activeErrorRowId on its own.
      return { kind: 'ok' }
    } catch (err) {
      rows.value.splice(idx, 0, removed)

      if (isApiError(err) && err.response) {
        const status = err.response.status
        const detail = err.response.data?.detail

        if (status === 422 && typeof detail === 'string') {
          rows.value[idx] = { ...removed, processing_error: detail }
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
    loadErrored, openError, closeError, loadDetail, correct,
    loadDiscarded, openDiscardedDrawer, closeDiscardedDrawer,
    discardRow, restoreRow,
  }
})
