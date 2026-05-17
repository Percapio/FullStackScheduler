<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { fetchAwaitingReview, type AwaitingReviewBatch } from '@/api/review'

const router  = useRouter()
const batches = ref<AwaitingReviewBatch[]>([])
const loading = ref(false)
const error   = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    batches.value = await fetchAwaitingReview()
  } catch (err: any) {
    error.value = err?.response?.data?.detail ?? err?.message ?? 'Failed to load in-flight uploads.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}
</script>

<template>
  <section class="p-6 max-w-2xl mx-auto">
    <div class="flex items-baseline justify-between mb-4">
      <h1 class="text-xl font-semibold text-slate-800 dark:text-slate-100">
        In-flight uploads
      </h1>
      <button type="button"
              class="text-sm text-sky-600 dark:text-sky-400 hover:underline"
              :disabled="loading"
              @click="load">
        Refresh
      </button>
    </div>

    <div v-if="loading" class="text-sm text-slate-500 dark:text-slate-400">Loading…</div>

    <div v-else-if="error"
         class="px-4 py-3 rounded-lg border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 text-red-800 dark:text-red-200 text-sm">
      {{ error }}
    </div>

    <div v-else-if="batches.length === 0"
         class="bg-white dark:bg-slate-800 rounded-lg p-12 text-center shadow text-slate-500 dark:text-slate-400">
      No batches awaiting review.
    </div>

    <ul v-else class="space-y-2">
      <li v-for="b in batches"
          :key="b.batch_id"
          class="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 px-4 py-3 flex items-center justify-between hover:bg-slate-50 dark:hover:bg-slate-750 cursor-pointer transition-colors duration-75"
          @click="router.push({ name: 'batch-review', params: { batchId: b.batch_id } })">
        <div>
          <p class="font-semibold text-slate-800 dark:text-slate-100 text-sm">
            Batch #{{ b.batch_id }}
            <span v-if="b.source_file" class="ml-2 font-normal text-slate-500 dark:text-slate-400">
              {{ b.source_file }}
            </span>
          </p>
          <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            {{ formatDate(b.created_at) }}
            &middot; {{ b.new_b_count }} new B# part(s)
            &middot; {{ b.pending_row_count }} pending row(s)
          </p>
        </div>
        <span class="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 dark:bg-amber-800/30 dark:text-amber-200 font-medium shrink-0">
          awaiting review
        </span>
      </li>
    </ul>
  </section>
</template>
