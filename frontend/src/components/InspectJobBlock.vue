<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ConfirmDiscardJobModal from './ConfirmDiscardJobModal.vue'
import { useJobActions } from '@/composables/useJobActions'
import type { HistoryEditDraft, JobReadExpanded } from '@/api/history'
import { useJobFormatters } from '@/composables/useJobFormatters'

import type { PhotoDirectoryStatus } from '@/api/photos'

const props = defineProps<{
  job: JobReadExpanded
  isAnchor: boolean
  showAllData: boolean
  editLocked: boolean
  photoFolders: string[]
  photoStatus: PhotoDirectoryStatus | 'unknown'
  openPhotosCallback: (date_folder: string) => Promise<any>
  openGalleryCallback: (date_folder: string) => Promise<void>
}>()

const emit = defineEmits<{
  editStarted: [jobId: number]
  editEnded: [jobId: number]
  edited: [job: JobReadExpanded]
  discarded: [jobId: number]
}>()

const { canEdit, canDiscard, editJob, discardJob } = useJobActions()
const { identitySuffix, buildLabel } = useJobFormatters()

const mode = ref<'read' | 'edit'>('read')
const draft = ref<HistoryEditDraft>({})
const prefill = ref<HistoryEditDraft>({})
const reason = ref('')

type SaveOutcomeKind = 'validation' | 'collision' | 'conflict' | 'network' | null
const saveErrorKind = ref<SaveOutcomeKind>(null)
const saveErrorMessage = ref<string | null>(null)
const saveErrorField = ref<string | null>(null)
const saveErrorCollidingJobId = ref<number | null>(null)

const discardError = ref<string | null>(null)

const saving = ref(false)
const discarding = ref(false)
const confirmOpen = ref(false)

watch(() => props.job.id, () => {
  mode.value = 'read'
  draft.value = {}
  prefill.value = {}
  reason.value = ''
  saveErrorKind.value = null
  saveErrorMessage.value = null
  saveErrorField.value = null
  saveErrorCollidingJobId.value = null
  discardError.value = null
  saving.value = false
  discarding.value = false
  confirmOpen.value = false
})

const discardAvailable = computed(() => canDiscard(props.job))

import { photo_folder_for } from '@/api/photos'
import { useToast } from '@/composables/useToast'
const { show: showToast } = useToast()

const photoFolder = computed(() => photo_folder_for(props.job))

const photosAvailable = computed(() => {
  return photoFolder.value !== null &&
         props.photoStatus === 'ok' &&
         props.photoFolders.includes(photoFolder.value)
})

const photoDisabledTooltip = computed(() => {
  if (photosAvailable.value) return ''
  if (photoFolder.value === null) return 'Job has not shipped'
  if (props.photoStatus === 'unconfigured') return 'Shipping photos directory is not configured'
  if (props.photoStatus === 'unavailable') return 'Shipping photos directory is unreachable'
  if (props.photoStatus === 'unknown') return 'Could not check for photos'
  return `No photos folder for ${photoFolder.value}`
})

const photoOpening = ref(false)

async function onOpenPhotos() {
  if (!photoFolder.value || !photosAvailable.value) return
  photoOpening.value = true
  
  const result = await props.openPhotosCallback(photoFolder.value)
  photoOpening.value = false
  
  if (result.kind === 'ok') {
    showToast(`Opened ${result.date_folder} on the production computer.`, 'success')
  } else if (result.kind === 'rate_limited') {
    showToast(`Please wait ${result.retry_after_seconds} seconds before opening another folder.`, 'error')
  } else if (result.kind === 'not_found') {
    showToast(`Photos folder ${photoFolder.value} no longer exists.`, 'error')
  } else if (result.kind === 'unconfigured') {
    showToast('Shipping photos directory is not configured.', 'error')
  } else if (result.kind === 'unavailable') {
    showToast('Shipping photos directory is unreachable.', 'error')
  } else if (result.kind === 'shell_error' || result.kind === 'network') {
    showToast('Failed to open photos folder.', 'error')
  }
}
function enterEditMode(): void {
  const pre: HistoryEditDraft = {
    part_number:      props.job.assembly.part_number,
    build_type:       props.job.build_type ?? '',
    split_suffix:     props.job.split_suffix ?? '',
    repeat_reference: props.job.repeat_reference ?? '',
    build_qualifier:  props.job.build_qualifier ?? '',
    raw_customer:     props.job.customer.name,
    raw_qty:          String(props.job.quantity),
    raw_shipped:      props.job.shipped_at ?? '',
  }
  prefill.value = pre
  draft.value   = { ...pre }
  mode.value    = 'edit'
  emit('editStarted', props.job.id)
}

