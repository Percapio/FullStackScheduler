import axios, { AxiosError } from 'axios'

// In production the SPA is served by FastAPI on the same origin as the API,
// so we use relative URLs to inherit whatever host:port the page was loaded
// from (localhost, 127.0.0.1, LAN IP, hostname). Hard-coding
// 'http://localhost:8000' here breaks that contract: the multipart upload
// counts as a "simple request" so the browser sends it, the server commits
// the data, then strips the response because Access-Control-Allow-Origin
// is missing — surfacing as a misleading "Network Error" in the UI.
// Dev (Vite at :5173) still needs an explicit backend URL.
const _devFallback = import.meta.env.DEV ? 'http://localhost:8000' : ''

// Per-endpoint timeout contract (Phase 18b Patch 02):
//   - Default (this file):    30 s
//   - POST /ingest:           60 s   (UploadModal.vue submit())
//   - POST /confirm:          60 s   (review.ts confirmReview())
//   - Everything else inherits the 30 s default.
//
// Any new endpoint whose worst-case wall time exceeds 25 s MUST pass an
// explicit per-request `timeout` override at the call site. Long-running
// endpoints without an override silently inherit 30 s and will cancel under load.
export const baseURL = import.meta.env.VITE_API_BASE ?? _devFallback

export const apiClient = axios.create({
  baseURL,
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

apiClient.interceptors.request.use(async (config) => {
  const { CLIENT_ID } = await import('@/composables/useUpdateChannel')
  config.headers['X-Client-Id'] = CLIENT_ID
  return config
})

export type ApiError = AxiosError<{ detail: unknown }>
export const isApiError = (e: unknown): e is ApiError =>
  axios.isAxiosError(e)
