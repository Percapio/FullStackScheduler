<script setup lang="ts">
import { computed, watch, ref } from 'vue'
import { usePhotoGallery } from '@/composables/usePhotoGallery'

const props = defineProps<{
  gallery: ReturnType<typeof usePhotoGallery>
}>()

const state = computed(() => props.gallery.state.value)
const isOpen = computed(() => state.value.state !== 'closed')

const downloadError = ref<string | null>(null)
const downloading = ref(false)

const galleryGeneration = ref(0)
const loadedImages = ref<Set<string>>(new Set())
const failedImages = ref<Set<string>>(new Set())
const retryNonces = ref<Record<string, number>>({})

watch(() => (state.value.state === 'ready' ? state.value.date_folder : null), (newFolder, oldFolder) => {
  if (newFolder !== oldFolder) {
    galleryGeneration.value++
    loadedImages.value.clear()
    failedImages.value.clear()
    retryNonces.value = {}
  }
})

watch(() => state.value.state, () => {
  downloadError.value = null
})

const previewableCount = computed(() => {
  if (state.value.state === 'ready') {
    return state.value.entries.filter(e => e.previewable).length
  }
  return 0
})

const resolvedCount = computed(() => {
  return loadedImages.value.size + failedImages.value.size
})

function close() {
  props.gallery.closeGallery()
}

async function onDownload() {
  downloadError.value = null
  downloading.value = true
  const err = await props.gallery.downloadSelection()
  downloading.value = false
  if (err) {
    downloadError.value = err
  }
}

function thumbUrl(filename: string, date_folder: string, version: string) {
  return `/api/photos/thumb/${encodeURIComponent(filename)}?date_folder=${encodeURIComponent(date_folder)}&v=${encodeURIComponent(version)}`
}

// Both handlers are built during render so they CLOSE OVER the generation they
// were issued under. An inline `@load="handleImgLoad(name, galleryGeneration)"`
// reads galleryGeneration when the event fires, not when the tile rendered, so
// the guard would always compare the current generation against itself and
// never discard anything. The browser still fires `load` on a detached <img>
// whose request completes, which is exactly the stale event this guards.
function makeLoadHandler(filename: string, generation: number) {
  return () => {
    if (generation !== galleryGeneration.value) return
    loadedImages.value.add(filename)
    failedImages.value.delete(filename)
  }
}

function makeErrorHandler(filename: string, generation: number) {
  return () => {
    if (generation !== galleryGeneration.value) return
    failedImages.value.add(filename)
    loadedImages.value.delete(filename)
  }
}

