<script setup lang="ts">
/**
 * SecondOpsEntryModal — the only writing surface for 2nd OPS.
 *
 * The `fetch` prop has three arms because "loading" and "never audited" both
 * render an empty grid but need opposite affordances:
 *   loading — chrome plus a loading indicator; paste area, grid and ACCEPT are
 *             all DISABLED. Pasting into a grid that is about to be replaced by
 *             the arriving fetch would silently discard the paste.
 *   failed  — message and Retry; no editing surface, ACCEPT unreachable.
 *   loaded  — the only interactive arm.
 *
 * row_cap comes from record.limits.max_lines — never a client-side constant.
 * The note's maxlength comes from record.limits.note_max_chars for the same
 * reason.
 *
 * Every rendered value is text-interpolated; raw-HTML rendering is never applied
 * to pasted BOM content.
 */
import { computed, ref, watch } from 'vue'
import {
  describePasteRejection,
  emptyAuditBomFields,
  isBlankAuditBomLine,
  parseAuditBomPaste,
  type AuditBomFields,
  type SecondOpsFetch,
  type SecondOpsRecord,
  type SecondOpsSaveResult,
  type SecondOpsWritePayload,
} from '@/api/secondOps'
import type { JobReadExpanded } from '@/api/history'

const props = defineProps<{
  job: JobReadExpanded | null
  fetch: SecondOpsFetch
  isOpen: boolean
  /**
   * Injected so this component has no store dependency and no PUT of its own to
   * mis-wire. Mirrors InspectDrawer's discard-impl / edit-impl seam.
   */
  saveImpl: (jobId: number, payload: SecondOpsWritePayload) => Promise<SecondOpsSaveResult>
}>()

const emit = defineEmits<{
  close: []
  retry: []
  saved: [record: SecondOpsRecord]
  inspect: [fields: AuditBomFields]
}>()

type GridRowKey = number
interface GridRow {
  key: GridRowKey
  fields: AuditBomFields
}

const COLUMNS: { key: keyof AuditBomFields; label: string }[] = [
  { key: 'find_number', label: 'Find #' },
  { key: 'component_part_number', label: 'Component P/N' },
  { key: 'per_board_count', label: 'Per board' },
  { key: 'ref_des', label: 'Ref Des' },
  { key: 'description', label: 'Description' },
  { key: 'mount_type', label: 'Mount' },
  { key: 'quantity_needed', label: 'Qty need' },
  { key: 'quantity_on_hand', label: 'Qty on hand' },
]

let rowKeyCounter = 0
function nextGridRowKey(): GridRowKey {
  return rowKeyCounter++
}

const rows = ref<GridRow[]>([{ key: nextGridRowKey(), fields: emptyAuditBomFields() }])
const note = ref('')
const pasteText = ref('')
const banner = ref<{ tone: 'error' | 'warn'; message: string } | null>(null)
const saving = ref(false)
/** Set only by the `stale` arm: retrying is guaranteed to fail forever. */
const acceptPermanentlyDisabled = ref(false)

// Every bound is read straight off the loaded arm. There is deliberately no
// `SecondOpsRecord | null` intermediate: the nullable-record shape is the
// loading-versus-unaudited conflation SecondOpsFetch replaced, and it should not
// reappear inside the one component that consumes the union.
const rowCap = computed(() =>
  props.fetch.status === 'loaded' ? props.fetch.record.limits.max_lines : 0,
)
const noteMaxChars = computed(() =>
  props.fetch.status === 'loaded' ? props.fetch.record.limits.note_max_chars : 0,
)

const acceptDisabled = computed(
  () => saving.value || acceptPermanentlyDisabled.value || props.fetch.status !== 'loaded',
)

function seedFromRecord(record: SecondOpsRecord): void {
  rows.value = [
    ...(record.lines ?? []).map((line) => ({
      key: nextGridRowKey(),
      fields: {
        find_number: line.find_number,
        component_part_number: line.component_part_number,
        per_board_count: line.per_board_count,
        ref_des: line.ref_des,
        description: line.description,
        mount_type: line.mount_type,
        quantity_needed: line.quantity_needed,
        quantity_on_hand: line.quantity_on_hand,
      }
    })),
    { key: nextGridRowKey(), fields: emptyAuditBomFields() },
  ]
  note.value = record.unexpected_inclusions ?? ''
}