function cancelEdit(): void {
  mode.value = 'read'
  draft.value = {}
  prefill.value = {}
  reason.value = ''
  saveErrorKind.value = null
  saveErrorMessage.value = null
  saveErrorField.value = null
  saveErrorCollidingJobId.value = null
  emit('editEnded', props.job.id)
}

const CLEARABLE_FIELDS = ['split_suffix', 'repeat_reference', 'build_qualifier'] as const
type ClearableField = typeof CLEARABLE_FIELDS[number]

function isDirty(field: keyof HistoryEditDraft): boolean {
  const d = (draft.value[field] ?? '').trim()
  const p = (prefill.value[field] ?? '').trim()
  const clearable = (CLEARABLE_FIELDS as readonly string[]).includes(field)
  if (clearable) {
    return d !== p
  }
  return d !== '' && d !== p
}

const reasonLength = computed(() => reason.value.trim().length)
const reasonValid  = computed(() => reasonLength.value >= 1 && reasonLength.value <= 500)

const saveEnabled = computed((): boolean => {
  if (!reasonValid.value) return false
  if (saveErrorKind.value === 'conflict') return false
  const allFields: Array<keyof HistoryEditDraft> = [
    'part_number', 'build_type', 'split_suffix', 'repeat_reference', 'build_qualifier',
    'raw_qty', 'raw_customer', 'raw_shipped',
  ]
  return allFields.some(f => isDirty(f))
})

function buildEditPayload(): HistoryEditDraft {
  const payload: HistoryEditDraft = {}
  const allFields: Array<keyof HistoryEditDraft> = [
    'part_number', 'build_type', 'split_suffix', 'repeat_reference', 'build_qualifier',
    'raw_qty', 'raw_customer', 'raw_shipped',
  ]
  for (const f of allFields) {
    const d = (draft.value[f] ?? '').trim()
    const p = (prefill.value[f] ?? '').trim()
    if (d === p) continue
    const clearable = (CLEARABLE_FIELDS as readonly string[]).includes(f)
    if (d === '') {
      if (clearable) payload[f as ClearableField] = ''
    } else {
      (payload as Record<string, string>)[f] = d
    }
  }
  return payload
}

async function onSave(): Promise<void> {
  if (!saveEnabled.value) return
  saving.value = true
  saveErrorKind.value = null
  saveErrorMessage.value = null
  saveErrorField.value = null
  saveErrorCollidingJobId.value = null
  
  const outcome = await editJob(props.job.id, buildEditPayload(), reason.value.trim())
  saving.value = false

  if (outcome.kind === 'ok') {
    mode.value = 'read'
    draft.value = {}
    prefill.value = {}
    reason.value = ''
    emit('edited', outcome.job)
    emit('editEnded', props.job.id)
  } else if (outcome.kind === 'validation') {
    saveErrorKind.value = 'validation'
    saveErrorField.value = outcome.field
    saveErrorMessage.value = outcome.message
  } else if (outcome.kind === 'collision') {
    saveErrorKind.value = 'collision'
    saveErrorCollidingJobId.value = outcome.colliding_job_id
  } else if (outcome.kind === 'conflict') {
    saveErrorKind.value = 'conflict'
    saveErrorMessage.value = outcome.message
  } else if (outcome.kind === 'network') {
    saveErrorKind.value = 'network'
    saveErrorMessage.value = outcome.message
  }
}

