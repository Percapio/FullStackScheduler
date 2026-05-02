import { computed, ref, watch, type Ref } from 'vue'
import type { CorrectionPayload, StagingRowDetail } from '@/api/staging'

export const PRIORITY_KEYS = ['raw_job', 'raw_qty', 'raw_customer'] as const
export const REMAINING_KEYS = [
  'raw_sales_p', 'raw_ship_date', 'raw_shipped', 'raw_ship_method',
  'raw_doc_rel', 'raw_kit_rel',
  'raw_prog', 'raw_smt_lines', 'raw_smt_plcmnts',
  'raw_line_1', 'raw_line_2', 'raw_line_3',
  'raw_pcb_notes', 'raw_mfg_notes', 'raw_kit_notes', 'raw_scheduling_notes',
  'raw_code', 'raw_bom_compare_photos',
] as const

export const RAW_KEYS: readonly (keyof CorrectionPayload)[] = [
  ...PRIORITY_KEYS, ...REMAINING_KEYS,
]

export type DraftKey = typeof RAW_KEYS[number]
export type Draft = Record<DraftKey, string>

const blankDraft = (): Draft =>
  Object.fromEntries(RAW_KEYS.map(k => [k, ''])) as Draft

export function useCorrectionDraft(detail: Ref<StagingRowDetail | undefined>) {
  const draft = ref<Draft>(blankDraft())

  watch(detail, d => {
    if (!d) return
    draft.value = Object.fromEntries(
      RAW_KEYS.map(k => [k, (d[k] ?? '') as string]),
    ) as Draft
  }, { immediate: true })

  const changedPayload = computed<Partial<CorrectionPayload>>(() => {
    if (!detail.value) return {}
    const out: Partial<CorrectionPayload> = {}
    for (const k of RAW_KEYS) {
      const original = (detail.value[k] ?? '') as string
      const next = draft.value[k]
      if (next === original) continue
      out[k] = next === '' ? null : next
    }
    return out
  })

  const hasChanges = computed(() => Object.keys(changedPayload.value).length > 0)

  function setField(key: DraftKey, value: string) {
    draft.value[key] = value
  }

  function originalFor(key: DraftKey): string {
    return (detail.value?.[key] ?? '') as string
  }

  return { draft, changedPayload, hasChanges, setField, originalFor }
}
