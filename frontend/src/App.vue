<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppNav from '@/components/AppNav.vue'
import ToastContainer from '@/components/ToastContainer.vue'
import RefreshNotice from '@/components/RefreshNotice.vue'
import { useUpdateChannel, type ScheduleMerged, type UpdateEvent } from '@/composables/useUpdateChannel'

const route = useRoute()
const navPadding = computed(() => route.name === 'history' ? 'pt-32' : 'pt-20')

const mergedEvents = ref<ScheduleMerged[]>([])

const { status } = useUpdateChannel((event: UpdateEvent) => {
  if (event.type === 'schedule_merged') {
    mergedEvents.value.push(event)
  }
})

// Dismiss when route changes
watch(() => route.path, () => {
  mergedEvents.value = []
})

function dismissNotice() {
  mergedEvents.value = []
}
</script>

<template>
  <div class="min-h-screen flex flex-col bg-slate-50 dark:bg-slate-900">
    <AppNav :ws-status="status" />
    <main :class="[navPadding, 'flex-1 max-w-7xl w-full mx-auto px-6 pb-8']">
      <RouterView />
    </main>
    <ToastContainer />
    <RefreshNotice :events="mergedEvents" @dismiss="dismissNotice" />
  </div>
</template>