async function onConfirmDiscard(discardReason: string): Promise<void> {
  discarding.value = true
  const outcome = await discardJob(props.job.id, discardReason)
  discarding.value = false

  if (outcome.kind === 'ok' || outcome.kind === 'gone') {
    emit('editEnded', props.job.id)
    emit('discarded', props.job.id)
    return
  }
  confirmOpen.value = false
  discardError.value = outcome.message
}

function clearFieldError(field: keyof HistoryEditDraft): void {
  if (saveErrorKind.value === 'validation' && saveErrorField.value === field) {
    saveErrorKind.value = null
  }
  if (saveErrorKind.value === 'conflict') {
    saveErrorKind.value = null
  }
}

function fieldError(field: keyof HistoryEditDraft): string | null {
  if (saveErrorKind.value === 'validation' && saveErrorField.value === field) {
    return saveErrorMessage.value
  }
  return null
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

const flatEntries = computed<Array<[string, string]>>(() => flatten(props.job))

interface CuratedField {
  label: string
  value: string
}

function salespersonLabel(job: JobReadExpanded): string {
  if (!job.salesperson) return '—'
  const name = (job.salesperson.name ?? '').trim()
  return name ? `${job.salesperson.code} — ${name}` : job.salesperson.code
}

function classificationLabel(assembly: any): string {
  if (!assembly.classifications || assembly.classifications.length === 0) return '—'
  return assembly.classifications.map((c: any) => c.code).join(', ')
}

function buildTypeLabel(job: JobReadExpanded): string {
  const bt = buildLabel(job.build_type)
  const rr = (job.repeat_reference ?? '').trim()
  if (bt && rr) return `${bt} ${rr}`
  if (bt) return bt
  if (rr) return rr
  return '—'
}

function lineAssignmentLabel(job: JobReadExpanded): string {
  const flags = []
  if (job.line_1) flags.push('1')
  if (job.line_2) flags.push('2')
  if (job.line_3) flags.push('3')
  if (flags.length === 0) return '—'
  return `Line: ${flags.join(', ')}`
}

function shipDateField(job: JobReadExpanded): CuratedField {
  if (job.status === 'planned') {
    return { label: 'Ship date (planned)', value: job.resolved_ship_date ?? '—' }
  }
  return { label: 'Shipped', value: job.shipped_at ?? '—' }
}

const curated = computed<CuratedField[]>(() => {
  const job = props.job
  return [
    { label: 'Part number', value: job.assembly.part_number + identitySuffix(job) },
    { label: 'Customer', value: job.customer.name },
    { label: 'Sales person', value: salespersonLabel(job) },
    { label: 'Classification', value: classificationLabel(job.assembly) },
    { label: 'Build type', value: buildTypeLabel(job) },
    { label: 'Quantity', value: String(job.quantity) },
    shipDateField(job),
    { label: 'Ship lead time', value: job.ship_lead_time_raw ?? '—' },
    { label: 'Ship method', value: job.ship_method ?? '—' },
    { label: 'Line', value: lineAssignmentLabel(job) },
  ]
})
</script>

<template>
  <div class="inspect-job-block" data-testid="inspect-job-block" :data-job-id="job.id">
    <template v-if="mode === 'read'">
      <div v-if="discardError"
           class="mb-4 rounded-lg border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 px-4 py-3 text-red-800 dark:text-red-200 text-sm flex items-start justify-between gap-2">
        <span>{{ discardError }}</span>
        <button @click="discardError = null" aria-label="Dismiss error"
                class="shrink-0 text-red-600 dark:text-red-400 hover:text-red-800 dark:hover:text-red-200">✕</button>
      </div>

      <dl v-if="!showAllData" class="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm">
        <template v-for="(field, i) in curated" :key="i">
          <dt class="font-medium text-slate-500 dark:text-slate-400 whitespace-nowrap">{{ field.label }}</dt>
          <dd class="text-slate-800 dark:text-slate-200 break-words">{{ field.value }}</dd>
        </template>
      </dl>
      <dl v-else class="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm">
        <template v-for="[key, value] in flatEntries" :key="key">
          <dt class="font-mono text-xs text-slate-500 dark:text-slate-400 pt-1 whitespace-nowrap">{{ key }}</dt>
          <dd class="text-slate-800 dark:text-slate-200 break-words">{{ value }}</dd>
        </template>
      </dl>

      <div class="mt-6 pt-4 border-t border-slate-200 dark:border-slate-700 flex justify-end gap-3">
        <div class="flex gap-2 mr-auto">
          <button
            data-testid="inspect-gallery-btn"
            type="button"
            :disabled="!photosAvailable"
            :title="photoDisabledTooltip || 'View photos in gallery'"
            class="rounded px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 transition-colors disabled:opacity-50 disabled:bg-slate-100 dark:bg-slate-700 dark:hover:bg-slate-600 dark:text-slate-300 dark:disabled:bg-slate-800 flex items-center gap-2"
            @click="photoFolder && props.openGalleryCallback(photoFolder)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            Gallery
          </button>
          
          <!-- Only show folder button if we are on loopback (isConsole), but actually the old button was always visible just didn't work over LAN. We can just keep it -->
          <button
            data-testid="inspect-photos-btn"
            type="button"
            :disabled="!photosAvailable || photoOpening"
            :title="photoDisabledTooltip || 'Open photos folder on production computer'"
            class="rounded px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 transition-colors disabled:opacity-50 disabled:bg-slate-100 dark:bg-slate-700 dark:hover:bg-slate-600 dark:text-slate-300 dark:disabled:bg-slate-800 flex items-center gap-2"
            @click="onOpenPhotos"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
            </svg>
            Folder
          </button>
        </div>
        <button
          v-if="canEdit(job)"
          data-testid="inspect-edit-btn"
          type="button"
          :disabled="editLocked"
          :title="editLocked ? 'Another job is being edited' : ''"
          class="rounded px-3 py-1.5 text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50 disabled:bg-slate-400"
          @click="enterEditMode"
        >
          Edit
        </button>
        <button
          v-if="discardAvailable"
          data-testid="inspect-discard-btn"
          type="button"
          class="rounded px-3 py-1.5 text-sm font-medium bg-red-600 hover:bg-red-700 text-white transition-colors"
          @click="confirmOpen = true"
        >
          Discard job
        </button>
      </div>
    </template>
    
    <template v-else>
      <div v-if="saveErrorKind === 'collision'"
           class="mb-4 rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-amber-800 dark:text-amber-200 text-sm">
        Conflicts with job #{{ saveErrorCollidingJobId }}. Edit the job number to a unique identity.
      </div>
      <div v-else-if="saveErrorKind === 'conflict' || saveErrorKind === 'network'"
           class="mb-4 rounded-lg border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 px-4 py-3 text-red-800 dark:text-red-200 text-sm flex items-start justify-between gap-2">
        <span>{{ saveErrorMessage }}</span>
        <button v-if="saveErrorKind === 'network'" @click="onSave" :disabled="saving"
                class="shrink-0 rounded px-2 py-1 text-xs font-medium bg-red-200 dark:bg-red-700 hover:bg-red-300 dark:hover:bg-red-600 transition-colors disabled:opacity-50">
          Retry
        </button>
      </div>

      <label class="block mb-4">
        <span class="text-sm font-medium text-slate-700 dark:text-slate-300">
          Reason <span class="text-red-500">*</span>
        </span>
        <textarea
          v-model="reason"
          data-testid="edit-reason-textarea"
          rows="2"
          placeholder="Required — briefly explain the correction."
          class="mt-1 block w-full rounded border text-sm px-3 py-2 resize-none bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
          :class="reasonLength > 500 ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
        />
        <span class="block text-right text-xs mt-0.5" :class="reasonLength > 500 ? 'text-red-600 dark:text-red-400' : 'text-slate-400'">
          {{ reasonLength }} / 500
        </span>
      </label>

      <div class="space-y-4">
        <p class="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Identity</p>
        
        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Part number</label>
          <input
            v-model="draft.part_number"
            data-testid="edit-part-number"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('part_number') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('part_number')"
          />
          <p v-if="fieldError('part_number')" class="mt-1 text-xs text-red-600 dark:text-red-400">{{ fieldError('part_number') }}</p>
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Build type (new / ronc / rowc)</label>
          <input
            v-model="draft.build_type"
            data-testid="edit-build-type"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('build_type') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('build_type')"
          />
          <p v-if="fieldError('build_type')" class="mt-1 text-xs text-red-600 dark:text-red-400">{{ fieldError('build_type') }}</p>
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Split suffix <span class="font-normal text-slate-400">(optional — blank to clear)</span></label>
          <input
            v-model="draft.split_suffix"
            data-testid="edit-split-suffix"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('split_suffix') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('split_suffix')"
          />
          <p v-if="fieldError('split_suffix')" class="mt-1 text-xs text-red-600 dark:text-red-400">{{ fieldError('split_suffix') }}</p>
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Repeat reference <span class="font-normal text-slate-400">(optional — blank to clear)</span></label>
          <input
            v-model="draft.repeat_reference"
            data-testid="edit-repeat-reference"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('repeat_reference') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('repeat_reference')"
          />
          <p v-if="fieldError('repeat_reference')" class="mt-1 text-xs text-red-600 dark:text-red-400">{{ fieldError('repeat_reference') }}</p>
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Build qualifier <span class="font-normal text-slate-400">(rwk / rework / rma — blank to clear)</span></label>
          <input
            v-model="draft.build_qualifier"
            data-testid="edit-build-qualifier"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('build_qualifier') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('build_qualifier')"
          />
          <p v-if="fieldError('build_qualifier')" class="mt-1 text-xs text-red-600 dark:text-red-400">{{ fieldError('build_qualifier') }}</p>
        </div>

        <p class="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 pt-2">Ship-time</p>
        
        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Customer</label>
          <input
            v-model="draft.raw_customer"
            data-testid="edit-raw-customer"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('raw_customer') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('raw_customer')"
          />
          <p v-if="fieldError('raw_customer')" class="mt-1 text-xs text-red-600 dark:text-red-400">{{ fieldError('raw_customer') }}</p>
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Quantity</label>
          <input
            v-model="draft.raw_qty"
            data-testid="edit-raw-qty"
            type="text"
            inputmode="numeric"
            class="block w-full rounded border px-3 py-2 text-sm bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('raw_qty') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('raw_qty')"
          />
          <p v-if="fieldError('raw_qty')" class="mt-1 text-xs text-red-600 dark:text-red-400">{{ fieldError('raw_qty') }}</p>
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Shipped date (MM/DD/YYYY or YYYY-MM-DD)</label>
          <input
            v-model="draft.raw_shipped"
            data-testid="edit-raw-shipped"
            type="text"
            placeholder="e.g. 05/01/2026"
            class="block w-full rounded border px-3 py-2 text-sm bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('raw_shipped') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('raw_shipped')"
          />
          <p v-if="fieldError('raw_shipped')" class="mt-1 text-xs text-red-600 dark:text-red-400">{{ fieldError('raw_shipped') }}</p>
        </div>
      </div>

      <div class="mt-6 pt-4 border-t border-slate-200 dark:border-slate-700 flex justify-end gap-3">
        <button
          data-testid="edit-cancel-btn"
          type="button"
          class="rounded px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors"
          @click="cancelEdit"
        >
          Cancel
        </button>
        <button
          data-testid="edit-save-btn"
          type="button"
          :disabled="!saveEnabled || saving"
          class="rounded px-3 py-1.5 text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          @click="onSave"
        >
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
      </div>
    </template>

    <ConfirmDiscardJobModal
      v-if="canDiscard(job)"
      :open="confirmOpen"
      :job="job"
      @confirm="onConfirmDiscard"
      @cancel="confirmOpen = false"
    />
  </div>
</template>