watch(
  () => props.fetch,
  (next) => {
    if (next.status === 'loaded') seedFromRecord(next.record)
  },
  { immediate: true, deep: false },
)

watch(
  () => props.isOpen,
  (open) => {
    if (!open) {
      pasteText.value = ''
      banner.value = null
      saving.value = false
      acceptPermanentlyDisabled.value = false
    }
  },
)

/**
 * Paste APPENDS. It never clears the grid: sectioned pastes are supported and
 * hand-typed rows cannot be destroyed. One trailing blank row is re-established
 * afterwards.
 */
function handlePaste(): void {
  const result = parseAuditBomPaste(pasteText.value, rowCap.value)
  if (!result.ok) {
    banner.value = { tone: 'error', message: describePasteRejection(result.rejection) }
    return
  }
  banner.value = null
  const kept = rows.value.filter((row) => !isBlankAuditBomLine(row.fields))
  rows.value = [
    ...kept,
    ...result.lines.map(fields => ({ key: nextGridRowKey(), fields })),
    { key: nextGridRowKey(), fields: emptyAuditBomFields() }
  ]
  pasteText.value = ''
}

/**
 * The paste event fires BEFORE the textarea's value updates, so parsing has to
 * wait a tick. Kept in the script rather than the template because `window` is
 * not in template scope.
 */
function handlePasteEvent(): void {
  setTimeout(handlePaste, 0)
}

function ensureTrailingBlankRow(): void {
  const last = rows.value[rows.value.length - 1]
  if (last === undefined || !isBlankAuditBomLine(last.fields)) {
    rows.value = [...rows.value, { key: nextGridRowKey(), fields: emptyAuditBomFields() }]
  }
}

function removeGridRow(index: number): void {
  rows.value.splice(index, 1)
  ensureTrailingBlankRow()
}

