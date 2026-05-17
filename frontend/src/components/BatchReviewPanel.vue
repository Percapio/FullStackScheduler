<script setup lang="ts">
/**
 * BatchReviewPanel — Phase 18a Phase 1 review UI.
 *
 * Shows the review payload for a held batch: one card per parsed canonical B#,
 * with similar-assembly hints, per-row verify/delete, and a canonical-override
 * input. Emits `confirmed` with a ConfirmResult on successful confirmation, and
 * `abandoned` when the operator abandons the batch.
 */
import { ref, computed, onMounted } from 'vue'
import {
  fetchReviewPayload,
  setCanonical,
  patchSplitSuffix,
  verifyRow,
  deleteRow,
  confirmReview,
  abandonReview,
  type ReviewPayload,
  type ReviewGroup,
  type ReviewRow,
  type ConfirmResult,
  type MutationResponse,
  type CanonicalMutationResponse,
} from '@/api/review'

const props = defineProps<{
  batchId: number
}>()

const emit = defineEmits<{
  (e: 'confirmed', result: ConfirmResult): void
  (e: 'abandoned'): void
}>()

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const payload        = ref<ReviewPayload | null>(null)
const loadError      = ref<string | null>(null)
const loading        = ref(false)
const actionError    = ref<string | null>(null)
const confirming     = ref(false)
const abandoning     = ref(false)
/** Counts mutations currently in-flight; used to disable all mutating buttons. */
const mutationInFlightCount = ref(0)

/** canonical override input value, keyed by parsed_part_number */
const canonicalInputs = ref<Record<string, string>>({})
/** per-group loading flag for canonical set operations */
const groupLoading    = ref<Record<string, boolean>>({})
/** split-suffix override input value, keyed by staging_row_id */
const splitSuffixInputs = ref<Record<number, string>>({})

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

const allGroups = computed<ReviewGroup[]>(() => {
  if (!payload.value) return []
  return [
    ...payload.value.new_b_numbers,
    ...payload.value.new_non_b_numbers,
    ...payload.value.intra_file_duplicates,
  ]
})

/** Confirm is allowed only when every non-deleted row is verified or edited. */
const canConfirm = computed(() =>
  allGroups.value.length > 0 &&
  allGroups.value.every(g => g.review_status !== 'pending')
)

const anyBusy = computed(() => confirming.value || abandoning.value || mutationInFlightCount.value > 0)

// ---------------------------------------------------------------------------
// Load
// ---------------------------------------------------------------------------

