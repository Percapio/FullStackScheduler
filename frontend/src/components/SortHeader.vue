<script setup lang="ts">
import { computed } from 'vue'
import type { FlatSortKey, SortState } from '@/composables/useShippingSort'

const props = defineProps<{ label: string; sortKey: FlatSortKey; current: SortState }>()
const emit = defineEmits<{ sort: [key: FlatSortKey] }>()

const active = computed(() => props.current.key === props.sortKey)
const arrow  = computed(() => !active.value ? '↕' : props.current.direction === 'asc' ? '↑' : '↓')
</script>

<template>
  <th @click="emit('sort', sortKey)"
      class="cursor-pointer select-none px-3 py-2 text-left text-xs font-medium uppercase tracking-wider
             text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white
             transition-colors duration-100 ease-out whitespace-nowrap">
    <span class="inline-flex items-center gap-1">
      <span :class="['inline-block w-3 text-center',
                     active ? 'text-slate-700 dark:text-slate-200' : 'text-slate-400']">
        {{ arrow }}
      </span>
      <span>{{ label }}</span>
    </span>
  </th>
</template>
