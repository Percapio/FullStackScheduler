<script setup lang="ts">
/**
 * BatchReviewView — persistent URL for /uploads/:batchId/review.
 *
 * Allows an operator to resume a review after navigating away or reopening
 * the browser. On confirm, navigates to /reconciliation with a toast.
 * On abandon, navigates to /uploads/in-flight.
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BatchReviewPanel from '@/components/BatchReviewPanel.vue'
import { useToast } from '@/composables/useToast'
import type { ConfirmResult } from '@/api/review'

const route  = useRoute()
const router = useRouter()
const { show: pushToast } = useToast()

const batchId = computed(() => Number(route.params.batchId))

function onConfirmed(result: ConfirmResult) {
  pushToast(
    `Batch #${result.batch_id}: ${result.rows_inserted} inserted, ${result.rows_updated} updated, ${result.rows_errored} errored.`,
    'success',
  )
  router.push('/reconciliation')
}

function onAbandoned() {
  router.push({ name: 'uploads-in-flight' })
}
</script>

<template>
  <section class="p-6 max-w-2xl mx-auto">
    <div class="flex items-baseline gap-4 mb-4">
      <button type="button"
              class="text-sm text-sky-600 dark:text-sky-400 hover:underline"
              @click="router.push({ name: 'uploads-in-flight' })">
        ← In-flight uploads
      </button>
      <h1 class="text-xl font-semibold text-slate-800 dark:text-slate-100">
        Review batch #{{ batchId }}
      </h1>
    </div>

    <div class="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
      <BatchReviewPanel
        :batch-id="batchId"
        @confirmed="onConfirmed"
        @abandoned="onAbandoned"
      />
    </div>
  </section>
</template>
