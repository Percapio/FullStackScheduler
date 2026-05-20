<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
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

onMounted(() => {
  store.loadErrored()
  store.loadDiscarded()
  store.loadConflicts()
})

// Debounce wiring: the bar emits raw input; we debounce before calling setErroredSearch.
const debouncedSearchQuery = useDebouncedRef(erroredSearchQuery.value, 300)
watch(debouncedSearchQuery, (q) => store.setErroredSearch(q))

function truncate(s: string | null, n = 80) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n - 1) + '…' : s
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
