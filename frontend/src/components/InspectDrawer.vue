<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import SlideOverPanel from './SlideOverPanel.vue'
import ConfirmDiscardJobModal from './ConfirmDiscardJobModal.vue'
import type { HistoryEditDraft, HistoryEditError, JobReadExpanded } from '@/api/history'

const props = defineProps<{
  row:          JobReadExpanded | null
  canDiscard?:  boolean
  canEdit?:     boolean
  /** Called when the operator confirms a discard. Pre: canDiscard is true. */
  discardImpl?: (jobId: number, reason: string) => Promise<void>
  /** Called when the operator saves an edit. Pre: canEdit is true. */
  editImpl?:    (jobId: number, edit: HistoryEditDraft, reason: string) => Promise<void>
}>()

const emit = defineEmits<{
  close: []
}>()

// ── drawer mode ─────────────────────────────────────────────────────────────
const mode         = ref<'read' | 'edit'>('read')
const draft        = ref<HistoryEditDraft>({})
const prefill      = ref<HistoryEditDraft>({})
const reason       = ref('')
const saveError    = ref<HistoryEditError | null>(null)
const discardError = ref<string | null>(null)
const saving       = ref(false)
const discarding   = ref(false)
const confirmOpen  = ref(false)

// Reset edit state whenever the drawer row changes or closes.
watch(() => props.row, () => {
  mode.value      = 'read'
  draft.value     = {}
  prefill.value   = {}
  reason.value    = ''
  saveError.value = null
  discardError.value = null
  saving.value    = false
  discarding.value = false
  confirmOpen.value = false
})

function enterEditMode(): void {
  if (!props.row) return
  const job = props.row
  const pre: HistoryEditDraft = {
    part_number:      job.assembly.part_number,
    build_type:       job.build_type ?? '',
    split_suffix:     job.split_suffix ?? '',
    repeat_reference: job.repeat_reference ?? '',
    build_qualifier:  job.build_qualifier ?? '',
    raw_customer:     job.customer.name,
    raw_qty:          String(job.quantity),
    raw_shipped:      job.shipped_at ?? '',
  }
  prefill.value = pre
  draft.value   = { ...pre }
  mode.value    = 'edit'
}

function cancelEdit(): void {
  mode.value      = 'read'
  draft.value     = {}
  prefill.value   = {}
  reason.value    = ''
  saveError.value = null
}

// Fields that may be explicitly cleared to null by sending "" on the wire.
const CLEARABLE_FIELDS = ['split_suffix', 'repeat_reference', 'build_qualifier'] as const
type ClearableField = typeof CLEARABLE_FIELDS[number]

function isDirty(field: keyof HistoryEditDraft): boolean {
  const d = (draft.value[field] ?? '').trim()
  const p = (prefill.value[field] ?? '').trim()
  const clearable = (CLEARABLE_FIELDS as readonly string[]).includes(field)
  if (clearable) {
    // Clearable: any change (including blank) is meaningful.
    return d !== p
  }
  // Non-clearable: blank is not a valid edit — skip those.
  return d !== '' && d !== p
}

const reasonLength = computed(() => reason.value.trim().length)
const reasonValid  = computed(() => reasonLength.value >= 1 && reasonLength.value <= 500)

// Save button is enabled when reason is valid AND at least one field is dirty.
const saveEnabled = computed((): boolean => {
  if (!reasonValid.value) return false
  const allFields: Array<keyof HistoryEditDraft> = [
    'part_number', 'build_type', 'split_suffix', 'repeat_reference', 'build_qualifier',
    'raw_qty', 'raw_customer', 'raw_shipped',
  ]
  return allFields.some(f => isDirty(f))
})

/**
 * Build minimal PATCH payload:
 *  - absent key  → field unchanged (omit from payload)
 *  - ""          → clear to null (clearable fields only)
 *  - "<value>"   → set to trimmed value
 * Non-clearable fields blanked by the user are silently omitted (no-op).
 */
