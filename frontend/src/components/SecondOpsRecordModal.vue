<script setup lang="ts">
/**
 * SecondOpsRecordModal — read-only whole-record view.
 *
 * Exists because the summary is BOUNDED: History renders at most
 * second_ops_preview_lines per cell and has_unexpected_inclusions is a boolean,
 * so without this a 56-line audit shows 3 lines and the operator's note is
 * entirely undisplayable — on the surface whose whole purpose is being the audit
 * trail. That is a functional hole, not a density trade-off.
 *
 * It is a SEPARATE COMPONENT rather than SecondOpsEntryModal behind a `readonly`
 * flag. Threading a boolean through paste handling, grid mutation, note editing
 * and submit would put the write guard's frontend half inside one prop, on the
 * surface that must never write. A component with no PUT in its import graph
 * cannot regress into one.
 *
 * Text interpolation only — this content crossed a trust boundary.
 */
import type { AuditBomFields, SecondOpsFetch } from '@/api/secondOps'
import type { JobReadExpanded } from '@/api/history'

defineProps<{
  job: JobReadExpanded | null
  fetch: SecondOpsFetch
}>()

const emit = defineEmits<{
  close: []
  retry: []
  inspect: [fields: AuditBomFields]
}>()

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
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="job !== null"
        class="fixed inset-0 z-50 flex items-center justify-center"
        data-testid="second-ops-record-modal"
      >
        <div class="absolute inset-0 bg-slate-900/50" @click="emit('close')" />
        <div
          class="relative z-10 bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-5xl mx-4 p-6 max-h-[90vh] overflow-y-auto"
          role="dialog"
          aria-labelledby="second-ops-record-title"
        >
          <h3
            id="second-ops-record-title"
            class="text-base font-semibold text-slate-800 dark:text-slate-100 mb-4"
          >
            2nd OPS — {{ job.assembly.part_number }}
          </h3>

          <div
            v-if="fetch.status === 'loading'"
            data-testid="second-ops-record-loading"
            class="py-10 text-center text-sm text-slate-500 dark:text-slate-400"
          >
            Loading audit…
          </div>

          <div
            v-else-if="fetch.status === 'failed'"
            data-testid="second-ops-record-failed"
            class="py-8 text-center"
          >
            <p class="text-sm text-slate-600 dark:text-slate-300 mb-4">{{ fetch.message }}</p>
            <button
              type="button"
              data-testid="second-ops-record-retry-btn"
              class="rounded px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors"
              @click="emit('retry')"
            >
              Retry
            </button>
          </div>

          <template v-else>
            <div class="overflow-x-auto mb-4">
              <table class="w-full text-sm" data-testid="second-ops-record-table">
                <thead class="bg-slate-100 dark:bg-slate-700 text-left text-xs uppercase tracking-wide text-slate-600 dark:text-slate-300">
                  <tr>
                    <th v-for="column in COLUMNS" :key="column.key" class="px-2 py-1">
                      {{ column.label }}
                    </th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-200 dark:divide-slate-700">
                  <tr
                    v-for="line in (fetch.record.lines ?? [])"
                    :key="line.id"
                    data-testid="second-ops-record-row"
                    tabindex="0"
                    class="cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/70"
                    @click.stop="emit('inspect', line)"
                    @keydown.enter.space.prevent="emit('inspect', line)"
                  >
                    <td
                      v-for="column in COLUMNS"
                      :key="column.key"
                      class="px-2 py-1 text-slate-700 dark:text-slate-300 break-words"
                    >{{ line[column.key] ?? '—' }}</td>
                  </tr>
                  <tr v-if="(fetch.record.lines ?? []).length === 0">
                    <td
                      :colspan="COLUMNS.length"
                      class="px-2 py-4 text-center text-slate-500 dark:text-slate-400"
                      data-testid="second-ops-record-empty"
                    >
                      No Audit BOM lines recorded.
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <section class="mb-6">
              <h4 class="text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                Unexpected Inclusions
              </h4>
              <p
                data-testid="second-ops-record-note"
                class="text-sm text-slate-600 dark:text-slate-300 whitespace-pre-wrap break-words"
              >{{ fetch.record.unexpected_inclusions ?? '—' }}</p>
            </section>

            <div class="flex justify-end">
              <button
                type="button"
                data-testid="second-ops-record-close-btn"
                class="rounded px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors"
                @click="emit('close')"
              >
                Close
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
