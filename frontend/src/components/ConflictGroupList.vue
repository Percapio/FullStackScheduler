<script setup lang="ts">
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useStagingStore } from '@/stores/staging'

/** Max conflict cards expanded before the "Show N more" collapse toggle. */
const MAX_EXPANDED_CONFLICT_CARDS = 3

const store = useStagingStore()
const { conflictGroups, conflictsLoading } = storeToRefs(store)

const showAll = ref(false)

const visibleGroups = computed(() =>
  showAll.value
    ? conflictGroups.value
    : conflictGroups.value.slice(0, MAX_EXPANDED_CONFLICT_CARDS),
)
const hiddenCount = computed(() =>
  Math.max(0, conflictGroups.value.length - MAX_EXPANDED_CONFLICT_CARDS),
)

/** Parse the display label from a group_key like "128764|new||". */
function labelFromKey(groupKey: string): string {
  const [pn, buildType] = groupKey.split('|')
  return `P/N ${pn} · ${buildType.toUpperCase()}`
}

/** Collect source_row_numbers from a group's rows. */
function rowNumbers(group: typeof conflictGroups.value[0]): string {
  return group.rows.map(r => r.source_row_number).sort((a, b) => a - b).join(', ')
}
</script>

<template>
  <div v-if="conflictsLoading" class="text-sm text-slate-500 dark:text-slate-400 mb-4">
    Loading conflicts…
  </div>

  <div v-else-if="conflictGroups.length > 0" class="mb-6 space-y-2">
    <h3 class="text-xs font-semibold uppercase tracking-wide text-rose-600 dark:text-rose-400 mb-2">
      {{ conflictGroups.length }} duplicate conflict{{ conflictGroups.length !== 1 ? 's' : '' }}
    </h3>

    <div
      v-for="group in visibleGroups"
      :key="`${group.batch_id}:${group.group_key}`"
      class="flex items-center justify-between gap-3 bg-rose-50 dark:bg-rose-900/20
             border border-rose-200 dark:border-rose-700/40 rounded-lg px-4 py-3 text-sm"
    >
      <div class="min-w-0">
        <p class="font-medium text-rose-800 dark:text-rose-200 truncate">
          {{ labelFromKey(group.group_key) }}
        </p>
        <p class="text-xs text-rose-600 dark:text-rose-400 mt-0.5">
          rows {{ rowNumbers(group) }} ·
          <span class="tabular-nums font-medium">{{ group.rows.length }} duplicates</span>
        </p>
      </div>
      <button
        type="button"
        class="shrink-0 px-3 py-1.5 rounded-md bg-rose-600 hover:bg-rose-700 text-white text-xs font-medium
               transition-colors focus-ring"
        @click="store.openConflictGroup(group.batch_id, group.group_key)"
      >
        Resolve
      </button>
    </div>

    <button
      v-if="hiddenCount > 0 && !showAll"
      type="button"
      class="text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 underline"
      @click="showAll = true"
    >
      Show {{ hiddenCount }} more conflict{{ hiddenCount !== 1 ? 's' : '' }}
    </button>
  </div>
</template>
