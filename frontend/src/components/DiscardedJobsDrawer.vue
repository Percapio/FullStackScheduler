<script setup lang="ts">
import { ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useShippingStore } from '@/stores/shipping'
import { useToast } from '@/composables/useToast'
import { useDebouncedRef } from '@/composables/useDebouncedRef'
import SlideOverPanel from './SlideOverPanel.vue'
import SearchPaginatorBar from './SearchPaginatorBar.vue'
import RestoreConflictPreviewModal from './RestoreConflictPreview.vue'
import type { RestoreConflictPreview, StagingRestoreAction } from '@/api/staging'

const store = useShippingStore()
const {
  discardedJobs, discardedTotal, discardedLoading, discardedDrawerOpen,
  discardedHasPrev, discardedHasNext, discardedPageStart, discardedPageEnd,
  discardedSearchQuery,
} = storeToRefs(store)
const { show: showToast } = useToast()

const debouncedSearch = useDebouncedRef(discardedSearchQuery.value, 300)
watch(debouncedSearch, (q) => store.setDiscardedJobsSearch(q))

// Restore-preview modal state
const restorePreviewOpen  = ref(false)
const restorePreview      = ref<RestoreConflictPreview | null>(null)
const restoreSubmitting   = ref(false)
const restoringJobId      = ref<number | null>(null)

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60)  return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60)  return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24)    return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

async function onRestore(jobId: number) {
  const result = await store.beginJobRestore(jobId)
  if (result.kind === 'ok') {
    showToast(`Job ${jobId} restored`, 'success')
  } else if (result.kind === 'stale') {
    // row already gone — nothing actionable
  } else if (result.kind === 'preview') {
    restoringJobId.value = jobId
    restorePreview.value = result.preview
    restorePreviewOpen.value = true
  } else if (result.kind === 'conflict') {
    showToast(result.message, 'error')
    await store.loadDiscardedJobs()
  } else {
    showToast(result.message, 'error')
  }
}

function onCancelRestore() {
  restorePreviewOpen.value = false
  restorePreview.value = null
  restoringJobId.value = null
}

async function onCommitRestore({ actions }: { actions: StagingRestoreAction[] }) {
  if (restoringJobId.value === null) return
  restoreSubmitting.value = true
  const result = await store.commitJobRestore(restoringJobId.value, actions)
  restoreSubmitting.value = false
  if (result.kind === 'ok') {
    restorePreviewOpen.value = false
    restorePreview.value = null
    restoringJobId.value = null
    showToast(`Job ${result.job.id} restored`, 'success')
  } else if (result.kind === 'conflict') {
    restorePreview.value = result.preview
    showToast(result.message, 'error')
  } else if (result.kind === 'invalid-edit') {
    showToast(result.message, 'error')
  } else if (result.kind === 'stale') {
    restorePreviewOpen.value = false
  } else if (result.kind === 'preview') {
    // beginJobRestore may return preview; commitJobRestore should not, but handle defensively.
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
    ariaLabel="Discarded jobs"
    @close="store.closeDiscardedJobsDrawer()"
  >
    <template #header>
      <div class="flex items-center justify-between w-full">
        <h2 class="text-base font-semibold text-slate-800 dark:text-slate-100">
          Discarded jobs
          <span v-if="discardedTotal > 0" class="ml-1 tabular-nums">({{ discardedTotal }})</span>
        </h2>
        <button
          @click="store.closeDiscardedJobsDrawer()"
          aria-label="Close discarded jobs drawer"
          data-testid="discarded-jobs-drawer-close-btn"
          class="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-slate-500" fill="none"
               viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </template>

    <div v-if="discardedLoading && discardedJobs.length === 0"
         class="text-slate-500 dark:text-slate-400 text-sm">
      Loading…
    </div>

    <template v-else>
      <SearchPaginatorBar
        :search-query="debouncedSearch"
        :page-start="discardedPageStart"
        :page-end="discardedPageEnd"
        :total="discardedTotal"
        :has-prev="discardedHasPrev"
        :has-next="discardedHasNext"
        :loading="discardedLoading"
        placeholder="Search part #, customer…"
        @update:search-query="debouncedSearch = $event"
        @prev="store.prevDiscardedJobsPage()"
        @next="store.nextDiscardedJobsPage()"
      />

      <div v-if="discardedTotal === 0"
           class="text-slate-500 dark:text-slate-400 text-sm py-8 text-center">
        No discarded jobs.
      </div>

      <div v-else>
        <table class="w-full text-sm">
          <thead class="bg-slate-50 dark:bg-slate-700 text-left text-xs uppercase tracking-wide text-slate-600 dark:text-slate-300">
            <tr>
              <th class="px-4 py-2">Part #</th>
              <th class="px-4 py-2">Customer</th>
              <th class="px-4 py-2 w-16 text-right">Qty</th>
              <th class="px-4 py-2 w-32">Ship Date</th>
              <th class="px-4 py-2 w-28">Discarded</th>
              <th class="px-4 py-2 w-24"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="job in discardedJobs"
              :key="job.id"
              class="border-t border-slate-100 dark:border-slate-700"
              :data-testid="`discarded-job-${job.id}`"
            >
              <td class="px-4 py-3 font-semibold text-slate-800 dark:text-slate-100">
                {{ job.assembly?.part_number ?? '—' }}
              </td>
              <td class="px-4 py-3 text-slate-600 dark:text-slate-300">
                {{ job.customer?.name ?? '—' }}
              </td>
              <td class="px-4 py-3 tabular-nums text-right text-slate-600 dark:text-slate-300">
                {{ job.quantity }}
              </td>
              <td class="px-4 py-3 text-slate-500 dark:text-slate-400 text-xs tabular-nums">
                {{ formatDate(job.resolved_ship_date) }}
              </td>
              <td class="px-4 py-3 text-slate-500 dark:text-slate-400 text-xs"
                  :title="job.discarded_at ?? ''">
                {{ formatRelative(job.discarded_at) }}
              </td>
              <td class="px-4 py-3">
                <button
                  type="button"
                  :data-testid="`restore-job-btn-${job.id}`"
                  class="text-xs font-medium px-2 py-1 rounded
                         bg-teal-50 hover:bg-teal-100 text-teal-700
                         ring-1 ring-teal-300 transition-colors"
                  @click="onRestore(job.id)"
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
