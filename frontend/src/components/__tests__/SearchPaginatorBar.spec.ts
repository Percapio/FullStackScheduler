import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SearchPaginatorBar from '../SearchPaginatorBar.vue'

function defaultProps(overrides: Record<string, unknown> = {}) {
  return {
    searchQuery: '',
    pageStart: 1,
    pageEnd: 50,
    total: 200,
    hasPrev: false,
    hasNext: true,
    loading: false,
    ...overrides,
  }
}

describe('SearchPaginatorBar', () => {
  it('renders the search input with the given placeholder', () => {
    const w = mount(SearchPaginatorBar, {
      props: { ...defaultProps(), placeholder: 'Search row #, error, batch…' },
    })
    const input = w.find('input[aria-label="Search"]')
    expect(input.exists()).toBe(true)
    expect(input.attributes('placeholder')).toBe('Search row #, error, batch…')
  })

  it('uses default placeholder when none supplied', () => {
    const w = mount(SearchPaginatorBar, { props: defaultProps() })
    expect(w.find('input').attributes('placeholder')).toBe('Search…')
  })

  it('emits update:searchQuery with raw input value on input event', async () => {
    const w = mount(SearchPaginatorBar, { props: defaultProps() })
    const input = w.find('input')
    await input.setValue('needle')
    expect(w.emitted('update:searchQuery')).toBeTruthy()
    expect(w.emitted('update:searchQuery')![0]).toEqual(['needle'])
  })

  it('shows loading spinner when loading=true', () => {
    const w = mount(SearchPaginatorBar, { props: defaultProps({ loading: true }) })
    expect(w.text()).toContain('…')
  })

  it('hides loading spinner when loading=false', () => {
    const w = mount(SearchPaginatorBar, { props: defaultProps({ loading: false }) })
    // The spinner span only renders when loading=true
    expect(w.find('span').text()).not.toBe('…')
  })

  it('renders paginator readout with pageStart, pageEnd, total', () => {
    const w = mount(SearchPaginatorBar, {
      props: defaultProps({ pageStart: 1, pageEnd: 50, total: 200 }),
    })
    expect(w.get('[data-testid="paginator-readout"]').text()).toBe('Showing 1–50 of 200')
  })

  it('Prev button is disabled when hasPrev=false', () => {
    const w = mount(SearchPaginatorBar, { props: defaultProps({ hasPrev: false }) })
    const prevBtn = w.find('button[aria-label="Previous page"]')
    expect(prevBtn.attributes('disabled')).toBeDefined()
  })

  it('Next button is disabled when hasNext=false', () => {
    const w = mount(SearchPaginatorBar, { props: defaultProps({ hasNext: false }) })
    const nextBtn = w.find('button[aria-label="Next page"]')
    expect(nextBtn.attributes('disabled')).toBeDefined()
  })

  it('Prev button is disabled when loading=true even if hasPrev=true', () => {
    const w = mount(SearchPaginatorBar, {
      props: defaultProps({ hasPrev: true, loading: true }),
    })
    const prevBtn = w.find('button[aria-label="Previous page"]')
    expect(prevBtn.attributes('disabled')).toBeDefined()
  })

  it('Next button is disabled when loading=true even if hasNext=true', () => {
    const w = mount(SearchPaginatorBar, {
      props: defaultProps({ hasNext: true, loading: true }),
    })
    const nextBtn = w.find('button[aria-label="Next page"]')
    expect(nextBtn.attributes('disabled')).toBeDefined()
  })

  it('clicking Prev emits prev event', async () => {
    const w = mount(SearchPaginatorBar, { props: defaultProps({ hasPrev: true }) })
    await w.find('button[aria-label="Previous page"]').trigger('click')
    expect(w.emitted('prev')).toBeTruthy()
  })

  it('clicking Next emits next event', async () => {
    const w = mount(SearchPaginatorBar, { props: defaultProps({ hasNext: true }) })
    await w.find('button[aria-label="Next page"]').trigger('click')
    expect(w.emitted('next')).toBeTruthy()
  })

  it('scroll buttons are hidden when showScrollControls is false (default)', () => {
    const w = mount(SearchPaginatorBar, { props: defaultProps() })
    expect(w.find('button[aria-label="Scroll to top"]').exists()).toBe(false)
    expect(w.find('button[aria-label="Scroll to bottom"]').exists()).toBe(false)
  })

  it('scroll buttons are shown when showScrollControls=true', () => {
    const w = mount(SearchPaginatorBar, {
      props: defaultProps({ showScrollControls: true }),
    })
    expect(w.find('button[aria-label="Scroll to top"]').exists()).toBe(true)
    expect(w.find('button[aria-label="Scroll to bottom"]').exists()).toBe(true)
  })

  it('clicking scroll-top emits scroll-top event', async () => {
    const w = mount(SearchPaginatorBar, {
      props: defaultProps({ showScrollControls: true }),
    })
    await w.find('button[aria-label="Scroll to top"]').trigger('click')
    expect(w.emitted('scroll-top')).toBeTruthy()
  })

  it('clicking scroll-bottom emits scroll-bottom event', async () => {
    const w = mount(SearchPaginatorBar, {
      props: defaultProps({ showScrollControls: true }),
    })
    await w.find('button[aria-label="Scroll to bottom"]').trigger('click')
    expect(w.emitted('scroll-bottom')).toBeTruthy()
  })
})
