<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { RestoreConflictPreview, StagingRestoreAction, StagingRowDetail } from '@/api/staging'

const props = defineProps<{
  open: boolean
  preview: RestoreConflictPreview | null
  submitting: boolean
}>()

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'restore', payload: { actions: StagingRestoreAction[] }): void
}>()

// ---------------------------------------------------------------------------
// Per-row discard draft state for class-(i) errored colliders
// ---------------------------------------------------------------------------

/** Track which errored colliders the operator chose to discard. */
const discardedIds = ref<Set<number>>(new Set())

watch(
  () => props.preview,
  () => { discardedIds.value = new Set() },
)

function toggleDiscard(rowId: number) {
  const next = new Set(discardedIds.value)
  if (next.has(rowId)) {
    next.delete(rowId)
  } else {
    next.add(rowId)
  }
  discardedIds.value = next
}

// ---------------------------------------------------------------------------
// Enable predicate (§4.7)
// Enable iff: at least one errored collider is marked for discard AND
// there are no live-job colliders (which the operator cannot resolve here).
// ---------------------------------------------------------------------------

const hasAnyDraft = computed(() => discardedIds.value.size > 0)

const hasLiveJobColliders = computed(
  () => (props.preview?.colliding_live_jobs?.length ?? 0) > 0,
)

const isRestoreEnabled = computed(
  () => !props.submitting && hasAnyDraft.value && !hasLiveJobColliders.value,
)

// ---------------------------------------------------------------------------
// Build the actions list and emit
// ---------------------------------------------------------------------------

function onRestore() {
  if (!isRestoreEnabled.value) return
  const actions: StagingRestoreAction[] = Array.from(discardedIds.value).map((rowId) => ({
    kind: 'discard' as const,
    row_id: rowId,
  }))
  emit('restore', { actions })
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatRowLabel(row: StagingRowDetail): string {
  return `Row #${row.source_row_number} (id ${row.id}): ${row.raw_job ?? '—'}`
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open && preview"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      data-testid="restore-conflict-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Restore conflict preview"
    >
      <div
        class="bg-white rounded-lg shadow-xl max-w-3xl w-full mx-4 overflow-y-auto max-h-[90vh] flex flex-col"
        data-testid="restore-conflict-modal"
      >
        <!-- Header -->
        <div class="px-6 py-4 border-b flex items-center justify-between">
          <h2 class="text-lg font-semibold text-gray-800">Restore conflict preview</h2>
          <button
            class="text-gray-400 hover:text-gray-600 text-xl leading-none"
            data-testid="restore-conflict-cancel-btn"
            :disabled="submitting"
            @click="emit('cancel')"
          >
            &times;
          </button>
        </div>

        <!-- Body -->
        <div class="px-6 py-4 space-y-6 flex-1 overflow-y-auto">
          <!-- Incoming row (read-only, no discard toggle) -->
          <section>
            <h3 class="text-sm font-medium text-gray-700 mb-2">Incoming (to be restored)</h3>
            <div
              v-if="preview.incoming.kind === 'staging' && preview.incoming.staging"
              class="rounded border border-surface-hairline bg-surface-overlay p-3 text-sm text-slate-800 dark:text-slate-200"
              data-testid="restore-conflict-incoming"
            >
              {{ formatRowLabel(preview.incoming.staging) }}
            </div>
            <div
              v-else-if="preview.incoming.kind === 'job' && preview.incoming.job"
              class="rounded border border-surface-hairline bg-surface-overlay p-3 text-sm text-slate-800 dark:text-slate-200"
              data-testid="restore-conflict-incoming"
            >
              Job #{{ preview.incoming.job.id }} —
              {{ preview.incoming.job.assembly?.part_number }} /
              {{ preview.incoming.job.customer?.name }} /
              qty {{ preview.incoming.job.quantity }}
            </div>
          </section>

          <!-- Class (i): errored staging colliders — Discard toggle enabled -->
          <section v-if="preview.colliding_staging_errored_rows.length > 0">
            <h3 class="text-sm font-medium text-gray-700 mb-2">
              Errored rows with the same identity
              <span class="text-xs text-gray-500">(mark to discard before restoring)</span>
            </h3>
            <ul class="space-y-2">
              <li
                v-for="row in preview.colliding_staging_errored_rows"
                :key="row.id"
                class="flex items-center gap-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm"
                :data-testid="`restore-conflict-errored-${row.id}`"
              >
                <input
                  type="checkbox"
                  :checked="discardedIds.has(row.id)"
                  :disabled="submitting"
                  :aria-label="`Discard row ${row.id}`"
                  @change="toggleDiscard(row.id)"
                />
                <span class="flex-1 text-amber-900">{{ formatRowLabel(row) }}</span>
                <span
                  v-if="discardedIds.has(row.id)"
                  class="text-xs font-medium text-amber-700 bg-amber-200 px-2 py-0.5 rounded"
                >Will discard</span>
              </li>
            </ul>
          </section>

          <!-- Class (ii): discarded staging colliders — read-only, no toggle -->
          <section v-if="preview.colliding_staging_discarded_rows.length > 0">
            <h3 class="text-sm font-medium text-gray-700 mb-2">
              Other discarded rows with the same identity
              <span class="text-xs text-gray-500">(informational only)</span>
            </h3>
            <ul class="space-y-2">
              <li
                v-for="row in preview.colliding_staging_discarded_rows"
                :key="row.id"
                class="rounded border border-gray-200 bg-gray-50 p-3 text-sm text-gray-600"
                :data-testid="`restore-conflict-discarded-${row.id}`"
              >
                {{ formatRowLabel(row) }}
              </li>
            </ul>
          </section>

          <!-- Class (iii): live job colliders — read-only, "Resolve in History" -->
          <section v-if="preview.colliding_live_jobs.length > 0">
            <h3 class="text-sm font-medium text-gray-700 mb-2">
              Active jobs with the same identity
              <span class="text-xs text-red-500">(must be resolved in History before restore)</span>
            </h3>
            <ul class="space-y-2">
              <li
                v-for="job in preview.colliding_live_jobs"
                :key="job.id"
                class="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-900 flex items-center justify-between"
                :data-testid="`restore-conflict-live-job-${job.id}`"
              >
                <span>Job #{{ job.id }}</span>
                <a
                  href="/history"
                  class="text-xs underline text-red-700 hover:text-red-900"
                  data-testid="restore-conflict-history-link"
                >Resolve in History</a>
              </li>
            </ul>
          </section>
        </div>

        <!-- Footer -->
        <div class="px-6 py-4 border-t flex justify-end gap-3">
          <button
            class="px-4 py-2 text-sm rounded border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            :disabled="submitting"
            data-testid="restore-conflict-cancel-footer-btn"
            @click="emit('cancel')"
          >
            Cancel
          </button>
          <button
            class="px-4 py-2 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="!isRestoreEnabled"
            data-testid="restore-conflict-restore-btn"
            @click="onRestore"
          >
            <span v-if="submitting">Restoring…</span>
            <span v-else>Restore</span>
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
