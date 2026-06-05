<script setup lang="ts">
import { onMounted, watch, ref } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { fetchAwaitingReview, type AwaitingReviewBatch } from '@/api/review'
import { useStagingStore } from '@/stores/staging'
import { useDebouncedRef } from '@/composables/useDebouncedRef'
import ReconciliationSidebar from '@/components/ReconciliationSidebar.vue'
import DiscardedRowsDrawer from '@/components/DiscardedRowsDrawer.vue'
import ConflictGroupList from '@/components/ConflictGroupList.vue'
import SearchPaginatorBar from '@/components/SearchPaginatorBar.vue'
import WarnIcon from '@/components/WarnIcon.vue'

const store = useStagingStore()
const {
  rows, total, loading, discardedTotal,
  erroredSearchQuery,
  erroredHasPrev, erroredHasNext, erroredPageStart, erroredPageEnd,
} = storeToRefs(store)

const router = useRouter()
const batches = ref<AwaitingReviewBatch[]>([])
const batchesLoading = ref(false)
const batchesError = ref<string | null>(null)

async function loadBatches() {
  batchesLoading.value = true
  batchesError.value = null
  try {
    batches.value = await fetchAwaitingReview()
  } catch (err: any) {
    batchesError.value = err?.response?.data?.detail ?? err?.message ?? 'Failed to load in-flight uploads.'
  } finally {
    batchesLoading.value = false
  }
}

onMounted(() => {
  store.loadErrored()
  store.loadDiscarded()
  store.loadConflicts()
  loadBatches()
})

// Debounce wiring: the bar emits raw input; we debounce before calling setErroredSearch.
const debouncedSearchQuery = useDebouncedRef(erroredSearchQuery.value, 300)
watch(debouncedSearchQuery, (q) => store.setErroredSearch(q))

function truncate(s: string | null, n = 80) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}
</script>

<template>
  <section>
    <header class="flex items-baseline justify-between mb-6">
      <button
        type="button"
        data-testid="discarded-pill-btn"
        class="text-xs font-medium px-2 py-1 rounded-full
               bg-stone-100 hover:bg-stone-200 text-stone-700
               ring-1 ring-stone-300"
        @click="store.openDiscardedDrawer()"
      >
        Discarded
        <span v-if="discardedTotal > 0" class="ml-1 tabular-nums">({{ discardedTotal }})</span>
      </button>
    </header>

    <!-- In-Flight Uploads List -->
    <div v-if="batchesError" class="mb-6 px-4 py-3 rounded-lg border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 text-red-800 dark:text-red-200 text-sm">
      {{ batchesError }}
    </div>
    <ul v-else-if="batches.length > 0" class="mb-6 space-y-2">
      <li v-for="b in batches"
          :key="b.batch_id"
          class="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 px-4 py-3 flex items-center justify-between hover:bg-slate-50 dark:hover:bg-slate-750 cursor-pointer transition-colors duration-75 shadow-sm"
          @click="router.push({ name: 'batch-review', params: { batchId: b.batch_id } })">
        <div>
          <p class="font-semibold text-slate-800 dark:text-slate-100 text-sm">
            Batch #{{ b.batch_id }}
            <span v-if="b.source_file" class="ml-2 font-normal text-slate-500 dark:text-slate-400">
              {{ b.source_file }}
            </span>
          </p>
          <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            {{ formatDate(b.created_at) }}
            &middot; {{ b.new_b_count }} new B# part(s)
            &middot; {{ b.pending_row_count }} pending row(s)
          </p>
        </div>
        <span class="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 dark:bg-amber-800/30 dark:text-amber-200 font-medium shrink-0">
          awaiting review
        </span>
      </li>
    </ul>

    <!-- Conflict group cards — rendered above the errored table (§3.5.5) -->
    <ConflictGroupList />

    <!-- Search + paginator bar (Epoch 1) -->
    <SearchPaginatorBar
      data-testid="errored-search-bar"
      :search-query="debouncedSearchQuery"
      :page-start="erroredPageStart"
      :page-end="erroredPageEnd"
      :total="total"
      :has-prev="erroredHasPrev"
      :has-next="erroredHasNext"
      :loading="loading"
      placeholder="Search row #, error, batch…"
      @update:search-query="debouncedSearchQuery = $event"
      @prev="store.prevErroredPage()"
      @next="store.nextErroredPage()"
    />

    <div v-if="!loading && rows.length === 0"
         class="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-12 text-center text-slate-500 dark:text-slate-400">
      No errored rows. Nothing to reconcile.
    </div>

    <div v-else class="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-slate-100 dark:bg-slate-700 text-left text-xs uppercase tracking-wide text-slate-600 dark:text-slate-300">
          <tr>
            <th class="px-4 py-3 w-16">Row</th>
            <th class="px-4 py-3">Processing Error</th>
            <th class="px-4 py-3 w-32">Status</th>
            <th class="px-4 py-3 w-12"></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.id"
            class="border-t border-slate-100 dark:border-slate-700 cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-750 transition-colors"
            @click="store.openError(row.id)"
          >
            <td class="px-4 py-3 text-slate-500 dark:text-slate-400 font-mono">{{ row.source_row_number }}</td>
            <td class="px-4 py-3 text-warn-800 dark:text-warn-200">
              <span class="inline-flex items-center gap-1">
                <WarnIcon class="text-warn-600" />
                {{ truncate(row.processing_error) }}
              </span>
            </td>
            <td class="px-4 py-3">
              <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-warn-50 dark:bg-warn-800/20 text-warn-600 dark:text-warn-200 text-xs font-medium">
                <WarnIcon />
                {{ row.processing_status }}
              </span>
            </td>
            <td class="px-4 py-3 text-slate-400">
              <span aria-hidden="true">↗</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <ReconciliationSidebar />
    <DiscardedRowsDrawer />
  </section>
</template>
