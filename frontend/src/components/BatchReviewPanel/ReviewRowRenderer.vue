<script setup lang="ts">
/**
 * ReviewRowRenderer — per-row list item for BatchReviewPanel.
 *
 * Renders a single staging row with its status badge, verify / delete buttons,
 * B# toggle, per-row canonical input, and split-suffix override input.
 * All mutation handlers are passed in from the parent to keep shared reactive
 * state (`isBNumberByRow`, `canonicalByRow`, etc.) in a single owner.
 */
import type { ReviewGroup, ReviewRow } from '@/api/review'

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
  (e: 'update:canonicalValue', value: string): void
  (e: 'update:splitSuffixValue', value: string): void
}>()

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
              @click="emit('verify', row.staging_row_id)">
        Verify
      </button>
      <button type="button"
              class="px-1.5 py-0.5 rounded border border-red-300 text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors duration-75 disabled:opacity-50"
              :disabled="anyBusy"
              @click="emit('delete', row.staging_row_id)">
        Delete
      </button>
    </div>

    <!-- B# toggle + per-row canonical -->
    <div v-if="row.review_status !== 'deleted'"
         class="flex items-center gap-2 text-xs mt-1 w-full">
      <label :for="`bnumber-${row.staging_row_id}`"
             class="flex items-center gap-1 text-slate-500 dark:text-slate-400 shrink-0 cursor-pointer select-none">
        <input :id="`bnumber-${row.staging_row_id}`"
               type="checkbox"
               :checked="isBNumber"
               :disabled="anyBusy"
               class="accent-sky-600"
               @change="emit('bNumberToggle', row.staging_row_id, ($event.target as HTMLInputElement).checked)" />
        B#
      </label>
      <template v-if="!isBNumber">
        <input :id="`canonical-row-${row.staging_row_id}`"
               :value="canonicalValue"
               type="text"
               placeholder="canonical"
               class="flex-1 px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 font-mono focus:outline-none focus:ring-1 focus:ring-sky-500"
               :disabled="anyBusy"
               @input="emit('update:canonicalValue', ($event.target as HTMLInputElement).value)"
               @keydown.enter="emit('applyCanonical', row.staging_row_id, canonicalValue)" />
        <button type="button"
                class="shrink-0 px-1.5 py-0.5 rounded border border-sky-300 text-sky-700 dark:text-sky-300 hover:bg-sky-50 dark:hover:bg-sky-900/20 transition-colors duration-75 disabled:opacity-50"
                :disabled="!canonicalValue?.trim() || anyBusy"
                @click="emit('applyCanonical', row.staging_row_id, canonicalValue)">
          Apply
        </button>
      </template>
    </div>

    <!-- Split-suffix override -->
    <div v-if="canEditSplitSuffix()"
         class="flex items-center gap-1.5 text-xs mt-1 w-full">
      <label :for="`split-${row.staging_row_id}`"
             class="text-slate-500 dark:text-slate-400 shrink-0">
        Split:
      </label>
      <input :id="`split-${row.staging_row_id}`"
             :value="splitSuffixValue"
             type="text"
             placeholder="-par"
             class="w-24 px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 font-mono focus:outline-none focus:ring-1 focus:ring-sky-500"
             :disabled="anyBusy"
             @input="emit('update:splitSuffixValue', ($event.target as HTMLInputElement).value)"
             @keydown.enter="emit('setSplitSuffix', row.staging_row_id, splitSuffixValue)" />
      <button type="button"
              class="px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-600 transition-colors duration-75 disabled:opacity-50"
              :disabled="anyBusy"
              :title="'Set or clear the per-row split suffix override'"
              @click="emit('setSplitSuffix', row.staging_row_id, splitSuffixValue)">
        Apply
      </button>
    </div>
  </li>
</template>
