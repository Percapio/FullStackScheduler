<script setup lang="ts">
/** Pure presentation search + paginator bar, no store coupling.
 *  Each parent constructs its own useDebouncedRef and passes the debounced
 *  value as `searchQuery`; the bar emits raw user input via
 *  `update:searchQuery`. This keeps timing logic out of the component and
 *  allows test harnesses to drive input synchronously. */
defineProps<{
  searchQuery:       string
  pageStart:         number
  pageEnd:           number
  total:             number
  hasPrev:           boolean
  hasNext:           boolean
  loading:           boolean
  placeholder?:      string
  /** When true, ↑ Top / ↓ Bottom buttons are rendered. */
  showScrollControls?: boolean
}>()

const emit = defineEmits<{
  'update:searchQuery': [next: string]
  'prev':              []
  'next':              []
  'scroll-top':        []
  'scroll-bottom':     []
}>()
</script>

<template>
  <div class="flex items-center gap-4 h-12">
    <div class="relative flex-1 max-w-md">
      <input
        :value="searchQuery"
        type="search"
        :placeholder="placeholder ?? 'Search…'"
        aria-label="Search"
        class="w-full px-3 py-1.5 pr-8 rounded border border-slate-300 dark:border-slate-600
               bg-white dark:bg-slate-800 text-sm text-slate-800 dark:text-slate-100 focus-ring"
        @input="emit('update:searchQuery', ($event.target as HTMLInputElement).value)"
      />
      <span v-if="loading"
            class="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-slate-400">…</span>
    </div>

    <template v-if="showScrollControls">
      <div class="flex gap-1">
        <button type="button" aria-label="Scroll to top"
                class="px-3 py-1 rounded text-sm text-slate-600 dark:text-slate-300
                       hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors
                       duration-100 ease-out focus-ring"
                @click="emit('scroll-top')">
          ↑ Top
        </button>
        <button type="button" aria-label="Scroll to bottom"
                class="px-3 py-1 rounded text-sm text-slate-600 dark:text-slate-300
                       hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors
                       duration-100 ease-out focus-ring"
                @click="emit('scroll-bottom')">
          ↓ Bottom
        </button>
      </div>
    </template>

    <span class="ml-auto text-sm text-slate-500 dark:text-slate-400 tabular-nums"
          data-testid="paginator-readout">
      Showing {{ pageStart }}–{{ pageEnd }} of {{ total }}
    </span>

    <div class="flex gap-2">
      <button type="button"
              :disabled="!hasPrev || loading"
              aria-label="Previous page"
              class="px-3 py-1 rounded border border-slate-300 dark:border-slate-600 text-sm
                     font-medium disabled:opacity-40 disabled:cursor-not-allowed
                     hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors
                     duration-100 ease-out focus-ring"
              @click="emit('prev')">
        ← Prev
      </button>
      <button type="button"
              :disabled="!hasNext || loading"
              aria-label="Next page"
              class="px-3 py-1 rounded border border-slate-300 dark:border-slate-600 text-sm
                     font-medium disabled:opacity-40 disabled:cursor-not-allowed
                     hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors
                     duration-100 ease-out focus-ring"
              @click="emit('next')">
        Next →
      </button>
    </div>
    
    <slot name="actions" />
  </div>
</template>