async function load() {
  loading.value = true
  loadError.value = null
  try {
    payload.value = await fetchReviewPayload(props.batchId)
    // Seed canonical inputs with the parsed value so operator sees the default
    for (const g of allGroups.value) {
      canonicalInputs.value[g.parsed_part_number] = g.parsed_part_number
      for (const r of g.rows) {
        splitSuffixInputs.value[r.staging_row_id] = r.review_split_suffix_override ?? ''
      }
    }
  } catch (err: any) {
    loadError.value = err?.response?.data?.detail ?? err?.message ?? 'Failed to load review data.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

// ---------------------------------------------------------------------------
// Row actions
// ---------------------------------------------------------------------------

/**
 * Wrap a mutating API call: increment/decrement mutationInFlightCount so that
 * anyBusy gates all action buttons while any mutation is in flight.
 */
async function runMutation<T>(fn: () => Promise<T>): Promise<T> {
  mutationInFlightCount.value++
  try {
    return await fn()
  } finally {
    mutationInFlightCount.value--
  }
}

/**
 * Apply a MutationResponse or CanonicalMutationResponse directly to local
 * state, avoiding a full GET /review refetch on every action.
 */
function applyMutationResponse(resp: MutationResponse | CanonicalMutationResponse) {
  if (!payload.value) return
  const pn = resp.group.parsed_part_number
  for (const section of ['new_b_numbers', 'new_non_b_numbers', 'intra_file_duplicates'] as const) {
    const groupIdx = payload.value[section].findIndex(g => g.parsed_part_number === pn)
    if (groupIdx === -1) continue
    // Update group-level status derived from the server response.
    payload.value[section][groupIdx].review_status = resp.group.review_status as ReviewGroup['review_status']
    const rowsToApply = 'updated_rows' in resp ? resp.updated_rows : [resp.row]
    for (const incoming of rowsToApply) {
      const rowIdx = payload.value[section][groupIdx].rows.findIndex(
        r => r.staging_row_id === incoming.staging_row_id
      )
      if (rowIdx !== -1) {
        Object.assign(payload.value[section][groupIdx].rows[rowIdx], incoming)
      }
    }
    break
  }
}

function rowDetail(cellText: string): string {
  // Show the first line of the cell for brevity; full text on title.
  return cellText.split('\n')[0] ?? cellText
}

async function handleVerify(_group: ReviewGroup, rowId: number) {
  actionError.value = null
  await runMutation(async () => {
    try {
      const resp = await verifyRow(props.batchId, rowId)
      applyMutationResponse(resp)
    } catch (err: any) {
      actionError.value = err?.response?.data?.detail ?? err?.message ?? 'Verify failed.'
    }
  })
}

async function handleDelete(_group: ReviewGroup, rowId: number) {
  actionError.value = null
  await runMutation(async () => {
    try {
      const resp = await deleteRow(props.batchId, rowId)
      applyMutationResponse(resp)
    } catch (err: any) {
      actionError.value = err?.response?.data?.detail ?? err?.message ?? 'Delete failed.'
    }
  })
}

// ---------------------------------------------------------------------------
// Split-suffix override
// ---------------------------------------------------------------------------

function canEditSplitSuffix(row: ReviewRow): boolean {
  // PUT /canonical must have run first (review_status moves away from 'pending').
  return row.review_status !== 'deleted' && row.review_status !== 'pending'
}

async function handleSetSplitSuffix(_group: ReviewGroup, row: ReviewRow) {
  const raw = (splitSuffixInputs.value[row.staging_row_id] ?? '').trim()
  const normalised = raw || null
  actionError.value = null
  await runMutation(async () => {
    try {
      const resp = await patchSplitSuffix(props.batchId, row.staging_row_id, normalised)
      applyMutationResponse(resp)
    } catch (err: any) {
      actionError.value = err?.response?.data?.detail ?? err?.message ?? 'Set split suffix failed.'
    }
  })
}

// ---------------------------------------------------------------------------
// Canonical override
// ---------------------------------------------------------------------------

async function handleSetCanonical(group: ReviewGroup) {
  const canonical = (canonicalInputs.value[group.parsed_part_number] ?? '').trim()
  if (!canonical) return
  actionError.value = null
  groupLoading.value[group.parsed_part_number] = true
  await runMutation(async () => {
    try {
      const resp = await setCanonical(props.batchId, group.parsed_part_number, canonical)
      applyMutationResponse(resp)
    } catch (err: any) {
      actionError.value = err?.response?.data?.detail ?? err?.message ?? 'Set canonical failed.'
    } finally {
      groupLoading.value[group.parsed_part_number] = false
    }
  })
}

function useSuggestion(group: ReviewGroup, suggestion: string) {
  canonicalInputs.value[group.parsed_part_number] = suggestion
}

// ---------------------------------------------------------------------------
// Confirm / Abandon
// ---------------------------------------------------------------------------

async function handleConfirm() {
  if (!canConfirm.value || confirming.value) return
  confirming.value = true
  actionError.value = null
  try {
    const result = await confirmReview(props.batchId)
    emit('confirmed', result)
  } catch (err: any) {
    actionError.value = err?.response?.data?.detail ?? err?.message ?? 'Confirm failed.'
    confirming.value = false
  }
}

async function handleAbandon() {
  if (abandoning.value) return
  abandoning.value = true
  actionError.value = null
  try {
    await abandonReview(props.batchId)
    emit('abandoned')
  } catch (err: any) {
    actionError.value = err?.response?.data?.detail ?? err?.message ?? 'Abandon failed.'
    abandoning.value = false
  }
}
</script>

<template>
  <div class="space-y-4">

    <!-- Load / error state -->
    <div v-if="loading" class="text-sm text-slate-500 dark:text-slate-400">Loading review data…</div>
    <div v-else-if="loadError"
         class="px-3 py-2 rounded border border-red-200 bg-red-50 dark:bg-red-900/20 dark:border-red-700 text-red-800 dark:text-red-200 text-sm"
         role="alert">
      {{ loadError }}
    </div>

    <template v-else-if="payload">

      <!-- Section header -->
      <div v-if="payload.new_b_numbers.length > 0">
        <h3 class="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-2">
          New B# parts ({{ payload.new_b_numbers.length }})
        </h3>
        <p class="text-xs text-slate-500 dark:text-slate-400 mb-3">
          These part numbers were not found in the assembly registry.
          Verify each to accept it as a new assembly, edit the canonical to correct a typo,
          or delete rows you don't want to import.
        </p>

        <!-- One card per canonical -->
        <div v-for="group in payload.new_b_numbers"
             :key="group.parsed_part_number"
             class="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-820 mb-3">

          <!-- Card header -->
          <div class="flex items-center justify-between px-4 py-2 border-b border-slate-200 dark:border-slate-700">
            <div class="flex items-center gap-2">
              <span class="font-mono text-sm font-semibold text-slate-800 dark:text-slate-100">
                {{ group.parsed_part_number }}
              </span>
              <span :class="[
                      'text-xs px-1.5 py-0.5 rounded-full font-medium',
                      group.review_status === 'pending'
                        ? 'bg-amber-100 text-amber-800 dark:bg-amber-800/30 dark:text-amber-200'
                        : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-800/30 dark:text-emerald-200'
                    ]">
                {{ group.review_status }}
              </span>
            </div>
            <span class="text-xs text-slate-500 dark:text-slate-400">
              {{ group.rows.filter(r => r.review_status !== 'deleted').length }} row(s)
            </span>
          </div>

          <div class="px-4 py-3 space-y-3">

            <!-- Similar assemblies hints -->
            <div v-if="group.similar_assemblies.length > 0"
                 class="text-xs text-slate-500 dark:text-slate-400">
              <span class="font-medium">Similar:</span>
              <button
                v-for="sim in group.similar_assemblies"
                :key="sim.part_number"
                type="button"
                class="ml-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-600 hover:bg-white dark:hover:bg-slate-700 font-mono transition-colors duration-75"
                :title="`Edit distance ${sim.edit_distance} — click to use as canonical`"
                @click="useSuggestion(group, sim.part_number)">
                {{ sim.part_number }}
                <span class="text-slate-400">({{ sim.edit_distance }})</span>
              </button>
            </div>
            <div v-else class="text-xs text-slate-400 dark:text-slate-500 italic">
              No similar assemblies found.
            </div>

            <!-- Canonical override input -->
            <div class="flex items-center gap-2">
              <label :for="`canonical-${group.parsed_part_number}`"
                     class="text-xs text-slate-600 dark:text-slate-400 shrink-0">
                Canonical:
              </label>
              <input :id="`canonical-${group.parsed_part_number}`"
                     v-model="canonicalInputs[group.parsed_part_number]"
                     type="text"
                     class="flex-1 px-2 py-1 text-xs font-mono rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-sky-500"
                     :disabled="groupLoading[group.parsed_part_number] || anyBusy"
                     @keydown.enter="handleSetCanonical(group)" />
              <button type="button"
                      class="shrink-0 px-2 py-1 text-xs rounded bg-sky-600 text-white hover:bg-sky-500 disabled:opacity-50 transition-colors duration-75"
                      :disabled="!canonicalInputs[group.parsed_part_number]?.trim() || groupLoading[group.parsed_part_number] || anyBusy"
                      @click="handleSetCanonical(group)">
                {{ canonicalInputs[group.parsed_part_number] === group.parsed_part_number ? 'Verify all' : 'Apply' }}
              </button>
            </div>

            <!-- Row list -->
            <ul class="space-y-1">
              <li v-for="row in group.rows"
                  :key="row.staging_row_id"
                  :class="[
                    'flex items-center justify-between gap-2 text-xs rounded px-2 py-1',
                    row.review_status === 'deleted'
                      ? 'opacity-40 line-through bg-slate-100 dark:bg-slate-700'
                      : 'bg-white dark:bg-slate-750',
                  ]">
                <span class="font-mono text-slate-600 dark:text-slate-300 truncate"
                      :title="row.original_cell_text">
                  row {{ row.source_row_number }}: {{ rowDetail(row.original_cell_text) }}
                </span>
                <span :class="[
                        'shrink-0 px-1 rounded-full',
                        row.review_status === 'verified' || row.review_status === 'edited'
                          ? 'text-emerald-700 dark:text-emerald-300'
                          : row.review_status === 'deleted'
                            ? 'text-slate-400'
                            : 'text-amber-700 dark:text-amber-300',
                      ]">
                  {{ row.review_status }}
                </span>
                <div v-if="row.review_status !== 'deleted'"
                     class="flex items-center gap-1 shrink-0">
                  <button v-if="row.review_status === 'pending'"
                          type="button"
                          class="px-1.5 py-0.5 rounded border border-emerald-300 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 transition-colors duration-75 disabled:opacity-50"
                          :disabled="anyBusy"
                          @click="handleVerify(group, row.staging_row_id)">
                    Verify
                  </button>
                  <button type="button"
                          class="px-1.5 py-0.5 rounded border border-red-300 text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors duration-75 disabled:opacity-50"
                          :disabled="anyBusy"
                          @click="handleDelete(group, row.staging_row_id)">
                    Delete
                  </button>
                </div>

                <!-- Split-suffix override (visible after PUT /canonical has run) -->
                <div v-if="canEditSplitSuffix(row)"
                     class="flex items-center gap-1.5 text-xs mt-1 w-full">
                  <label :for="`split-${row.staging_row_id}`"
                         class="text-slate-500 dark:text-slate-400 shrink-0">
                    Split:
                  </label>
                  <input :id="`split-${row.staging_row_id}`"
                         v-model="splitSuffixInputs[row.staging_row_id]"
                         type="text"
                         placeholder="-par"
                         class="w-24 px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 font-mono focus:outline-none focus:ring-1 focus:ring-sky-500"
                         :disabled="anyBusy"
                         @keydown.enter="handleSetSplitSuffix(group, row)" />
                  <button type="button"
                          class="px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-600 transition-colors duration-75 disabled:opacity-50"
                          :disabled="anyBusy"
                          :title="'Set or clear the per-row split suffix override'"
                          @click="handleSetSplitSuffix(group, row)">
                    Apply
                  </button>
                </div>
              </li>
            </ul>

          </div>
        </div>
      </div>

      <!-- Action error -->
      <div v-if="actionError"
           class="px-3 py-2 rounded border border-red-200 bg-red-50 dark:bg-red-900/20 dark:border-red-700 text-red-800 dark:text-red-200 text-sm"
           role="alert">
        {{ actionError }}
      </div>

      <!-- Confirm / Abandon -->
      <div class="flex justify-between items-center pt-1">
        <button type="button"
                class="px-3 py-1.5 rounded text-sm text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 border border-red-300 dark:border-red-700 focus:outline-none focus:ring-1 focus:ring-red-400 transition-colors duration-75 disabled:opacity-50"
                :disabled="anyBusy"
                @click="handleAbandon">
          <span v-if="abandoning">Abandoning…</span>
          <span v-else>Abandon batch</span>
        </button>

        <button type="button"
                class="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-sky-600 text-white text-sm font-medium hover:bg-sky-500 disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-sky-500 transition-colors duration-100"
                :disabled="!canConfirm || anyBusy"
                :title="canConfirm ? undefined : 'All rows must be verified or deleted before confirming'"
                @click="handleConfirm">
          <svg v-if="confirming"
               class="w-4 h-4 animate-spin"
               viewBox="0 0 24 24" fill="none"
               aria-hidden="true">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor"
                  d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
          </svg>
          <span>{{ confirming ? 'Confirming…' : 'Confirm import' }}</span>
        </button>
      </div>

    </template>
  </div>
</template>
