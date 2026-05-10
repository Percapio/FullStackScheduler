<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useShippingStore } from '@/stores/shipping'
import { useShippingSort, type FlatSortKey, type SortState } from '@/composables/useShippingSort'
import { useFontSize } from '@/composables/useFontSize'
import { useJobFormatters } from '@/composables/useJobFormatters'
import SortHeader from '@/components/SortHeader.vue'
import EyeIcon from '@/components/EyeIcon.vue'
import InspectDrawer from '@/components/InspectDrawer.vue'
import DiscardedJobsDrawer from '@/components/DiscardedJobsDrawer.vue'

const store = useShippingStore()
const { jobs, loading, error, inspected, discardedTotal } = storeToRefs(store)
const { carrierBadge, formatDate, buildLabel, renderNotes } = useJobFormatters()

const sort = ref<SortState>({ key: 'resolved_ship_date', direction: 'asc' })
const { sorted } = useShippingSort(jobs, sort)
const { fontClass } = useFontSize()

function cycleSort(key: FlatSortKey) {
  if (sort.value.key === key) {
    sort.value = { key, direction: sort.value.direction === 'asc' ? 'desc' : 'asc' }
  } else {
    sort.value = { key, direction: 'asc' }
  }
}

onMounted(() => {
  store.load()
  store.loadDiscardedJobs()
})

async function discardJob(jobId: number, reason: string): Promise<void> {
  await store.discardJob(jobId, reason)
}
</script>

<template>
  <section>
    <header class="flex items-baseline justify-between mb-4">
      <span class="text-sm text-slate-500 dark:text-slate-400">
        <template v-if="loading">Loading…</template>
        <template v-else>{{ sorted.length }} open jobs</template>
      </span>
      <button
        data-testid="discarded-jobs-pill-btn"
        class="text-xs font-medium px-3 py-1 rounded-full border border-slate-300 dark:border-slate-600
               text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
        @click="store.openDiscardedJobsDrawer()"
      >
        Discarded<span v-if="discardedTotal > 0" class="ml-1 tabular-nums">({{ discardedTotal }})</span>
      </button>
    </header>

    <div v-if="error && !loading"
         class="mb-4 rounded-lg border border-warn-300 dark:border-warn-700 bg-warn-50 dark:bg-warn-800/20 px-4 py-3 text-warn-800 dark:text-warn-200 text-sm flex items-center justify-between">
      <span>{{ error }}</span>
      <button @click="store.load()"
              class="ml-4 rounded px-3 py-1 text-xs font-medium bg-warn-200 dark:bg-warn-700 hover:bg-warn-300 dark:hover:bg-warn-600 transition-colors">
        Retry
      </button>
    </div>

    <div v-if="!loading && !error && jobs.length === 0"
         class="bg-white dark:bg-slate-800 rounded-lg p-12 text-center shadow-[var(--shadow-hover)] text-slate-500 dark:text-slate-400">
      No open jobs.
    </div>

    <div v-else-if="!error || jobs.length > 0"
         class="bg-white dark:bg-slate-800 rounded-lg overflow-x-auto shadow-[var(--shadow-elevated)]">
      <table class="w-full">
        <thead class="bg-slate-100 dark:bg-slate-700 text-left text-xs uppercase tracking-wide text-slate-600 dark:text-slate-300">
          <tr>
            <SortHeader label="Ship Date"    sort-key="resolved_ship_date" :current="sort" @sort="cycleSort" />
            <SortHeader label="Job"          sort-key="part_number"        :current="sort" @sort="cycleSort" />
            <SortHeader label="Qty"          sort-key="quantity"           :current="sort" @sort="cycleSort" />
            <SortHeader label="ROWC/RONC"    sort-key="build_type"        :current="sort" @sort="cycleSort" />
            <SortHeader label="Mfg Notes"    sort-key="base_mfg_notes"    :current="sort" @sort="cycleSort" />
            <SortHeader label="Ship Method"  sort-key="ship_method"       :current="sort" @sort="cycleSort" />
            <SortHeader label="Customer"     sort-key="customer_name"     :current="sort" @sort="cycleSort" />
            <th class="px-3 py-2 w-10"></th>
          </tr>
        </thead>
        <tbody :class="fontClass" class="divide-y divide-slate-200 dark:divide-slate-700">
          <tr v-for="job in sorted" :key="job.id"
              class="transition-colors
                     odd:bg-white even:bg-slate-100
                     dark:odd:bg-slate-800 dark:even:bg-slate-820
                     odd:hover:bg-slate-50 even:hover:bg-slate-200
                     dark:odd:hover:bg-slate-700 dark:even:hover:bg-slate-840">
            <td class="px-3 py-2 tabular-nums whitespace-nowrap text-slate-700 dark:text-slate-300"
                :title="job.ship_date_text ?? undefined">
              {{ formatDate(job.resolved_ship_date) }}
            </td>
            <td class="px-3 py-2 font-semibold text-slate-800 dark:text-slate-100">
              {{ job.assembly.part_number }}
            </td>
            <td class="px-3 py-2 tabular-nums text-right text-slate-700 dark:text-slate-300">
              {{ job.quantity }}
            </td>
            <td class="px-3 py-2 font-medium tracking-wider text-slate-700 dark:text-slate-300">
              {{ buildLabel(job.build_type) }}
            </td>
            <td class="px-3 py-2 whitespace-normal text-slate-500 dark:text-slate-400 prose prose-sm dark:prose-invert max-w-none"
                v-html="renderNotes(job.assembly.base_mfg_notes)"></td>
            <td class="px-3 py-2">
              <template v-if="carrierBadge(job.ship_method)">
                <span :class="['inline-flex items-center px-2 py-0.5 rounded-full border',
                               carrierBadge(job.ship_method)!.class]">
                  {{ carrierBadge(job.ship_method)!.text }}
                </span>
              </template>
            </td>
            <td class="px-3 py-2 text-slate-700 dark:text-slate-300">
              {{ job.customer.name }}
            </td>
            <td class="px-3 py-2 text-right">
              <button
                :aria-label="`Inspect job ${job.id}`"
                class="p-1 rounded hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors"
                @click.stop="store.inspect(job)"
              >
                <EyeIcon />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <InspectDrawer
      :row="inspected"
      :can-discard="true"
      :discard-impl="discardJob"
      @close="store.closeInspect()"
    />
    <DiscardedJobsDrawer />
  </section>
</template>
