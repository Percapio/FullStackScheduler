import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  fetchSupersessionCandidates,
  approveSupersessionCandidate,
  rejectSupersessionCandidate,
  bulkApproveSupersessionCandidates,
  type SupersessionCandidate,
  type BulkApprovalResult,
} from '@/api/supersession'
import { isApiError } from '@/api/client'

export type { BulkApprovalResult }

export const useSupersessionStore = defineStore('supersession', () => {
  const candidates     = ref<SupersessionCandidate[]>([])
  const total          = ref(0)
  const loading        = ref(false)
  const lastError      = ref<string | null>(null)
  const lastBulkSummary = ref<BulkApprovalResult | null>(null)

  // Set of candidate ids currently selected for bulk action.
  const selectedIds = ref<Set<number>>(new Set())

  // Set of candidate ids that have an in-flight request.
  const inFlightIds = ref<Set<number>>(new Set())

  // ---- loaders -------------------------------------------------------------

  async function loadPending(): Promise<void> {
    loading.value = true
    lastError.value = null
    try {
      const page = await fetchSupersessionCandidates({ status: 'pending', limit: 200 })
      candidates.value = page.items
      total.value = page.total
    } catch (err) {
      lastError.value = isApiError(err)
        ? String((err.response?.data as { detail?: unknown })?.detail ?? err.message)
        : 'Could not load supersession candidates.'
    } finally {
      loading.value = false
    }
  }

  async function loadResolved(): Promise<void> {
    loading.value = true
    lastError.value = null
    try {
      const page = await fetchSupersessionCandidates({ status: 'resolved', limit: 200 })
      candidates.value = page.items
      total.value = page.total
    } catch (err) {
      lastError.value = isApiError(err)
        ? String((err.response?.data as { detail?: unknown })?.detail ?? err.message)
        : 'Could not load resolved candidates.'
    } finally {
      loading.value = false
    }
  }

  // ---- single-row actions --------------------------------------------------

  async function approve(id: number): Promise<SupersessionCandidate | null> {
    inFlightIds.value.add(id)
    lastError.value = null
    try {
      const result = await approveSupersessionCandidate(id)
      // Remove from list regardless of whether it approved or shield-rejected;
      // either way the candidate is now resolved (non-optimistic: server settled).
      candidates.value = candidates.value.filter(c => c.id !== id)
      total.value = Math.max(0, total.value - 1)
      selectedIds.value.delete(id)
      return result
    } catch (err) {
      if (isApiError(err) && err.response?.status === 409) {
        // Already closed: refresh pending list so UI reflects server truth.
        await loadPending()
      } else {
        lastError.value = 'Approval request failed. Please retry.'
      }
      return null
    } finally {
      inFlightIds.value.delete(id)
    }
  }

  async function reject(id: number): Promise<SupersessionCandidate | null> {
    inFlightIds.value.add(id)
    lastError.value = null
    try {
      const result = await rejectSupersessionCandidate(id)
      candidates.value = candidates.value.filter(c => c.id !== id)
      total.value = Math.max(0, total.value - 1)
      selectedIds.value.delete(id)
      return result
    } catch (err) {
      if (isApiError(err) && err.response?.status === 409) {
        await loadPending()
      } else {
        lastError.value = 'Rejection request failed. Please retry.'
      }
      return null
    } finally {
      inFlightIds.value.delete(id)
    }
  }

  // ---- bulk action ---------------------------------------------------------

  async function bulkApprove(ids: number[]): Promise<BulkApprovalResult | null> {
    loading.value = true
    lastError.value = null
    try {
      const result = await bulkApproveSupersessionCandidates(ids)
      lastBulkSummary.value = result
      // Reload pending to get server truth after the bulk operation.
      await loadPending()
      clearSelection()
      return result
    } catch (err) {
      lastError.value = isApiError(err)
        ? String((err.response?.data as { detail?: unknown })?.detail ?? err.message)
        : 'Bulk approval failed. Please retry.'
      return null
    } finally {
      loading.value = false
    }
  }

  // ---- selection -----------------------------------------------------------

  function toggleSelection(id: number): void {
    if (selectedIds.value.has(id)) {
      selectedIds.value.delete(id)
    } else {
      selectedIds.value.add(id)
    }
  }

  function clearSelection(): void {
    selectedIds.value.clear()
  }

  return {
    candidates,
    total,
    loading,
    lastError,
    lastBulkSummary,
    selectedIds,
    inFlightIds,
    loadPending,
    loadResolved,
    approve,
    reject,
    bulkApprove,
    toggleSelection,
    clearSelection,
  }
})
