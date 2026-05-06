<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useSupersessionStore } from '@/stores/supersession'
import { useToast } from '@/composables/useToast'

const store = useSupersessionStore()
const { candidates, loading, selectedIds, inFlightIds, lastBulkSummary } = storeToRefs(store)
const { show: showToast } = useToast()

const REASON_LABELS: Record<string, string> = {
  orphan_after_split:     'Split detected',
  orphan_after_recombine: 'Recombined',
  orphan_other:           'Removed from workbook',
}

const REASON_BADGE_CLASS: Record<string, string> = {
  orphan_after_split:
    'bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-900/40 dark:text-amber-200 dark:border-amber-700',
  orphan_after_recombine:
    'bg-blue-100 text-blue-800 border-blue-300 dark:bg-blue-900/40 dark:text-blue-200 dark:border-blue-700',
  orphan_other:
    'bg-slate-100 text-slate-700 border-slate-300 dark:bg-slate-700/60 dark:text-slate-200 dark:border-slate-600',
}

function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason
}
function reasonClass(reason: string): string {
  return REASON_BADGE_CLASS[reason] ?? REASON_BADGE_CLASS.orphan_other
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString()
}

const selectedArray = computed(() => [...selectedIds.value])

function isSelected(id: number): boolean {
  return selectedIds.value.has(id)
}

function isInFlight(id: number): boolean {
  return inFlightIds.value.has(id)
}

async function handleApprove(id: number): Promise<void> {
  const result = await store.approve(id)
  if (result && result.closed_by_shield_reason) {
    showToast(
      `Job was not superseded: ${result.closed_by_shield_reason}. The candidate was closed.`,
      'error',
    )
  }
}

async function handleReject(id: number): Promise<void> {
  await store.reject(id)
}

async function handleBulkApprove(): Promise<void> {
  const ids = [...selectedIds.value]
  if (ids.length === 0) return
  const result = await store.bulkApprove(ids)
  if (result && lastBulkSummary.value) {
    const { shield_rejected } = lastBulkSummary.value
    if (shield_rejected.length > 0) {
      showToast(
        `${shield_rejected.length} candidate(s) could not be superseded: job(s) already shipped.`,
        'error',
      )
    }
  }
}
</script>

<template>
  <div v-if="loading" class="text-sm text-slate-500 dark:text-slate-400 mb-4">
    Loading supersession candidates…
  </div>

  <div v-else-if="candidates.length === 0"
       class="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700
              rounded-lg p-8 text-center text-slate-500 dark:text-slate-400 mb-6">
    No supersession candidates. Nothing to retire.
  </div>

  <div v-else class="mb-6">
    <h3
      data-testid="supersession-heading"
      class="text-xs font-semibold uppercase tracking-wide text-violet-600 dark:text-violet-400 mb-2"
    >
      {{ candidates.length }} supersession candidate{{ candidates.length !== 1 ? 's' : '' }}
    </h3>

    <div class="space-y-2">
      <div
        v-for="cand in candidates"
        :key="cand.id"
        data-testid="candidate-row"
        class="flex items-center justify-between gap-3 bg-violet-50 dark:bg-violet-900/20
               border border-violet-200 dark:border-violet-700/40 rounded-lg px-4 py-3 text-sm"
      >
        <!-- Checkbox -->
        <input
          type="checkbox"
          class="shrink-0 h-4 w-4 rounded border-slate-300 dark:border-slate-600
                 text-violet-600 focus:ring-violet-500"
          :checked="isSelected(cand.id)"
          :aria-label="`Select candidate ${cand.id}`"
          @change="store.toggleSelection(cand.id)"
        />

        <!-- Candidate info -->
        <div class="min-w-0 flex-1">
          <p class="font-medium text-violet-800 dark:text-violet-200 truncate">
            Job #{{ cand.job_id }}
          </p>
          <div class="flex items-center gap-2 mt-0.5">
            <span
              class="inline-block text-xs font-medium px-1.5 py-0.5 rounded border"
              :class="reasonClass(cand.reason)"
            >
              {{ reasonLabel(cand.reason) }}
            </span>
            <span class="text-xs text-slate-500 dark:text-slate-400">
              {{ formatDateTime(cand.detected_at) }}
            </span>
          </div>
        </div>

        <!-- Per-row actions -->
        <div class="shrink-0 flex gap-2">
          <button
            type="button"
            data-testid="approve-btn"
            class="px-3 py-1.5 rounded-md bg-violet-600 hover:bg-violet-700 text-white
                   text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="isInFlight(cand.id)"
            @click="handleApprove(cand.id)"
          >
            Approve
          </button>
          <button
            type="button"
            data-testid="reject-btn"
            class="px-3 py-1.5 rounded-md bg-slate-200 hover:bg-slate-300 text-slate-700
                   dark:bg-slate-700 dark:hover:bg-slate-600 dark:text-slate-200
                   text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="isInFlight(cand.id)"
            @click="handleReject(cand.id)"
          >
            Reject
          </button>
        </div>
      </div>
    </div>

    <!-- Sticky bulk-action footer -->
    <div
      v-if="selectedArray.length > 0"
      data-testid="bulk-footer"
      class="sticky bottom-0 mt-3 flex items-center justify-between gap-3
             bg-violet-50 dark:bg-violet-900/30 border border-violet-200
             dark:border-violet-700/40 rounded-lg px-4 py-3"
    >
      <span class="text-sm text-violet-700 dark:text-violet-300 font-medium">
        {{ selectedArray.length }} selected
      </span>
      <div class="flex gap-2">
        <button
          type="button"
          data-testid="bulk-approve-btn"
          class="px-3 py-1.5 rounded-md bg-violet-600 hover:bg-violet-700 text-white
                 text-xs font-medium transition-colors"
          @click="handleBulkApprove"
        >
          Approve {{ selectedArray.length }} selected
        </button>
        <button
          type="button"
          data-testid="clear-selection-btn"
          class="px-3 py-1.5 rounded-md bg-slate-200 hover:bg-slate-300 text-slate-700
                 dark:bg-slate-700 dark:hover:bg-slate-600 dark:text-slate-200
                 text-xs font-medium transition-colors"
          @click="store.clearSelection"
        >
          Clear
        </button>
      </div>
    </div>
  </div>
</template>