function retryImage(filename: string, e: Event) {
  e.stopPropagation()
  retryNonces.value[filename] = (retryNonces.value[filename] || 0) + 1
  failedImages.value.delete(filename)
}
</script>
<template>
  <Teleport to="body">
    <div v-if="isOpen" class="relative z-[60]" aria-labelledby="modal-title" role="dialog" aria-modal="true">
      <div class="fixed inset-0 bg-slate-900/50 backdrop-blur-sm transition-opacity" @click="close"></div>

      <div class="fixed inset-0 z-10 overflow-y-auto">
        <div class="flex min-h-full items-end justify-center p-4 text-center sm:items-center sm:p-0">
          <div class="relative transform overflow-hidden rounded-xl bg-white dark:bg-slate-800 p-6 text-left shadow-2xl transition-all sm:my-8 sm:w-full sm:max-w-5xl flex flex-col max-h-[85vh]">
            
            <div v-if="state.state === 'loading'" class="flex flex-col items-center justify-center py-20">
              <p class="text-slate-500">Loading photos for {{ state.date_folder }}...</p>
            </div>

            <div v-else-if="state.state === 'error'" class="flex flex-col items-center justify-center py-20">
              <p class="text-red-500 mb-4">{{ state.message }}</p>
              <button @click="close" class="px-4 py-2 bg-slate-200 hover:bg-slate-300 rounded text-slate-800">Close</button>
            </div>

            <template v-else-if="state.state === 'ready'">
              <div class="flex items-center justify-between mb-4 pb-4 border-b border-slate-200 dark:border-slate-700">
                <h3 class="text-lg font-semibold leading-6 text-slate-900 dark:text-slate-100 flex items-center gap-4" id="modal-title">
                  <span>Photos for {{ state.date_folder }}</span>
                  <span v-if="resolvedCount < previewableCount" class="text-sm font-normal text-slate-500 bg-slate-100 dark:bg-slate-700 px-2 py-1 rounded">
                    {{ resolvedCount }} of {{ previewableCount }} ready
                  </span>
                </h3>
                <button @click="close" class="text-slate-400 hover:text-slate-500">
                  <span class="sr-only">Close panel</span>
                  <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <div v-if="state.truncated" class="mb-4 p-3 bg-amber-50 border border-amber-200 text-amber-800 rounded text-sm">
                This folder contains more photos than can be displayed. Showing the first {{ state.entries.length }}.
              </div>

              <div class="flex-1 overflow-y-auto min-h-0">
                <div v-if="state.entries.length === 0" class="text-center py-12 text-slate-500">
                  No previewable photos found in this folder.
                </div>
                
                <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
                  <div
                    v-for="entry in state.entries"
                    :key="`${state.date_folder}/${entry.name}/${retryNonces[entry.name] || 0}`"
                    class="relative aspect-square cursor-pointer group rounded overflow-hidden bg-slate-100 dark:bg-slate-900"
                    @click="props.gallery.toggleSelection(entry.name)"
                  >
                    <template v-if="entry.previewable">
                      <div v-if="!loadedImages.has(entry.name) && !failedImages.has(entry.name)" class="absolute inset-0 bg-slate-200 dark:bg-slate-700 animate-pulse"></div>
                      <img
                        v-if="!failedImages.has(entry.name)"
                        :src="thumbUrl(entry.name, state.date_folder, entry.version)"
                        :alt="entry.name"
                        class="w-full h-full object-cover transition-transform group-hover:scale-105"
                        :class="{ 'opacity-0': !loadedImages.has(entry.name) }"
                        :onLoad="makeLoadHandler(entry.name, galleryGeneration)"
                        :onError="makeErrorHandler(entry.name, galleryGeneration)"
                      />
                      <div v-else class="absolute inset-0 flex flex-col items-center justify-center text-xs text-slate-400 p-2 text-center bg-slate-50 dark:bg-slate-800 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors" @click.stop="retryImage(entry.name, $event)">
                        <svg class="w-6 h-6 mb-1 text-slate-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 001.5-1.5V6a1.5 1.5 0 00-1.5-1.5H3.75A1.5 1.5 0 002.25 6v12a1.5 1.5 0 001.5 1.5zm10.5-11.25h.008v.008h-.008V8.25zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" />
                        </svg>
                        {{ entry.name }}<br/>(Click to retry)
                      </div>
                    </template>
                    <div v-else class="w-full h-full flex items-center justify-center text-xs text-slate-400 p-2 text-center">
                      {{ entry.name }}<br/>(No preview)
                    </div>
                    
                    <div
                      class="absolute inset-0 border-4 transition-colors"
                      :class="state.selection.has(entry.name) ? 'border-blue-500' : 'border-transparent group-hover:border-slate-300/50'"
                    ></div>
                    
                    <div v-if="state.selection.has(entry.name)" class="absolute top-2 right-2 bg-blue-500 text-white rounded-full p-0.5">
                      <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                  </div>
                </div>
              </div>

              <div class="mt-4 pt-4 border-t border-slate-200 dark:border-slate-700 flex flex-col gap-2">
                <div v-if="downloadError" class="text-sm text-red-600 mb-2">{{ downloadError }}</div>
                
                <div class="flex items-center justify-between">
                  <div class="flex gap-2">
                    <button @click="props.gallery.selectAll()" class="text-sm text-blue-600 hover:text-blue-800">Select All</button>
                    <button @click="props.gallery.clearSelection()" class="text-sm text-slate-500 hover:text-slate-700">Clear</button>
                  </div>
                  
                  <button
                    @click="onDownload"
                    :disabled="downloading || (state.selection.size === 0 && state.entries.length > 0)"
                    class="px-4 py-2 bg-blue-600 text-white rounded font-medium disabled:opacity-50 hover:bg-blue-700"
                  >
                    {{ downloading ? 'Downloading...' : 'Download' }}
                  </button>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