function buildEditPayload(): HistoryEditDraft {
  const payload: HistoryEditDraft = {}
  const allFields: Array<keyof HistoryEditDraft> = [
    'part_number', 'build_type', 'split_suffix', 'repeat_reference', 'build_qualifier',
    'raw_qty', 'raw_customer', 'raw_shipped',
  ]
  for (const f of allFields) {
    const d = (draft.value[f] ?? '').trim()
    const p = (prefill.value[f] ?? '').trim()
    if (d === p) continue  // unchanged — omit
    const clearable = (CLEARABLE_FIELDS as readonly string[]).includes(f)
    if (d === '') {
      if (clearable) payload[f as ClearableField] = ''  // explicit clear
      // non-clearable blank → omit (not a valid edit)
    } else {
      (payload as Record<string, string>)[f] = d
    }
  }
  return payload
}

async function onSave(): Promise<void> {
  if (!props.row || !props.editImpl || !saveEnabled.value) return
  saving.value = true
  saveError.value = null
  try {
    await props.editImpl(props.row.id, buildEditPayload(), reason.value.trim())
    // Success — the store has already updated inspected; switch back to read.
    mode.value      = 'read'
    draft.value     = {}
    prefill.value   = {}
    reason.value    = ''
  } catch (err: unknown) {
    saveError.value = err as HistoryEditError
  } finally {
    saving.value = false
  }
}

async function onConfirmDiscard(discardReason: string): Promise<void> {
  if (!props.row || !props.discardImpl) return
  discarding.value = true
  try {
    await props.discardImpl(props.row.id, discardReason)
    // Store cleared inspected → row becomes null → drawer closes naturally.
    confirmOpen.value = false
  } catch (err: unknown) {
    confirmOpen.value = false
    const msg =
      err instanceof Error ? err.message : 'Could not discard job. Please retry.'
    discardError.value = msg
  } finally {
    discarding.value = false
  }
}

function handleClose(): void {
  if (mode.value === 'edit') {
    // ConfirmingDiscardChanges — synchronous confirm to stay simple.
    const ok = window.confirm('Discard unsaved changes?')
    if (!ok) return
  }
  emit('close')
}

type EditableField = keyof HistoryEditDraft

function clearFieldError(field: EditableField): void {
  if (saveError.value?.kind === 'validation' && saveError.value.field === field) {
    saveError.value = null
  }
}

function fieldError(field: EditableField): string | null {
  if (
    saveError.value?.kind === 'validation' &&
    saveError.value.field === field
  ) {
    return saveError.value.message
  }
  return null
}

// ── read-mode flat rendering ─────────────────────────────────────────────────
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

// ── read-mode curated rendering ──────────────────────────────────────────────
import { useInspectVerbosity } from '@/composables/useInspectVerbosity'
import { useJobFormatters } from '@/composables/useJobFormatters'

