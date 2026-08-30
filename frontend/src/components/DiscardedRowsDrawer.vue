<script setup lang="ts">
import { ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useStagingStore } from '@/stores/staging'
import { useToast } from '@/composables/useToast'
import { useDebouncedRef } from '@/composables/useDebouncedRef'
import SlideOverPanel from './SlideOverPanel.vue'
import SearchPaginatorBar from './SearchPaginatorBar.vue'
import RestoreConflictPreviewModal from './RestoreConflictPreview.vue'
import type { RestoreConflictPreview, StagingRestoreAction } from '@/api/staging'

const store = useStagingStore()
const {
  discardedRows, discardedTotal, discardedLoading, discardedDrawerOpen,
  discardedHasPrev, discardedHasNext, discardedPageStart, discardedPageEnd,
  discardedSearchQuery,
} = storeToRefs(store)
const { show: showToast } = useToast()

const debouncedDiscardedSearch = useDebouncedRef(discardedSearchQuery.value, 300)
watch(debouncedDiscardedSearch, (q) => store.setDiscardedSearch(q))

// Restore-preview modal state
const restorePreviewOpen    = ref(false)
const restorePreview        = ref<RestoreConflictPreview | null>(null)
const restoreSubmitting     = ref(false)
const restoringRowId        = ref<number | null>(null)

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60)  return `${seconds} second${seconds !== 1 ? 's' : ''} ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60)  return `${minutes} minute${minutes !== 1 ? 's' : ''} ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24)    return `${hours} hour${hours !== 1 ? 's' : ''} ago`
  const days = Math.floor(hours / 24)
  return `${days} day${days !== 1 ? 's' : ''} ago`
}

function truncate(s: string | null, n = 80): string {
  if (!s) return ''
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

async function onRestore(rowId: number) {
  const result = await store.beginRestore(rowId)
  if (result.kind === 'ok') {
    showToast(`Row ${rowId} restored`, 'success')
  } else if (result.kind === 'stale') {
    // Row was already gone from the local cache — nothing actionable.
  } else if (result.kind === 'preview') {
    restoringRowId.value = rowId
    restorePreview.value = result.preview
    restorePreviewOpen.value = true
  } else if (result.kind === 'conflict') {
    showToast(result.message, 'error')
    await store.loadDiscarded()
  } else {
    showToast(result.message, 'error')
  }
}

function onCancelRestore() {
  restorePreviewOpen.value = false
  restorePreview.value = null
  restoringRowId.value = null
}

async function onCommitRestore({ actions }: { actions: StagingRestoreAction[] }) {
  if (restoringRowId.value === null) return
  restoreSubmitting.value = true
  const result = await store.commitRestore(restoringRowId.value, actions)
  restoreSubmitting.value = false
  if (result.kind === 'ok') {
    restorePreviewOpen.value = false
    restorePreview.value = null
    restoringRowId.value = null
    showToast(`Row ${result.row.id} restored`, 'success')
  } else if (result.kind === 'conflict') {
    // Backend sent a fresh preview — update the modal.
    restorePreview.value = result.preview
    showToast(result.message, 'error')
  } else if (result.kind === 'invalid-edit') {
    showToast(result.message, 'error')
  } else if (result.kind === 'stale') {
    // row vanished between preview and commit — nothing actionable
  } else if (result.kind === 'preview') {
    // re-show the modal with the latest preview (defensive; normally unreachable from commitRestore)
    restorePreview.value = result.preview
  } else {
    showToast(result.message, 'error')
  }
}
</script>

<template>
  <SlideOverPanel
    :open="discardedDrawerOpen"
    width="xl"
    ariaLabel="Discarded staging rows"
    @close="store.closeDiscardedDrawer()"
  >
    <template #header>
      <div class="flex items-center justify-between w-full">
        <h2 class="text-base font-semibold text-slate-800 dark:text-slate-100">
          Discarded
          <span v-if="discardedTotal > 0" class="ml-1 tabular-nums">({{ discardedTotal }})</span>
        </h2>
        <button
          @click="store.closeDiscardedDrawer()"
          aria-label="Close discarded rows drawer"
          data-testid="discarded-drawer-close-btn"
          class="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring focus-ring-raised"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-slate-500" fill="none"
               viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </template>

    <div v-if="discardedLoading && discardedRows.length === 0"
         class="text-slate-500 dark:text-slate-400 text-sm">
      Loading…
    </div>

    <template v-else>
      <SearchPaginatorBar
        :search-query="debouncedDiscardedSearch"
        :page-start="discardedPageStart"
        :page-end="discardedPageEnd"
        :total="discardedTotal"
        :has-prev="discardedHasPrev"
        :has-next="discardedHasNext"
        :loading="discardedLoading"
        placeholder="Search row #, error, batch…"
        @update:search-query="debouncedDiscardedSearch = $event"
        @prev="store.prevDiscardedPage()"
        @next="store.nextDiscardedPage()"
      />

      <div v-if="discardedTotal === 0"
           class="text-slate-500 dark:text-slate-400 text-sm py-8 text-center">
        No discarded rows.
      </div>

      <div v-else class="space-y-0">
        <table class="w-full text-sm">
          <thead class="bg-slate-50 dark:bg-slate-700 text-left text-xs uppercase tracking-wide text-slate-600 dark:text-slate-300">
            <tr>
              <th class="px-4 py-2 w-16">Row</th>
              <th class="px-4 py-2 w-20">Batch</th>
              <th class="px-4 py-2">Error</th>
              <th class="px-4 py-2 w-36">Discarded</th>
              <th class="px-4 py-2 w-24"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in discardedRows"
              :key="row.id"
              class="border-t border-slate-100 dark:border-slate-700"
              :data-testid="`discarded-row-${row.id}`"
            >
              <td class="px-4 py-3 text-slate-500 dark:text-slate-400 font-mono">
                {{ row.source_row_number }}
              </td>
              <td class="px-4 py-3 text-slate-600 dark:text-slate-300">
                {{ row.batch_id }}
              </td>
              <td class="px-4 py-3 text-slate-700 dark:text-slate-200">
                <span :title="row.processing_error ?? ''">
                  {{ truncate(row.processing_error) }}
                </span>
              </td>
              <td class="px-4 py-3 text-slate-500 dark:text-slate-400 text-xs"
                  :title="row.discarded_at ?? ''">
                {{ formatRelative(row.discarded_at) }}
              </td>
              <td class="px-4 py-3">
                <button
                  type="button"
                  :data-testid="`restore-btn-${row.id}`"
                  class="text-xs font-medium px-2 py-1 rounded
                         bg-teal-50 hover:bg-teal-100 text-teal-700
                         ring-1 ring-teal-300 transition-colors"
                  @click="onRestore(row.id)"
                >
                  Restore
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </SlideOverPanel>

  <RestoreConflictPreviewModal
    :open="restorePreviewOpen"
    :preview="restorePreview"
    :submitting="restoreSubmitting"
    @cancel="onCancelRestore"
    @restore="onCommitRestore"
  />
</template>
