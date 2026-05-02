import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { AxiosError, AxiosHeaders } from 'axios'
import { useStagingStore } from '../staging'
import type { StagingRowDetail, StagingRowSummary } from '@/api/staging'

const mockFetchErrored      = vi.fn()
const mockFetchDetail       = vi.fn()
const mockSubmitCorrection  = vi.fn()

vi.mock('@/api/staging', () => ({
  fetchErrored:     (...a: unknown[]) => mockFetchErrored(...a),
  fetchDetail:      (...a: unknown[]) => mockFetchDetail(...a),
  submitCorrection: (...a: unknown[]) => mockSubmitCorrection(...a),
}))

const ts = '2026-04-19T00:00:00'

function summaryFixture(id: number, processing_error = 'Invalid QTY: 0'): StagingRowSummary {
  return {
    id,
    batch_id: 1,
    source_row_number: id,
    processing_status: 'error',
    processing_error,
    suggested_correction: null,
    resolved_job_id: null,
    processed_at: null,
    created_at: ts,
    updated_at: ts,
  } as StagingRowSummary
}

function detailFixture(id: number, overrides: Partial<StagingRowDetail> = {}): StagingRowDetail {
  return {
    ...summaryFixture(id),
    raw_job: 'X', raw_qty: '0', raw_customer: 'C',
    raw_shipped: null, raw_pcb_notes: null, raw_kit_notes: null,
    raw_scheduling_notes: null, raw_line_1: null, raw_line_2: null, raw_line_3: null,
    raw_ship_date: null, raw_prog: null, raw_mfg_notes: null,
    raw_smt_lines: null, raw_smt_plcmnts: null, raw_ship_method: null,
    raw_sales_p: null, raw_doc_rel: null, raw_kit_rel: null,
    raw_code: null, raw_bom_compare_photos: null,
    highlight_fields: ['raw_qty'],
    ...overrides,
  } as StagingRowDetail
}

function apiError(status: number, detail: string): AxiosError {
  const err = new AxiosError('mock', String(status))
  err.response = {
    status,
    statusText: '',
    data: { detail },
    headers: {},
    config: { headers: new AxiosHeaders() } as never,
  }
  return err
}

beforeEach(() => {
  setActivePinia(createPinia())
  mockFetchErrored.mockReset()
  mockFetchDetail.mockReset()
  mockSubmitCorrection.mockReset()
})

describe('useStagingStore', () => {
  it('openError lazy-fetches detail and sets activeErrorRowId', async () => {
    mockFetchDetail.mockResolvedValueOnce(detailFixture(7))
    const store = useStagingStore()

    await store.openError(7)

    expect(store.activeErrorRowId).toBe(7)
    expect(store.details[7]).toBeDefined()
    expect(mockFetchDetail).toHaveBeenCalledTimes(1)
  })

  it('openError is idempotent on cache hit', async () => {
    const store = useStagingStore()
    store.details[7] = detailFixture(7)

    await store.openError(7)

    expect(mockFetchDetail).not.toHaveBeenCalled()
    expect(store.activeErrorRowId).toBe(7)
  })

  it('closeError clears activeErrorRowId but keeps cache', () => {
    const store = useStagingStore()
    store.details[7] = detailFixture(7)
    store.activeErrorRowId = 7

    store.closeError()

    expect(store.activeErrorRowId).toBeNull()
    expect(store.details[7]).toBeDefined()
  })

  it('correct.422 keeps activeErrorRowId set and re-injects row with new processing_error', async () => {
    const store = useStagingStore()
    store.rows = [summaryFixture(7, 'old')]
    store.activeErrorRowId = 7
    store.details[7] = detailFixture(7, { processing_error: 'old' })

    mockSubmitCorrection.mockRejectedValueOnce(apiError(422, 'Invalid QTY'))

    const result = await store.correct(7, { raw_qty: '0' })

    expect(result.kind).toBe('transform-failed')
    expect(store.activeErrorRowId).toBe(7)
    expect(store.rows[0].processing_error).toBe('Invalid QTY')
    expect(store.details[7].processing_error).toBe('Invalid QTY')
  })

  it('correct.409 leaves activeErrorRowId untouched (sidebar will close explicitly)', async () => {
    const store = useStagingStore()
    store.rows = [summaryFixture(7)]
    store.activeErrorRowId = 7

    mockSubmitCorrection.mockRejectedValueOnce(apiError(409, 'Row no longer in error state'))

    const result = await store.correct(7, { raw_qty: '0' })

    expect(result.kind).toBe('conflict')
    expect(store.activeErrorRowId).toBe(7)
    expect(store.rows).toHaveLength(1)
  })

  it('correct.ok removes the row but does not touch activeErrorRowId', async () => {
    const store = useStagingStore()
    store.rows = [summaryFixture(7)]
    store.total = 1
    store.activeErrorRowId = 7

    mockSubmitCorrection.mockResolvedValueOnce({})

    const result = await store.correct(7, { raw_qty: '5' })

    expect(result.kind).toBe('ok')
    expect(store.rows).toHaveLength(0)
    expect(store.total).toBe(0)
    expect(store.activeErrorRowId).toBe(7)
  })
})
