<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useHistoryStore } from '@/stores/history'
import { useJobFormatters } from '@/composables/useJobFormatters'
import { useFontSize } from '@/composables/useFontSize'
import EyeIcon from '@/components/EyeIcon.vue'
import LineageAccordion from '@/components/LineageAccordion.vue'
import InspectDrawer from '@/components/InspectDrawer.vue'
// SecondOpsEntryModal is deliberately NOT imported here. History is read-only
// (Decision 10): no Audit button, no EDIT button, and no PUT anywhere in this
// view's import graph.
import SecondOpsCell from '@/components/SecondOpsCell.vue'
import SecondOpsRecordModal from '@/components/SecondOpsRecordModal.vue'
import SecondOpsItemModal from '@/components/SecondOpsItemModal.vue'

const store = useHistoryStore()
const {
  rows, total, loading, error, expanded, lineage, inspected, searchQuery,
  secondOpsRecordJob, secondOpsRecordFetch, secondOpsItem,
} = storeToRefs(store)
const { formatDate, buildLabel, identitySuffix, renderNotes } = useJobFormatters()
const { fontClass } = useFontSize()

const rowClasses =
  'relative z-0 hover:z-10 transition-[transform,box-shadow,background-color] duration-100 ease-out ' +
  '[&>td]:border-b [&>td]:border-slate-200/70 dark:[&>td]:border-slate-700/60 ' +
  'hover:shadow-[var(--shadow-hover)] ' +
  'cursor-pointer ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/70 focus-visible:ring-offset-1 focus-visible:ring-offset-white dark:focus-visible:ring-offset-slate-800'

const activeSearch = computed(() => searchQuery.value.trim())

onMounted(() => store.load())
</script>

<template>
  <section>
    <header class="flex items-baseline justify-between mb-4">
      <span v-if="loading" class="text-sm text-slate-500 dark:text-slate-400">Loading…</span>
    </header>

    <div v-if="error && !loading"
         class="mb-4 rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-800/20 px-4 py-3 text-amber-800 dark:text-amber-200 text-sm flex items-center justify-between">
      <span>{{ error }}</span>
      <button @click="store.load()"
              class="ml-4 rounded px-3 py-1 text-xs font-medium bg-amber-200 dark:bg-amber-700 hover:bg-amber-300 dark:hover:bg-amber-600 transition-colors">
        Retry
      </button>
    </div>

    <div v-if="!loading && !error && total === 0"
         class="bg-white dark:bg-slate-800 rounded-lg p-12 text-center shadow-[var(--shadow-hover)] text-slate-500 dark:text-slate-400">
      <template v-if="activeSearch">No matches for &ldquo;{{ activeSearch }}&rdquo;.</template>
      <template v-else>No shipped jobs.</template>
    </div>

    <div v-else-if="!error || rows.length > 0"
         class="bg-white dark:bg-slate-800 rounded-lg overflow-x-auto shadow-[var(--shadow-elevated)]">
      <table class="w-full border-separate border-spacing-0">
        <thead class="bg-slate-100 dark:bg-slate-700 text-left text-xs uppercase tracking-wider font-medium text-slate-600 dark:text-slate-300">
          <tr>
            <th class="px-3 py-2">Ship Date</th>
            <th class="px-3 py-2">Job</th>
            <th class="px-3 py-2 text-right">Qty</th>
            <th class="px-3 py-2">ROWC/RONC</th>
            <th class="px-3 py-2">Mfg Notes</th>
            <th class="px-3 py-2">Customer</th>
            <th class="px-3 py-2">2nd OPS</th>
            <th class="px-3 py-2 w-10"></th>
          </tr>
        </thead>
        <tbody :class="fontClass">
          <template v-for="(job, i) in rows" :key="job.id">
            <tr :class="[
                  rowClasses,
                  i % 2 === 0
                    ? 'bg-white hover:bg-slate-50 dark:bg-slate-800 dark:hover:bg-slate-700'
                    : 'bg-slate-100 hover:bg-slate-200 dark:bg-slate-820 dark:hover:bg-slate-840'
                ]"
                tabindex="0"
                @click="store.toggleExpand(job.id)"
                @keydown.enter.space.prevent="store.toggleExpand(job.id)">
              <td class="px-3 py-2 tabular-nums whitespace-nowrap text-slate-700 dark:text-slate-300"
                  :title="job.ship_date_text ?? undefined">
                {{ formatDate(job.shipped_at) }}
              </td>
              <td class="px-3 py-2 font-semibold text-slate-800 dark:text-slate-100">
                {{ job.assembly.part_number }}<span v-if="identitySuffix(job)" class="ml-1 text-slate-400 dark:text-slate-500 text-xs font-normal">{{ identitySuffix(job) }}</span>
              </td>
              <td class="px-3 py-2 tabular-nums text-right text-slate-700 dark:text-slate-300">
                {{ job.quantity }}
              </td>
              <td class="px-3 py-2 font-medium tracking-wider text-slate-700 dark:text-slate-300">
                {{ buildLabel(job.build_type) }}
              </td>
              <td class="px-3 py-2 whitespace-normal text-slate-500 dark:text-slate-400 prose prose-sm dark:prose-invert max-w-none"
                  v-html="renderNotes(job.assembly.base_mfg_notes)" />
              <td class="px-3 py-2 text-slate-700 dark:text-slate-300">
                {{ job.customer.name }}
              </td>
              <td class="px-3 py-2 align-top">
                <SecondOpsCell
                  :summary="job.second_ops ?? null"
                  readonly
                  @inspect="store.openSecondOpsItem($event)"
                  @view-all="store.openSecondOpsRecord(job)"
                />
              </td>
              <td class="px-3 py-2 text-right">
                <button @click.stop="store.inspect(job)"
                        class="p-1 rounded transition-[background-color,transform] duration-100 ease-out hover:bg-slate-200 dark:hover:bg-slate-700 active:scale-95 focus-ring"
                        :aria-label="`Inspect job ${job.id}`">
                  <EyeIcon class="w-5 h-5 text-slate-500 dark:text-slate-400 transition-colors duration-100" />
                </button>
              </td>
            </tr>
            <tr v-if="expanded.has(job.id)" :key="`lineage-${job.id}`">
              <td :colspan="8" class="p-0 border-b border-slate-200 dark:border-slate-700">
                <LineageAccordion :job-id="job.id" :state="lineage.get(job.id)"
                                  @retry="store.toggleExpand(job.id); store.toggleExpand(job.id)"
                                  @inspect="store.inspect" />
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <InspectDrawer
      :row="inspected"
      :can-edit="true"
      :can-discard="true"
      :edit-impl="store.editJob"
      :discard-impl="store.discardJob"
      @close="store.closeInspect()"
    />

    <SecondOpsRecordModal
      :job="secondOpsRecordJob"
      :fetch="secondOpsRecordFetch"
      @close="store.closeSecondOpsRecord()"
      @retry="store.retrySecondOpsRecord()"
      @inspect="store.openSecondOpsItem($event)"
    />
    <SecondOpsItemModal
      :fields="secondOpsItem"
      @close="store.closeSecondOpsItem()"
    />
  </section>
</template>
