import { describe, it, expect } from 'vitest'
import {
  AUDIT_BOM_COLUMN_COUNT,
  describePasteRejection,
  isBlankAuditBomLine,
  parseAuditBomPaste,
} from '../secondOps'

/** Build one 14-column TSV line; 1-based overrides replace the `cN` defaults. */
function tsvLine(overrides: Record<number, string> = {}): string {
  return Array.from(
    { length: AUDIT_BOM_COLUMN_COUNT },
    (_, index) => overrides[index + 1] ?? `c${index + 1}`,
  ).join('\t')
}

const HEADER_LINE = [
  'Find#', 'Part Number', 'Per Board', 'MSL', 'Pkg', 'Vendor', 'Ref_Des',
  'Alt', 'Description', 'Mount', 'Qty Need', 'Qty OH', 'Bin', 'Notes',
].join('\t')

describe('parseAuditBomPaste', () => {
  it('maps the eight retained fields from ordinals 1/2/3/7/9/10/11/12', () => {
    const result = parseAuditBomPaste(tsvLine(), 500)

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.lines[0]).toEqual({
      find_number: 'c1',
      component_part_number: 'c2',
      per_board_count: 'c3',
      ref_des: 'c7',
      description: 'c9',
      mount_type: 'c10',
      quantity_needed: 'c11',
      quantity_on_hand: 'c12',
    })
  })

  it('rejects a 13-column row rather than shifting the mapping', () => {
    // A hidden source column is exactly this shape. Ref_Des would land in
    // Description with entirely plausible-looking output.
    const shifted = Array.from({ length: 13 }, (_, i) => `c${i + 1}`).join('\t')

    const result = parseAuditBomPaste(shifted, 500)

    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.rejection).toEqual({
      kind: 'column-count-mismatch',
      lineNumber: 1,
      observed: 13,
      expected: 14,
    })
  })

  it('rejects a 15-column row', () => {
    const wide = Array.from({ length: 15 }, (_, i) => `c${i + 1}`).join('\t')

    const result = parseAuditBomPaste(wide, 500)

    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.rejection.kind).toBe('column-count-mismatch')
  })

  it('names the offending line by its position in the original paste', () => {
    const text = [tsvLine(), tsvLine(), 'short'].join('\n')

    const result = parseAuditBomPaste(text, 500)

    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.rejection).toMatchObject({ lineNumber: 3 })
  })

  it('drops a leading Find# header echo', () => {
    const result = parseAuditBomPaste([HEADER_LINE, tsvLine()].join('\n'), 500)

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.lines).toHaveLength(1)
    expect(result.lines[0].find_number).toBe('c1')
  })

  it('matches the header echo case-insensitively and after trimming', () => {
    const result = parseAuditBomPaste(
      [HEADER_LINE.replace('Find#', '  fInD#  '), tsvLine()].join('\n'),
      500,
    )

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.lines).toHaveLength(1)
  })

  it('rejects a field beginning with a quote and inserts nothing', () => {
    const quoted = tsvLine({ 7: '"C1, C2\nC3"' })

    const result = parseAuditBomPaste(quoted, 500)

    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.rejection).toEqual({ kind: 'quoted-field-unsupported', lineNumber: 1 })
  })

  it('parses \\r\\n and \\n identically', () => {
    const crlf = parseAuditBomPaste([tsvLine(), tsvLine()].join('\r\n'), 500)
    const lf = parseAuditBomPaste([tsvLine(), tsvLine()].join('\n'), 500)

    expect(crlf).toEqual(lf)
  })

  it('drops trailing blank lines', () => {
    const result = parseAuditBomPaste([tsvLine(), '', '', ''].join('\n'), 500)

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.lines).toHaveLength(1)
  })

  it('drops leading blank lines rather than reporting a mismatch on line 1', () => {
    // Selecting a range with empty rows above the header is ordinary Excel habit.
    const result = parseAuditBomPaste(['', '', tsvLine()].join('\n'), 500)

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.lines).toHaveLength(1)
  })

  it('drops a blank line between two data lines and preserves order', () => {
    const text = [tsvLine({ 1: 'first' }), '', tsvLine({ 1: 'second' })].join('\n')

    const result = parseAuditBomPaste(text, 500)

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.lines.map((line) => line.find_number)).toEqual(['first', 'second'])
  })

  it('treats a row of 13 tabs as blank, not as a 14-field row of empties', () => {
    const tabsOnly = '\t'.repeat(13)

    const result = parseAuditBomPaste([tsvLine(), tabsOnly].join('\n'), 500)

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.lines).toHaveLength(1)
  })

  it('drops blank lines above a Find# header line', () => {
    // Asserts the header check runs AFTER blank-dropping, not before.
    const result = parseAuditBomPaste(['', '', HEADER_LINE, tsvLine()].join('\n'), 500)

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.lines).toHaveLength(1)
    expect(result.lines[0].find_number).toBe('c1')
  })

  it('rejects a paste over the row cap', () => {
    const text = Array.from({ length: 4 }, () => tsvLine()).join('\n')

    const result = parseAuditBomPaste(text, 3)

    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.rejection).toEqual({ kind: 'row-cap-exceeded', observed: 4, cap: 3 })
  })

  it('rejects a whitespace-only paste as EmptyPaste', () => {
    const result = parseAuditBomPaste('   \n  \n', 500)

    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.rejection).toEqual({ kind: 'empty-paste' })
  })

  it('carries field whitespace through verbatim', () => {
    const result = parseAuditBomPaste(tsvLine({ 9: '  padded  ' }), 500)

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.lines[0].description).toBe('  padded  ')
  })

  it('maps an empty cell to null rather than an empty string', () => {
    const result = parseAuditBomPaste(tsvLine({ 12: '' }), 500)

    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.lines[0].quantity_on_hand).toBeNull()
  })
})

describe('describePasteRejection', () => {
  it('names the line for a column-count mismatch', () => {
    const message = describePasteRejection({
      kind: 'column-count-mismatch',
      lineNumber: 7,
      observed: 13,
      expected: 14,
    })
    expect(message).toContain('Line 7')
    expect(message).toContain('13')
  })

  it('names the line for a quoted field', () => {
    expect(
      describePasteRejection({ kind: 'quoted-field-unsupported', lineNumber: 4 }),
    ).toContain('Line 4')
  })

  it('names both numbers for a row-cap breach', () => {
    const message = describePasteRejection({
      kind: 'row-cap-exceeded',
      observed: 900,
      cap: 500,
    })
    expect(message).toContain('900')
    expect(message).toContain('500')
  })
})

describe('isBlankAuditBomLine', () => {
  it('is true for an all-null bag', () => {
    expect(
      isBlankAuditBomLine({
        find_number: null,
        component_part_number: null,
        per_board_count: null,
        ref_des: null,
        description: null,
        mount_type: null,
        quantity_needed: null,
        quantity_on_hand: null,
      }),
    ).toBe(true)
  })

  it('is true when every field is whitespace', () => {
    expect(isBlankAuditBomLine({ description: '   ' })).toBe(true)
  })

  it('is false when any field carries content', () => {
    expect(isBlankAuditBomLine({ description: 'CAP' })).toBe(false)
  })
})
