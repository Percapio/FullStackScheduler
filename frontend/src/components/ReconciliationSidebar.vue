<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useStagingStore } from '@/stores/staging'
import { useCorrectionDraft } from '@/composables/useCorrectionDraft'
import { useToast } from '@/composables/useToast'
import SlideOverPanel from './SlideOverPanel.vue'
import RawFieldGrid from './RawFieldGrid.vue'
import WarnIcon from './WarnIcon.vue'
import ConflictRowComparison from './ConflictRowComparison.vue'

const store = useStagingStore()
const { activeErrorRowId, details, rows, sidebarMode, conflictGroups } = storeToRefs(store)
const { show: showToast } = useToast()

const detail = computed(() =>
  activeErrorRowId.value != null ? details.value[activeErrorRowId.value] : undefined,
)
const { draft, changedPayload, hasChanges, setField, originalFor } = useCorrectionDraft(detail)

const submitting = ref(false)
const discarding = ref(false)

const currentGroup = computed(() => {
  if (sidebarMode.value.kind !== 'group') return undefined
  const { batchId, groupKey } = sidebarMode.value
  return conflictGroups.value.find(g => g.batch_id === batchId && g.group_key === groupKey)
})

const panelOpen = computed(() =>
  sidebarMode.value.kind === 'group' || activeErrorRowId.value !== null,
)

watch([activeErrorRowId, rows], ([id, list]) => {
  if (id != null && !list.some(r => r.id === id)) store.closeError()
}, { deep: true })

async function onDiscard() {
  if (activeErrorRowId.value === null) return
  if (discarding.value) return
  discarding.value = true
  const result = await store.discardRow(activeErrorRowId.value)
  discarding.value = false

  if (result.kind === 'ok') {
    showToast('Row discarded', 'success')
    // store.discardRow already cleared activeErrorRowId; the panel unmounts.
  } else if (result.kind === 'stale') {
    // Row already gone — close quietly; nothing actionable for the user.
    store.closeError()
  } else if (result.kind === 'conflict') {
    showToast(result.message, 'error')
    await store.loadErrored()
    store.closeError()
  } else {
    showToast(result.message, 'error')  // network — user can retry
  }
}

async function onSubmit() {
  if (!hasChanges.value || submitting.value) return
  if (activeErrorRowId.value == null) return
  const id = activeErrorRowId.value
  submitting.value = true
  const result = await store.correct(id, changedPayload.value)
  submitting.value = false

  if (result.kind === 'ok') return
  if (result.kind === 'transform-failed') {
    await store.loadDetail(id)
    return
  }
  if (result.kind === 'conflict') {
    showToast(result.message, 'error')
    store.closeError()
    return
  }
  if (result.kind === 'network') {
    showToast(result.message, 'error')
  }
}
</script>

<template>
  <SlideOverPanel
    :open="panelOpen"
    width="xl"
    :ariaLabel="sidebarMode.kind === 'group' ? 'Conflict group comparison' : (detail ? `Reconcile row ${detail.source_row_number}` : 'Reconciliation editor')"
    @close="store.closeError()"
  >
    <!-- Conflict-group mode -->
    <template v-if="sidebarMode.kind === 'group'">
      <ConflictRowComparison :group="currentGroup" />
    </template>

    <!-- Single-row mode (existing UI, unchanged) -->
    <template v-else>
      <template #header>
      <div class="flex items-center justify-between w-full">
        <h2 class="text-base font-semibold text-slate-800 dark:text-slate-100">
          Row {{ detail?.source_row_number }} · Batch {{ detail?.batch_id }}
          <span class="ml-2 inline-flex items-center gap-1 px-2 py-0.5 rounded
                       bg-warn-50 text-warn-600 text-xs font-medium">
            <WarnIcon /> {{ detail?.processing_status }}
          </span>
        </h2>
        <button @click="store.closeError()" aria-label="Close reconciliation editor"
                data-testid="sidebar-close-btn"
                class="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-slate-500" fill="none"
               viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </template>

    <div v-if="!detail" class="text-slate-500 dark:text-slate-400 text-sm">Loading detail…</div>

    <div v-else class="space-y-4">
      <div data-testid="why-banner"
           class="bg-warn-50 dark:bg-warn-800/20 border border-warn-200 dark:border-warn-600/40 rounded-md px-4 py-3">
        <p class="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-warn-600 mb-1">
          <WarnIcon size="md" />
          Processing Error
        </p>
        <pre class="text-sm text-warn-800 dark:text-warn-200 whitespace-pre-wrap font-mono">{{ detail.processing_error }}</pre>
      </div>

      <div v-if="detail.suggested_correction" data-testid="how-callout"
           class="bg-teal-50 dark:bg-teal-900/20 border border-teal-200 dark:border-teal-600/40 rounded-md px-4 py-3">
        <p class="text-xs font-semibold uppercase tracking-wide text-teal-700 dark:text-teal-300 mb-1">
          Suggested correction
        </p>
        <p class="text-sm text-teal-800 dark:text-teal-200">{{ detail.suggested_correction }}</p>
      </div>

      <form @submit.prevent="onSubmit" class="space-y-4">
        <RawFieldGrid
          :draft="draft"
          :original-for="originalFor"
          :highlight-fields="detail.highlight_fields"
          @update:field="setField"
        />
        <div class="flex items-center justify-between pt-2 border-t border-slate-200 dark:border-slate-700">
          <button
            type="button"
            :disabled="hasChanges || discarding"
            :title="hasChanges ? 'Reset edits to enable Discard' : 'Discard this row'"
            tabindex="-1"
            data-testid="sidebar-discard-btn"
            class="px-4 py-2 rounded-md text-rose-700 text-sm font-medium
                   hover:bg-rose-50 dark:hover:bg-rose-900/30 transition-colors
                   disabled:opacity-40 disabled:cursor-not-allowed"
            @click="onDiscard"
          >
            Discard
          </button>
          <button
            type="submit"
            :disabled="!hasChanges || submitting"
            data-testid="sidebar-submit-btn"
            class="px-4 py-2 rounded-md bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 text-sm font-medium
                   hover:bg-slate-700 dark:hover:bg-slate-300 transition-colors
                   disabled:bg-slate-300 dark:disabled:bg-slate-600 disabled:cursor-not-allowed"
          >
            {{ submitting ? 'Submitting…' : 'Apply correction' }}
          </button>
        </div>
      </form>
    </div>
    </template><!-- end single-row mode -->
  </SlideOverPanel>
</template>
