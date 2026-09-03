<script setup lang="ts">
import { computed, watch, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { usePstClock } from '@/composables/usePstClock'
import { useFontSize } from '@/composables/useFontSize'
import { useDebouncedRef } from '@/composables/useDebouncedRef'
import { useHistoryStore } from '@/stores/history'
import { ref } from 'vue'
import UploadModal from '@/components/UploadModal.vue'
import SettingsModal from '@/components/SettingsModal.vue'
import SearchPaginatorBar from '@/components/SearchPaginatorBar.vue'
import HistoryExportModal from '@/components/HistoryExportModal.vue'
import { useToast } from '@/composables/useToast'
import { fetchAwaitingReview } from '@/api/review'

const props = defineProps<{
  wsStatus?: 'Connecting' | 'Live' | 'Backoff'
}>()

const uploadOpen = ref(false)
const settingsOpen = ref(false)
const exportModalIsOpen = ref(false)
const { show: pushToast } = useToast()
const inFlightCount = ref(0)

async function refreshInFlightCount() {
  try {
    const batches = await fetchAwaitingReview()
    inFlightCount.value = batches.length
  } catch {
    // non-blocking; badge stays at last known value
  }
}

onMounted(refreshInFlightCount)

function onUploadSuccess(payload: { batch_id: number; rows_total?: number; rows_inserted?: number; rows_updated?: number; rows_errored?: number }) {
  uploadOpen.value = false
  refreshInFlightCount()
  pushToast(
    `Batch #${payload.batch_id}: ${payload.rows_inserted ?? '?'} inserted, ${payload.rows_updated ?? '?'} updated, ${payload.rows_errored ?? '?'} errored.`,
    (payload.rows_errored ?? 0) > 0 ? 'error' : 'success'
  )
}

function onUploadClose() {
  uploadOpen.value = false
  refreshInFlightCount()
}

const router = useRouter()
const route  = useRoute()
const tabs = computed(() =>
  router.options.routes
    .filter(r => r.name && r.meta?.label)
    .map(r => ({ name: r.name as string, path: r.path, label: r.meta!.label as string })),
)
const { time } = usePstClock()
const { canDecrease, canIncrease, decrease, increase } = useFontSize()

const isHistory = computed(() => route.name === 'history')

const historyStore = useHistoryStore()
const { pageStart, pageEnd, total, hasPrev, hasNext, loading } = storeToRefs(historyStore)

const debouncedQuery = useDebouncedRef(historyStore.searchQuery, 300)
watch(debouncedQuery, (q) => historyStore.setSearch(q))

function scrollTop()    { window.scrollTo({ top: 0, behavior: 'smooth' }) }
function scrollBottom() { window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }) }
</script>

<template>
  <nav class="fixed top-0 inset-x-0 z-50 bg-slate-50/95 dark:bg-slate-900/95 backdrop-blur-sm shadow-[var(--shadow-hover)]">
    <div class="max-w-7xl mx-auto px-6 flex items-center h-14">
      <ul class="flex gap-1">
        <li v-for="tab in tabs" :key="tab.name">
          <RouterLink
            :to="tab.path"
            class="px-4 py-2 rounded-md rounded-b-none border-b-2 border-transparent text-sm font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring"
            active-class="!border-accent-500 !text-accent-600 dark:!text-accent-300"
          >
            <template v-if="tab.name === 'reconciliation' && inFlightCount > 0">
              {{ tab.label }} <span class="ml-1 inline-flex items-center justify-center px-1.5 py-0.5 text-xs font-semibold rounded-full bg-amber-400 text-amber-900">{{ inFlightCount }}</span>
            </template>
            <template v-else>{{ tab.label }}</template>
          </RouterLink>
        </li>
      </ul>
       <div class="ml-auto flex items-center gap-3">
         <button type="button"
                 class="px-3 py-1.5 rounded-md bg-sky-600 text-white text-sm font-medium hover:bg-accent-700 focus-ring transition-colors duration-100"
                 @click="uploadOpen = true">
           Upload Schedule
         </button>
         <button type="button"
                 class="px-3 py-1.5 rounded-md text-slate-600 dark:text-slate-300 text-sm font-medium hover:bg-slate-200 dark:hover:bg-slate-700 focus-ring transition-colors duration-100"
                 @click="settingsOpen = true">
           Settings
         </button>
         <div class="flex items-center gap-1">

          <button @click="decrease" :disabled="!canDecrease"
            class="px-1.5 py-0.5 rounded text-sm font-medium text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-600 disabled:opacity-30 disabled:cursor-not-allowed transition-colors duration-100 ease-out focus-ring">A−</button>
          <button @click="increase" :disabled="!canIncrease"
            class="px-1.5 py-0.5 rounded text-sm font-medium text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-600 disabled:opacity-30 disabled:cursor-not-allowed transition-colors duration-100 ease-out focus-ring">A+</button>
        </div>
        <div class="flex items-center gap-2">
          <div v-if="wsStatus"
               :class="[
                 'w-2.5 h-2.5 rounded-full shrink-0',
                 {
                   'bg-green-500': wsStatus === 'Live',
                   'bg-amber-400': wsStatus === 'Connecting',
                   'bg-red-500': wsStatus === 'Backoff',
                 }
               ]"
               :title="wsStatus === 'Live' ? 'Connected to live updates' : (wsStatus === 'Connecting' ? 'Connecting to live updates...' : 'Disconnected from live updates')"
          ></div>
          <span class="text-lg font-mono text-slate-500 dark:text-slate-400 tabular-nums"
                :title="'Pacific Time (America/Los_Angeles)'"
          >{{ time }}</span>
        </div>
      </div>
    </div>

    <div v-if="isHistory"
         class="max-w-7xl mx-auto px-6 border-t border-slate-200 dark:border-slate-700">
      <SearchPaginatorBar
        v-model:searchQuery="debouncedQuery"
        :page-start="pageStart"
        :page-end="pageEnd"
        :total="total"
        :has-prev="hasPrev"
        :has-next="hasNext"
        :loading="loading"
        :show-scroll-controls="true"
        placeholder="Search B#, customer, notes…"
        @prev="historyStore.prev()"
        @next="historyStore.next()"
        @scroll-top="scrollTop"
        @scroll-bottom="scrollBottom"
      >
        <template #actions>
          <button type="button" @click="exportModalIsOpen = true"
            class="px-3 py-1 rounded border border-slate-300 dark:border-slate-600 text-sm font-medium hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring">
            CSV Export
          </button>
        </template>
      </SearchPaginatorBar>
    </div>
     <UploadModal :open="uploadOpen" @close="onUploadClose" @success="onUploadSuccess" />
     <SettingsModal :open="settingsOpen" @close="settingsOpen = false" />
     <HistoryExportModal :is-open="exportModalIsOpen" :search-query="historyStore.searchQuery" :total-rows="historyStore.total" @close="exportModalIsOpen = false" />
   </nav>

</template>
