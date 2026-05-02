import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  fetchErrored, fetchDetail, submitCorrection,
  type StagingRowSummary, type StagingRowDetail, type CorrectionPayload,
} from '@/api/staging'
import { isApiError } from '@/api/client'

type SubmitOutcome =
  | { kind: 'ok' }
  | { kind: 'transform-failed'; processingError: string }
  | { kind: 'conflict'; message: string }
  | { kind: 'network'; message: string }

export const useStagingStore = defineStore('staging', () => {
  const rows         = ref<StagingRowSummary[]>([])
  const total        = ref(0)
  const loading      = ref(false)
  const details      = ref<Record<number, StagingRowDetail>>({})
  const expandedId   = ref<number | null>(null)

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

  async function expand(rowId: number) {
    expandedId.value = expandedId.value === rowId ? null : rowId
    if (expandedId.value === rowId && !details.value[rowId]) {
      details.value[rowId] = await fetchDetail(rowId)
    }
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
      if (expandedId.value === rowId) expandedId.value = null
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

  return {
    rows, visibleRows, total, loading, details, expandedId,
    loadErrored, expand, correct,
  }
})
