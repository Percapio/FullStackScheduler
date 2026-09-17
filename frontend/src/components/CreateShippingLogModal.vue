<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import {
  fetchShippingLogCandidates,
  generateShippingLog,
  type IneligibleJob,
  type IneligibleReason,
  type ShippingLogCandidate,
  type ShippingLogCandidateList,
  type ShippingLogDownload,
} from '@/api/shippingLog'
import { useJobFormatters } from '@/composables/useJobFormatters'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const { show: pushToast } = useToast()
const { buildLabel, formatShortDate, isShippingToday } = useJobFormatters()

// loading → ready | load_failed; ready → generating → closed | loading (ineligible) | ready.
type ModalPhase = 'loading' | 'load_failed' | 'ready' | 'generating'

const phase = ref<ModalPhase>('loading')
const loadError = ref<string | null>(null)
const candidateList = ref<ShippingLogCandidateList | null>(null)
const selectedIds = ref<Set<number>>(new Set())
const inlineError = ref<string | null>(null)

// Not reactive: a guard against a slow response landing after close or a newer load.
let loadSeq = 0

const candidates = computed<ShippingLogCandidate[]>(() => candidateList.value?.candidates ?? [])
const maxJobsPerLog = computed(() => candidateList.value?.max_jobs_per_log ?? 0)
const templateReady = computed(() => candidateList.value?.template_ready === true)
const selectedCount = computed(() => selectedIds.value.size)
const overMax = computed(() => selectedCount.value > maxJobsPerLog.value)
const allSelected = computed(() =>
  candidates.value.length > 0 && selectedCount.value === candidates.value.length,
)
// "Today" is the shop's Pacific date, the same test the Shipping grid uses for
// its ship-date highlight. Overdue jobs are not included.
const dueTodayIds = computed(() =>
  candidates.value
    .filter(candidate => isShippingToday(candidate.resolved_ship_date))
    .map(candidate => candidate.job_id),
)
const canGenerate = computed(() =>
  phase.value === 'ready' && selectedCount.value > 0 && !overMax.value && templateReady.value,
)
const generateLabel = computed(() => {
  if (phase.value === 'generating') return 'Generating…'
  if (overMax.value) return `Generate Log (${selectedCount.value} / ${maxJobsPerLog.value})`
  return selectedCount.value > 0 ? `Generate Log (${selectedCount.value})` : 'Generate Log'
})

function resetState(): void {
  phase.value = 'loading'
  loadError.value = null
  candidateList.value = null
  selectedIds.value = new Set()
  inlineError.value = null
}

async function loadCandidates(): Promise<void> {
  const seq = ++loadSeq
  phase.value = 'loading'
  loadError.value = null
  const outcome = await fetchShippingLogCandidates()
  if (seq !== loadSeq) return

  if (outcome.kind === 'transport') {
    loadError.value = `Could not load planned jobs: ${outcome.message}`
    phase.value = 'load_failed'
    return
  }
  candidateList.value = outcome.list
  // A refetch keeps only the selections still in the list; nothing the operator
  // didn't see can end up in a log.
  const listedIds = new Set(outcome.list.candidates.map(candidate => candidate.job_id))
  selectedIds.value = new Set([...selectedIds.value].filter(jobId => listedIds.has(jobId)))
  phase.value = 'ready'
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') requestClose()
}

watch(() => props.open, (isOpen) => {
  if (isOpen) {
    resetState()
    window.addEventListener('keydown', onKeydown)
    loadCandidates()
  } else {
    loadSeq++
    window.removeEventListener('keydown', onKeydown)
    resetState()
  }
}, { immediate: true })

onUnmounted(() => window.removeEventListener('keydown', onKeydown))

// An in-flight request must not outlive its UI, so only completion closes it.
function requestClose(): void {
  if (phase.value === 'generating') return
  emit('close')
}

function toggleAll(): void {
  selectedIds.value = allSelected.value
    ? new Set()
    : new Set(candidates.value.map(candidate => candidate.job_id))
}

// Replaces the selection rather than adding to it. An explicit action, so the
// list can still open with nothing selected.
function selectDueToday(): void {
  selectedIds.value = new Set(dueTodayIds.value)
}

function toggleOne(jobId: number, checked: boolean): void {
  const next = new Set(selectedIds.value)
  if (checked) {
    next.add(jobId)
  } else {
    next.delete(jobId)
  }
  selectedIds.value = next
}

function identityLabel(candidate: ShippingLogCandidate): string {
  return candidate.split_suffix
    ? `${candidate.part_number} ${candidate.split_suffix}`
    : candidate.part_number
}

