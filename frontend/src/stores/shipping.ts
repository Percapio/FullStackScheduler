import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchShippingJobs, type JobReadExpanded } from '@/api/shipping'
import { useToast } from '@/composables/useToast'

export const useShippingStore = defineStore('shipping', () => {
  const jobs    = ref<JobReadExpanded[]>([])
  const loading = ref(false)
  const error   = ref<string | null>(null)

  async function load() {
    loading.value = true
    error.value = null
    try {
      const { rows, total } = await fetchShippingJobs(500)
      jobs.value = rows
      if (total > rows.length) {
        useToast().show(
          `Showing ${rows.length} of ${total} open jobs. Contact admin if the full list is needed.`,
          'error',
          8000,
        )
      }
    } catch {
      error.value = 'Could not load open jobs. Check that the backend is running and retry.'
    } finally {
      loading.value = false
    }
  }

  return { jobs, loading, error, load }
})
