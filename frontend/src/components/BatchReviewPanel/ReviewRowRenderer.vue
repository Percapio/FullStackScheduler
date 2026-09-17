<script setup lang="ts">
/**
 * ReviewRowRenderer — per-row list item for BatchReviewPanel.
 *
 * Renders a single staging row with its status badge, verify / delete buttons,
 * B# toggle, per-row canonical input, split-suffix override input, and — for an
 * operator-authored suffix — a revert action.
 * All mutation handlers are passed in from the parent to keep shared reactive
 * state (`isBNumberByRow`, `canonicalByRow`, etc.) in a single owner.
 *
 * A row with `actionable === false` (outside review, collided into by another
 * row's edit) renders read-only: every row endpoint would reject it, so the
 * operator resolves the collision by acting on the other row.
 */
import { computed } from 'vue'
import type { ReviewGroup, ReviewGroupIdentity, ReviewRow } from '@/api/review'

const props = defineProps<{
  group: ReviewGroup
  row: ReviewRow
  isBNumber: boolean
  canonicalValue: string
  splitSuffixValue: string
  anyBusy: boolean
  rowMutating: boolean
}>()

const emit = defineEmits<{
  (e: 'verify', rowId: number): void
  (e: 'delete', rowId: number): void
  (e: 'bNumberToggle', rowId: number, checked: boolean): void
  (e: 'applyCanonical', rowId: number, value: string): void
  (e: 'setSplitSuffix', rowId: number, value: string): void
  (e: 'revertSplitSuffix', rowId: number): void
  (e: 'update:canonicalValue', value: string): void
  (e: 'update:splitSuffixValue', value: string): void
}>()

const actionable = computed(() => props.row.actionable !== false)
const controlsDisabled = computed(() => props.anyBusy || !actionable.value)
/** A row can render in a staged and an edit_induced group at once; DOM ids must not collide. */
const idScope = computed(() => `${props.group.origin ?? 'group'}-${props.row.staging_row_id}`)

/** The row's effective identity, shown only when it differs from the group's. */
const divergentIdentity = computed<ReviewGroupIdentity | null>(() => {
  const effective = props.row.effective_identity
  const groupIdentity = props.group.identity
  if (!effective || !groupIdentity) return null
  return sameIdentity(effective, groupIdentity) ? null : effective
})

function sameIdentity(a: ReviewGroupIdentity, b: ReviewGroupIdentity): boolean {
  return a.part_number === b.part_number
    && a.build_type === b.build_type
    && a.split_suffix === b.split_suffix
    && a.repeat_reference === b.repeat_reference
    && a.build_qualifier === b.build_qualifier
}

function formatIdentity(identity: ReviewGroupIdentity): string {
  return [
    identity.part_number,
    identity.build_type,
    identity.split_suffix,
    identity.repeat_reference,
    identity.build_qualifier,
  ].filter(part => part !== null && part !== '').join(' · ')
}

function rowDetail(cellText: string): string {
  return cellText.split('\n')[0] ?? cellText
}

function canEditSplitSuffix(): boolean {
  return props.row.review_status !== 'deleted' && props.row.review_status !== 'pending'
}
</script>

