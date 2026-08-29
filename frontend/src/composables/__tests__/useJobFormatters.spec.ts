import { describe, it, expect } from 'vitest'
import { useJobFormatters } from '../useJobFormatters'

const { formatDate } = useJobFormatters()

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
