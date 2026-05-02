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
export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? _devFallback,
  timeout: 10_000,
  headers: { 'Content-Type': 'application/json' },
})

export type ApiError = AxiosError<{ detail: unknown }>
export const isApiError = (e: unknown): e is ApiError =>
  axios.isAxiosError(e)
