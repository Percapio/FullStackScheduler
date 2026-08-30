<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import SlideOverPanel from './SlideOverPanel.vue'
import InspectJobBlock from './InspectJobBlock.vue'
import { fetchJobLineage, type JobReadExpanded } from '@/api/history'
import { useInspectVerbosity } from '@/composables/useInspectVerbosity'

const props = defineProps<{
  anchor: JobReadExpanded | null
}>()

const emit = defineEmits<{
  close: []
}>()

type LineageFetch =
  | { status: 'loading' }
  | { status: 'ready'; jobs: JobReadExpanded[] }
  | { status: 'degraded'; message: string }

const lineageFetch = ref<LineageFetch>({ status: 'loading' })
let lineageRequestSeq = 0

async function loadLineage(anchorId: number) {
  const seq = ++lineageRequestSeq
  lineageFetch.value = { status: 'loading' }
  try {
    const jobs = await fetchJobLineage(anchorId)
    if (seq !== lineageRequestSeq) return
    lineageFetch.value = { status: 'ready', jobs }
  } catch (err) {
    if (seq !== lineageRequestSeq) return
    lineageFetch.value = { status: 'degraded', message: 'Could not load lineage for this job.' }
  }
  
  nextTick(() => {
    if (!props.anchor) return
    const el = document.getElementById(`inspect-job-${props.anchor.id}`)
    if (el) {
      const container = el.closest('.overflow-y-auto')
      if (container) {
        const top = el.getBoundingClientRect().top - container.getBoundingClientRect().top
        if (top > 0) {
          container.scrollTop += top
        }
      }
    }
  })
}

watch(() => props.anchor?.id, (newId) => {
  if (newId) {
    loadLineage(newId)
  }
}, { immediate: true })

const visibleJobs = computed<JobReadExpanded[]>(() => {
  if (lineageFetch.value.status === 'loading') return []
  if (lineageFetch.value.status === 'degraded') {
    return props.anchor && props.anchor.discarded_at === null ? [props.anchor] : []
  }

  // ready
  let chain = lineageFetch.value.jobs.filter(j => j.discarded_at === null)
  if (!props.anchor) return chain

  const anchorId = props.anchor.id
  const idx = chain.findIndex(j => j.id === anchorId)
  if (idx !== -1) {
    chain = [...chain.slice(0, idx), props.anchor, ...chain.slice(idx + 1)]
  } else {
    if (props.anchor.discarded_at === null) {
      chain = [...chain, props.anchor]
    }
  }
  return chain
})

const { showAllData } = useInspectVerbosity()

const editingJobId = ref<number | null>(null)

function onEditStarted(jobId: number) {
  editingJobId.value = jobId
}

function onEditEnded(jobId: number) {
  if (editingJobId.value === jobId) {
    editingJobId.value = null
  }
}

function onEdited(job: JobReadExpanded) {
  if (lineageFetch.value.status === 'ready') {
    const jobs = lineageFetch.value.jobs
    const idx = jobs.findIndex(j => j.id === job.id)
    if (idx !== -1) {
      lineageFetch.value = {
        ...lineageFetch.value,
        jobs: [...jobs.slice(0, idx), job, ...jobs.slice(idx + 1)]
      }
    }
  }
}

function onDiscarded(jobId: number) {
  editingJobId.value = null // unconditional clear
  if (lineageFetch.value.status === 'ready') {
    lineageFetch.value = {
      ...lineageFetch.value,
      jobs: lineageFetch.value.jobs.filter(j => j.id !== jobId)
    }
  }
  nextTick(() => {
    if (visibleJobs.value.length === 0) {
      emit('close')
    }
  })
}

function handleClose() {
  if (editingJobId.value !== null) {
    const ok = window.confirm('Discard unsaved changes?')
    if (!ok) return
  }
  emit('close')
}
</script>

<template>
  <SlideOverPanel
    :open="anchor !== null"
    width="xl"
    :ariaLabel="anchor ? `Job ${anchor.id} lineage` : ''"
    @close="handleClose"
  >
    <template #header>
      <button @click="handleClose" aria-label="Close inspector"
              data-testid="drawer-close-btn"
              class="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring focus-ring-raised">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-slate-500" fill="none"
             viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </template>

    <div v-if="lineageFetch.status === 'loading'" class="p-4 flex justify-center">
      <span class="text-slate-500 text-sm">Loading lineage...</span>
    </div>

    <div v-else-if="lineageFetch.status === 'degraded' && visibleJobs.length > 0" class="mb-4 mx-4 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-red-800 text-sm flex items-start justify-between gap-2">
      <span>{{ lineageFetch.message }}</span>
      <button v-if="anchor" @click="loadLineage(anchor.id)" class="shrink-0 rounded px-2 py-1 text-xs font-medium bg-red-200 hover:bg-red-300 transition-colors">Retry</button>
    </div>

    <div class="mb-4 flex items-center justify-end px-4 pt-4" v-if="lineageFetch.status !== 'loading'">
      <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400 cursor-pointer">
        <input
          type="checkbox"
          v-model="showAllData"
          data-testid="inspect-show-all-toggle"
          class="rounded border-slate-300 text-blue-600 focus:ring-blue-500/60"
        />
        Show All Data (this job)
      </label>
    </div>

    <div class="px-4 pb-4 space-y-6">
      <template v-for="job in visibleJobs" :key="job.id">
        <div :class="['border border-surface-hairline rounded-lg p-4 bg-surface-overlay',
                      anchor !== null && job.id === anchor.id ? 'lineage-anchor' : 'shadow-sm']"
               :id="`inspect-job-${job.id}`">
          <div class="flex items-center gap-2 mb-4">
            <h3 class="text-base font-semibold text-slate-800 dark:text-slate-100">
              Job #{{ job.id }} — {{ job.assembly.part_number }}
            </h3>
            <span v-if="editingJobId === job.id" class="text-xs font-normal text-blue-600 dark:text-blue-400">
              Editing
            </span>
          </div>
          <InspectJobBlock
            :job="job"
            :isAnchor="anchor !== null && job.id === anchor.id"
            :showAllData="showAllData && anchor !== null && job.id === anchor.id"
            :editLocked="editingJobId !== null && editingJobId !== job.id"
            @editStarted="onEditStarted"
            @editEnded="onEditEnded"
            @edited="onEdited"
            @discarded="onDiscarded"
          />
        </div>
      </template>
    </div>
  </SlideOverPanel>
</template>