const { showAllData, toggle: toggleShowAllData } = useInspectVerbosity()
const { identitySuffix, buildLabel } = useJobFormatters()

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
  if (bt && rr) return `${bt} · RONC ${rr}`
  if (bt) return bt
  if (rr) return `RONC ${rr}`
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
  const job = props.row
  if (!job) return []
  return [
    { label: 'Part number', value: job.assembly.part_number + identitySuffix(job) },
    { label: 'Customer', value: job.customer.name },
    { label: 'Salesperson', value: salespersonLabel(job) },
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
  <SlideOverPanel
    :open="row !== null"
    width="xl"
    :ariaLabel="row ? `Job ${row.id} details` : ''"
    @close="handleClose"
  >
    <template #header>
      <h2 class="text-base font-semibold text-slate-800 dark:text-slate-100">
        Job #{{ row?.id }} — {{ row?.assembly.part_number }}
        <span v-if="mode === 'edit'" class="ml-2 text-xs font-normal text-blue-600 dark:text-blue-400">
          Editing
        </span>
      </h2>
      <button @click="handleClose" aria-label="Close inspector"
              data-testid="drawer-close-btn"
              class="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-100 ease-out focus-ring">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-slate-500" fill="none"
             viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </template>

    <!-- ── read mode ─────────────────────────────────────────────────────── -->
    <template v-if="mode === 'read'">
      <!-- Discard error banner (shown when discardImpl rejects) -->
      <div v-if="discardError"
           class="mb-4 rounded-lg border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 px-4 py-3 text-red-800 dark:text-red-200 text-sm flex items-start justify-between gap-2">
        <span>{{ discardError }}</span>
        <button @click="discardError = null" aria-label="Dismiss error"
                class="shrink-0 text-red-600 dark:text-red-400 hover:text-red-800 dark:hover:text-red-200">✕</button>
      </div>

      <div class="mb-4 flex items-center justify-end">
        <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400 cursor-pointer">
          <input
            type="checkbox"
            :checked="showAllData"
            @change="toggleShowAllData"
            data-testid="inspect-show-all-toggle"
            class="rounded border-slate-300 text-blue-600 focus:ring-blue-500/60"
          />
          Show All Data
        </label>
      </div>

      <dl v-if="!showAllData" class="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm">
        <template v-for="(field, i) in curated" :key="i">
          <dt class="font-medium text-slate-500 dark:text-slate-400
                     whitespace-nowrap">{{ field.label }}</dt>
          <dd class="text-slate-800 dark:text-slate-200 break-words">
            {{ field.value }}
          </dd>
        </template>
      </dl>

      <dl v-else class="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm">
        <template v-for="[key, value] in flatEntries" :key="key">
          <dt class="font-mono text-xs text-slate-500 dark:text-slate-400
                     pt-1 whitespace-nowrap">{{ key }}</dt>
          <dd class="text-slate-800 dark:text-slate-200 break-words">
            {{ value }}
          </dd>
        </template>
      </dl>

      <div v-if="(canDiscard || canEdit) && row !== null"
           class="mt-6 pt-4 border-t border-slate-200 dark:border-slate-700 flex justify-end gap-3">
        <button
          v-if="canEdit"
          data-testid="inspect-edit-btn"
          type="button"
          class="rounded px-3 py-1.5 text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white transition-colors"
          @click="enterEditMode"
        >
          Edit
        </button>
        <button
          v-if="canDiscard"
          data-testid="inspect-discard-btn"
          type="button"
          class="rounded px-3 py-1.5 text-sm font-medium bg-red-600 hover:bg-red-700 text-white transition-colors"
          @click="confirmOpen = true"
        >
          Discard job
        </button>
      </div>
    </template>

    <!-- ── edit mode ─────────────────────────────────────────────────────── -->
    <template v-else>
      <!-- Global error banners (collision / conflict / transport) -->
      <div v-if="saveError && saveError.kind === 'collision'"
           class="mb-4 rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-amber-800 dark:text-amber-200 text-sm">
        Conflicts with job #{{ (saveError as { kind: 'collision'; collidingJobId: number }).collidingJobId }}.
        Edit the job number to a unique identity.
      </div>
      <div v-else-if="saveError && (saveError.kind === 'conflict' || saveError.kind === 'transport')"
           class="mb-4 rounded-lg border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 px-4 py-3 text-red-800 dark:text-red-200 text-sm flex items-start justify-between gap-2">
        <span>{{ saveError.message }}</span>
        <button @click="onSave" :disabled="saving"
                class="shrink-0 rounded px-2 py-1 text-xs font-medium bg-red-200 dark:bg-red-700 hover:bg-red-300 dark:hover:bg-red-600 transition-colors disabled:opacity-50">
          Retry
        </button>
      </div>

      <!-- Reason textarea (required, above form fields) -->
      <label class="block mb-4">
        <span class="text-sm font-medium text-slate-700 dark:text-slate-300">
          Reason <span class="text-red-500">*</span>
        </span>
        <textarea
          v-model="reason"
          data-testid="edit-reason-textarea"
          rows="2"
          placeholder="Required — briefly explain the correction."
          class="mt-1 block w-full rounded border text-sm px-3 py-2 resize-none
                 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
          :class="reasonLength > 500
            ? 'border-red-400'
            : 'border-slate-300 dark:border-slate-600'"
        />
        <span class="block text-right text-xs mt-0.5"
              :class="reasonLength > 500 ? 'text-red-600 dark:text-red-400' : 'text-slate-400'">
          {{ reasonLength }} / 500
        </span>
      </label>

      <!-- Edit form — identity group then ship-time group -->
      <div class="space-y-4">
        <p class="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Identity
        </p>

        <!-- part_number -->
        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
            Part number
          </label>
          <input
            v-model="draft.part_number"
            data-testid="edit-part-number"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm
                   bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                   focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('part_number') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('part_number')"
          />
          <p v-if="fieldError('part_number')" class="mt-1 text-xs text-red-600 dark:text-red-400">
            {{ fieldError('part_number') }}
          </p>
        </div>

        <!-- build_type -->
        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
            Build type (new / ronc / rowc)
          </label>
          <input
            v-model="draft.build_type"
            data-testid="edit-build-type"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm
                   bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                   focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('build_type') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('build_type')"
          />
          <p v-if="fieldError('build_type')" class="mt-1 text-xs text-red-600 dark:text-red-400">
            {{ fieldError('build_type') }}
          </p>
        </div>

        <!-- split_suffix (clearable) -->
        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
            Split suffix <span class="font-normal text-slate-400">(optional — blank to clear)</span>
          </label>
          <input
            v-model="draft.split_suffix"
            data-testid="edit-split-suffix"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm
                   bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                   focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('split_suffix') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('split_suffix')"
          />
          <p v-if="fieldError('split_suffix')" class="mt-1 text-xs text-red-600 dark:text-red-400">
            {{ fieldError('split_suffix') }}
          </p>
        </div>

        <!-- repeat_reference (clearable) -->
        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
            Repeat reference <span class="font-normal text-slate-400">(optional — blank to clear)</span>
          </label>
          <input
            v-model="draft.repeat_reference"
            data-testid="edit-repeat-reference"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm
                   bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                   focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('repeat_reference') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('repeat_reference')"
          />
          <p v-if="fieldError('repeat_reference')" class="mt-1 text-xs text-red-600 dark:text-red-400">
            {{ fieldError('repeat_reference') }}
          </p>
        </div>

        <!-- build_qualifier (clearable) -->
        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
            Build qualifier <span class="font-normal text-slate-400">(rwk / rework / rma — blank to clear)</span>
          </label>
          <input
            v-model="draft.build_qualifier"
            data-testid="edit-build-qualifier"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm
                   bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                   focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('build_qualifier') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('build_qualifier')"
          />
          <p v-if="fieldError('build_qualifier')" class="mt-1 text-xs text-red-600 dark:text-red-400">
            {{ fieldError('build_qualifier') }}
          </p>
        </div>

        <p class="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 pt-2">
          Ship-time
        </p>

        <!-- raw_customer -->
        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
            Customer
          </label>
          <input
            v-model="draft.raw_customer"
            data-testid="edit-raw-customer"
            type="text"
            class="block w-full rounded border px-3 py-2 text-sm
                   bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                   focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('raw_customer') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('raw_customer')"
          />
          <p v-if="fieldError('raw_customer')" class="mt-1 text-xs text-red-600 dark:text-red-400">
            {{ fieldError('raw_customer') }}
          </p>
        </div>

        <!-- raw_qty -->
        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
            Quantity
          </label>
          <input
            v-model="draft.raw_qty"
            data-testid="edit-raw-qty"
            type="text"
            inputmode="numeric"
            class="block w-full rounded border px-3 py-2 text-sm
                   bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                   focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('raw_qty') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('raw_qty')"
          />
          <p v-if="fieldError('raw_qty')" class="mt-1 text-xs text-red-600 dark:text-red-400">
            {{ fieldError('raw_qty') }}
          </p>
        </div>

        <!-- raw_shipped -->
        <div>
          <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
            Shipped date (MM/DD/YYYY or YYYY-MM-DD)
          </label>
          <input
            v-model="draft.raw_shipped"
            data-testid="edit-raw-shipped"
            type="text"
            placeholder="e.g. 05/01/2026"
            class="block w-full rounded border px-3 py-2 text-sm
                   bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                   focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            :class="fieldError('raw_shipped') ? 'border-red-400' : 'border-slate-300 dark:border-slate-600'"
            @input="clearFieldError('raw_shipped')"
          />
          <p v-if="fieldError('raw_shipped')" class="mt-1 text-xs text-red-600 dark:text-red-400">
            {{ fieldError('raw_shipped') }}
          </p>
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
      v-if="canDiscard"
      :open="confirmOpen"
      :job="row"
      @confirm="onConfirmDiscard"
      @cancel="confirmOpen = false"
    />
  </SlideOverPanel>
</template>

