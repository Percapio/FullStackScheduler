<script setup lang="ts">
import { computed, ref } from 'vue'
import SlideOverPanel from './SlideOverPanel.vue'
import ConfirmDiscardJobModal from './ConfirmDiscardJobModal.vue'
import type { JobReadExpanded } from '@/api/history'

const props = defineProps<{
  row: JobReadExpanded | null
  canDiscard?: boolean
}>()
const emit = defineEmits<{
  close: []
  discard: [jobId: number]
}>()

const confirmOpen = ref(false)

function onConfirmDiscard(): void {
  confirmOpen.value = false
  if (props.row) {
    emit('discard', props.row.id)
  }
}

function flatten(value: unknown, prefix = ''): Array<[string, string]> {
  if (value === null || value === undefined) return [[prefix, '—']]
  if (Array.isArray(value)) {
    const allScalar = value.every(v => v === null || typeof v !== 'object')
    if (allScalar) {
      return [[prefix, value.length ? value.join(', ') : '—']]
    }
    return value.flatMap((v, i) => flatten(v, `${prefix}[${i}]`))
  }
  if (typeof value === 'object') {
    return Object.entries(value).flatMap(([k, v]) =>
      flatten(v, prefix ? `${prefix}.${k}` : k),
    )
  }
  return [[prefix, String(value)]]
}

const flatEntries = computed<Array<[string, string]>>(() =>
  props.row ? flatten(props.row) : [],
)
</script>

<template>
  <SlideOverPanel
    :open="row !== null"
    width="xl"
    :ariaLabel="row ? `Job ${row.id} details` : ''"
    @close="emit('close')"
  >
    <template #header>
      <h2 class="text-base font-semibold text-slate-800 dark:text-slate-100">
        Job #{{ row?.id }} — {{ row?.assembly.part_number }}
      </h2>
      <button @click="emit('close')" aria-label="Close inspector"
              data-testid="drawer-close-btn"
              class="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-slate-500" fill="none"
             viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </template>

    <dl class="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm">
      <template v-for="[key, value] in flatEntries" :key="key">
        <dt class="font-mono text-xs text-slate-500 dark:text-slate-400
                   pt-1 whitespace-nowrap">{{ key }}</dt>
        <dd class="text-slate-800 dark:text-slate-200 break-words">
          {{ value }}
        </dd>
      </template>
    </dl>

    <div v-if="canDiscard && row !== null"
         class="mt-6 pt-4 border-t border-slate-200 dark:border-slate-700 flex justify-end">
      <button
        data-testid="inspect-discard-btn"
        type="button"
        class="rounded px-3 py-1.5 text-sm font-medium bg-red-600 hover:bg-red-700 text-white transition-colors"
        @click="confirmOpen = true"
      >
        Discard job
      </button>
    </div>

    <ConfirmDiscardJobModal
      v-if="canDiscard"
      :open="confirmOpen"
      :job="row"
      @confirm="onConfirmDiscard"
      @cancel="confirmOpen = false"
    />
  </SlideOverPanel>
</template>
