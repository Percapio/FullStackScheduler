import { describe, it, expect } from 'vitest'
import { useJobFormatters } from '../useJobFormatters'

const { carrierBadge, formatDate } = useJobFormatters()

describe('useJobFormatters.carrierBadge', () => {
  it('returns null for null, undefined, empty, or whitespace-only input', () => {
    expect(carrierBadge(null)).toBeNull()
    expect(carrierBadge(undefined)).toBeNull()
    expect(carrierBadge('')).toBeNull()
    expect(carrierBadge('   ')).toBeNull()
  })

  it('matches FedEx variants (case / whitespace / dash tolerant)', () => {
    const variants = ['FedEx', 'FEDEX', 'fedex', 'Fed Ex', 'fedex-ground', 'FedEx_Express']
    for (const v of variants) {
      const badge = carrierBadge(v)
      expect(badge, v).not.toBeNull()
      expect(badge!.class).toContain('bg-purple-100')
      expect(badge!.text).toBe(v)
    }
  })

  it('matches UPS variants', () => {
    const variants = ['UPS', 'ups', 'UPS Ground', 'ups-next-day']
    for (const v of variants) {
      const badge = carrierBadge(v)
      expect(badge, v).not.toBeNull()
      expect(badge!.class).toContain('bg-amber-100')
      expect(badge!.text).toBe(v)
    }
  })

  it('returns neutral slate class for unknown carriers (USPS, DHL, LTL, Will Call)', () => {
    const variants = ['USPS', 'DHL', 'LTL', 'Will Call', 'Customer Pickup', 'Freight']
    for (const v of variants) {
      const badge = carrierBadge(v)
      expect(badge, v).not.toBeNull()
      expect(badge!.class).toContain('bg-slate-100')
      expect(badge!.text).toBe(v)
    }
  })

  it('collapses embedded newlines in carrier display text', () => {
    expect(carrierBadge('FedEx\n')!.text).toBe('FedEx')
    expect(carrierBadge('FedEx\r\nGround')!.text).toBe('FedEx Ground')
  })

  it('collapses multi-space and tab runs', () => {
    expect(carrierBadge('UPS   Next\tDay')!.text).toBe('UPS Next Day')
  })
})

describe('useJobFormatters.formatDate', () => {
  it('returns "\u2014" for null / undefined / empty', () => {
    expect(formatDate(null)).toBe('\u2014')
    expect(formatDate(undefined)).toBe('\u2014')
    expect(formatDate('')).toBe('\u2014')
  })

  it('preserves the calendar date irrespective of TZ (regression: phase 08 patch 02)', () => {
    expect(formatDate('2025-09-15')).toBe('2025-09-15')
    expect(formatDate('2025-01-01')).toBe('2025-01-01')
    expect(formatDate('2025-12-31')).toBe('2025-12-31')
  })

  it('tolerates a datetime suffix by truncating to the date portion', () => {
    expect(formatDate('2025-09-15T00:00:00Z')).toBe('2025-09-15')
  })
})