async function accept(): Promise<void> {
  const job = props.job
  if (job === null || acceptDisabled.value) return

  const lines = rows.value.map(r => r.fields).filter((fields) => !isBlankAuditBomLine(fields))
  const trimmedNote = note.value.trim()
  saving.value = true
  banner.value = null
  const result = await props.saveImpl(job.id, {
    lines,
    unexpected_inclusions: trimmedNote === '' ? null : trimmedNote,
  })
  saving.value = false

  switch (result.kind) {
    case 'saved':
      emit('saved', result.record)
      emit('close')
      return
    case 'rejected':
      banner.value = { tone: 'error', message: result.message }
      return
    case 'stale':
      acceptPermanentlyDisabled.value = true
      banner.value = { tone: 'warn', message: result.message }
      return
    case 'unreachable':
      banner.value = { tone: 'error', message: result.message }
      return
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="isOpen && job !== null"
        class="fixed inset-0 z-50 flex items-center justify-center"
        data-testid="second-ops-entry-modal"
      >
        <div class="absolute inset-0 bg-black/50" @click="emit('close')" />
        <div
          class="relative z-10 bg-surface-raised rounded-xl shadow-2xl w-full max-w-5xl mx-4 p-6 max-h-[90vh] overflow-y-auto"
          role="dialog"
          aria-labelledby="second-ops-entry-title"
        >
          <h3
            id="second-ops-entry-title"
            class="text-base font-semibold text-slate-800 dark:text-slate-100 mb-4"
          >
            2nd OPS — {{ job.assembly.part_number }}
          </h3>

          <!-- loading -->
          <div
            v-if="fetch.status === 'loading'"
            data-testid="second-ops-entry-loading"
            class="py-10 text-center text-sm text-slate-500 dark:text-slate-400"
          >
            Loading audit…
          </div>

          <!-- failed -->
          <div
            v-else-if="fetch.status === 'failed'"
            data-testid="second-ops-entry-failed"
            class="py-8 text-center"
          >
            <p class="text-sm text-slate-600 dark:text-slate-300 mb-4">{{ fetch.message }}</p>
            <button
              type="button"
              data-testid="second-ops-entry-retry-btn"
              class="rounded px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors"
              @click="emit('retry')"
            >
              Retry
            </button>
          </div>

          <!-- loaded -->
          <template v-else>
            <div
              v-if="banner !== null"
              :data-testid="`second-ops-entry-banner-${banner.tone}`"
              class="mb-4 rounded-lg px-4 py-3 text-sm"
              :class="banner.tone === 'warn'
                ? 'border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-800/20 text-amber-800 dark:text-amber-200'
                : 'border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-800/20 text-red-800 dark:text-red-200'"
            >{{ banner.message }}</div>

            <label class="block mb-4">
              <span class="text-sm font-medium text-slate-700 dark:text-slate-300">
                Paste from the Audit BOM sheet
              </span>
              <textarea
                v-model="pasteText"
                data-testid="second-ops-paste-area"
                rows="3"
                placeholder="Select the Audit BOM rows in Excel, copy, and paste here."
                class="mt-1 block w-full rounded border border-slate-300 dark:border-slate-600
                       bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                       text-sm px-3 py-2 font-mono resize-y
                       focus:outline-none focus:ring-2 focus:ring-blue-500/60"
                @paste="handlePasteEvent"
              />
              <button
                type="button"
                data-testid="second-ops-parse-btn"
                class="mt-2 rounded px-3 py-1 text-xs font-medium bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors"
                @click="handlePaste"
              >
                Add pasted rows
              </button>
            </label>

            <div class="overflow-x-auto mb-4">
              <table class="w-full text-sm" data-testid="second-ops-grid">
                <thead class="bg-slate-100 dark:bg-slate-700 text-left text-xs uppercase tracking-wide text-slate-600 dark:text-slate-300">
                  <tr>
                    <th v-for="column in COLUMNS" :key="column.key" class="px-2 py-1">
                      {{ column.label }}
                    </th>
                    <th class="px-2 py-1 w-20"></th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-200 dark:divide-slate-700">
                  <tr v-for="(row, index) in rows" :key="row.key" data-testid="second-ops-grid-row">
                    <td v-for="column in COLUMNS" :key="column.key" class="px-1 py-0.5">
                      <input
                        :value="row.fields[column.key] ?? ''"
                        :data-testid="`second-ops-grid-${column.key}`"
                        class="w-full min-w-24 rounded border border-transparent hover:border-slate-300 dark:hover:border-slate-600
                               bg-transparent text-slate-800 dark:text-slate-200 px-1 py-0.5
                               focus:outline-none focus:ring-2 focus:ring-blue-500/60"
                        @input="(event) => {
                          const value = (event.target as HTMLInputElement).value
                          row.fields[column.key] = value === '' ? null : value
                          ensureTrailingBlankRow()
                        }"
                      />
                    </td>
                    <td class="px-1 py-0.5 text-right whitespace-nowrap">
                      <button
                        v-if="index !== rows.length - 1"
                        type="button"
                        data-testid="second-ops-grid-remove-btn"
                        class="px-1 text-xs text-red-600 dark:text-red-400 hover:underline mr-2"
                        @click.stop="removeGridRow(index)"
                      >
                        Remove
                      </button>
                      <button
                        type="button"
                        data-testid="second-ops-grid-inspect-btn"
                        class="px-1 text-xs text-blue-600 dark:text-blue-400 hover:underline"
                        @click.stop="emit('inspect', row.fields)"
                      >
                        View
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <label class="block mb-4">
              <span class="text-sm font-medium text-slate-700 dark:text-slate-300">
                Unexpected Inclusions
              </span>
              <textarea
                v-model="note"
                data-testid="second-ops-note"
                rows="3"
                :maxlength="noteMaxChars"
                placeholder="Anything present on the board that the BOM does not account for."
                class="mt-1 block w-full rounded border border-slate-300 dark:border-slate-600
                       bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                       text-sm px-3 py-2 resize-y
                       focus:outline-none focus:ring-2 focus:ring-blue-500/60"
              />
            </label>

            <div class="flex justify-end gap-3">
              <button
                type="button"
                data-testid="second-ops-cancel-btn"
                class="rounded px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors"
                @click="emit('close')"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="second-ops-accept-btn"
                :disabled="acceptDisabled"
                class="rounded px-3 py-1.5 text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                @click="accept"
              >
                Accept
              </button>
            </div>
          </template>
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
