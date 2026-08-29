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

  describe('formatShortDate', () => {
    it('format_short_date(null) / \'\'', () => {
      const { formatShortDate } = useJobFormatters()
      expect(formatShortDate(null)).toBe('—')
      expect(formatShortDate('')).toBe('—')
    })

    it('Year boundary via an injected clock', () => {
      const clock2026 = { currentYear: () => 2026 }
      const clock2027 = { currentYear: () => 2027 }
      
      const { formatShortDate: f26 } = useJobFormatters(clock2026)
      const { formatShortDate: f27 } = useJobFormatters(clock2027)

      expect(f26('2026-01-05')).toBe('01-05')
      expect(f27('2026-01-05')).toBe('01-05-26')
    })

    it('currentYear() is re-read per call', () => {
      let year = 2026
      const clock = { currentYear: () => year }
      const { formatShortDate } = useJobFormatters(clock)
      
      expect(formatShortDate('2026-01-05')).toBe('01-05')
      year = 2027
      expect(formatShortDate('2026-01-05')).toBe('01-05-26')
    })
  })
})
