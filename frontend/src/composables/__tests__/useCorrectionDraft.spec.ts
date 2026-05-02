import { describe, it, expect } from 'vitest'
import { ref } from 'vue'
import { useCorrectionDraft, RAW_KEYS } from '../useCorrectionDraft'
import type { StagingRowDetail } from '@/api/staging'

function makeDetail(overrides: Partial<StagingRowDetail> = {}): StagingRowDetail {
  const raw = Object.fromEntries(RAW_KEYS.map(k => [k, null]))
  return {
    id: 1, batch_id: 1, source_row_number: 1,
    processing_status: 'error', processing_error: '',
    resolved_job_id: null, processed_at: null,
    created_at: '2026-04-19T00:00:00', updated_at: '2026-04-19T00:00:00',
    ...raw,
    ...overrides,
  } as StagingRowDetail
}

describe('useCorrectionDraft — null-roundtrip correctness', () => {
  it('omits null-valued fields the user never touched (no phantom diff)', () => {
    const detail = ref<StagingRowDetail>(makeDetail({ raw_job: null }))
    const { changedPayload } = useCorrectionDraft(detail)
    expect('raw_job' in changedPayload.value).toBe(false)
    expect(Object.keys(changedPayload.value)).toHaveLength(0)
  })

  it('emits null when user clears a previously populated field', () => {
    const detail = ref<StagingRowDetail>(makeDetail({ raw_job: '128764\nNEW' }))
    const { setField, changedPayload } = useCorrectionDraft(detail)
    setField('raw_job', '')
    expect(changedPayload.value.raw_job).toBeNull()
  })

  it('emits the new value when user changes a populated field', () => {
    const detail = ref<StagingRowDetail>(makeDetail({ raw_job: '128764\nNEW' }))
    const { setField, changedPayload } = useCorrectionDraft(detail)
    setField('raw_job', '137845\nNEW')
    expect(changedPayload.value.raw_job).toBe('137845\nNEW')
  })

  it('only the changed key appears in the payload', () => {
    const detail = ref<StagingRowDetail>(
      makeDetail({ raw_job: '128764\nNEW', raw_qty: '5', raw_customer: 'ACME' }),
    )
    const { setField, changedPayload } = useCorrectionDraft(detail)
    setField('raw_qty', '10')
    expect(Object.keys(changedPayload.value)).toEqual(['raw_qty'])
    expect(changedPayload.value.raw_qty).toBe('10')
  })
})
