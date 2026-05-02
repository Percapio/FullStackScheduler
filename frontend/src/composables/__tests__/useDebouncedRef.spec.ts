import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { nextTick, watch } from 'vue'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { useDebouncedRef } from '../useDebouncedRef'

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

describe('useDebouncedRef', () => {
  it('does not flush the value before the delay', async () => {
    const r = useDebouncedRef('', 300)
    r.value = 'hello'
    vi.advanceTimersByTime(150)
    await nextTick()
    expect(r.value).toBe('')
  })

  it('flushes the latest value after the delay', async () => {
    const r = useDebouncedRef('', 300)
    r.value = 'hello'
    vi.advanceTimersByTime(300)
    await nextTick()
    expect(r.value).toBe('hello')
  })

  it('collapses rapid writes into a single flush (debounce, not throttle)', async () => {
    const r = useDebouncedRef('', 300)
    const seen: string[] = []
    watch(r, (v) => seen.push(v))

    r.value = 'a'; vi.advanceTimersByTime(100)
    r.value = 'ab'; vi.advanceTimersByTime(100)
    r.value = 'abc'; vi.advanceTimersByTime(100)
    await nextTick()
    expect(seen).toEqual([])

    vi.advanceTimersByTime(300)
    await nextTick()
    expect(seen).toEqual(['abc'])
  })

  it('v-model bound to this ref shows typed characters immediately in the DOM while the watcher fires only after the delay (A3 regression)', async () => {
    const seen: string[] = []
    const Component = defineComponent({
      setup() {
        const q = useDebouncedRef('', 300)
        watch(q, (v) => seen.push(v))
        return () => h('input', {
          value: q.value,
          onInput: (e: Event) => { q.value = (e.target as HTMLInputElement).value },
        })
      },
    })

    const w = mount(Component)
    const input = w.find('input')
    await input.setValue('a')
    await input.setValue('ab')
    await input.setValue('abc')
    expect((input.element as HTMLInputElement).value).toBe('abc')
    expect(seen).toEqual([])

    vi.advanceTimersByTime(300)
    await nextTick()
    expect(seen).toEqual(['abc'])
    expect((input.element as HTMLInputElement).value).toBe('abc')
  })
})
