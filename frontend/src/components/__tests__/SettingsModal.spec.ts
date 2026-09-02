import { mount } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import SettingsModal from '../SettingsModal.vue'
import * as settingsApi from '@/api/settings'

vi.mock('@/api/settings', () => ({
  getPhotosDir: vi.fn(),
  browseDirectory: vi.fn(),
  savePhotosDir: vi.fn(),
}))

const mockPushToast = vi.fn()
vi.mock('@/composables/useToast', () => ({
  useToast: () => ({ show: mockPushToast })
}))

describe('SettingsModal.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('editable: false renders read-only without path or tree', async () => {
    vi.mocked(settingsApi.getPhotosDir).mockResolvedValueOnce({
      kind: 'ok',
      path: null,
      source: 'unset',
      configured: true,
      editable: false
    })

    const wrapper = mount(SettingsModal, {
      props: { open: true },
      global: { stubs: { Teleport: true } }
    })

    await flushPromises()

    expect(wrapper.text()).toContain('configured on the production computer')
    expect(wrapper.text()).toContain('Currently configured: Yes')
    
    // No path field, no browser
    expect(wrapper.find('input[type="text"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Drive Roots')
  })

  it('editable: true renders browser and field', async () => {
    vi.mocked(settingsApi.getPhotosDir).mockResolvedValueOnce({
      kind: 'ok',
      path: 'C:\\test',
      source: 'env',
      configured: true,
      editable: true
    })
    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'ok',
      parent: null,
      entries: [],
      truncated: false
    })

    const wrapper = mount(SettingsModal, {
      props: { open: true },
      global: { stubs: { Teleport: true } }
    })

    await flushPromises()

    const inputs = wrapper.findAll('input[type="text"]')
    expect(inputs.length).toBe(1)
    expect((inputs[0].element as HTMLInputElement).value).toBe('C:\\test')
    
    expect(wrapper.text()).toContain('C:\\test')
    expect(wrapper.text()).toContain('Loaded from .env file')
  })

  it('failed initial fetch renders error and retry', async () => {
    vi.mocked(settingsApi.getPhotosDir).mockResolvedValueOnce({
      kind: 'network',
      message: 'Network offline'
    })

    const wrapper = mount(SettingsModal, {
      props: { open: true },
      global: { stubs: { Teleport: true } }
    })

    await flushPromises()

    expect(wrapper.text()).toContain('Network offline')
    expect(wrapper.text()).toContain('Retry')
    expect(wrapper.find('input').exists()).toBe(false)
  })

  it('configured: false with editable: true renders empty field (normalised to \'\')', async () => {
    vi.mocked(settingsApi.getPhotosDir).mockResolvedValueOnce({
      kind: 'ok',
      path: null,
      source: 'unset',
      configured: false,
      editable: true
    })
    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'ok', parent: null, entries: [], truncated: false
    })

    const wrapper = mount(SettingsModal, {
      props: { open: true },
      global: { stubs: { Teleport: true } }
    })

    await flushPromises()

    const input = wrapper.find('input[type="text"]')
    expect((input.element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).not.toContain('null')
  })

  it('navigation click issues browse and updates breadcrumb', async () => {
    vi.mocked(settingsApi.getPhotosDir).mockResolvedValueOnce({
      kind: 'ok', path: null, source: 'unset', configured: false, editable: true
    })
    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'ok', parent: null, entries: [{ name: 'C:', path: 'C:\\' }], truncated: false
    })

    const wrapper = mount(SettingsModal, {
      props: { open: true },
      global: { stubs: { Teleport: true } }
    })

    await flushPromises()
    expect(settingsApi.browseDirectory).toHaveBeenCalledWith('', '')

    // Click C:
    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'ok', parent: '', entries: [], truncated: false
    })
    
    const entryButton = wrapper.findAll('button').find(b => b.text().includes('C:'))
    await entryButton?.trigger('click')
    await flushPromises()

    expect(settingsApi.browseDirectory).toHaveBeenCalledWith('C:\\', '')
    // Field should reflect new path
    expect((wrapper.find('input[type="text"]').element as HTMLInputElement).value).toBe('C:\\')
  })

  it('up click uses parent path', async () => {
    vi.mocked(settingsApi.getPhotosDir).mockResolvedValueOnce({
      kind: 'ok', path: 'C:\\test', source: 'unset', configured: false, editable: true
    })
    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'ok', parent: 'C:\\', entries: [], truncated: false
    })

    const wrapper = mount(SettingsModal, {
      props: { open: true },
      global: { stubs: { Teleport: true } }
    })

    await flushPromises()
    
    const upBtn = wrapper.findAll('button').find(b => b.text().includes('Up'))
    await upBtn?.trigger('click')
    await flushPromises()
    
    expect(settingsApi.browseDirectory).toHaveBeenCalledWith('C:\\', '')
  })

  it('truncated renders notice and reveals filter box', async () => {
    vi.mocked(settingsApi.getPhotosDir).mockResolvedValueOnce({
      kind: 'ok', path: null, source: 'unset', configured: false, editable: true
    })
    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'ok', parent: null, entries: [], truncated: true
    })

    const wrapper = mount(SettingsModal, {
      props: { open: true },
      global: { stubs: { Teleport: true } }
    })

    await flushPromises()

    expect(wrapper.text()).toContain('Too many folders')
    const filterBox = wrapper.find('input[placeholder="Filter by prefix..."]')
    expect(filterBox.exists()).toBe(true)

    // Type in filter
    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'ok', parent: null, entries: [], truncated: false
    })
    await filterBox.setValue('do')
    await flushPromises()

    expect(settingsApi.browseDirectory).toHaveBeenCalledWith('', 'do')
  })

  it('busy outcome renders retry line and leaves listing', async () => {
    vi.mocked(settingsApi.getPhotosDir).mockResolvedValueOnce({
      kind: 'ok', path: null, source: 'unset', configured: false, editable: true
    })
    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'ok', parent: null, entries: [{ name: 'C:', path: 'C:\\' }], truncated: false
    })

    const wrapper = mount(SettingsModal, {
      props: { open: true },
      global: { stubs: { Teleport: true } }
    })
    await flushPromises()

    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'busy'
    })
    
    const entryButton = wrapper.findAll('button').find(b => b.text().includes('C:'))
    await entryButton?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Still reading the previous folder')
    // Old entries still there
    expect(wrapper.text()).toContain('C:')
  })

  it('save posts text field value', async () => {
    vi.mocked(settingsApi.getPhotosDir).mockResolvedValueOnce({
      kind: 'ok', path: null, source: 'unset', configured: false, editable: true
    })
    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'ok', parent: null, entries: [], truncated: false
    })

    const wrapper = mount(SettingsModal, {
      props: { open: true },
      global: { stubs: { Teleport: true } }
    })
    await flushPromises()

    const input = wrapper.find('input[type="text"]')
    await input.setValue('C:\\typed')

    vi.mocked(settingsApi.savePhotosDir).mockResolvedValueOnce({
      kind: 'ok', path: 'C:\\typed', source: 'runtime', configured: true, editable: true, folder_count: 10
    })

    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('Save'))
    await saveBtn?.trigger('click')
    await flushPromises()

    expect(settingsApi.savePhotosDir).toHaveBeenCalledWith('C:\\typed')
    expect(mockPushToast).toHaveBeenCalledWith('Saved. 10 photo folders found.', 'success')
  })

  it('invalid save renders inline and no toast', async () => {
    vi.mocked(settingsApi.getPhotosDir).mockResolvedValueOnce({
      kind: 'ok', path: null, source: 'unset', configured: false, editable: true
    })
    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'ok', parent: null, entries: [], truncated: false
    })

    const wrapper = mount(SettingsModal, {
      props: { open: true },
      global: { stubs: { Teleport: true } }
    })
    await flushPromises()

    const input = wrapper.find('input[type="text"]')
    await input.setValue('nope')

    vi.mocked(settingsApi.savePhotosDir).mockResolvedValueOnce({
      kind: 'invalid', reason: 'not_absolute'
    })

    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('Save'))
    await saveBtn?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Path must be absolute.')
    expect(mockPushToast).not.toHaveBeenCalled()
  })

  it('storage save error renders toast', async () => {
    vi.mocked(settingsApi.getPhotosDir).mockResolvedValueOnce({
      kind: 'ok', path: null, source: 'unset', configured: false, editable: true
    })
    vi.mocked(settingsApi.browseDirectory).mockResolvedValueOnce({
      kind: 'ok', parent: null, entries: [], truncated: false
    })

    const wrapper = mount(SettingsModal, {
      props: { open: true },
      global: { stubs: { Teleport: true } }
    })
    await flushPromises()

    const input = wrapper.find('input[type="text"]')
    await input.setValue('C:\\dir')

    vi.mocked(settingsApi.savePhotosDir).mockResolvedValueOnce({
      kind: 'storage'
    })

    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('Save'))
    await saveBtn?.trigger('click')
    await flushPromises()

    expect(mockPushToast).toHaveBeenCalledWith('Failed to save to configuration file.', 'error')
  })
})

// flushPromises helper for Vue test utils
import { flushPromises } from '@vue/test-utils'
