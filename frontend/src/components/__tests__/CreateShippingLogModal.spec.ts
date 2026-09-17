import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import CreateShippingLogModal from '../CreateShippingLogModal.vue'
import type { ShippingLogCandidate, ShippingLogCandidateList } from '@/api/shippingLog'

const { mockGet, mockPost, mockToast } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockToast: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  apiClient: { get: mockGet, post: mockPost },
}))

vi.mock('@/composables/useToast', () => ({
  useToast: () => ({ show: mockToast }),
}))

function candidate(jobId: number, overrides: Partial<ShippingLogCandidate> = {}): ShippingLogCandidate {
  return {
    job_id: jobId,
    part_number: `1385${jobId}`,
    split_suffix: null,
    build_type: 'new',
    repeat_reference: null,
    build_qualifier: null,
    quantity: 10 + jobId,
    resolved_ship_date: '2026-09-20',
    ship_date_text: '09/20',
    customer_name: 'ACME',
    ...overrides,
  }
}

function candidateList(
  candidates: ShippingLogCandidate[],
  overrides: Partial<ShippingLogCandidateList> = {},
): ShippingLogCandidateList {
  return {
    candidates,
    total: candidates.length,
    truncated: false,
    max_jobs_per_log: 200,
    template_ready: true,
    ...overrides,
  }
}

function jsonBlob(body: unknown): Blob {
  return new Blob([JSON.stringify(body)], { type: 'application/json' })
}

function httpError(status: number, data: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data } })
}

function xlsxResponse(filename: string, clipped?: string) {
  const headers: Record<string, string> = { 'content-disposition': `attachment; filename="${filename}"` }
  if (clipped) headers['x-shipping-log-clipped'] = clipped
  return { data: new Blob(['xlsx']), headers }
}

let wrapper: VueWrapper | null = null

async function mountOpen(list: ShippingLogCandidateList) {
  mockGet.mockResolvedValueOnce({ data: list })
  wrapper = mount(CreateShippingLogModal, {
    props: { open: true },
    global: { stubs: { Teleport: true } },
  })
  await flushPromises()
  return wrapper
}

function generateButton(w: VueWrapper) {
  return w.get('[data-testid="shipping-log-generate"]')
}

function rowCheckbox(w: VueWrapper, jobId: number) {
  return w.get(`[data-testid="shipping-log-row"][data-job-id="${jobId}"] input[type="checkbox"]`)
}

async function selectJobs(w: VueWrapper, jobIds: number[]) {
  for (const jobId of jobIds) {
    await rowCheckbox(w, jobId).setValue(true)
  }
}

function nextMacrotask() {
  return new Promise(resolve => setTimeout(resolve, 0))
}

beforeEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
  mockToast.mockReset()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.restoreAllMocks()
})