// Same rule as the Inspect drawer's build type and the CSV export's Build column.
function classifierLabel(candidate: ShippingLogCandidate): string {
  const build = buildLabel(candidate.build_type)
  const qualifier = (candidate.build_qualifier ?? '').toUpperCase()
  const reference = (candidate.repeat_reference ?? '').trim()
  if (build) {
    const withReference = reference ? `${build} ${reference}` : build
    return qualifier ? `${withReference} · ${qualifier}` : withReference
  }
  if (qualifier) return reference ? `${qualifier} ${reference}` : qualifier
  return reference
}

function dueLabel(candidate: ShippingLogCandidate): string {
  if (candidate.resolved_ship_date) return formatShortDate(candidate.resolved_ship_date)
  return candidate.ship_date_text ?? '—'
}

const REASON_LABELS: Record<IneligibleReason, string> = {
  shipped: 'shipped',
  discarded: 'discarded',
  superseded: 'superseded',
  not_found: 'no longer found',
}

function ineligibleMessage(jobs: IneligibleJob[]): string {
  const counts = new Map<IneligibleReason, number>()
  for (const job of jobs) counts.set(job.reason, (counts.get(job.reason) ?? 0) + 1)
  const reasons = [...counts.entries()].map(([reason, count]) => `${count} ${REASON_LABELS[reason]}`)
  const subject = jobs.length === 1 ? '1 selected job changed' : `${jobs.length} selected jobs changed`
  return `${subject} since this list loaded (${reasons.join(', ')}). ` +
    'The list was refreshed — review the selection and generate again.'
}

function clippedMessage(clippedJobIds: number[]): string {
  const labels = clippedJobIds.map(jobId => {
    const candidate = candidates.value.find(entry => entry.job_id === jobId)
    return candidate ? identityLabel(candidate) : `job #${jobId}`
  })
  return `Notes won't print in full for ${labels.join(', ')}. Check those boxes before printing.`
}

