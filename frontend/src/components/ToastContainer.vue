<script setup lang="ts">
import { useToast } from '@/composables/useToast'
import WarnIcon from './WarnIcon.vue'

const { toasts, dismiss } = useToast()
</script>

<template>
  <Teleport to="body">
    <div class="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-sm" aria-live="assertive">
      <TransitionGroup
        enter-active-class="transition-all duration-200 ease-out"
        enter-from-class="opacity-0 translate-x-4"
        enter-to-class="opacity-100 translate-x-0"
        leave-active-class="transition-all duration-150 ease-in"
        leave-from-class="opacity-100 translate-x-0"
        leave-to-class="opacity-0 translate-x-4"
      >
        <div v-for="toast in toasts" :key="toast.id"
          :class="[
            'flex items-start gap-2 px-4 py-3 rounded-lg shadow-[var(--shadow-elevated)] text-sm cursor-pointer',
            toast.kind === 'error'
              ? 'bg-warn-50 border border-warn-200 text-warn-800'
              : 'bg-emerald-50 border border-emerald-200 text-emerald-800',
          ]"
          @click="dismiss(toast.id)"
        >
          <WarnIcon v-if="toast.kind === 'error'" class="text-warn-600 mt-0.5" />
          <span>{{ toast.message }}</span>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>
