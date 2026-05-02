<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  fieldKey: string
  modelValue: string
  originalValue: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const isDirty = computed(() => props.modelValue !== props.originalValue)
</script>

<template>
  <label class="block text-sm">
    <span class="block text-sm text-slate-400 dark:text-slate-500 mb-1 font-mono font-bold">{{ fieldKey }}</span>
    <textarea
      :value="modelValue"
      rows="1"
      :class="[
        'w-full px-2 py-1 border rounded font-mono text-xs resize-y',
        'focus:outline-none focus:ring-2 focus:ring-slate-400 focus:border-transparent',
        isDirty
          ? 'border-amber-400 bg-amber-50'
          : 'border-slate-300 bg-white',
      ]"
      @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
    />
  </label>
</template>
