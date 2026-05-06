import { describe, it, expect } from 'vitest'
import { useJobFormatters } from '../useJobFormatters'
import type { JobReadExpanded } from '@/api/history'

const { jobLabel } = useJobFormatters()

/** Minimal stub — only the fields jobLabel reads. */
function job(overrides: Partial<JobReadExpanded> = {}): JobReadExpanded {
  return {
    split_suffix: null,
    repeat_reference: null,
    build_type: 'new',
    ...overrides,
  } as unknown as JobReadExpanded
}

describe('useJobFormatters.jobLabel', () => {
  it('snapshot: bare new job → part number only', () => {
    expect(jobLabel('128764', job())).toBe('128764')
  })

  it('snapshot: split job with no repeat_reference and new build_type → part + suffix', () => {
    expect(jobLabel('128764', job({ split_suffix: '-1par' }))).toBe('128764 -1par')
  })

  it('snapshot: split job with repeat_reference and ronc build_type → full label', () => {
    expect(
      jobLabel('128764', job({
        split_suffix: '-1par',
        repeat_reference: '12345',
        build_type: 'ronc',
      })),
    ).toBe('128764 -1par · RONC 12345 RONC')
  })

  it('appends non-new build label when no suffix', () => {
    expect(jobLabel('ABC', job({ build_type: 'rework' }))).toBe('ABC REWORK')
  })

  it('omits build label for build_type=new', () => {
    expect(jobLabel('ABC', job({ build_type: 'new' }))).toBe('ABC')
  })

  it('omits build label for null build_type', () => {
    expect(jobLabel('ABC', job({ build_type: null }))).toBe('ABC')
  })
})
