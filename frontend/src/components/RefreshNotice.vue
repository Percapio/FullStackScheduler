<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useShippingStore } from '@/stores/shipping'
import { useHistoryStore } from '@/stores/history'
import { useStagingStore } from '@/stores/staging'
import type { ScheduleMerged } from '@/composables/useUpdateChannel'

const props = defineProps<{
  events: ScheduleMerged[]
}>()

const emit = defineEmits<{
  dismiss: []
}>()

const route = useRoute()
const shipping = useShippingStore()
const history = useHistoryStore()
const staging = useStagingStore()

const isReviewing = computed(() => route.name === 'batch-review')

type RouteName = 'reconciliation' | 'shipping' | 'history' | 'batch-review'

async function apply_schedule_update(route_name: RouteName): Promise<void> {
  try {
    if (route_name === 'shipping') {
      await shipping.load()
    } else if (route_name === 'history') {
      await history.load()
    } else if (route_name === 'reconciliation') {
      await Promise.all([
        staging.loadErrored(),
        staging.loadDiscarded(),
        staging.loadConflicts()
      ])
    } else if (route_name === 'batch-review') {
      // Intentionally no refetch
    } else {
      // exhaustive check
      const _exhaustiveCheck: never = route_name
      return _exhaustiveCheck
    }
  } catch (e) {
    // Stores handle their own error state
  }
}

async function handleRefresh() {
  const routeName = route.name as RouteName | null | undefined
  if (routeName) {
    await apply_schedule_update(routeName)
  }
  emit('dismiss')
}
</script>

<template>
  <div v-if="events.length > 0" class="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 rounded-lg shadow-lg bg-slate-800 text-white px-4 py-3 text-sm flex items-center gap-3 w-max">
    <template v-if="isReviewing">
      <span>A new schedule was uploaded. It will not affect your current review.</span>
      <button @click="emit('dismiss')" class="text-slate-400 hover:text-white p-1 rounded-full bg-slate-700/50 hover:bg-slate-700 transition-colors">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
        </svg>
      </button>
    </template>
    <template v-else>
      <span v-if="events.length === 1">
        A new schedule was uploaded: {{ events[0].rows_inserted }} inserted, {{ events[0].rows_updated }} updated.
      </span>
      <span v-else>
        New schedules were uploaded.
      </span>
      <button @click="handleRefresh" class="font-medium text-sky-400 hover:text-sky-300 underline">
        Click to Refresh
      </button>
      <button @click="emit('dismiss')" class="text-slate-400 hover:text-white p-1 rounded-full bg-slate-700/50 hover:bg-slate-700 transition-colors">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
        </svg>
      </button>
    </template>
  </div>
</template>
