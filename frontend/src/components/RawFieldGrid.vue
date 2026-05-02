<script setup lang="ts">
import RawFieldInput from './RawFieldInput.vue'
import { RAW_KEYS, type Draft, type DraftKey } from '@/composables/useCorrectionDraft'

defineProps<{
  draft: Draft
  originalFor: (key: DraftKey) => string
}>()

const emit = defineEmits<{
  'update:field': [key: DraftKey, value: string]
}>()
</script>

<template>
  <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
    <RawFieldInput
      v-for="key in RAW_KEYS"
      :key="key"
      :field-key="key"
      :model-value="draft[key]"
      :original-value="originalFor(key)"
      @update:model-value="(v: string) => emit('update:field', key, v)"
    />
  </div>
</template>
