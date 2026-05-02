import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import AppNav from '../AppNav.vue'
import { useHistoryStore } from '@/stores/history'

const routes = [
  { path: '/reconciliation', name: 'reconciliation', component: { template: '<div />' }, meta: { label: 'Reconciliation' } },
  { path: '/history',        name: 'history',        component: { template: '<div />' }, meta: { label: 'History' } },
]

function makeRouter() {
  return createRouter({ history: createMemoryHistory(), routes })
}

vi.mock('@/api/history', () => ({
  fetchJobHistory: vi.fn(() => Promise.resolve({ rows: [], total: 0 })),
  fetchJobLineage: vi.fn(() => Promise.resolve([])),
}))

vi.mock('@/composables/useFontSize', () => ({
  useFontSize: () => ({
    fontClass: { value: 'text-base' },
    canDecrease: { value: true },
    canIncrease: { value: true },
    decrease: vi.fn(),
    increase: vi.fn(),
  }),
}))

vi.mock('@/composables/usePstClock', () => ({
  usePstClock: () => ({ time: { value: '12:34:56' } }),
}))

async function mountNav(routePath = '/reconciliation') {
  const router = makeRouter()
  await router.push(routePath)
  await router.isReady()
  return mount(AppNav, {
    global: { plugins: [createPinia(), router] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('AppNav', () => {
  it('renders the history-only control row when route.name === "history"', async () => {
    const w = await mountNav('/history')
    expect(w.find('input[aria-label="Search history"]').exists()).toBe(true)
    expect(w.find('button[aria-label="Scroll to top"]').exists()).toBe(true)
    expect(w.find('button[aria-label="Scroll to bottom"]').exists()).toBe(true)
  })

  it('hides the control row on non-history routes (search input is not in the DOM)', async () => {
    const w = await mountNav('/reconciliation')
    expect(w.find('input[aria-label="Search history"]').exists()).toBe(false)
    expect(w.find('button[aria-label="Scroll to top"]').exists()).toBe(false)
  })

  it('typing in the search input calls historyStore.setSearch after the 300ms debounce', async () => {
    const w = await mountNav('/history')
    const store = useHistoryStore()
    const spy = vi.spyOn(store, 'setSearch')

    const input = w.find('input[aria-label="Search history"]')
    await input.setValue('acme')
    expect(spy).not.toHaveBeenCalled()

    vi.advanceTimersByTime(300)
    await flushPromises()
    expect(spy).toHaveBeenCalledWith('acme')
  })

  it('clicking ↑ Top calls window.scrollTo with top=0, behavior: smooth', async () => {
    const w = await mountNav('/history')
    const spy = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    await w.find('button[aria-label="Scroll to top"]').trigger('click')
    expect(spy).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' })
    spy.mockRestore()
  })

  it('clicking ↓ Bottom calls window.scrollTo with top=document.body.scrollHeight, behavior: smooth', async () => {
    const w = await mountNav('/history')
    Object.defineProperty(document.body, 'scrollHeight', { configurable: true, value: 4242 })
    const spy = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    await w.find('button[aria-label="Scroll to bottom"]').trigger('click')
    expect(spy).toHaveBeenCalledWith({ top: 4242, behavior: 'smooth' })
    spy.mockRestore()
  })

  it('Prev/Next delegate to historyStore.prev/next and disable at page boundaries', async () => {
    const w = await mountNav('/history')
    const store = useHistoryStore()
    const prevSpy = vi.spyOn(store, 'prev').mockResolvedValue()
    const nextSpy = vi.spyOn(store, 'next').mockResolvedValue()

    const buttons = w.findAll('button')
    const prev = buttons.find(b => b.text().includes('Prev'))!
    const next = buttons.find(b => b.text().includes('Next'))!

    expect(prev.attributes('disabled')).toBeDefined()
    expect(next.attributes('disabled')).toBeDefined()

    store.offset = 50
    store.total = 200
    store.rows.push({} as never)
    await flushPromises()

    await next.trigger('click')
    expect(nextSpy).toHaveBeenCalled()
    await prev.trigger('click')
    expect(prevSpy).toHaveBeenCalled()
  })

  it('Showing X–Y of Z reflects pageStart/pageEnd/total from the store', async () => {
    const w = await mountNav('/history')
    const store = useHistoryStore()
    store.offset = 0
    store.total = 42
    store.rows.push(...(Array.from({ length: 5 }, () => ({}) as never)))
    await flushPromises()
    expect(w.text()).toContain('Showing 1–5 of 42')
  })
})
