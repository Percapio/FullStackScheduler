<script setup lang="ts">
import { computed, watchEffect } from 'vue'
import type { JobReadExpanded } from '@/api/history'

const props = defineProps<{ row: JobReadExpanded | null }>()
const emit = defineEmits<{ close: [] }>()

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

watchEffect((onCleanup) => {
  if (!props.row) return
  const handler = (e: KeyboardEvent) => {
    if (e.key === 'Escape') emit('close')
  }
  window.addEventListener('keydown', handler)
  onCleanup(() => window.removeEventListener('keydown', handler))
})
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="row" class="fixed inset-0 z-40" data-testid="drawer-overlay">
        <div class="absolute inset-0 bg-slate-900/40 dark:bg-slate-900/60"
             data-testid="drawer-backdrop"
             @click="emit('close')" />

        <aside class="absolute right-0 top-0 h-full w-full max-w-xl
                      bg-white dark:bg-slate-800 shadow-2xl
                      flex flex-col overflow-hidden"
               role="dialog" :aria-label="`Job ${row.id} details`">
          <header class="flex items-center justify-between px-6 py-4
                         border-b border-slate-200 dark:border-slate-700">
            <h2 class="text-base font-semibold text-slate-800 dark:text-slate-100">
              Job #{{ row.id }} — {{ row.assembly.part_number }}
            </h2>
            <button @click="emit('close')" aria-label="Close inspector"
                    data-testid="drawer-close-btn"
                    class="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring">
              <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-slate-500" fill="none"
                   viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </header>
          <div class="flex-1 overflow-y-auto px-6 py-4">
            <dl class="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm">
              <template v-for="[key, value] in flatEntries" :key="key">
                <dt class="font-mono text-xs text-slate-500 dark:text-slate-400
                           pt-1 whitespace-nowrap">{{ key }}</dt>
                <dd class="text-slate-800 dark:text-slate-200 break-words">
                  {{ value }}
                </dd>
              </template>
            </dl>
          </div>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-enter-active,
.drawer-leave-active { transition: transform 250ms ease-out, opacity 200ms ease-out; }
.drawer-enter-from,
.drawer-leave-to     { transform: translateX(100%); opacity: 0; }
</style>
