<script setup lang="ts">
/**
 * SecondOpsItemModal — read-only field dump for one Audit BOM line.
 *
 * The prop is AuditBomFields, not SecondOpsLine, precisely so both call sites
 * can satisfy it: the grid cell passes a saved SecondOpsLine straight out of
 * summary.preview, and the entry modal passes an unsaved ParsedAuditLine from
 * its grid. A row does not have to be persisted to be inspectable, and `id` /
 * `line_order` are not rendered, so requiring them would have bought nothing and
 * blocked the second call site.
 *
 * No fetch of its own. No editing path.
 *
 * Every value is TEXT-INTERPOLATED. This content originates in an operator's
 * spreadsheet and crosses a trust boundary; the Markdown helper in
 * useJobFormatters and raw-HTML rendering must never be applied to it.
 */
import type { AuditBomFields } from '@/api/secondOps'

defineProps<{ fields: AuditBomFields | null }>()

const emit = defineEmits<{ close: [] }>()

const LABELS: { key: keyof AuditBomFields; label: string }[] = [
  { key: 'find_number', label: 'Find #' },
  { key: 'component_part_number', label: 'Component P/N' },
  { key: 'per_board_count', label: 'Per board' },
  { key: 'ref_des', label: 'Ref Des' },
  { key: 'description', label: 'Description' },
  { key: 'mount_type', label: 'Mount type' },
  { key: 'quantity_needed', label: 'Qty need' },
  { key: 'quantity_on_hand', label: 'Qty on hand' },
]
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="fields !== null"
        class="fixed inset-0 z-50 flex items-center justify-center"
        data-testid="second-ops-item-modal"
      >
        <div
          class="absolute inset-0 bg-black/50"
          data-testid="second-ops-item-backdrop"
          @click="emit('close')"
        />
        <div
          class="relative z-10 bg-surface-raised rounded-xl shadow-2xl w-full max-w-md mx-4 p-6"
          role="dialog"
          aria-labelledby="second-ops-item-title"
        >
          <h3
            id="second-ops-item-title"
            class="text-base font-semibold text-slate-800 dark:text-slate-100 mb-4"
          >
            Audit BOM line
          </h3>

          <dl class="space-y-2 text-sm mb-6">
            <div v-for="entry in LABELS" :key="entry.key" class="flex gap-2">
              <dt class="font-medium text-slate-500 dark:text-slate-400 w-32 shrink-0">
                {{ entry.label }}
              </dt>
              <dd
                class="text-slate-800 dark:text-slate-200 break-words whitespace-pre-wrap"
                :data-testid="`second-ops-item-${entry.key}`"
              >{{ fields[entry.key] ?? '—' }}</dd>
            </div>
          </dl>

          <div class="flex justify-end">
            <button
              data-testid="second-ops-item-close-btn"
              type="button"
              class="rounded px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors"
              @click="emit('close')"
            >
              Close
            </button>
          </div>
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
