import { describe, it, expect } from 'vitest'
import type { ReviewGroup } from '@/api/review'
import { intraFileDuplicateKey } from '../keys'

function group(overrides: Partial<ReviewGroup>): ReviewGroup {
  return {
    parsed_part_number: '123456',
    rows: [],
    similar_assemblies: [],
    review_status: 'verified',
    identity: {
      part_number: '123456',
      build_type: 'new',
      split_suffix: '-2par',
      repeat_reference: null,
      build_qualifier: null,
    },
    ...overrides,
  }
}

describe('intraFileDuplicateKey', () => {
  it('differs for staged and edit_induced groups with equal identity (Patch 06 §5.5)', () => {
    expect(intraFileDuplicateKey(group({ origin: 'staged' })))
      .not.toBe(intraFileDuplicateKey(group({ origin: 'edit_induced' })))
  })

  it('is stable for the same origin and identity', () => {
    expect(intraFileDuplicateKey(group({ origin: 'staged' })))
      .toBe(intraFileDuplicateKey(group({ origin: 'staged' })))
  })

  it('differs by any identity field', () => {
    const base = group({ origin: 'staged' })
    const otherBuild = group({ origin: 'staged', identity: { ...base.identity!, build_type: 'ronc' } })
    expect(intraFileDuplicateKey(base)).not.toBe(intraFileDuplicateKey(otherBuild))
  })
})
