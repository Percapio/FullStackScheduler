import { describe, it, expect, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import SlideOverPanel from '../SlideOverPanel.vue'

let wrapper: VueWrapper | null = null

function mountPanel(
  props: { open: boolean; ariaLabel: string; width?: 'md' | 'xl' | 'full' },
  slots: Record<string, string> = {},
) {
  wrapper = mount(SlideOverPanel, { props, slots, attachTo: document.body })
  return wrapper
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

function bodyHtml() {
  return document.body.innerHTML
}

describe('SlideOverPanel', () => {
  it('renders nothing when open is false', () => {
    mountPanel({ open: false, ariaLabel: 'x' })
    expect(bodyHtml()).not.toContain('drawer-overlay')
  })

  it('mounts with role=dialog and aria-label when open is true', () => {
    mountPanel({ open: true, ariaLabel: 'Test panel' })
    const dialog = document.body.querySelector('[role="dialog"]') as HTMLElement
    expect(dialog).not.toBeNull()
    expect(dialog.getAttribute('aria-label')).toBe('Test panel')
    expect(document.body.querySelector('[data-testid="drawer-overlay"]')).not.toBeNull()
  })

  it('emits close on backdrop click', async () => {
    const w = mountPanel({ open: true, ariaLabel: 'x' })
    const backdrop = document.body.querySelector('[data-testid="drawer-backdrop"]') as HTMLElement
    backdrop.click()
    await w.vm.$nextTick()
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('emits close on Escape keydown when open', () => {
    const w = mountPanel({ open: true, ariaLabel: 'x' })
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('does NOT emit close on Escape when open is false', () => {
    const w = mountPanel({ open: false, ariaLabel: 'x' })
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(w.emitted('close')).toBeUndefined()
  })

  it('renders header slot inside sticky region and default slot inside scrollable region', () => {
    mountPanel(
      { open: true, ariaLabel: 'x' },
      { header: '<h2 data-testid="hdr">HEADER</h2>', default: '<p data-testid="bdy">BODY</p>' },
    )
    const stickyHeader = document.body.querySelector('header.sticky') as HTMLElement
    expect(stickyHeader).not.toBeNull()
    expect(stickyHeader.querySelector('[data-testid="hdr"]')?.textContent).toBe('HEADER')

    const scroller = document.body.querySelector('.overflow-y-auto') as HTMLElement
    expect(scroller).not.toBeNull()
    expect(scroller.querySelector('[data-testid="bdy"]')?.textContent).toBe('BODY')
  })

  describe('width prop', () => {
    it.each<['md' | 'xl' | 'full', string]>([
      ['md', 'max-w-md'],
      ['xl', 'max-w-2xl'],
      ['full', 'w-full'],
    ])('applies the expected class for width=%s', (width, expectedClass) => {
      mountPanel({ open: true, ariaLabel: 'x', width })
      const aside = document.body.querySelector('[role="dialog"]') as HTMLElement
      expect(aside.className).toContain(expectedClass)
    })
  })
})
