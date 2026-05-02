import { customRef } from 'vue'

export function useDebouncedRef<T>(initial: T, delayMs = 300) {
  let handle: ReturnType<typeof setTimeout> | undefined
  let value = initial
  return customRef<T>((track, trigger) => ({
    get() {
      track()
      return value
    },
    set(next) {
      if (handle) clearTimeout(handle)
      handle = setTimeout(() => {
        value = next
        trigger()
      }, delayMs)
    },
  }))
}
