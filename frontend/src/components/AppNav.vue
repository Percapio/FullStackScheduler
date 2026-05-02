<script setup lang="ts">
import { computed, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { usePstClock } from '@/composables/usePstClock'
import { useFontSize } from '@/composables/useFontSize'
import { useDebouncedRef } from '@/composables/useDebouncedRef'
import { useHistoryStore } from '@/stores/history'
import { ref } from 'vue'
import UploadModal from '@/components/UploadModal.vue'
import { useToast } from '@/composables/useToast'

const uploadOpen = ref(false)
const { show: pushToast } = useToast()

function onUploadSuccess(payload: { batch_id: number; rows_total: number; rows_inserted: number; rows_updated: number; rows_errored: number }) {
  uploadOpen.value = false
  pushToast(
    `Batch #${payload.batch_id}: ${payload.rows_inserted} inserted, ${payload.rows_updated} updated, ${payload.rows_errored} errored.`,
    payload.rows_errored > 0 ? 'error' : 'success'
  )
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
      <span class="font-semibold text-slate-800 dark:text-slate-100 mr-8">Scheduler</span>
      <ul class="flex gap-1">
        <li v-for="tab in tabs" :key="tab.name">
          <RouterLink
            :to="tab.path"
            class="px-4 py-2 rounded-md text-sm font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring"
            active-class="!bg-slate-900 !text-white dark:!bg-slate-100 dark:!text-slate-900"
          >
            {{ tab.label }}
          </RouterLink>
        </li>
      </ul>
       <div class="ml-auto flex items-center gap-3">
         <button type="button"
                 class="px-3 py-1.5 rounded-md bg-sky-600 text-white text-sm font-medium hover:bg-sky-500 focus-ring transition-colors duration-100"
                 @click="uploadOpen = true">
           Upload Schedule
         </button>
         <div class="flex items-center gap-1">

          <button @click="decrease" :disabled="!canDecrease"
            class="px-1.5 py-0.5 rounded text-sm font-medium text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-600 disabled:opacity-30 disabled:cursor-not-allowed transition-colors duration-100 ease-out focus-ring">A−</button>
          <button @click="increase" :disabled="!canIncrease"
            class="px-1.5 py-0.5 rounded text-sm font-medium text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-600 disabled:opacity-30 disabled:cursor-not-allowed transition-colors duration-100 ease-out focus-ring">A+</button>
        </div>
        <span class="text-lg font-mono text-slate-500 dark:text-slate-400 tabular-nums"
              :title="'Pacific Time (America/Los_Angeles)'"
        >{{ time }}</span>
      </div>
    </div>

    <div v-if="isHistory"
         class="max-w-7xl mx-auto px-6 h-12 flex items-center gap-4 border-t border-slate-200 dark:border-slate-700">
      <div class="relative flex-1 max-w-md">
        <input v-model="debouncedQuery"
               type="search"
               placeholder="Search B#, customer, notes…"
               aria-label="Search history"
               class="w-full px-3 py-1.5 pr-8 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-sm text-slate-800 dark:text-slate-100 focus-ring" />
        <span v-if="loading"
              class="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-slate-400">…</span>
      </div>

      <div class="flex gap-1">
        <button @click="scrollTop" aria-label="Scroll to top"
                class="px-3 py-1 rounded text-sm text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring">
          ↑ Top
        </button>
        <button @click="scrollBottom" aria-label="Scroll to bottom"
                class="px-3 py-1 rounded text-sm text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring">
          ↓ Bottom
        </button>
      </div>

      <span class="ml-auto text-sm text-slate-500 dark:text-slate-400 tabular-nums">
        Showing {{ pageStart }}–{{ pageEnd }} of {{ total }}
      </span>
      <div class="flex gap-2">
        <button @click="historyStore.prev()" :disabled="!hasPrev || loading"
                class="px-3 py-1 rounded border border-slate-300 dark:border-slate-600 text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring">
          ← Prev
        </button>
        <button @click="historyStore.next()" :disabled="!hasNext || loading"
                class="px-3 py-1 rounded border border-slate-300 dark:border-slate-600 text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring">
          Next →
        </button>
      </div>
    </div>
     <UploadModal :open="uploadOpen" @close="uploadOpen = false" @success="onUploadSuccess" />
   </nav>

</template>
