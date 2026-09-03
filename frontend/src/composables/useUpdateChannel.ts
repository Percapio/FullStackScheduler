import { ref, onUnmounted, type Ref } from 'vue'
import { baseURL } from '@/api/client'

export type ClientId = string
export type BatchId = number

export interface ScheduleMerged {
  type: 'schedule_merged'
  batch_id: BatchId
  rows_inserted: number
  rows_updated: number
}

export interface BatchAwaitingReview {
  type: 'batch_awaiting_review'
  batch_id: BatchId
}

export type UpdateEvent = ScheduleMerged | BatchAwaitingReview

export type ChannelStatus = 'Connecting' | 'Live' | 'Backoff'

export interface UpdateChannel {
  status: Ref<ChannelStatus>
  client_id: ClientId
}

export function mint_client_id(): ClientId {
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    const arr = new Uint8Array(16)
    crypto.getRandomValues(arr)
    return Array.from(arr).map(b => b.toString(16).padStart(2, '0')).join('')
  }
  return Math.random().toString(36).substring(2) + Date.now().toString(36)
}

export const CLIENT_ID: ClientId = mint_client_id()

function update_channel_url(client_id: ClientId): string {
  const base = baseURL || window.location.origin
  // Note: if base is relative (e.g. ""), use location.origin
  const absoluteBase = base.startsWith('http') ? base : window.location.origin
  const url = new URL('/api/ws/updates', absoluteBase)
  url.protocol = url.protocol.replace('http', 'ws')
  url.searchParams.set('client_id', client_id)
  return url.toString()
}

const WS_RECONNECT_BASE_MS = 500
const WS_RECONNECT_MAX_MS = 30_000
const WS_WATCHDOG_FACTOR = 2.5
const WS_INITIAL_WATCHDOG_MS = 90_000

export function useUpdateChannel(on_event: (event: UpdateEvent) => void): UpdateChannel {
  const status = ref<ChannelStatus>('Connecting')
  const client_id = CLIENT_ID
  
  let ws: WebSocket | null = null
  let watchdogTimer: number | null = null
  let reconnectTimer: number | null = null
  let consecutiveFailures = 0
  let isDisposed = false

  const clearTimers = () => {
    if (watchdogTimer !== null) window.clearTimeout(watchdogTimer)
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer)
    watchdogTimer = null
    reconnectTimer = null
  }

  const armWatchdog = (timeoutMs: number) => {
    if (watchdogTimer !== null) window.clearTimeout(watchdogTimer)
    watchdogTimer = window.setTimeout(() => {
      if (ws) {
        ws.close()
      }
      handleDisconnect()
    }, timeoutMs)
  }

  const handleDisconnect = () => {
    if (isDisposed) return
    clearTimers()
    status.value = 'Backoff'
    consecutiveFailures += 1
    const delay = Math.random() * Math.min(WS_RECONNECT_MAX_MS, WS_RECONNECT_BASE_MS * Math.pow(2, consecutiveFailures))
    reconnectTimer = window.setTimeout(connect, delay)
  }

  const connect = () => {
    if (isDisposed) return
    status.value = 'Connecting'
    ws = new WebSocket(update_channel_url(client_id))
    
    armWatchdog(WS_INITIAL_WATCHDOG_MS)

    ws.onopen = () => {
      if (isDisposed) {
        ws?.close()
        return
      }
      status.value = 'Live'
      consecutiveFailures = 0
    }

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data)
        if (payload.type === 'heartbeat') {
          armWatchdog(payload.heartbeat_seconds * 1000 * WS_WATCHDOG_FACTOR)
        } else if (payload.type === 'schedule_merged' || payload.type === 'batch_awaiting_review') {
          on_event(payload)
        }
      } catch (e) {
        // ignore malformed
      }
    }

    ws.onclose = () => {
      handleDisconnect()
    }
  }

  connect()

  onUnmounted(() => {
    isDisposed = true
    clearTimers()
    if (ws) {
      ws.close()
    }
  })

  return { status, client_id }
}