function saveDownload(download: ShippingLogDownload): void {
  const url = URL.createObjectURL(download.file)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = download.filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

async function onGenerate(): Promise<void> {
  if (!canGenerate.value) return
  phase.value = 'generating'
  inlineError.value = null
  const jobIds = candidates.value
    .map(candidate => candidate.job_id)
    .filter(jobId => selectedIds.value.has(jobId))

  const outcome = await generateShippingLog(jobIds)

  if (outcome.kind === 'ok') {
    saveDownload(outcome.download)
    pushToast(`Shipping log downloaded: ${outcome.download.filename}`, 'success')
    if (outcome.download.clippedJobIds.length > 0) {
      pushToast(clippedMessage(outcome.download.clippedJobIds), 'error', 12_000)
    }
    phase.value = 'ready'
    emit('close')
  } else if (outcome.kind === 'ineligible') {
    pushToast(ineligibleMessage(outcome.jobs), 'error', 12_000)
    await loadCandidates()
  } else if (outcome.kind === 'template_unavailable') {
    if (candidateList.value) {
      candidateList.value = { ...candidateList.value, template_ready: false }
    }
    phase.value = 'ready'
  } else if (outcome.kind === 'selection_size') {
    inlineError.value = `A shipping log holds 1 to ${outcome.max} jobs; ${outcome.requested} were sent.`
    phase.value = 'ready'
  } else {
    inlineError.value = `Could not generate the shipping log: ${outcome.message}`
    phase.value = 'ready'
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="props.open"
         class="fixed inset-0 z-50 flex items-center justify-center"
         data-testid="shipping-log-modal">
      <div class="absolute inset-0 bg-black/50" data-testid="shipping-log-backdrop" @click="requestClose" />
      <div class="relative z-10 w-full max-w-4xl mx-4 rounded-xl bg-surface-raised shadow-2xl flex flex-col max-h-[85vh]"
           role="dialog"
           aria-modal="true"
           aria-labelledby="shipping-log-title">
        <header class="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-slate-700">
          <h2 id="shipping-log-title" class="text-lg font-semibold text-slate-800 dark:text-slate-100">
            Create Shipping Log
          </h2>
          <span v-if="phase === 'ready' || phase === 'generating'"
                class="text-sm text-slate-500 dark:text-slate-400 tabular-nums"
                data-testid="shipping-log-selected-count">
            {{ selectedCount }} selected
          </span>
        </header>

        <div class="px-6 py-4 flex-1 min-h-0 flex flex-col gap-3">
          <div v-if="phase === 'loading'" class="text-sm text-slate-500" data-testid="shipping-log-loading">
            Loading planned jobs…
          </div>

          <div v-else-if="phase === 'load_failed'" class="space-y-3" data-testid="shipping-log-load-error">
            <p class="text-sm text-warn-600">{{ loadError }}</p>
            <button type="button"
                    class="px-3 py-1.5 rounded bg-slate-200 hover:bg-slate-300 dark:bg-slate-700 dark:hover:bg-slate-600 text-sm focus-ring"
                    data-testid="shipping-log-retry"
                    @click="loadCandidates">
              Retry
            </button>
          </div>

          <template v-else>
            <p v-if="!templateReady"
               class="rounded border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-sm text-amber-800 dark:text-amber-200"
               data-testid="shipping-log-template-missing">
              Shipping log template is missing from this build.
            </p>
            <p v-if="candidateList?.truncated"
               class="rounded border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-sm text-amber-800 dark:text-amber-200"
               data-testid="shipping-log-truncated">
              Showing first {{ candidates.length }} of {{ candidateList.total }} planned jobs.
            </p>
            <p v-if="inlineError"
               class="rounded border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 px-3 py-2 text-sm text-red-800 dark:text-red-200"
               data-testid="shipping-log-inline-error">
              {{ inlineError }}
            </p>

            <p v-if="candidates.length === 0" class="text-sm text-slate-500 italic" data-testid="shipping-log-empty">
              No planned jobs.
            </p>
            <div v-else class="flex items-center gap-2">
              <button type="button"
                      class="text-xs font-medium px-3 py-1 rounded-full border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus-ring"
                      data-testid="shipping-log-select-today"
                      :disabled="phase === 'generating' || dueTodayIds.length === 0"
                      :title="dueTodayIds.length === 0 ? 'No jobs are due today' : 'Select only the jobs due today'"
                      @click="selectDueToday">
                Select Today ({{ dueTodayIds.length }})
              </button>
            </div>
            <div v-if="candidates.length > 0" class="min-h-0 overflow-y-auto rounded border border-slate-200 dark:border-slate-700">
              <table class="w-full text-sm border-separate border-spacing-0">
                <thead class="sticky top-0 bg-slate-100 dark:bg-slate-700 text-left text-xs uppercase tracking-wider font-medium text-slate-600 dark:text-slate-300">
                  <tr>
                    <th class="px-3 py-2 w-28">
                      <button type="button"
                              class="text-xs normal-case tracking-normal text-blue-600 dark:text-blue-400 hover:underline disabled:opacity-50"
                              data-testid="shipping-log-toggle-all"
                              :disabled="phase === 'generating'"
                              @click="toggleAll">
                        {{ allSelected ? 'Deselect All' : 'Select All' }}
                      </button>
                    </th>
                    <th class="px-3 py-2">Job</th>
                    <th class="px-3 py-2">Build</th>
                    <th class="px-3 py-2 text-right">Qty</th>
                    <th class="px-3 py-2">Due</th>
                    <th class="px-3 py-2">Customer</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="candidate in candidates" :key="candidate.job_id"
                      class="[&>td]:border-b [&>td]:border-slate-200/70 dark:[&>td]:border-slate-700/60 hover:bg-slate-50 dark:hover:bg-slate-700/50"
                      data-testid="shipping-log-row"
                      :data-job-id="candidate.job_id">
                    <td class="px-3 py-1.5">
                      <input type="checkbox"
                             class="rounded border-slate-300 dark:border-slate-600 text-blue-600 focus:ring-blue-500"
                             data-testid="shipping-log-row-checkbox"
                             :aria-label="`Select ${identityLabel(candidate)}`"
                             :checked="selectedIds.has(candidate.job_id)"
                             :disabled="phase === 'generating'"
                             @change="toggleOne(candidate.job_id, ($event.target as HTMLInputElement).checked)" />
                    </td>
                    <td class="px-3 py-1.5 font-semibold text-slate-800 dark:text-slate-100 whitespace-nowrap">
                      {{ candidate.part_number }}<span v-if="candidate.split_suffix" class="ml-1 text-slate-400 dark:text-slate-500 text-xs font-normal">{{ candidate.split_suffix }}</span>
                    </td>
                    <td class="px-3 py-1.5 font-medium tracking-wider text-slate-700 dark:text-slate-300 whitespace-nowrap">
                      {{ classifierLabel(candidate) }}
                    </td>
                    <td class="px-3 py-1.5 tabular-nums text-right text-slate-700 dark:text-slate-300">
                      {{ candidate.quantity }}
                    </td>
                    <td class="px-3 py-1.5 tabular-nums whitespace-nowrap text-slate-700 dark:text-slate-300">
                      {{ dueLabel(candidate) }}
                    </td>
                    <td class="px-3 py-1.5 text-slate-700 dark:text-slate-300">
                      {{ candidate.customer_name }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </div>

        <footer class="flex justify-end gap-3 px-6 py-4 border-t border-slate-200 dark:border-slate-700">
          <button type="button"
                  class="rounded px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  data-testid="shipping-log-cancel"
                  :disabled="phase === 'generating'"
                  @click="requestClose">
            Cancel
          </button>
          <button type="button"
                  class="rounded px-3 py-1.5 text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  data-testid="shipping-log-generate"
                  :disabled="!canGenerate"
                  @click="onGenerate">
            {{ generateLabel }}
          </button>
        </footer>
      </div>
    </div>
  </Teleport>
</template>
