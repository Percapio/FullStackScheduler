<script setup lang="ts">
import { watchEffect } from 'vue'

type PanelWidth = 'md' | 'xl' | 'full'

const props = withDefaults(
  defineProps<{
    open: boolean
    width?: PanelWidth
    ariaLabel: string
  }>(),
  { width: 'xl' },
)

const emit = defineEmits<{ close: [] }>()

const WIDTH_TO_CLASS: Record<PanelWidth, string> = {
  md:   'w-full max-w-md',
  xl:   'w-full max-w-2xl',
  full: 'w-full',
}

watchEffect((onCleanup) => {
  if (!props.open) return
  const handler = (e: KeyboardEvent) => {
    if (e.key === 'Escape') emit('close')
  }
  window.addEventListener('keydown', handler)
  onCleanup(() => window.removeEventListener('keydown', handler))
})
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="open" class="fixed inset-0 z-40" data-testid="drawer-overlay">
        <div class="absolute inset-0 bg-slate-900/40 dark:bg-slate-900/60"
             data-testid="drawer-backdrop"
             @click="emit('close')" />
        <aside :class="['absolute right-0 top-0 h-full',
                        'bg-white dark:bg-slate-800',
                        'shadow-2xl flex flex-col overflow-hidden',
                        WIDTH_TO_CLASS[width]]"
               role="dialog" :aria-label="ariaLabel">
          <header class="sticky top-0 bg-inherit border-b border-slate-200 dark:border-slate-700
                         flex items-center justify-between px-6 py-4">
            <slot name="header" />
          </header>
          <div class="flex-1 overflow-y-auto px-6 py-4">
            <slot />
          </div>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-enter-active,
.drawer-leave-active { transition: transform 250ms ease-out, opacity 200ms ease-out; }
.drawer-enter-from,
.drawer-leave-to     { transform: translateX(100%); opacity: 0; }
</style>
