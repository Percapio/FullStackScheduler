import { apiClient } from './client'
import type { components } from './types.gen'

export type ShippingLogCandidate = components['schemas']['ShippingLogCandidate']
export type ShippingLogCandidateList = components['schemas']['ShippingLogCandidateList']
export type IneligibleJob = components['schemas']['ShippingLogIneligibleJob']
export type IneligibleReason = IneligibleJob['reason']

export type CandidatesOutcome =
  | { kind: 'ok'; list: ShippingLogCandidateList }
  | { kind: 'transport'; message: string }

export interface ShippingLogDownload {
  file: Blob
  filename: string
  clippedJobIds: number[]
}

/**
 * - "selection_size":       422 — zero jobs, or more than the server's max.
 * - "ineligible":           409 — a job left the Shipping-view population
 *                           (shipped, discarded, superseded) after the list loaded.
 * - "template_unavailable": 503 — the build shipped without the template.
 * - "transport":            anything else, including an error body that isn't JSON.
 */
export type ShippingLogOutcome =
  | { kind: 'ok'; download: ShippingLogDownload }
  | { kind: 'selection_size'; requested: number; max: number }
  | { kind: 'ineligible'; jobs: IneligibleJob[] }
  | { kind: 'template_unavailable' }
  | { kind: 'transport'; message: string }

export const FALLBACK_SHIPPING_LOG_FILENAME = 'Shipping_Log.xlsx'

function transportMessage(err: unknown): string {
  return err instanceof Error && err.message ? err.message : 'Network error'
}

export async function fetchShippingLogCandidates(): Promise<CandidatesOutcome> {
  try {
    const resp = await apiClient.get<ShippingLogCandidateList>('/api/shipping-log/candidates')
    return { kind: 'ok', list: resp.data }
  } catch (err: unknown) {
    return { kind: 'transport', message: transportMessage(err) }
  }
}

/**
 * Generate the shipping log. The server names the file; the client never computes one.
 *
 * Timeout: the 30 s default. 200 boxes measure well under a second, far inside
 * client.ts's 25 s override threshold.
 */
export async function generateShippingLog(jobIds: number[]): Promise<ShippingLogOutcome> {
  try {
    const resp = await apiClient.post<Blob>(
      '/api/shipping-log',
      { job_ids: jobIds },
      { responseType: 'blob' },
    )
    return {
      kind: 'ok',
      download: {
        file: resp.data,
        filename: shippingLogFilename(resp.headers['content-disposition']),
        clippedJobIds: parseClippedJobIds(resp.headers['x-shipping-log-clipped']),
      },
    }
  } catch (err: unknown) {
    return classifyShippingLogFailure(err)
  }
}

/**
 * With responseType 'blob', axios delivers error bodies as Blobs too, so the body
 * is parsed as JSON before it is classified. A body that won't parse is transport.
 */
export async function classifyShippingLogFailure(err: unknown): Promise<ShippingLogOutcome> {
  const response = (err as { response?: { status?: number; data?: unknown } })?.response
  if (!response) {
    return { kind: 'transport', message: transportMessage(err) }
  }
  const unexpected: ShippingLogOutcome = {
    kind: 'transport',
    message: `Unexpected response from the server (${response.status ?? 'no status'})`,
  }

  let body: Record<string, unknown>
  try {
    const parsed: unknown = await readJsonBody(response.data)
    if (parsed === null || typeof parsed !== 'object') return unexpected
    body = parsed as Record<string, unknown>
  } catch {
    return unexpected
  }

  if (response.status === 409 && body.kind === 'ineligible_jobs' && Array.isArray(body.jobs)) {
    return { kind: 'ineligible', jobs: body.jobs as IneligibleJob[] }
  }
  if (response.status === 422 && body.kind === 'selection_size') {
    return { kind: 'selection_size', requested: Number(body.requested), max: Number(body.max) }
  }
  if (response.status === 503 && body.kind === 'template_unavailable') {
    return { kind: 'template_unavailable' }
  }
  return unexpected
}

async function readJsonBody(data: unknown): Promise<unknown> {
  if (data instanceof Blob) return JSON.parse(await data.text())
  if (typeof data === 'string') return JSON.parse(data)
  return data
}

function shippingLogFilename(contentDisposition: unknown): string {
  const header = typeof contentDisposition === 'string' ? contentDisposition : ''
  const match = /filename="([^"]+)"/i.exec(header) ?? /filename=([^;\s]+)/i.exec(header)
  if (match) return match[1]
  // Only a dev CORS setup that doesn't expose Content-Disposition gets here.
  console.warn(
    `Shipping log response carried no filename; saving as ${FALLBACK_SHIPPING_LOG_FILENAME}. ` +
    'Check that the server exposes Content-Disposition.',
  )
  return FALLBACK_SHIPPING_LOG_FILENAME
}

function parseClippedJobIds(header: unknown): number[] {
  if (typeof header !== 'string' || header.trim() === '') return []
  return header
    .split(',')
    .map(part => Number(part.trim()))
    .filter(jobId => Number.isInteger(jobId) && jobId > 0)
}
