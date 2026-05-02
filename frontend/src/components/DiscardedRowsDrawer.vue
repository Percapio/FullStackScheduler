<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { useStagingStore } from '@/stores/staging'
import { useToast } from '@/composables/useToast'
import SlideOverPanel from './SlideOverPanel.vue'

const store = useStagingStore()
const { discardedRows, discardedTotal, discardedLoading, discardedDrawerOpen } =
  storeToRefs(store)
const { show: showToast } = useToast()

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
  const result = await store.restoreRow(rowId)
  if (result.kind === 'ok') {
    showToast(`Row ${rowId} restored`, 'success')
  } else if (result.kind === 'stale') {
    // Row was already gone from the local cache — nothing actionable.
  } else if (result.kind === 'conflict') {
    showToast(result.message, 'error')
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
          class="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-slate-500" fill="none"
               viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </template>

    <div v-if="discardedLoading" class="text-slate-500 dark:text-slate-400 text-sm">
      Loading…
    </div>

    <div v-else-if="discardedTotal === 0"
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

      <!-- Truncation footer: visible only when the server has more rows than
           the first page returned, so the user knows some rows aren't listed. -->
      <p
        v-if="discardedTotal > discardedRows.length"
        data-testid="discarded-truncation-footer"
        class="px-4 py-2 text-xs text-slate-500 dark:text-slate-400 border-t border-slate-100 dark:border-slate-700"
      >
        Showing {{ discardedRows.length }} of {{ discardedTotal }} — older discards not listed.
      </p>
    </div>
  </SlideOverPanel>
</template>
