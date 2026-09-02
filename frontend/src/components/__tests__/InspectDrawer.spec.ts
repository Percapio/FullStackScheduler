import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest'
import { mount, VueWrapper, flushPromises } from '@vue/test-utils'
import InspectDrawer from '../InspectDrawer.vue'
import InspectJobBlock from '../InspectJobBlock.vue'
import type { JobReadExpanded } from '@/api/history'
import { fetchJobLineage } from '@/api/history'

const ts = '2026-04-19T00:00:00'

function makeJob(overrides: Partial<JobReadExpanded> = {}): JobReadExpanded {
  return {
    id: 42,
    assembly_id: 1,
    customer_id: 1,
    status: 'shipped',
    quantity: 10,
    shipped_at: '2026-04-01',
    line_1: false,
    line_2: false,
    line_3: false,
    created_at: ts,
    updated_at: ts,
    assembly: {
      id: 1,
      part_number: 'TEST-001',
      created_at: ts,
      updated_at: ts,
      classifications: [{ id: 1, code: 'AS9100', description: 'Aero' }],
    },
    customer: { id: 1, name: 'Acme Corp', created_at: ts, updated_at: ts },
    salesperson: null,
    discarded_at: null,
    ...overrides,
  } as JobReadExpanded
}

vi.mock('@/api/history', () => ({
  fetchJobLineage: vi.fn(),
}))

vi.mock('@/api/photos', () => ({
  fetchAvailableDates: vi.fn().mockResolvedValue({ kind: 'ok', status: 'ok', folders: [], truncated: false }),
  openPhotoFolder: vi.fn(),
  photo_folder_for: vi.fn((job) => job.shipped_at ? job.shipped_at.replace(/-/g, '_') : null),
}))

vi.mock('@/composables/useToast', () => ({
  useToast: () => ({
    show: vi.fn(),
  }),
}))

let wrapper: VueWrapper | null = null

function mountDrawer(anchor: JobReadExpanded | null) {
  wrapper = mount(InspectDrawer, {
    props: { anchor },
    attachTo: document.body,
    global: {
      stubs: {
        InspectJobBlock: true,
      },
    },
  })
  return wrapper
}

beforeEach(() => {
  vi.mocked(fetchJobLineage).mockReset()
  vi.mocked(fetchJobLineage).mockResolvedValue([])
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

function bodyHtml() {
  return document.body.innerHTML
}

describe('InspectDrawer', () => {
  it('renders nothing when anchor is null', () => {
    mountDrawer(null)
    expect(bodyHtml()).not.toContain('drawer-overlay')
  })

  it('fetches lineage and renders InspectJobBlock for each job', async () => {
    const parent = makeJob({ id: 1, assembly: { ...makeJob().assembly, part_number: 'A' } })
    const anchorJob = makeJob({ id: 2, assembly: { ...makeJob().assembly, part_number: 'B' } })
    vi.mocked(fetchJobLineage).mockResolvedValue([parent, anchorJob])

    const w = mountDrawer(anchorJob)
    expect(bodyHtml()).toContain('Loading lineage')

    await flushPromises()
    expect(bodyHtml()).not.toContain('Loading lineage')
    
    const blocks = w.findAllComponents(InspectJobBlock)
    expect(blocks).toHaveLength(2)
    
    expect(blocks[0].props('job').id).toBe(1)
    expect(blocks[0].props('isAnchor')).toBe(false)
    expect(blocks[0].props('showAllData')).toBe(false)
    
    expect(blocks[1].props('job').id).toBe(2)
    expect(blocks[1].props('isAnchor')).toBe(true)
    expect(blocks[1].props('showAllData')).toBe(false)
  })

  it('shows degraded state on fetch error, falling back to anchor', async () => {
    const anchorJob = makeJob({ id: 42 })
    vi.mocked(fetchJobLineage).mockRejectedValue(new Error('Network'))

    const w = mountDrawer(anchorJob)
    await flushPromises()

    expect(bodyHtml()).toContain('Could not load lineage for this job')
    
    const blocks = w.findAllComponents(InspectJobBlock)
    expect(blocks).toHaveLength(1)
    expect(blocks[0].props('job').id).toBe(42)
  })

  it('passes showAllData=true to anchor only when toggled', async () => {
    const anchorJob = makeJob({ id: 42 })
    vi.mocked(fetchJobLineage).mockResolvedValue([anchorJob])

    const w = mountDrawer(anchorJob)
    await flushPromises()
    
    const checkbox = document.body.querySelector('input[type="checkbox"]') as HTMLInputElement
    checkbox.checked = true
    checkbox.dispatchEvent(new Event('change'))
    await w.vm.$nextTick()
    
    const blocks = w.findAllComponents(InspectJobBlock)
    expect(blocks[0].props('showAllData')).toBe(true)
  })

  it('emits close on X button click', async () => {
    const w = mountDrawer(makeJob())
    const btn = document.body.querySelector('[data-testid="drawer-close-btn"]') as HTMLElement
    btn.click()
    await w.vm.$nextTick()
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('emits close on backdrop click', async () => {
    const w = mountDrawer(makeJob())
    const backdrop = document.body.querySelector('[data-testid="drawer-backdrop"]') as HTMLElement
    backdrop.click()
    await w.vm.$nextTick()
    expect(w.emitted('close')).toHaveLength(1)
  })

  describe('photos integration', () => {
    it('fetches photos after lineage resolves', async () => {
      const fetchPhotos = (await import('@/api/photos')).fetchAvailableDates as any
      fetchPhotos.mockClear()
      
      const p1 = makeJob({ id: 1, shipped_at: '2023-07-24' })
      const p2 = makeJob({ id: 2, shipped_at: '2023-07-25' })
      vi.mocked(fetchJobLineage).mockResolvedValue([p1, p2])

      mountDrawer(p1)
      await flushPromises()
      
      expect(fetchPhotos).toHaveBeenCalledWith(['2023_07_24', '2023_07_25'])
    })

    it('issues no photos request for empty chain', async () => {
      const fetchPhotos = (await import('@/api/photos')).fetchAvailableDates as any
      fetchPhotos.mockClear()
      
      const p1 = makeJob({ id: 1, shipped_at: null })
      vi.mocked(fetchJobLineage).mockResolvedValue([p1])

      mountDrawer(p1)
      await flushPromises()
      
      expect(fetchPhotos).not.toHaveBeenCalled()
    })

    it('re-fetches if close and reopen on same anchor', async () => {
      const fetchPhotos = (await import('@/api/photos')).fetchAvailableDates as any
      fetchPhotos.mockClear()
      
      const p1 = makeJob({ id: 1, shipped_at: '2023-07-24' })
      vi.mocked(fetchJobLineage).mockResolvedValue([p1])

      const w = mountDrawer(p1)
      await flushPromises()
      expect(fetchPhotos).toHaveBeenCalledTimes(1)

      // Close
      await w.setProps({ anchor: null })
      await flushPromises()

      // Reopen
      await w.setProps({ anchor: p1 })
      await flushPromises()
      
      expect(fetchPhotos).toHaveBeenCalledTimes(2)
    })
  })
})
