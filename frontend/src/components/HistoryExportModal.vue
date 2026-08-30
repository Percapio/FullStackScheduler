<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import {
  fetchHistoryExportColumns,
  buildHistoryExportUrl,
  type HistoryExportColumn,
} from '@/api/history'

const props = defineProps<{
  isOpen: boolean
  searchQuery: string | null
  totalRows: number
}>()

const emit = defineEmits<{
  close: []
}>()

const columns = ref<HistoryExportColumn[]>([])
const checkedColumns = ref<Set<string>>(new Set())
const delimiterToken = ref('comma')

const delimiters = [
  { value: 'comma', label: 'Comma' },
  { value: 'tab', label: 'Tab' },
  { value: 'semicolon', label: 'Semicolon' },
  { value: 'pipe', label: 'Pipe' }
]

watch(() => props.isOpen, async (open) => {
  if (open) {
    delimiterToken.value = 'comma'
    columns.value = await fetchHistoryExportColumns()
    checkedColumns.value = new Set(columns.value.map(c => c.key))
  }
})

const canExport = computed(() => checkedColumns.value.size > 0)

function toggleSelectAll() {
  if (checkedColumns.value.size === columns.value.length) {
    checkedColumns.value.clear()
  } else {
    checkedColumns.value = new Set(columns.value.map(c => c.key))
  }
}

function handleExport() {
  if (!canExport.value) return
  const url = buildHistoryExportUrl(
    props.searchQuery,
    Array.from(columns.value.map(c => c.key).filter(k => checkedColumns.value.has(k))),
    delimiterToken.value
  )
  window.location.assign(url)
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="isOpen" class="fixed inset-0 z-50 flex items-center justify-center" data-testid="history-export-modal">
        <div class="absolute inset-0 bg-black/50" data-testid="history-export-backdrop" @click="emit('close')" />
        <div class="relative z-10 bg-surface-raised rounded-xl shadow-2xl w-full max-w-sm mx-4 p-6" role="dialog" aria-labelledby="history-export-title">
          <h3 id="history-export-title" class="text-base font-semibold text-slate-800 dark:text-slate-100 mb-4">
            Export History
          </h3>

          <div v-if="searchQuery" class="mb-4 text-sm text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-700 p-2 rounded">
            Filtered by: <strong>{{ searchQuery }}</strong>
          </div>

          <div class="mb-4">
            <div class="flex items-center justify-between mb-2">
              <span class="text-sm font-medium text-slate-700 dark:text-slate-300">Columns</span>
              <button type="button" class="text-xs text-blue-600 dark:text-blue-400 hover:underline" @click="toggleSelectAll">
                {{ checkedColumns.size === columns.length ? 'Deselect All' : 'Select All' }}
              </button>
            </div>
            <div class="space-y-2 max-h-48 overflow-y-auto p-1">
              <label v-for="col in columns" :key="col.key" class="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                <input
                  type="checkbox"
                  :value="col.key"
                  :checked="checkedColumns.has(col.key)"
                  @change="(e) => (e.target as HTMLInputElement).checked ? checkedColumns.add(col.key) : checkedColumns.delete(col.key)"
                  class="rounded border-slate-300 dark:border-slate-600 text-blue-600 focus:ring-blue-500"
                />
                {{ col.header }}
              </label>
            </div>
          </div>

          <div class="mb-6">
            <span class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Delimiter</span>
            <div class="grid grid-cols-2 gap-2">
              <label v-for="delim in delimiters" :key="delim.value" class="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                <input
                  type="radio"
                  name="delimiter"
                  :value="delim.value"
                  v-model="delimiterToken"
                  class="border-slate-300 dark:border-slate-600 text-blue-600 focus:ring-blue-500"
                />
                {{ delim.label }}
              </label>
            </div>
          </div>

          <div class="flex justify-end gap-3">
            <button type="button" class="rounded px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors" @click="emit('close')">
              Cancel
            </button>
            <button type="button" :disabled="!canExport" class="rounded px-3 py-1.5 text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed" @click="handleExport">
              Export {{ totalRows }} rows
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.modal-enter-active,
.modal-leave-active { transition: opacity 150ms ease-out; }
.modal-enter-from,
.modal-leave-to     { opacity: 0; }
</style>
