import { describe, it, expect } from 'vitest'
import { useJobFormatters } from '../useJobFormatters'
import type { JobReadExpanded } from '@/api/history'

function job(overrides: Partial<JobReadExpanded> = {}): JobReadExpanded {
  return {
    split_suffix: null,
    repeat_reference: null,
    build_type: 'new',
    ...overrides,
  } as unknown as JobReadExpanded
}

describe('useJobFormatters', () => {
  describe('identitySuffix', () => {
    const { identitySuffix } = useJobFormatters()
    
    it('with split_suffix only', () => {
      expect(identitySuffix(job({ split_suffix: '-1par' }))).toBe(' -1par')
    })
    
    it('with repeat_reference only', () => {
      expect(identitySuffix(job({ repeat_reference: '12345' }))).toBe('')
    })
    
    it('with both', () => {
      expect(identitySuffix(job({ split_suffix: '-1par', repeat_reference: '12345' }))).toBe(' -1par')
    })
  })

  describe('jobLabel', () => {
    const { jobLabel } = useJobFormatters()
    
    it('with both', () => {
      expect(
        jobLabel('128764', job({
          split_suffix: '-1par',
          repeat_reference: '12345',
          build_type: 'ronc',
        })),
      ).toBe('128764 -1par · RONC 12345 RONC')
    })
  })

  describe('formatDate', () => {
    const { formatDate } = useJobFormatters()

    it('renders a datetime as YYYY/MM/DD', () => {
      expect(formatDate('2026-01-05T14:30:00Z')).toBe('2026/01/05')
    })

    it('renders a bare date as YYYY/MM/DD', () => {
      expect(formatDate('2026-01-05')).toBe('2026/01/05')
    })

    it('renders an absent date as the em-dash placeholder', () => {
      expect(formatDate(null)).toBe('—')
      expect(formatDate('')).toBe('—')
    })
  })

  describe('isShippingToday', () => {
    const clock = { currentYear: () => 2026, currentDate: () => '2026-08-30' }
    const { isShippingToday } = useJobFormatters(clock)

    it('matches a bare date equal to the shop clock', () => {
      expect(isShippingToday('2026-08-30')).toBe(true)
    })

    it('matches a datetime whose date portion is today', () => {
      expect(isShippingToday('2026-08-30T23:59:59Z')).toBe(true)
    })

    it('rejects adjacent days', () => {
      expect(isShippingToday('2026-08-29')).toBe(false)
      expect(isShippingToday('2026-08-31')).toBe(false)
    })

    it('rejects an absent ship date rather than treating it as today', () => {
      expect(isShippingToday(null)).toBe(false)
      expect(isShippingToday(undefined)).toBe(false)
      expect(isShippingToday('')).toBe(false)
    })

    it('re-reads the clock per call, so the grid survives a midnight rollover', () => {
      let today = '2026-08-30'
      const { isShippingToday: check } = useJobFormatters({
        currentYear: () => 2026,
        currentDate: () => today,
      })
      expect(check('2026-08-30')).toBe(true)
      today = '2026-08-31'
      expect(check('2026-08-30')).toBe(false)
    })
  })

  describe('formatShortDate', () => {
    it('format_short_date(null) / \'\'', () => {
      const { formatShortDate } = useJobFormatters()
      expect(formatShortDate(null)).toBe('—')
      expect(formatShortDate('')).toBe('—')
    })

    it('Year boundary via an injected clock', () => {
      const clock2026 = { currentYear: () => 2026, currentDate: () => '2026-06-01' }
      const clock2027 = { currentYear: () => 2027, currentDate: () => '2027-06-01' }
      
      const { formatShortDate: f26 } = useJobFormatters(clock2026)
      const { formatShortDate: f27 } = useJobFormatters(clock2027)

      expect(f26('2026-01-05')).toBe('01/05')
      expect(f27('2026-01-05')).toBe('01/05/26')
    })

    it('currentYear() is re-read per call', () => {
      let year = 2026
      const clock = { currentYear: () => year, currentDate: () => `${year}-06-01` }
      const { formatShortDate } = useJobFormatters(clock)
      
      expect(formatShortDate('2026-01-05')).toBe('01/05')
      year = 2027
      expect(formatShortDate('2026-01-05')).toBe('01/05/26')
    })
  })
})
