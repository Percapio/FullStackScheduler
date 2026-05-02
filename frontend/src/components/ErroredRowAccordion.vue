<script setup lang="ts">
import { computed, ref } from 'vue'
import { useStagingStore } from '@/stores/staging'
import { useCorrectionDraft } from '@/composables/useCorrectionDraft'
import { useToast } from '@/composables/useToast'
import RawFieldGrid from './RawFieldGrid.vue'
import WarnIcon from './WarnIcon.vue'
import type { StagingRowDetail } from '@/api/staging'

const props = defineProps<{ rowId: number }>()
const store = useStagingStore()
const { show: showToast } = useToast()

const detail = computed<StagingRowDetail | undefined>(() => store.details[props.rowId])
const { draft, changedPayload, hasChanges, setField, originalFor } = useCorrectionDraft(detail)

const submitting = ref(false)
const lastOutcome = ref<string | null>(null)

async function onSubmit() {
  if (!hasChanges.value || submitting.value) return
  submitting.value = true
  lastOutcome.value = null
  const result = await store.correct(props.rowId, changedPayload.value)
  submitting.value = false
  if (result.kind === 'ok') return
  if (result.kind === 'network') {
    showToast(result.message, 'error')
    return
  }
  if (result.kind === 'transform-failed') lastOutcome.value = result.processingError
  else if (result.kind === 'conflict')    lastOutcome.value = result.message
}
</script>

<template>
  <div v-if="!detail" class="text-slate-500 dark:text-slate-400 text-sm">Loading detail…</div>

  <div v-else class="space-y-4">
    <div class="bg-warn-50 dark:bg-warn-800/20 border border-warn-200 dark:border-warn-600/40 rounded-md px-4 py-3">
      <p class="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-warn-600 mb-1">
        <WarnIcon size="md" />
        Processing Error
      </p>
      <pre class="text-sm text-warn-800 dark:text-warn-200 whitespace-pre-wrap font-mono">{{ detail.processing_error }}</pre>
    </div>

    <form @submit.prevent="onSubmit" class="space-y-4">
      <RawFieldGrid
        :draft="draft"
        :original-for="originalFor"
        @update:field="setField"
      />

      <div class="flex items-center justify-between pt-2 border-t border-slate-200 dark:border-slate-700">
        <p v-if="lastOutcome" class="text-warn-600 text-sm inline-flex items-center gap-1">
          <WarnIcon /> {{ lastOutcome }}
        </p>
        <p v-else-if="hasChanges" class="text-slate-500 dark:text-slate-400 text-xs">
          {{ Object.keys(changedPayload).length }} field(s) changed
        </p>
        <span v-else />

        <button
          type="submit"
          :disabled="!hasChanges || submitting"
          class="px-4 py-2 rounded-md bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 text-sm font-medium
                 hover:bg-slate-700 dark:hover:bg-slate-300 transition-colors
                 disabled:bg-slate-300 dark:disabled:bg-slate-600 disabled:cursor-not-allowed"
        >
          {{ submitting ? 'Submitting…' : 'Apply correction' }}
        </button>
      </div>
    </form>
  </div>
</template>
