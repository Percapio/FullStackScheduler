<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import { storeToRefs } from 'pinia'
import { useStagingStore } from '@/stores/staging'
import { RAW_KEYS } from '@/composables/useCorrectionDraft'
import type { DraftKey } from '@/composables/useCorrectionDraft'
import type { ConflictGroup, StagingRowDetail, CorrectionPayload } from '@/api/staging'
import type { SubmitOutcome, DiscardOutcome } from '@/stores/staging'

const props = defineProps<{
  group: ConflictGroup | undefined
}>()

const emit = defineEmits<{
  /** Fires once when the group is fully resolved (all perRow.kind === 'ok'
   *  AND the (batch_id, group_key) pair is absent from the post-submit snapshot). */
  (e: 'resolved'): void
}>()

// ---------------------------------------------------------------------------
// §2.1  Per-row draft state — discriminated union
// ---------------------------------------------------------------------------
type RowDraftState =
  | { kind: 'pristine' }
  | { kind: 'edited'; changedFields: Partial<CorrectionPayload> }
  | { kind: 'discarded' }

// ---------------------------------------------------------------------------
// §4.1  AcceptOutcome types
// ---------------------------------------------------------------------------
type PerRowOutcome =
  | ({ rowId: number; action: 'edit' } & SubmitOutcome)
  | ({ rowId: number; action: 'discard' } & DiscardOutcome)

type AcceptOutcome = {
  resolved:  boolean
  perRow:    PerRowOutcome[]
  groupKey:  string
  batchId:   number
}

// ---------------------------------------------------------------------------
// §2.2  Component-local state
// ---------------------------------------------------------------------------
const store = useStagingStore()
const { groupBusy } = storeToRefs(store)

/** One entry per group.rows[i].id. Invariant I1: keys ⊆ current row ids. */
const drafts      = ref<Map<number, RowDraftState>>(new Map())
/** Local async lock for the Accept flow (§2.7). */
const submitting  = ref(false)
/** Set after onAccept completes; cleared on the next user mutation (§3.3). */
const lastOutcome = ref<AcceptOutcome | null>(null)

const busyKey = computed(() =>
  props.group ? `${props.group.batch_id}:${props.group.group_key}` : '',
)

// ---------------------------------------------------------------------------
// §3.1  Auto-grow textarea helper
// ---------------------------------------------------------------------------
/**
 * Expand a textarea to fit its content (single line when empty, multi-line
 * when content wraps). Called on every input event and after group resets
 * so note fields (raw_mfg_notes, raw_pcb_notes, etc.) display without clipping.
 */