<template>
  <li :class="[
        'flex flex-wrap items-center justify-between gap-2 text-xs rounded px-2 py-1',
        row.review_status === 'deleted'
          ? 'opacity-40 line-through bg-slate-100 dark:bg-slate-700'
          : 'bg-white dark:bg-slate-750',
      ]"
      :data-row-id="row.staging_row_id">
    <span class="font-mono text-slate-600 dark:text-slate-300 truncate"
          :title="row.original_cell_text">
      row {{ row.source_row_number }}: {{ rowDetail(row.original_cell_text) }}
    </span>
    <span :class="[
            'shrink-0 px-1 rounded-full',
            row.review_status === 'verified' || row.review_status === 'edited'
              ? 'text-emerald-700 dark:text-emerald-300'
              : row.review_status === 'deleted' || row.review_status === null
                ? 'text-slate-400'
                : 'text-amber-700 dark:text-amber-300',
          ]">
      {{ row.review_status ?? 'not in review' }}
    </span>
    <div v-if="row.review_status !== 'deleted'"
         class="flex items-center gap-1 shrink-0">
      <button v-if="row.review_status === 'pending'"
              type="button"
              class="px-1.5 py-0.5 rounded border border-emerald-300 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 transition-colors duration-75 disabled:opacity-50"
              :disabled="controlsDisabled"
              @click="emit('verify', row.staging_row_id)">
        Verify
      </button>
      <button type="button"
              class="px-1.5 py-0.5 rounded border border-red-300 text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors duration-75 disabled:opacity-50"
              :disabled="controlsDisabled"
              @click="emit('delete', row.staging_row_id)">
        Delete
      </button>
    </div>

    <div v-if="divergentIdentity"
         class="w-full text-slate-500 dark:text-slate-400"
         data-testid="effective-identity">
      writes as <span class="font-mono text-slate-700 dark:text-slate-200">{{ formatIdentity(divergentIdentity) }}</span>
    </div>

    <div v-if="!actionable"
         class="w-full italic text-slate-500 dark:text-slate-400"
         data-testid="read-only-note">
      Read-only: this row is not under review. Rename or delete the other row to resolve the collision.
    </div>

    <!-- B# toggle + per-row canonical -->
    <div v-if="row.review_status !== 'deleted'"
         class="flex items-center gap-2 text-xs mt-1 w-full">
      <label :for="`bnumber-${idScope}`"
             class="flex items-center gap-1 text-slate-500 dark:text-slate-400 shrink-0 cursor-pointer select-none">
        <input :id="`bnumber-${idScope}`"
               type="checkbox"
               :checked="isBNumber"
               :disabled="controlsDisabled"
               class="accent-sky-600"
               @change="emit('bNumberToggle', row.staging_row_id, ($event.target as HTMLInputElement).checked)" />
        B#
      </label>
      <template v-if="!isBNumber">
        <input :id="`canonical-row-${idScope}`"
               :value="canonicalValue"
               type="text"
               placeholder="canonical"
               class="flex-1 px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 font-mono focus:outline-none focus:ring-1 focus:ring-sky-500"
               :disabled="controlsDisabled"
               @input="emit('update:canonicalValue', ($event.target as HTMLInputElement).value)"
               @keydown.enter="emit('applyCanonical', row.staging_row_id, canonicalValue)" />
        <button type="button"
                class="shrink-0 px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-75 disabled:opacity-50"
                :disabled="!canonicalValue?.trim() || controlsDisabled"
                @click="emit('applyCanonical', row.staging_row_id, canonicalValue)">
          Apply
        </button>
      </template>
    </div>

    <!-- Split-suffix override -->
    <div v-if="canEditSplitSuffix()"
         class="flex items-center gap-1.5 text-xs mt-1 w-full">
      <label :for="`split-${idScope}`"
             class="text-slate-500 dark:text-slate-400 shrink-0">
        Split:
      </label>
      <input :id="`split-${idScope}`"
             :value="splitSuffixValue"
             type="text"
             placeholder="-par"
             class="w-24 px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 font-mono focus:outline-none focus:ring-1 focus:ring-sky-500"
             :disabled="controlsDisabled"
             @input="emit('update:splitSuffixValue', ($event.target as HTMLInputElement).value)"
             @keydown.enter="emit('setSplitSuffix', row.staging_row_id, splitSuffixValue)" />
      <button type="button"
              class="px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-600 transition-colors duration-75 disabled:opacity-50"
              :disabled="controlsDisabled"
              :title="'Set the per-row split suffix; empty means this row has no suffix'"
              @click="emit('setSplitSuffix', row.staging_row_id, splitSuffixValue)">
        Apply
      </button>
      <button v-if="row.review_split_suffix_source === 'operator'"
              type="button"
              class="px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-600 transition-colors duration-75 disabled:opacity-50"
              :disabled="controlsDisabled"
              :title="'Discard the typed suffix and recompute it from the cell text'"
              @click="emit('revertSplitSuffix', row.staging_row_id)">
        Revert suffix
      </button>
    </div>
  </li>
</template>
