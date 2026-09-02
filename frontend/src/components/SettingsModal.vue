<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import {
  getPhotosDir,
  browseDirectory,
  savePhotosDir,
  type PhotosDirSource
} from '@/api/settings'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const { show: pushToast } = useToast()

const loading = ref(false)
const fetchError = ref<string | null>(null)

const editable = ref(false)
const configured = ref(false)
const source = ref<PhotosDirSource>('unset')
const serverPath = ref<string | null>(null)

const fieldPath = ref('')
const inlineError = ref<string | null>(null)
const saving = ref(false)

// Browser state
const browsePath = ref('')
const parentPath = ref<string | null>(null)
const entries = ref<{ name: string; path: string }[]>([])
const truncated = ref(false)
const filterPrefix = ref('')
const browseError = ref<string | null>(null)
const browseBusy = ref(false)

async function fetchSettings() {
  loading.value = true
  fetchError.value = null
  const res = await getPhotosDir()
  loading.value = false
  
  if (res.kind === 'ok') {
    editable.value = res.editable
    configured.value = res.configured
    source.value = res.source
    serverPath.value = res.path
    
    if (res.editable) {
      fieldPath.value = res.path ?? ''
      await navigateBrowse(res.path ?? '')
    }
  } else if (res.kind === 'forbidden') {
    editable.value = false
  } else if (res.kind === 'network') {
    fetchError.value = res.message
  } else {
    fetchError.value = 'Failed to load settings.'
  }
}

watch(() => props.open, (isOpen) => {
  if (isOpen) {
    fetchSettings()
  } else {
    // Reset state on close
    inlineError.value = null
    browsePath.value = ''
    filterPrefix.value = ''
  }
}, { immediate: true })

async function navigateBrowse(targetPath: string, prefix: string = '') {
  browseBusy.value = false
  browseError.value = null
  
  const res = await browseDirectory(targetPath, prefix)
  
  if (!res) return // handle vitest unhandled rejection
  
  if (res.kind === 'ok') {
    browsePath.value = targetPath
    parentPath.value = res.parent
    entries.value = res.entries
    truncated.value = res.truncated
    // Sync field
    fieldPath.value = targetPath
  } else if (res.kind === 'busy') {
    browseBusy.value = true
  } else if (res.kind === 'not_found') {
    browseError.value = 'Directory not found or not readable.'
  } else if (res.kind === 'forbidden') {
    editable.value = false
  } else {
    browseError.value = res.message || 'Failed to browse directory.'
  }
}

function onEntryClick(path: string) {
  filterPrefix.value = ''
  navigateBrowse(path)
}

function onUpClick() {
  if (parentPath.value !== null) {
    filterPrefix.value = ''
    navigateBrowse(parentPath.value)
  }
}

function onFilterChange() {
  navigateBrowse(browsePath.value, filterPrefix.value)
}

async function onSave() {
  if (saving.value) return
  
  saving.value = true
  inlineError.value = null
  
  const res = await savePhotosDir(fieldPath.value)
  saving.value = false
  
  if (res.kind === 'ok') {
    serverPath.value = res.path
    configured.value = res.configured
    source.value = res.source
    
    if (res.folder_count === 0) {
      pushToast('Saved. 0 photo folders found (expected on fresh install).', 'success')
    } else {
      pushToast(`Saved. ${res.folder_count} photo folders found.`, 'success')
    }
  } else if (res.kind === 'invalid') {
    switch (res.reason) {
      case 'blank': inlineError.value = 'Path cannot be blank.'; break;
      case 'not_absolute': inlineError.value = 'Path must be absolute.'; break;
      case 'not_found': inlineError.value = 'Directory does not exist.'; break;
      case 'not_a_dir': inlineError.value = 'Path is a file, not a directory.'; break;
      case 'not_readable': inlineError.value = 'Directory cannot be read.'; break;
    }
  } else if (res.kind === 'forbidden') {
    editable.value = false
  } else if (res.kind === 'storage') {
    pushToast('Failed to save to configuration file.', 'error')
  } else {
    pushToast(res.message || 'Failed to save settings.', 'error')
  }
}

function close() {
  emit('close')
}

const isDirty = computed(() => {
  const s = serverPath.value ?? ''
  const f = fieldPath.value ?? ''
  return s !== f
})

</script>

