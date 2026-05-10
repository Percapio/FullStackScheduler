<script setup lang="ts">
/**
 * ConfirmDiscardJobModal — pure presentation confirmation dialog.
 *
 * Shows job summary fields so the operator can sanity-check before committing
 * a discard. A required `reason` textarea must be filled before Confirm is enabled.
 * The modal emits `confirm(reason: string)` so the parent can forward the reason
 * to the discard implementation.
 *
 * Pre:  `open === true` implies `job !== null`. The parent MUST clear `open`
 *       before clearing `job` on close so the operator never sees a confirm
 *       dialog with no row visible.
 * Post: when `job` is null the modal renders nothing regardless of `open`
 *       (defensive default for the parent's closing transition).
 *       `reason` is cleared when `open` flips false.
 */
import { ref, watch } from 'vue'
import type { JobReadExpanded } from '@/api/history'

const props = defineProps<{
  open: boolean
  job: JobReadExpanded | null
}>()

const emit = defineEmits<{
  confirm: [reason: string]
  cancel: []
}>()

const reason = ref('')

watch(() => props.open, (open) => {
  if (!open) reason.value = ''
})
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="open && job !== null"
        class="fixed inset-0 z-50 flex items-center justify-center"
        data-testid="confirm-discard-modal"
      >
        <div
          class="absolute inset-0 bg-slate-900/50"
          data-testid="confirm-discard-backdrop"
          @click="emit('cancel')"
        />
        <div
          class="relative z-10 bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-sm mx-4 p-6"
          role="dialog"
          aria-labelledby="confirm-discard-title"
        >
          <h3
            id="confirm-discard-title"
            class="text-base font-semibold text-slate-800 dark:text-slate-100 mb-4"
          >
            Discard job?
          </h3>

          <dl class="space-y-2 text-sm mb-6">
            <div class="flex gap-2">
              <dt class="font-medium text-slate-500 dark:text-slate-400 w-28 shrink-0">Part number</dt>
              <dd class="text-slate-800 dark:text-slate-200 break-words">{{ job.assembly.part_number }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="font-medium text-slate-500 dark:text-slate-400 w-28 shrink-0">Customer</dt>
              <dd class="text-slate-800 dark:text-slate-200 break-words">{{ job.customer.name }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="font-medium text-slate-500 dark:text-slate-400 w-28 shrink-0">Quantity</dt>
              <dd class="text-slate-800 dark:text-slate-200 tabular-nums">{{ job.quantity }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="font-medium text-slate-500 dark:text-slate-400 w-28 shrink-0">Ship date</dt>
              <dd class="text-slate-800 dark:text-slate-200 tabular-nums">
                {{ job.resolved_ship_date ?? '—' }}
              </dd>
            </div>
          </dl>

          <p class="text-sm text-slate-500 dark:text-slate-400 mb-3">
            This job will be soft-deleted. The action can be undone from the Discarded jobs drawer.
          </p>

          <label class="block mb-4">
            <span class="text-sm font-medium text-slate-700 dark:text-slate-300">
              Reason <span class="text-red-500">*</span>
            </span>
            <textarea
              v-model="reason"
              data-testid="confirm-discard-reason"
              rows="3"
              maxlength="500"
              placeholder="Required — briefly explain why this job is being discarded."
              class="mt-1 block w-full rounded border border-slate-300 dark:border-slate-600
                     bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-200
                     text-sm px-3 py-2 resize-none
                     focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            />
          </label>

          <div class="flex justify-end gap-3">
            <button
              data-testid="confirm-discard-cancel-btn"
              type="button"
              class="rounded px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors"
              @click="emit('cancel')"
            >
              Cancel
            </button>
            <button
              data-testid="confirm-discard-confirm-btn"
              type="button"
              :disabled="reason.trim().length === 0"
              class="rounded px-3 py-1.5 text-sm font-medium bg-red-600 hover:bg-red-700 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              @click="emit('confirm', reason.trim())"
            >
              Discard
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