describe('CreateShippingLogModal', () => {
  it('loads candidates on open with nothing selected', async () => {
    const w = await mountOpen(candidateList([candidate(1), candidate(2)]))

    expect(mockGet).toHaveBeenCalledWith('/api/shipping-log/candidates')
    expect(w.findAll('[data-testid="shipping-log-row"]')).toHaveLength(2)
    expect(w.findAll('input[type="checkbox"]').every(box => !(box.element as HTMLInputElement).checked)).toBe(true)
    expect(generateButton(w).attributes('disabled')).toBeDefined()
  })

  it('renders the full identity, classifier and due date for each row', async () => {
    const w = await mountOpen(candidateList([
      candidate(1, {
        part_number: '138537', split_suffix: '-bal', build_type: 'ronc',
        repeat_reference: '137001', build_qualifier: 'rwk',
      }),
      candidate(2, { resolved_ship_date: null, ship_date_text: '???' }),
    ]))

    const rows = w.findAll('[data-testid="shipping-log-row"]')
    expect(rows[0].text()).toContain('138537')
    expect(rows[0].text()).toContain('-bal')
    expect(rows[0].text()).toContain('RONC 137001 · RWK')
    expect(rows[0].text()).toContain('09/20')
    expect(rows[1].text()).toContain('???')
  })

  it('toggles between Select All and Deselect All', async () => {
    const w = await mountOpen(candidateList([candidate(1), candidate(2)]))
    // Re-queried each time: the Teleport stub re-creates its children on render.
    const toggle = () => w.get('[data-testid="shipping-log-toggle-all"]')

    expect(toggle().text()).toBe('Select All')
    await toggle().trigger('click')
    expect(toggle().text()).toBe('Deselect All')
    expect(generateButton(w).attributes('disabled')).toBeUndefined()
    expect(generateButton(w).text()).toBe('Generate Log (2)')

    await toggle().trigger('click')
    expect(toggle().text()).toBe('Select All')
    expect(generateButton(w).attributes('disabled')).toBeDefined()
  })

  it('reads Select All again after a partial deselection', async () => {
    const w = await mountOpen(candidateList([candidate(1), candidate(2)]))
    await w.get('[data-testid="shipping-log-toggle-all"]').trigger('click')
    expect(w.get('[data-testid="shipping-log-toggle-all"]').text()).toBe('Deselect All')

    await rowCheckbox(w, 2).setValue(false)

    expect(w.get('[data-testid="shipping-log-toggle-all"]').text()).toBe('Select All')
    expect(generateButton(w).text()).toBe('Generate Log (1)')
  })

  it('disables Generate above the server max and shows n / max', async () => {
    const w = await mountOpen(candidateList([candidate(1), candidate(2), candidate(3)], { max_jobs_per_log: 2 }))

    await w.get('[data-testid="shipping-log-toggle-all"]').trigger('click')

    expect(generateButton(w).attributes('disabled')).toBeDefined()
    expect(generateButton(w).text()).toBe('Generate Log (3 / 2)')
  })

  it('disables Generate and says so when the template is not ready', async () => {
    const w = await mountOpen(candidateList([candidate(1)], { template_ready: false }))

    await selectJobs(w, [1])

    expect(w.get('[data-testid="shipping-log-template-missing"]').text())
      .toBe('Shipping log template is missing from this build.')
    expect(generateButton(w).attributes('disabled')).toBeDefined()
  })

  it('shows a banner when the list is truncated', async () => {
    const w = await mountOpen(candidateList([candidate(1), candidate(2)], { total: 5, truncated: true }))

    expect(w.get('[data-testid="shipping-log-truncated"]').text()).toBe('Showing first 2 of 5 planned jobs.')
  })

  it('shows a retry after a failed load', async () => {
    mockGet.mockRejectedValueOnce(new Error('Network Error'))
    wrapper = mount(CreateShippingLogModal, { props: { open: true }, global: { stubs: { Teleport: true } } })
    await flushPromises()

    expect(wrapper.get('[data-testid="shipping-log-load-error"]').text()).toContain('Network Error')

    mockGet.mockResolvedValueOnce({ data: candidateList([candidate(1)]) })
    await wrapper.get('[data-testid="shipping-log-retry"]').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('[data-testid="shipping-log-row"]')).toHaveLength(1)
  })

  it('submits the selection in list order, whatever order it was clicked', async () => {
    const w = await mountOpen(candidateList([candidate(1), candidate(2), candidate(3)]))
    mockPost.mockResolvedValueOnce(xlsxResponse('Shipping_Log_20260916_101500.xlsx'))
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:log'), revokeObjectURL: vi.fn() })

    await selectJobs(w, [3, 1])
    await generateButton(w).trigger('click')
    await flushPromises()

    expect(mockPost).toHaveBeenCalledWith('/api/shipping-log', { job_ids: [1, 3] }, { responseType: 'blob' })
  })

  it('downloads with the header filename, revokes the URL and closes', async () => {
    const w = await mountOpen(candidateList([candidate(1)]))
    const response = xlsxResponse('Shipping_Log_20260916_101500.xlsx')
    mockPost.mockResolvedValueOnce(response)
    const createObjectURL = vi.fn(() => 'blob:log')
    const revokeObjectURL = vi.fn()
    Object.assign(URL, { createObjectURL, revokeObjectURL })
    const clicked: Array<{ href: string; download: string; connected: boolean }> = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ href: this.href, download: this.download, connected: this.isConnected })
    })

    await selectJobs(w, [1])
    await generateButton(w).trigger('click')
    await flushPromises()

    expect(createObjectURL).toHaveBeenCalledWith(response.data)
    expect(clicked).toEqual([{ href: 'blob:log', download: 'Shipping_Log_20260916_101500.xlsx', connected: false }])
    await nextMacrotask()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:log')
    expect(mockToast).toHaveBeenCalledWith('Shipping log downloaded: Shipping_Log_20260916_101500.xlsx', 'success')
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('warns with part numbers when notes were clipped', async () => {
    const w = await mountOpen(candidateList([
      candidate(1, { part_number: '138537', split_suffix: '-bal' }),
      candidate(2, { part_number: '139270' }),
    ]))
    mockPost.mockResolvedValueOnce(xlsxResponse('Shipping_Log.xlsx', '1'))
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:log'), revokeObjectURL: vi.fn() })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    await selectJobs(w, [1, 2])
    await generateButton(w).trigger('click')
    await flushPromises()

    const warning = mockToast.mock.calls.find(([, kind]) => kind === 'error')
    expect(warning?.[0]).toContain('138537 -bal')
    expect(warning?.[0]).not.toContain('139270')
  })

  it('on a 409 blob body toasts, refetches and keeps only surviving selections', async () => {
    const w = await mountOpen(candidateList([candidate(1), candidate(2), candidate(3)]))
    mockPost.mockRejectedValueOnce(httpError(409, jsonBlob({
      kind: 'ineligible_jobs',
      jobs: [{ job_id: 2, reason: 'shipped' }],
    })))
    mockGet.mockResolvedValueOnce({ data: candidateList([candidate(1), candidate(3), candidate(4)]) })

    await selectJobs(w, [1, 2])
    await generateButton(w).trigger('click')
    await flushPromises()

    expect(mockGet).toHaveBeenCalledTimes(2)
    expect(mockToast).toHaveBeenCalledWith(expect.stringContaining('1 shipped'), 'error', expect.any(Number))
    expect((rowCheckbox(w, 1).element as HTMLInputElement).checked).toBe(true)
    expect((rowCheckbox(w, 3).element as HTMLInputElement).checked).toBe(false)
    expect((rowCheckbox(w, 4).element as HTMLInputElement).checked).toBe(false)
    expect(w.find('[data-job-id="2"]').exists()).toBe(false)
    expect(w.emitted('close')).toBeUndefined()
    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(generateButton(w).text()).toBe('Generate Log (1)')
  })

  it('on a 503 blob body shows the template notice inline and disables Generate', async () => {
    const w = await mountOpen(candidateList([candidate(1)]))
    mockPost.mockRejectedValueOnce(httpError(503, jsonBlob({ kind: 'template_unavailable' })))

    await selectJobs(w, [1])
    await generateButton(w).trigger('click')
    await flushPromises()

    expect(w.find('[data-testid="shipping-log-template-missing"]').exists()).toBe(true)
    expect(generateButton(w).attributes('disabled')).toBeDefined()
    expect(w.emitted('close')).toBeUndefined()
  })

  it('on a non-JSON error blob shows a transport error inline and stays open', async () => {
    const w = await mountOpen(candidateList([candidate(1)]))
    mockPost.mockRejectedValueOnce(httpError(502, new Blob(['<html>Bad Gateway</html>'], { type: 'text/html' })))

    await selectJobs(w, [1])
    await generateButton(w).trigger('click')
    await flushPromises()

    expect(w.get('[data-testid="shipping-log-inline-error"]').text()).toContain('Could not generate the shipping log')
    expect(generateButton(w).attributes('disabled')).toBeUndefined()
    expect(w.emitted('close')).toBeUndefined()
  })

  it('ignores backdrop, Escape and Cancel while generating', async () => {
    const w = await mountOpen(candidateList([candidate(1)]))
    let resolvePost: (value: unknown) => void = () => {}
    mockPost.mockReturnValueOnce(new Promise(resolve => { resolvePost = resolve }))
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:log'), revokeObjectURL: vi.fn() })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    await selectJobs(w, [1])
    await generateButton(w).trigger('click')
    await w.get('[data-testid="shipping-log-backdrop"]').trigger('click')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await w.get('[data-testid="shipping-log-cancel"]').trigger('click')

    expect(generateButton(w).text()).toBe('Generating…')
    expect(w.emitted('close')).toBeUndefined()

    resolvePost(xlsxResponse('Shipping_Log.xlsx'))
    await flushPromises()
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('closes on backdrop, Escape and Cancel when idle', async () => {
    const w = await mountOpen(candidateList([candidate(1)]))

    await w.get('[data-testid="shipping-log-backdrop"]').trigger('click')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await w.get('[data-testid="shipping-log-cancel"]').trigger('click')

    expect(w.emitted('close')).toHaveLength(3)
  })

  it('stops listening for Escape once closed', async () => {
    const w = await mountOpen(candidateList([candidate(1)]))

    await w.setProps({ open: false })
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))

    expect(w.emitted('close')).toBeUndefined()
  })

  describe('Select Today', () => {
    // Fake timers drive `new Date()` inside the shop clock. 18:00Z is 11:00 PDT
    // on 2026-09-17, clear of both midnight boundaries.
    afterEach(() => { vi.useRealTimers() })

    function selectTodayButton(w: VueWrapper) {
      return w.get('[data-testid="shipping-log-select-today"]')
    }

    function checkedIds(w: VueWrapper): number[] {
      return w.findAll('[data-testid="shipping-log-row"]')
        .filter(row => (row.get('input[type="checkbox"]').element as HTMLInputElement).checked)
        .map(row => Number(row.attributes('data-job-id')))
    }

    it('replaces the selection with exactly the jobs due today', async () => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-09-17T18:00:00Z'))
      const w = await mountOpen(candidateList([
        candidate(1, { resolved_ship_date: '2026-09-17' }),
        candidate(2, { resolved_ship_date: '2026-09-18' }),
        candidate(3, { resolved_ship_date: '2026-09-17' }),
        candidate(4, { resolved_ship_date: null, ship_date_text: '???' }),
        candidate(5, { resolved_ship_date: '2026-09-16' }),
      ]))
      await selectJobs(w, [2])

      expect(selectTodayButton(w).text()).toBe('Select Today (2)')
      await selectTodayButton(w).trigger('click')

      expect(checkedIds(w)).toEqual([1, 3])
      expect(generateButton(w).text()).toBe('Generate Log (2)')
    })

    it('reads today in Pacific time, not the viewer local zone', async () => {
      // 05:00Z on the 18th is still 22:00 PDT on the 17th.
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-09-18T05:00:00Z'))
      const w = await mountOpen(candidateList([
        candidate(1, { resolved_ship_date: '2026-09-17' }),
        candidate(2, { resolved_ship_date: '2026-09-18' }),
      ]))

      await selectTodayButton(w).trigger('click')

      expect(checkedIds(w)).toEqual([1])
    })

    it('still opens with nothing selected when jobs are due today', async () => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-09-17T18:00:00Z'))
      const w = await mountOpen(candidateList([candidate(1, { resolved_ship_date: '2026-09-17' })]))

      expect(checkedIds(w)).toEqual([])
      expect(selectTodayButton(w).text()).toBe('Select Today (1)')
    })

    it('is disabled when nothing is due today', async () => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-09-17T18:00:00Z'))
      const w = await mountOpen(candidateList([candidate(1, { resolved_ship_date: '2026-09-18' })]))

      expect(selectTodayButton(w).text()).toBe('Select Today (0)')
      expect(selectTodayButton(w).attributes('disabled')).toBeDefined()
      expect(selectTodayButton(w).attributes('title')).toBe('No jobs are due today')
    })

    it('is disabled while generating', async () => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-09-17T18:00:00Z'))
      const w = await mountOpen(candidateList([candidate(1, { resolved_ship_date: '2026-09-17' })]))
      mockPost.mockReturnValueOnce(new Promise(() => {}))

      await selectTodayButton(w).trigger('click')
      await generateButton(w).trigger('click')

      expect(generateButton(w).text()).toBe('Generating…')
      expect(selectTodayButton(w).attributes('disabled')).toBeDefined()
    })

    it('is not shown when the list is empty', async () => {
      const w = await mountOpen(candidateList([]))

      expect(w.find('[data-testid="shipping-log-empty"]').exists()).toBe(true)
      expect(w.find('[data-testid="shipping-log-select-today"]').exists()).toBe(false)
    })
  })

  it('starts from an empty selection each time it opens', async () => {
    const w = await mountOpen(candidateList([candidate(1), candidate(2)]))
    await selectJobs(w, [1])

    await w.setProps({ open: false })
    mockGet.mockResolvedValueOnce({ data: candidateList([candidate(1), candidate(2)]) })
    await w.setProps({ open: true })
    await flushPromises()

    expect((rowCheckbox(w, 1).element as HTMLInputElement).checked).toBe(false)
  })
})