<template>
  <Teleport to="body">
    <div v-if="props.open"
         class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
         role="dialog"
         aria-modal="true"
         aria-labelledby="settings-modal-title"
         @click.self="close()">
      <div class="w-full max-w-2xl mx-4 rounded-lg bg-surface-raised shadow-[var(--shadow-elevated)] flex flex-col max-h-[85vh]">
        <header class="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-slate-700">
          <h2 id="settings-modal-title" class="text-lg font-semibold text-slate-800 dark:text-slate-100">
            Settings
          </h2>
          <button type="button"
                  class="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 focus-ring focus-ring-raised rounded px-2"
                  aria-label="Close settings dialog"
                  @click="close">
            ✕
          </button>
        </header>

        <div class="p-6 overflow-y-auto">
          <div v-if="loading" class="text-slate-500 text-sm">Loading settings...</div>
          
          <div v-else-if="fetchError" class="space-y-3">
            <p class="text-warn-600 text-sm">{{ fetchError }}</p>
            <button class="px-3 py-1.5 rounded bg-slate-200 hover:bg-slate-300 text-sm focus-ring" @click="fetchSettings">Retry</button>
          </div>
          
          <div v-else-if="!editable" class="space-y-4">
            <div class="p-4 rounded-md bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
              <p class="text-sm text-slate-700 dark:text-slate-300">
                Shipping photos directory is configured on the production computer.
              </p>
              <p class="text-sm text-slate-600 dark:text-slate-400 mt-2 font-medium">
                Currently configured: {{ configured ? 'Yes' : 'No' }}
              </p>
            </div>
          </div>
          
          <div v-else class="space-y-6">
            <section class="space-y-2">
              <label class="block text-sm font-medium text-slate-700 dark:text-slate-200">
                Shipping Photos Directory
              </label>
              
              <div class="flex gap-2">
                <input v-model="fieldPath" type="text" 
                       class="flex-1 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 text-sm focus-ring focus-ring-overlay"
                       placeholder="e.g. C:\Photos" />
                <button type="button" @click="onSave" :disabled="saving || !isDirty"
                        class="px-4 py-1.5 rounded bg-sky-600 text-white text-sm font-medium hover:bg-accent-700 disabled:opacity-50 disabled:cursor-not-allowed focus-ring">
                  Save
                </button>
              </div>
              <p v-if="inlineError" class="text-sm text-warn-600">{{ inlineError }}</p>
              <p v-if="source === 'env'" class="text-xs text-slate-500">
                Loaded from .env file
              </p>
              <p v-else-if="source === 'runtime'" class="text-xs text-slate-500">
                Loaded from runtime configuration
              </p>
            </section>
            
            <section class="border border-slate-200 dark:border-slate-700 rounded-md flex flex-col h-[400px]">
              <div class="flex items-center gap-2 px-3 py-2 border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50">
                <button type="button" @click="onUpClick" :disabled="parentPath === null"
                        class="px-2 py-1 rounded hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed text-sm font-medium">
                  ↑ Up
                </button>
                <div class="text-sm font-mono truncate flex-1 text-slate-600 dark:text-slate-400" :title="browsePath">
                  {{ browsePath || 'Drive Roots' }}
                </div>
              </div>
              
              <div class="flex-1 overflow-y-auto p-2 space-y-1">
                <div v-if="browseBusy" class="text-sm text-amber-600 p-2 border border-amber-200 bg-amber-50 rounded mb-2">
                  Still reading the previous folder — try again.
                </div>
                <div v-if="browseError" class="text-sm text-warn-600 p-2">
                  {{ browseError }}
                </div>
                <div v-else-if="entries.length === 0" class="text-sm text-slate-500 italic p-2">
                  No subdirectories.
                </div>
                <template v-else>
                  <button v-for="entry in entries" :key="entry.path"
                          @click="onEntryClick(entry.path)"
                          class="w-full text-left px-3 py-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-800 text-sm truncate flex items-center gap-2 group">
                    <span class="text-slate-400 group-hover:text-sky-500">📁</span>
                    <span>{{ entry.name }}</span>
                  </button>
                </template>
              </div>
              
              <div v-if="truncated" class="p-3 border-t border-slate-200 dark:border-slate-700 bg-amber-50 dark:bg-amber-900/20 flex flex-col gap-2">
                <p class="text-xs text-amber-800 dark:text-amber-200">
                  Too many folders. Some are hidden.
                </p>
                <input v-model="filterPrefix" @input="onFilterChange" type="text"
                       placeholder="Filter by prefix..."
                       class="w-full rounded border border-amber-300 dark:border-amber-700 bg-white dark:bg-slate-800 px-2 py-1 text-sm focus-ring" />
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
