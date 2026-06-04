<script setup lang="ts">
import { computed } from 'vue'
import type { LineageState } from '@/stores/history'
import type { JobReadExpanded } from '@/api/history'
import { useJobFormatters } from '@/composables/useJobFormatters'
import EyeIcon from '@/components/EyeIcon.vue'

const props = defineProps<{
  jobId: number
  state: LineageState | undefined
}>()

const emit = defineEmits<{
  retry: []
  inspect: [JobReadExpanded]
}>()

const { formatDate, buildLabel, identitySuffix, renderNotes, carrierBadge } = useJobFormatters()

const isAnchor = (job: JobReadExpanded) => job.id === props.jobId

const sortedRows = computed(() => {
  const rows = props.state?.status === 'ready' ? props.state.rows : []
  return [...rows].sort((a, b) => {
    if (!a.shipped_at && !b.shipped_at) return 0
    if (!a.shipped_at) return 1
    if (!b.shipped_at) return -1
    return b.shipped_at.localeCompare(a.shipped_at)
  })
})

const statusClasses: Record<string, string> = {
  shipped: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200',
  planned: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  on_hold: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200',
}
</script>

<template>
  <div class="bg-slate-100 dark:bg-slate-900/70 pl-8 shadow-[inset_0_1px_2px_rgba(15,23,42,0.08),inset_0_-1px_1px_rgba(15,23,42,0.04)] dark:shadow-[inset_0_1px_3px_rgba(0,0,0,0.5)]">
    <!-- Loading skeleton -->
    <table v-if="!state || state.status === 'loading'"
           class="w-full text-sm"
           aria-busy="true" aria-label="Loading lineage data">
      <thead class="bg-slate-100/60 dark:bg-slate-900/60 text-left text-xs uppercase tracking-wider font-medium text-slate-500 dark:text-slate-400">
        <tr>
          <th class="px-3 py-2">Job</th>
          <th class="px-3 py-2">Ship Date</th>
          <th class="px-3 py-2 text-right">Qty</th>
          <th class="px-3 py-2">ROWC/RONC</th>
          <th class="px-3 py-2">Mfg Notes</th>
          <th class="px-3 py-2">Ship Method</th>
          <th class="px-3 py-2">Customer</th>
          <th class="px-3 py-2">Status</th>
          <th class="px-3 py-2 w-10"></th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-200 dark:divide-slate-700 animate-pulse">
        <tr v-for="i in 3" :key="i" class="bg-white dark:bg-slate-800">
          <td class="px-3 py-2"><div class="h-3 w-24 rounded bg-slate-200 dark:bg-slate-700" /></td>
          <td class="px-3 py-2"><div class="h-3 w-16 rounded bg-slate-200 dark:bg-slate-700" /></td>
          <td class="px-3 py-2 text-right"><div class="h-3 w-8 rounded bg-slate-200 dark:bg-slate-700 ml-auto" /></td>
          <td class="px-3 py-2"><div class="h-3 w-12 rounded bg-slate-200 dark:bg-slate-700" /></td>
          <td class="px-3 py-2"><div class="h-3 w-32 rounded bg-slate-200 dark:bg-slate-700" /></td>
          <td class="px-3 py-2"><div class="h-5 w-20 rounded-full bg-slate-200 dark:bg-slate-700" /></td>
          <td class="px-3 py-2"><div class="h-3 w-24 rounded bg-slate-200 dark:bg-slate-700" /></td>
          <td class="px-3 py-2"><div class="h-5 w-16 rounded bg-slate-200 dark:bg-slate-700" /></td>
          <td class="px-3 py-2 w-10"></td>
        </tr>
      </tbody>
    </table>

    <!-- Error -->
    <div v-else-if="state.status === 'error'"
         class="py-4 px-3 text-sm text-amber-800 dark:text-amber-200 flex items-center gap-3">
      <span>{{ state.message }}</span>
      <button @click="emit('retry')"
              class="rounded px-3 py-1 text-xs font-medium bg-amber-200 dark:bg-amber-700 hover:bg-amber-300 dark:hover:bg-amber-600 transition-colors duration-100 ease-out">
        Retry
      </button>
    </div>

    <!-- Ready -->
    <table v-else-if="state.status === 'ready'" class="w-full text-sm">
      <thead class="bg-slate-100/60 dark:bg-slate-900/60 text-left text-xs uppercase tracking-wider font-medium text-slate-500 dark:text-slate-400">
        <tr>
          <th class="px-3 py-2">Job</th>
          <th class="px-3 py-2">Ship Date</th>
          <th class="px-3 py-2 text-right">Qty</th>
          <th class="px-3 py-2">ROWC/RONC</th>
          <th class="px-3 py-2">Mfg Notes</th>
          <th class="px-3 py-2">Ship Method</th>
          <th class="px-3 py-2">Customer</th>
          <th class="px-3 py-2">Status</th>
          <th class="px-3 py-2 w-10"></th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-200 dark:divide-slate-700">
        <tr v-for="job in sortedRows" :key="job.id"
            :class="isAnchor(job) ? 'lineage-anchor' : ''">
          <td class="px-3 py-2 font-semibold text-slate-800 dark:text-slate-100">
            {{ job.assembly.part_number }}<span v-if="identitySuffix(job)"
              class="ml-1 text-slate-400 dark:text-slate-500 text-xs font-normal">{{ identitySuffix(job) }}</span>
          </td>
          <td class="px-3 py-2 tabular-nums whitespace-nowrap text-slate-700 dark:text-slate-300">
            {{ formatDate(job.shipped_at) }}
          </td>
          <td class="px-3 py-2 tabular-nums text-right text-slate-700 dark:text-slate-300">
            {{ job.quantity }}
          </td>
          <td class="px-3 py-2 font-medium tracking-wider text-slate-700 dark:text-slate-300">
            {{ buildLabel(job.build_type) }}
          </td>
          <td class="px-3 py-2 whitespace-normal text-slate-500 dark:text-slate-400 prose prose-sm dark:prose-invert max-w-none"
              v-html="renderNotes(job.assembly?.base_mfg_notes)" />
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
          <td class="px-3 py-2">
            <span :class="[
              'inline-flex items-center px-2 py-0.5 rounded text-xs font-medium uppercase tracking-wider',
              statusClasses[job.status] ?? 'bg-slate-100 text-slate-600',
            ]">{{ job.status }}</span>
          </td>
          <td class="px-3 py-2 text-right">
            <button @click.stop="emit('inspect', job)"
                    class="p-1 rounded transition-[background-color,transform] duration-100 ease-out hover:bg-slate-200 dark:hover:bg-slate-700 active:scale-95 focus-ring"
                    :aria-label="`Inspect job ${job.id}`">
              <EyeIcon class="w-5 h-5 text-slate-500 dark:text-slate-400 transition-colors duration-100" />
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
