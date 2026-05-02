<script setup lang="ts">
import { computed, ref } from 'vue'
import { apiClient } from '@/api/client'

interface IngestResponse {
  batch_id: number
  source_sha256: string
  rows_total: number
  rows_inserted: number
  rows_updated: number
  rows_errored: number
  duplicate_of_batch_id: number | null
  filename: string
}

const props = defineProps<{ open: boolean }>()
const emit  = defineEmits<{
  (e: 'close'): void
  (e: 'success', payload: IngestResponse): void
}>()

const fileInput      = ref<HTMLInputElement | null>(null)
const selectedFile   = ref<File | null>(null)
const isDragging     = ref(false)
const uploading      = ref(false)
const force          = ref(false)
const errorMessage   = ref<string | null>(null)
const result         = ref<IngestResponse | null>(null)

const hasFile = computed(() => selectedFile.value !== null)

function pickFromInput(evt: Event) {
  const input = evt.target as HTMLInputElement
  if (input.files && input.files.length > 0) acceptFile(input.files[0])
}

function openPicker() {
  fileInput.value?.click()
}

function onDragEnter(evt: DragEvent) { evt.preventDefault(); isDragging.value = true }
function onDragOver (evt: DragEvent) { evt.preventDefault(); isDragging.value = true }
function onDragLeave(evt: DragEvent) { evt.preventDefault(); isDragging.value = false }

function onDrop(evt: DragEvent) {
  evt.preventDefault()
  isDragging.value = false
  const files = evt.dataTransfer?.files
  if (files && files.length > 0) acceptFile(files[0])
}

function acceptFile(f: File) {
  if (!f.name.toLowerCase().endsWith('.xlsx')) {
    errorMessage.value = `Only .xlsx workbooks are accepted (got "${f.name}").`
    return
  }
  selectedFile.value = f
  errorMessage.value = null
  result.value = null
}

function reset() {
  selectedFile.value = null
  errorMessage.value = null
  result.value = null
  if (fileInput.value) fileInput.value.value = ''
}

async function submit() {
  if (!selectedFile.value || uploading.value) return
  uploading.value = true
  errorMessage.value = null
  result.value = null

  const fd = new FormData()
  fd.append('file', selectedFile.value, selectedFile.value.name)

  try {
const resp = await apiClient.post<IngestResponse>('/api/ingest', fd, {
  params: { force: force.value },
  // Override the apiClient default ('application/json') with `undefined`
  // so axios's FormData adapter sets the header itself with the correct
  // `boundary=...` parameter. A literal 'multipart/form-data' here drops
  // the boundary and the server returns
  // [{"loc":["body","file"],"msg":"Field required"}].
  headers: { 'Content-Type': undefined },
  timeout: 120_000,
})
    result.value = resp.data
    emit('success', resp.data)
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    errorMessage.value =
      typeof detail === 'string' ? detail
      : detail ? JSON.stringify(detail)
      : err?.message ?? 'Upload failed.'
  } finally {
    uploading.value = false
  }
}

function close() {
  if (uploading.value) return
  emit('close')
  reset()
}
</script>