function autoGrow(el: HTMLTextAreaElement): void {
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

/** Combined input handler: update draft state then auto-grow the textarea. */
function onCellInput(rowId: number, field: DraftKey, event: Event): void {
  const el = event.target as HTMLTextAreaElement
  setCell(rowId, field, el.value)
  autoGrow(el)
}

/** Re-size all visible textareas in the table after a group swap so
 *  pre-populated content is not truncated. */
function regrowAllCells(): void {
  nextTick(() => {
    document.querySelectorAll<HTMLTextAreaElement>('[data-testid^="conflict-cell-"]').forEach(autoGrow)
  })
}

// ---------------------------------------------------------------------------
// §2.3  Reset drafts when the group identity changes
// ---------------------------------------------------------------------------
watch(
  () => props.group && `${props.group.batch_id}:${props.group.group_key}`,
  (newKey, oldKey) => {
    if (newKey !== oldKey) {
      drafts.value.clear()
      submitting.value = false
      lastOutcome.value = null
      regrowAllCells()
    }
  },
)

// ---------------------------------------------------------------------------
// §2.4  Field-edit semantics
// ---------------------------------------------------------------------------
/** Clears lastOutcome whenever the user makes any edit or discard toggle
 *  so stale annotations don't survive an edit-and-retry cycle (§3.3). */
function clearOutcomeOnMutation() {
  lastOutcome.value = null
}

/**
 * Update a single cell's value in the drafts map.
 *
 * Pre:  drafts.get(rowId).kind ≠ 'discarded'
 *       field ∈ RAW_KEYS
 * Post: if nextValue equals original → field removed; if changedFields empty → pristine.
 *       otherwise → edited with changedFields updated.
 */
function setCell(rowId: number, field: DraftKey, nextValue: string): void {
  clearOutcomeOnMutation()
  const row = props.group?.rows.find(r => r.id === rowId)
  if (!row) return

  const state = drafts.value.get(rowId) ?? { kind: 'pristine' }
  // Guard: never mutate a discarded row (G-3.1.A enforces this at template level too)
  if (state.kind === 'discarded') return

  const original = ((row as Record<string, unknown>)[field] ?? '') as string
  const prior: Partial<CorrectionPayload> = state.kind === 'edited' ? state.changedFields : {}

  if (nextValue === original) {
    const next: Partial<CorrectionPayload> = { ...prior }
    delete next[field]
    if (Object.keys(next).length === 0) {
      // Collapse to pristine (invariant I2)
      drafts.value.set(rowId, { kind: 'pristine' })
    } else {
      drafts.value.set(rowId, { kind: 'edited', changedFields: next })
    }
  } else {
    const next: Partial<CorrectionPayload> = {
      ...prior,
      [field]: nextValue === '' ? null : nextValue,
    }
    drafts.value.set(rowId, { kind: 'edited', changedFields: next })
  }
}

// ---------------------------------------------------------------------------
// §2.5 / §2.6  Discard toggle semantics
// ---------------------------------------------------------------------------
/**
 * Toggle the discard state for a row.
 *
 * Invariant: a row's draft state never simultaneously holds { kind: 'edited' }
 * AND { kind: 'discarded' }. Toggling discard from 'edited' DROPS the edits;
 * toggling discard off returns to 'pristine', not 'edited'.
 * Rationale: prevents the surprise vector "I discarded then undiscarded —
 * why are my old keystrokes back?"
 *
 * Pre:  rowId ∈ {r.id : r ∈ props.group.rows}
 * Post: drafts[rowId] :=
 *         if prior kind === 'discarded' then { kind: 'pristine' }
 *         else                               { kind: 'discarded' }
 */
function toggleDiscard(rowId: number): void {
  clearOutcomeOnMutation()
  const prior = drafts.value.get(rowId) ?? { kind: 'pristine' }
  if (prior.kind === 'discarded') {
    drafts.value.set(rowId, { kind: 'pristine' })
  } else {
    // Covers both 'pristine' and 'edited' — edits are dropped intentionally.
    drafts.value.set(rowId, { kind: 'discarded' })
  }
}

// ---------------------------------------------------------------------------
// §2.7  Accept-enable predicate
// ---------------------------------------------------------------------------
const hasPendingChanges = computed(() => {
  for (const d of drafts.value.values()) {
    if (d.kind !== 'pristine') return true
  }
  return false
})

const acceptEnabled = computed(() =>
  hasPendingChanges.value
  && !submitting.value
  && !(groupBusy.value.get(busyKey.value) ?? false),
)

// ---------------------------------------------------------------------------
// §3.2  Diff highlight predicate
// ---------------------------------------------------------------------------
function cellIsDirtyOrHighlighted(row: StagingRowDetail, field: DraftKey): boolean {
  if (row.highlight_fields?.includes(field)) return true
  const state = drafts.value.get(row.id)
  if (!state || state.kind !== 'edited') return false
  return field in state.changedFields
}

function cellIsDiscarded(rowId: number): boolean {
  return (drafts.value.get(rowId)?.kind ?? 'pristine') === 'discarded'
}

/** Current display value for a cell (draft overrides original). */
function cellValue(row: StagingRowDetail, field: DraftKey): string {
  const state = drafts.value.get(row.id)
  if (state?.kind === 'edited' && field in state.changedFields) {
    const v = state.changedFields[field]
    return v == null ? '' : String(v)
  }
  return ((row as Record<string, unknown>)[field] ?? '') as string
}

// ---------------------------------------------------------------------------
// §5.2  Inline diff helper (component-local, per audit C5 adjudication)
// ---------------------------------------------------------------------------
/**
 * Produce the change-only-changed-keys payload that satisfies the server's
 * exclude_unset contract for /correct.
 *
 * Pre:  original is the row's pre-edit raw_* snapshot.
 * Post: returned object contains exactly the keys whose next-value differs
 *       from original; '' maps to null to distinguish "cleared" from "unchanged."
 */
function diffPayload(
  original: StagingRowDetail,
  changedFields: Partial<CorrectionPayload>,
): Partial<CorrectionPayload> {
  const out: Partial<CorrectionPayload> = {}
  for (const k of RAW_KEYS) {
    if (!(k in changedFields)) continue
    const o = ((original as Record<string, unknown>)[k] ?? '') as string
    const n = changedFields[k]
    const nStr = n == null ? '' : String(n)
    if (nStr === o) continue
    out[k] = n
  }
  return out
}

// ---------------------------------------------------------------------------
// §3.3  Status line text
// ---------------------------------------------------------------------------
const statusLine = computed<string>(() => {
  if (lastOutcome.value) {
    const outcome = lastOutcome.value
    if (outcome.resolved && outcome.perRow.every(o => o.kind === 'ok')) {
      return 'Group resolved.'
    }
    if (outcome.resolved) {
      return 'Group resolved with warnings — see column annotations above.'
    }
    const ok    = outcome.perRow.filter(o => o.kind === 'ok').length
    const total = outcome.perRow.length
    const bad   = total - ok
    return `${ok}/${total} applied. ${bad} need attention — see column annotations above.`
  }
  if (!hasPendingChanges.value) return 'No pending changes.'
  let edits = 0, discards = 0
  for (const d of drafts.value.values()) {
    if (d.kind === 'edited')    edits++
    if (d.kind === 'discarded') discards++
  }
  const parts: string[] = []
  if (edits    > 0) parts.push(`${edits} edit${edits    > 1 ? 's' : ''}`)
  if (discards > 0) parts.push(`${discards} discard${discards > 1 ? 's' : ''}`)
  return `${parts.join(', ')} pending.`
})

// ---------------------------------------------------------------------------
// §3.3  Per-row outcome annotation
// ---------------------------------------------------------------------------
function rowOutcomeAnnotation(rowId: number): string | null {
  if (!lastOutcome.value) return null
  const entry = lastOutcome.value.perRow.find(o => o.rowId === rowId)
  if (!entry || entry.kind === 'ok') return null

  if (entry.action === 'edit') {
    if (entry.kind === 'transform-failed') return entry.processingError
    if (entry.kind === 'conflict')         return entry.message
    if (entry.kind === 'network')          return entry.message
    // Exhaustiveness guard (G-4.1.A)
    const _exhaustive: never = entry
    throw new Error(`Unhandled edit PerRowOutcome variant: ${JSON.stringify(_exhaustive)}`)
  }

  // entry.action === 'discard' (TypeScript narrows after the edit block above)
  if (entry.kind === 'stale') return 'Row was already removed.'
  if (entry.kind === 'conflict') {
    // §4.2 Asymmetric trap: discard conflict after a key-changing edit
    const editOk = lastOutcome.value.perRow.some(
      o => o.action === 'edit' && o.kind === 'ok',
    )
    if (editOk && lastOutcome.value.resolved) {
      const editRow = lastOutcome.value.perRow.find(o => o.action === 'edit' && o.kind === 'ok')
      return `Row R${rowId} was reactivated by sibling re-evaluation after R${editRow?.rowId}'s edit and is no longer eligible for discard. Reload to inspect its current state.`
    }
    return entry.message
  }
  if (entry.kind === 'network') return entry.message
  // Exhaustiveness guard (G-4.1.A)
  const _exhaustive: never = entry
  throw new Error(`Unhandled discard PerRowOutcome variant: ${JSON.stringify(_exhaustive)}`)
}

// ---------------------------------------------------------------------------
// §4.2  Submission flow
// ---------------------------------------------------------------------------
/**
 * Submit all dirty drafts in edits-first, then discards order.
 *
 * Pre:  acceptEnabled === true
 * Post: every dirty draft submitted once; store.loadConflicts() called once;
 *       drafts reset; submitting → false even on partial failure.
 *       Does NOT throw; all failures captured into lastOutcome.
 */
async function onAccept(): Promise<void> {
  if (!acceptEnabled.value || !props.group) return

  const { batch_id, group_key } = props.group
  const key = busyKey.value

  submitting.value = true
  groupBusy.value.set(key, true)

  const perRow: PerRowOutcome[] = []

  try {
    // Edits first (§4.2 — preserves intent before lone-survivor re-evaluation)
    for (const [rowId, state] of drafts.value.entries()) {
      if (state.kind !== 'edited') continue
      const originalRow = props.group.rows.find(r => r.id === rowId)
      if (!originalRow) continue
      const payload = diffPayload(originalRow, state.changedFields)
      const outcome = await store.correctConflictRow(rowId, payload, batch_id, group_key)
      perRow.push({ rowId, action: 'edit', ...outcome })
      // §4.3 — best-effort: never short-circuit on non-ok outcome
    }

    // Discards second
    for (const [rowId, state] of drafts.value.entries()) {
      if (state.kind !== 'discarded') continue
      const outcome = await store.discardConflictRow(rowId, batch_id, group_key)
      perRow.push({ rowId, action: 'discard', ...outcome })
    }

    // Reload conflict snapshot (§4.2 sequence diagram)
    await store.loadConflicts()

    const resolved = !store.reconciledConflictGroups.some(
      g => g.batch_id === batch_id && g.group_key === group_key,
    )

    const cleanResolved = resolved && perRow.every(o => o.kind === 'ok')

    lastOutcome.value = { resolved, perRow, groupKey: group_key, batchId: batch_id }
    drafts.value.clear()

    if (cleanResolved) {
      emit('resolved')
    }
    // §4.5: else panel stays open; user reads annotations and can iterate
  } finally {
    submitting.value = false
    groupBusy.value.set(key, false)
  }
}
</script>

<template>
  <div v-if="!group" class="text-slate-500 dark:text-slate-400 text-sm p-4">
    Select a conflict group to compare.
  </div>

  <div v-else class="overflow-y-auto">
    <!-- Header -->
    <div class="px-4 py-3 border-b border-slate-200 dark:border-slate-700">
      <h2 class="text-base font-semibold text-slate-800 dark:text-slate-100">
        Conflict: {{ group.group_key }}
      </h2>
      <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
        Batch {{ group.batch_id }} · {{ group.rows.length }} duplicates
      </p>
    </div>

    <!-- Side-by-side comparison table -->
    <div class="overflow-x-auto">
      <table class="w-full text-xs border-collapse">
        <thead>
          <tr class="bg-slate-50 dark:bg-slate-800">
            <th class="px-3 py-2 text-left text-slate-500 font-medium w-32 sticky left-0 bg-slate-50 dark:bg-slate-800">
              Field
            </th>
            <th
              v-for="row in group.rows"
              :key="row.id"
              class="px-3 py-2 text-left font-medium text-slate-700 dark:text-slate-300 min-w-40"
            >
              <span>Row {{ row.source_row_number }}</span>
              <!-- Per-row outcome annotation (§3.3) -->
              <div
                v-if="rowOutcomeAnnotation(row.id)"
                :data-testid="`conflict-row-outcome-${row.id}`"
                class="mt-1 text-xs font-normal text-rose-600 dark:text-rose-400 whitespace-pre-wrap"
              >
                {{ rowOutcomeAnnotation(row.id) }}
              </div>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="field in RAW_KEYS"
            :key="field"
            class="border-t border-slate-100 dark:border-slate-700/50"
          >
            <td class="px-3 py-1.5 text-slate-400 font-mono sticky left-0 bg-white dark:bg-slate-900">
              {{ field.replace('raw_', '') }}
            </td>
            <td
              v-for="row in group.rows"
              :key="row.id"
              class="px-3 py-1.5 font-mono"
              :class="{
                'bg-rose-50 dark:bg-rose-900/20': cellIsDirtyOrHighlighted(row, field),
                'opacity-40 bg-slate-100 dark:bg-slate-800': cellIsDiscarded(row.id),
              }"
            >
              <!-- G-3.1.A: disabled binding for discarded columns prevents typing
                   into a column whose edits would be silently dropped on Accept. -->
              <textarea
                rows="1"
                class="w-full resize-none bg-transparent font-mono text-slate-700 dark:text-slate-300
                       border border-transparent rounded px-1 focus:outline-none focus:border-slate-400
                       dark:focus:border-slate-500 disabled:cursor-not-allowed overflow-hidden"
                :value="cellValue(row, field)"
                :disabled="cellIsDiscarded(row.id) || submitting"
                :data-testid="`conflict-cell-${row.id}-${field}`"
                @input="onCellInput(row.id, field, $event)"
              />
            </td>
          </tr>
        </tbody>
        <!-- tfoot: one Discard toggle per column (§3) -->
        <tfoot>
          <tr class="border-t border-slate-200 dark:border-slate-700">
            <td class="px-3 py-2 text-slate-400 sticky left-0 bg-white dark:bg-slate-900" />
            <td
              v-for="row in group.rows"
              :key="row.id"
              class="px-3 py-2"
            >
              <button
                type="button"
                :disabled="submitting"
                :aria-pressed="cellIsDiscarded(row.id)"
                :data-testid="`conflict-discard-btn-${row.id}`"
                class="px-3 py-1.5 rounded-md text-xs font-medium transition-colors
                       disabled:opacity-40 disabled:cursor-not-allowed"
                :class="cellIsDiscarded(row.id)
                  ? 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-300 border border-rose-300 dark:border-rose-700'
                  : 'border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700'"
                @click="toggleDiscard(row.id)"
              >
                {{ cellIsDiscarded(row.id) ? 'Undo discard' : 'Discard' }}
              </button>
            </td>
          </tr>
        </tfoot>
      </table>
    </div>

    <!-- Footer: status line + Accept button (§3) -->
    <div class="flex items-center justify-between gap-3 px-4 py-3 border-t border-slate-200 dark:border-slate-700 mt-2">
      <p class="text-xs text-slate-500 dark:text-slate-400" data-testid="conflict-status-line">
        {{ statusLine }}
      </p>
      <button
        type="button"
        :disabled="!acceptEnabled"
        data-testid="conflict-accept-btn"
        class="px-4 py-2 rounded-md text-xs font-semibold bg-blue-600 text-white
               hover:bg-blue-700 transition-colors
               disabled:opacity-40 disabled:cursor-not-allowed"
        @click="onAccept"
      >
        Accept
      </button>
    </div>
  </div>
</template>
