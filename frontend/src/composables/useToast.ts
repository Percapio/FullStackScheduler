import { ref } from 'vue'

export interface Toast {
  id: number
  message: string
  kind: 'error' | 'success'
}

let nextId = 0
const toasts = ref<Toast[]>([])

export function useToast() {
  function show(message: string, kind: Toast['kind'] = 'error', durationMs = 6000) {
    const id = nextId++
    toasts.value.push({ id, message, kind })
    setTimeout(() => dismiss(id), durationMs)
  }

  function dismiss(id: number) {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }

  return { toasts, show, dismiss }
}
