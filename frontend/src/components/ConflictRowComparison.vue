<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useStagingStore } from '@/stores/staging'
import type { ConflictGroup } from '@/api/staging'

const props = defineProps<{
  group: ConflictGroup | undefined
}>()

const emit = defineEmits<{
  (e: 'resolved', rowId: number): void
  (e: 'discarded', rowId: number): void
}>()

const store = useStagingStore()
const { reconciledConflictGroups, groupBusy } = storeToRefs(store)

/** The reconciled (server-truth) group to gate disabled state. */
const reconciledGroup = computed(() => {
  if (!props.group) return undefined
  return reconciledConflictGroups.value.find(
    g => g.batch_id === props.group!.batch_id && g.group_key === props.group!.group_key,
  )
})

const busyKey = computed(() =>
  props.group ? `${props.group.batch_id}:${props.group.group_key}` : '',
)

/** "Keep this one" is disabled when the conflict is still active (reconciled
 *  population >= 2) or when any action for this group is in flight.  §3.5.4 */
function keepDisabled(): boolean {
  const rg = reconciledGroup.value
  if (!rg) return true
  return rg.rows.length >= 2 || (groupBusy.value.get(busyKey.value) ?? false)
}

async function onKeep(rowId: number): Promise<void> {
  if (keepDisabled()) return
  groupBusy.value.set(busyKey.value, true)
  try {
    // "Keep this one" re-submits the row unchanged; the backend re-transforms it.
    const result = await store.correct(rowId, {})
    if (result.kind === 'ok') {
      emit('resolved', rowId)
      await store.loadConflicts()
    }
  } finally {
    groupBusy.value.set(busyKey.value, false)
  }
}

async function onDiscard(rowId: number): Promise<void> {
  if (groupBusy.value.get(busyKey.value)) return
  groupBusy.value.set(busyKey.value, true)
  try {
    const result = await store.discardRow(rowId)
    if (result.kind === 'ok') {
      emit('discarded', rowId)
      await store.loadConflicts()
    }
  } finally {
    groupBusy.value.set(busyKey.value, false)
  }
}

/** Raw fields to render side-by-side. */
const RAW_FIELDS = [
  'raw_job', 'raw_qty', 'raw_ship_date', 'raw_shipped',
  'raw_customer', 'raw_sales_p', 'raw_prog', 'raw_mfg_notes',
  'raw_pcb_notes', 'raw_kit_notes', 'raw_scheduling_notes',
  'raw_smt_lines', 'raw_smt_plcmnts', 'raw_ship_method',
  'raw_doc_rel', 'raw_kit_rel', 'raw_code', 'raw_bom_compare_photos',
  'raw_line_1', 'raw_line_2', 'raw_line_3',
] as const
</script>

<template>
  <div v-if="!group" class="text-slate-500 dark:text-slate-400 text-sm p-4">
    Select a conflict group to compare.
  </div>

  <div v-else class="overflow-y-auto">
    <div class="px-4 py-3 border-b border-slate-200 dark:border-slate-700">
      <h2 class="text-base font-semibold text-slate-800 dark:text-slate-100">
        Conflict: {{ group.group_key }}
      </h2>
      <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
        Batch {{ group.batch_id }} · {{ group.rows.length }} duplicates
      </p>
    </div>

    <!-- Side-by-side comparison table -->
    <div class="overflow-x-auto">
      <table class="w-full text-xs border-collapse">
        <thead>
          <tr class="bg-slate-50 dark:bg-slate-800">
            <th class="px-3 py-2 text-left text-slate-500 font-medium w-32 sticky left-0 bg-slate-50 dark:bg-slate-800">
              Field
            </th>
            <th
              v-for="row in group.rows"
              :key="row.id"
              class="px-3 py-2 text-left font-medium text-slate-700 dark:text-slate-300 min-w-40"
            >
              Row {{ row.source_row_number }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="field in RAW_FIELDS"
            :key="field"
            class="border-t border-slate-100 dark:border-slate-700/50"
          >
            <td class="px-3 py-1.5 text-slate-400 font-mono sticky left-0 bg-white dark:bg-slate-900">
              {{ field.replace('raw_', '') }}
            </td>
            <td
              v-for="row in group.rows"
              :key="row.id"
              class="px-3 py-1.5 font-mono text-slate-700 dark:text-slate-300"
              :class="{ 'bg-rose-50 dark:bg-rose-900/20': row.highlight_fields?.includes(field) }"
            >
              {{ (row as any)[field] ?? '—' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Per-row action footers -->
    <div class="divide-y divide-slate-200 dark:divide-slate-700 border-t border-slate-200 dark:border-slate-700 mt-2">
      <div
        v-for="row in group.rows"
        :key="row.id"
        class="flex items-center justify-between gap-3 px-4 py-3 text-sm"
      >
        <span class="text-slate-600 dark:text-slate-400 text-xs">Row {{ row.source_row_number }}</span>
        <div class="flex gap-2">
          <button
            type="button"
            :disabled="keepDisabled()"
            :title="keepDisabled() ? 'Resolve another row first to enable this' : 'Keep this row\'s data'"
            class="px-3 py-1.5 rounded-md text-xs font-medium border border-slate-300 dark:border-slate-600
                   hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors
                   disabled:opacity-40 disabled:cursor-not-allowed"
            data-testid="keep-this-one-btn"
            @click="onKeep(row.id)"
          >
            Keep this one
          </button>
          <button
            type="button"
            :disabled="groupBusy.get(busyKey) ?? false"
            class="px-3 py-1.5 rounded-md text-xs font-medium text-rose-700
                   hover:bg-rose-50 dark:hover:bg-rose-900/30 transition-colors
                   disabled:opacity-40 disabled:cursor-not-allowed"
            data-testid="conflict-discard-btn"
            @click="onDiscard(row.id)"
          >
            Discard
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
