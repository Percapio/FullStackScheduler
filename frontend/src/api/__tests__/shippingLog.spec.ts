import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  FALLBACK_SHIPPING_LOG_FILENAME,
  classifyShippingLogFailure,
  fetchShippingLogCandidates,
  generateShippingLog,
} from '../shippingLog'
import { apiClient } from '../client'

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

function jsonBlob(body: unknown): Blob {
  return new Blob([JSON.stringify(body)], { type: 'application/json' })
}

function httpError(status: number, data: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data },
  })
}

afterEach(() => {
  vi.mocked(apiClient.get).mockReset()
  vi.mocked(apiClient.post).mockReset()
  vi.restoreAllMocks()
})

describe('generateShippingLog', () => {
  it('posts the IDs as a blob request and reads the server filename', async () => {
    const file = new Blob(['xlsx bytes'])
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: file,
      headers: { 'content-disposition': 'attachment; filename="Shipping_Log_20260916_101500.xlsx"' },
    })

    const outcome = await generateShippingLog([3, 1])

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/shipping-log', { job_ids: [3, 1] }, { responseType: 'blob' },
    )
    expect(outcome).toEqual({
      kind: 'ok',
      download: { file, filename: 'Shipping_Log_20260916_101500.xlsx', clippedJobIds: [] },
    })
  })

  it('parses the clipped header into job IDs', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: new Blob(['x']),
      headers: {
        'content-disposition': 'attachment; filename="Shipping_Log.xlsx"',
        'x-shipping-log-clipped': '12, 40,7',
      },
    })

    const outcome = await generateShippingLog([12, 40, 7])

    expect(outcome.kind === 'ok' && outcome.download.clippedJobIds).toEqual([12, 40, 7])
  })

  it('falls back to a fixed filename and warns when Content-Disposition is unreadable', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: new Blob(['x']), headers: {} })

    const outcome = await generateShippingLog([1])

    expect(outcome.kind === 'ok' && outcome.download.filename).toBe(FALLBACK_SHIPPING_LOG_FILENAME)
    expect(warn).toHaveBeenCalledOnce()
  })

  it('parses a 409 blob body to ineligible', async () => {
    const jobs = [{ job_id: 4, reason: 'shipped' }, { job_id: 9, reason: 'discarded' }]
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      httpError(409, jsonBlob({ kind: 'ineligible_jobs', jobs })),
    )

    expect(await generateShippingLog([4, 9])).toEqual({ kind: 'ineligible', jobs })
  })

  it('parses a 503 blob body to template_unavailable', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      httpError(503, jsonBlob({ kind: 'template_unavailable' })),
    )

    expect(await generateShippingLog([1])).toEqual({ kind: 'template_unavailable' })
  })

  it('parses a 422 selection_size blob body', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      httpError(422, jsonBlob({ kind: 'selection_size', requested: 201, max: 200 })),
    )

    expect(await generateShippingLog([1])).toEqual({ kind: 'selection_size', requested: 201, max: 200 })
  })

  it('maps a non-JSON error blob to transport', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      httpError(500, new Blob(['Internal Server Error'], { type: 'text/plain' })),
    )

    const outcome = await generateShippingLog([1])

    expect(outcome.kind).toBe('transport')
  })

  it('maps a JSON body of an unexpected shape to transport', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(httpError(500, jsonBlob({ kind: 'internal' })))
    expect((await generateShippingLog([1])).kind).toBe('transport')

    vi.mocked(apiClient.post).mockRejectedValueOnce(httpError(422, jsonBlob({ detail: [] })))
    expect((await generateShippingLog([1])).kind).toBe('transport')
  })

  it('maps a network failure with no response to transport with its message', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new Error('timeout of 30000ms exceeded'))

    expect(await generateShippingLog([1])).toEqual({ kind: 'transport', message: 'timeout of 30000ms exceeded' })
  })
})

describe('classifyShippingLogFailure', () => {
  it('accepts an already-parsed body', async () => {
    expect(await classifyShippingLogFailure(httpError(503, { kind: 'template_unavailable' })))
      .toEqual({ kind: 'template_unavailable' })
  })
})

describe('fetchShippingLogCandidates', () => {
  it('returns the list', async () => {
    const list = { candidates: [], total: 0, truncated: false, max_jobs_per_log: 200, template_ready: true }
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: list })

    expect(await fetchShippingLogCandidates()).toEqual({ kind: 'ok', list })
    expect(apiClient.get).toHaveBeenCalledWith('/api/shipping-log/candidates')
  })

  it('maps any failure to transport', async () => {
    vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('Network Error'))

    expect(await fetchShippingLogCandidates()).toEqual({ kind: 'transport', message: 'Network Error' })
  })
})