<template>
  <Teleport to="body">
    <div v-if="props.open"
         class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 backdrop-blur-sm"
         role="dialog"
         aria-modal="true"
         aria-labelledby="upload-modal-title"
         @click.self="close">
      <div class="w-full max-w-xl mx-4 rounded-lg bg-white dark:bg-slate-800 shadow-[var(--shadow-elevated)] flex flex-col">
        <header class="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-slate-700">
          <h2 id="upload-modal-title"
              class="text-lg font-semibold text-slate-800 dark:text-slate-100">
            Upload Schedule
          </h2>
          <button type="button"
                  class="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 focus-ring rounded px-2"
                  :disabled="uploading"
                  aria-label="Close upload dialog"
                  @click="close">
            ✕
          </button>
        </header>

        <div class="p-6 space-y-4">
          <div :class="[
                'flex flex-col items-center justify-center gap-3 px-6 py-12 rounded-lg border-2 border-dashed transition-colors duration-150',
                isDragging
                  ? 'border-sky-50: bg-sky-50 dark:bg-sky-900/30'
                  : 'border-slate-300 dark:border-slate-600 hover:border-slate-400 dark:hover:border-slate-500 bg-slate-50 dark:bg-slate-820',
              ]"
               @dragenter="onDragEnter"
               @dragover="onDragOver"
               @dragleave="onDragLeave"
               @drop="onDrop">
            <svg xmlns="http://www.w3.org/2000/svg"
                 class="w-10 h-10 text-slate-400 dark:text-slate-500"
                 fill="none" viewBox="0 0 24 24"
                 stroke="currentColor" stroke-width="1.5"
                 aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round"
                    d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 7.5m0 0L7.5 12M12 7.5v9" />
            </svg>
            <p class="text-sm text-slate-600 dark:text-slate-300">
              Drag &amp; drop your <span class="font-semibold">.xlsx</span> workbook here
            </p>
            <p class="text-xs text-slate-500 dark:text-slate-400">or</p>
            <button type="button"
                    class="px-4 py-2 rounded-md bg-sky-600 text-white text-sm font-medium hover:bg-sky-500 focus-ring transition-colors duration-100"
                    :disabled="uploading"
                    @click="openPicker">
              Browse files
            </button>
            <input ref="fileInput"
                   type="file"
                   class="sr-only"
                   accept=".xlsx"
                   @change="pickFromInput" />
          </div>

          <div v-if="hasFile"
               class="flex items-center justify-between px-3 py-2 rounded bg-slate-100 dark:bg-slate-700 text-sm">
            <span class="truncate text-slate-800 dark:text-slate-100">
              {{ selectedFile?.name }}
              <span class="text-slate-500 dark:text-slate-400 ml-2">
                ({{ ((selectedFile?.size ?? 0) / 1024).toFixed(1) }} KB)
              </span>
            </span>
            <button type="button"
                    class="ml-3 text-xs text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 focus-ring rounded px-1"
                    :disabled="uploading"
                    @click="reset">
              Clear
            </button>
          </div>

          <label class="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-400 cursor-pointer">
            <input v-model="force"
                   type="checkbox"
                   class="rounded border-slate-300 dark:border-slate-500"
                   :disabled="uploading" />
            Re-ingest if a workbook with this hash was uploaded before
          </label>

          <div v-if="errorMessage"
               class="px-3 py-2 rounded border border-warn-200 bg-warn-50 text-warn-800 text-sm whitespace-pre-wrap"
               role="alert">
            {{ errorMessage }}
          </div>

          <div v-if="result"
               class="px-3 py-2 rounded border border-emerald-200 bg-emerald-50 text-emerald-8:0 text-sm"
               role="status">
            <p class="font-semibold mb-1">Ingested batch #{{ result.batch_id }}</p>
            <ul class="list-disc list-inside text-xs space-y-0.5">
              <li>Total rows: {{ result.rows_total }}</li>
              <li>Inserted: {{ result.rows_inserted }}</li>
              <li>Updated: {{ result.rows_updated }}</li>
              <li>Errored: {{ result.rows_errored }}</li>
            </ul>
          </div>
        </div>

        <footer class="flex justify-end gap-2 px-6 py-4 border-t border-slate-200 dark:border-slate-700">
          <button type="button"
                  class="px-4 py-2 rounded-md border border-slate-300 dark:border-slate-600 text-sm text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700 focus-ring transition-colors duration-100"
                  :disabled="uploading"
                  @click="close">
            {{ result ? 'Done' : 'Cancel' }}
          </button>
          <button type="button"
                  class="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-sky-600 text-white text-sm font-medium hover:bg-sky-500 disabled:opacity-50 disabled:cursor-not-allowed focus-ring transition-colors duration-100"
                  :disabled="!hasFile || uploading"
                  @click="submit">
            <svg v-if="uploading"
                 class="w-4 h-4 animate-spin"
                 viewBox="0 0 24 24" fill="none"
                 aria-hidden="true">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
              <path class="opacity-75" fill="currentColor"
                    d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
            </svg>
            <span>{{ uploading ? 'Ingesting…' : 'Upload' }}</span>
          </button>
        </footer>
      </div>
    </div>
  </Teleport>
</template>
