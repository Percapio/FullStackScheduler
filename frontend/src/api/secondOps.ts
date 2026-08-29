import { apiClient, isApiError } from './client'
import type { components } from './types.gen'

/**
 * 2nd OPS client layer (Phase 22 Part 2).
 *
 * The client parses the Audit BOM paste and maps the columns; the server
 * re-validates count, widths and caps independently. Nothing here is a
 * substitute for the server-side guard — it exists so the operator gets a line
 * number instead of a bare 422 after a round-trip.
 */

/**
 * Wire types are DERIVED from the generated schema, never hand-restated. A
 * hand-written twin drifts the moment a column width or a field name changes on
 * the server, and the drift is invisible until runtime.
 */

/** The eight retained Audit BOM fields, and nothing else. */
export type AuditBomFields = components['schemas']['AuditBomFields']

/**
 * A parsed-but-unsaved line. Structurally AuditBomFields; it carries no id and
 * no line_order because neither exists until the PUT lands.
 */
export type ParsedAuditLine = AuditBomFields

/** A persisted line: the eight fields plus its identity and position. */
export type SecondOpsLine = components['schemas']['SecondOpsLine']

/** Server-owned input bounds, echoed on the record read so no constant drifts. */
export type SecondOpsLimits = components['schemas']['SecondOpsLimits']

export type SecondOpsState = SecondOpsSummary['state']

/** `preview` carries whole lines — the item modal opens with no second fetch. */
export type SecondOpsSummary = components['schemas']['SecondOpsSummary']

export type SecondOpsRecord = components['schemas']['SecondOpsRecord']

export type SecondOpsWritePayload = components['schemas']['SecondOpsWriteRequest']

/**
 * Three arms, not a nullable record. "Loading" and "never audited" both render
 * an empty grid but need opposite affordances — a spinner versus a fully
 * interactive one — and a nullable prop cannot tell them apart.
 *
 * `loaded` always carries a record: GET returns 200 with state "unaudited" and
 * an empty line set for a job never audited, so "never audited" lives INSIDE
 * the record and never appears as an absent one.
 */
export type SecondOpsFetch =
  | { status: 'loading' }
  | { status: 'loaded'; record: SecondOpsRecord }
  | { status: 'failed'; message: string }

/**
 * The write needs its own result type; SecondOpsFetch covers only the read.
 *
 * The three failure arms are not interchangeable: `rejected` means fix the input
 * and retry, `unreachable` means retry unchanged, and `stale` means the job left
 * the writable set and retrying can never succeed. A single "failed" banner
 * would invite an operator to retry the one case guaranteed to fail forever.
 */
export type SecondOpsSaveResult =
  | { kind: 'saved'; record: SecondOpsRecord }
  | { kind: 'rejected'; message: string }
  | { kind: 'stale'; message: string }
  | { kind: 'unreachable'; message: string }

export type PasteRejection =
  | { kind: 'empty-paste' }
  | { kind: 'column-count-mismatch'; lineNumber: number; observed: number; expected: number }
  | { kind: 'quoted-field-unsupported'; lineNumber: number }
  | { kind: 'row-cap-exceeded'; observed: number; cap: number }

export type PasteResult =
  | { ok: true; lines: ParsedAuditLine[] }
  | { ok: false; rejection: PasteRejection }

/**
 * Excel copies only VISIBLE columns. One hidden `MSL level` shifts every ordinal
 * left by one and Ref_Des lands in Description with entirely plausible-looking
 * output — the same silent mis-map class as the KIT REL bug. The width
 * assertion is what converts that into a visible rejection, so it is not
 * optional. It does not catch a same-width reordering; nothing short of
 * header-name mapping would.
 */
export const AUDIT_BOM_COLUMN_COUNT = 14

/** 1-based source ordinals 1, 2, 3, 7, 9, 10, 11, 12, expressed 0-based. */
const RETAINED_COLUMN_INDICES = [0, 1, 2, 6, 8, 9, 10, 11] as const

const FIELD_NAMES: readonly (keyof AuditBomFields)[] = [
  'find_number',
  'component_part_number',
  'per_board_count',
  'ref_des',
  'description',
  'mount_type',
  'quantity_needed',
  'quantity_on_hand',
]

const HEADER_ECHO_FIRST_FIELD = 'find#'

interface NumberedLine {
  /** 1-based index in the ORIGINAL paste, so the operator can find it in Excel. */
  lineNumber: number
  fields: string[]
}

function isBlankLine(line: NumberedLine): boolean {
  return line.fields.every((field) => field.trim() === '')
}

/** Returns an empty AuditBomFields bag — the shape the grid's trailing row uses. */
export function emptyAuditBomFields(): AuditBomFields {
  return {
    find_number: null,
    component_part_number: null,
    per_board_count: null,
    ref_des: null,
    description: null,
    mount_type: null,
    quantity_needed: null,
    quantity_on_hand: null,
  }
}

export function isBlankAuditBomLine(line: AuditBomFields): boolean {
  return FIELD_NAMES.every((name) => (line[name] ?? '').trim() === '')
}

/**
 * Parse clipboard TSV copied from the Audit BOM sheet.
 *
 * Pre:  pastedText is the raw clipboard payload.
 *       rowCap is SecondOpsRecord.limits.max_lines from the fetch that opened
 *       the modal — NEVER a client-side constant. Callers do not get to supply
 *       their own bound.
 * Post: on success returns one ParsedAuditLine per surviving source line, in
 *       source order, each carrying the eight retained fields.
 *       Lines are filtered in this order, and the order is part of the contract:
 *         1. Split on line endings, then each line on tabs.
 *         2. Drop every BLANK line — one whose fields are all whitespace —
 *            REGARDLESS OF POSITION. A row of 13 tabs and a row of nothing are
 *            both blank. This precedes the width check, so a blank line can
 *            never surface as a column-count mismatch.
 *         3. Drop a now-leading line whose first field case-insensitively
 *            equals "Find#" after trimming, as a header echo.
 *         4. Reject a quoted field before measuring width: Excel wraps a cell
 *            in quotes when it contains a tab, newline or quote, and a naive
 *            split on such a payload produces a short line AND a long line.
 *            Naming the quote is actionable; naming the width is not.
 *         5. Assert exactly AUDIT_BOM_COLUMN_COUNT fields on every survivor.
 *         6. Enforce rowCap last, so a concrete bad line is reported ahead of a
 *            bulk bound.
 *       Every rejection names the 1-based line number in the ORIGINAL paste.
 *       RFC4180 quoted-field handling is NOT implemented; that choice is
 *       recorded here so the next reader does not assume it exists.
 * Raises: never — every failure is returned as a PasteRejection value.
 */
export function parseAuditBomPaste(pastedText: string, rowCap: number): PasteResult {
  if (pastedText.trim() === '') {
    return { ok: false, rejection: { kind: 'empty-paste' } }
  }

  const numbered: NumberedLine[] = pastedText
    .split(/\r\n|\r|\n/)
    .map((line, index) => ({ lineNumber: index + 1, fields: line.split('\t') }))

  const surviving = numbered.filter((line) => !isBlankLine(line))

  if (
    surviving.length > 0 &&
    (surviving[0].fields[0] ?? '').trim().toLowerCase() === HEADER_ECHO_FIRST_FIELD
  ) {
    surviving.shift()
  }

  for (const line of surviving) {
    const quoted = line.fields.find((field) => field.startsWith('"'))
    if (quoted !== undefined) {
      return {
        ok: false,
        rejection: { kind: 'quoted-field-unsupported', lineNumber: line.lineNumber },
      }
    }
    if (line.fields.length !== AUDIT_BOM_COLUMN_COUNT) {
      return {
        ok: false,
        rejection: {
          kind: 'column-count-mismatch',
          lineNumber: line.lineNumber,
          observed: line.fields.length,
          expected: AUDIT_BOM_COLUMN_COUNT,
        },
      }
    }
  }

  if (surviving.length > rowCap) {
    return {
      ok: false,
      rejection: { kind: 'row-cap-exceeded', observed: surviving.length, cap: rowCap },
    }
  }

  const lines: ParsedAuditLine[] = surviving.map((line) => {
    const parsed = emptyAuditBomFields()
    FIELD_NAMES.forEach((name, position) => {
      const value = line.fields[RETAINED_COLUMN_INDICES[position]]
      parsed[name] = value === '' ? null : value
    })
    return parsed
  })

  return { ok: true, lines }
}

/** Operator-facing text for a rejection. Names the line so it can be found in Excel. */
export function describePasteRejection(rejection: PasteRejection): string {
  switch (rejection.kind) {
    case 'empty-paste':
      return 'Nothing was pasted.'
    case 'column-count-mismatch':
      return (
        `Line ${rejection.lineNumber} has ${rejection.observed} columns, ` +
        `expected ${rejection.expected}. Unhide every column in the Audit BOM ` +
        'sheet and copy again.'
      )
    case 'quoted-field-unsupported':
      return (
        `Line ${rejection.lineNumber} contains a quoted cell. Remove the line ` +
        'break or tab from that cell and copy again.'
      )
    case 'row-cap-exceeded':
      return `Paste has ${rejection.observed} rows; the maximum is ${rejection.cap}.`
  }
}

export async function fetchSecondOps(jobId: number): Promise<SecondOpsRecord> {
  const resp = await apiClient.get<SecondOpsRecord>(`/api/jobs/${jobId}/second-ops`)
  return resp.data
}

/**
 * Whole-set replace of a job's 2nd OPS record.
 *
 * Post: never throws — the three failure modes are returned as values because
 *       they need three different affordances (see SecondOpsSaveResult).
 */
export async function putSecondOps(
  jobId: number,
  payload: SecondOpsWritePayload,
): Promise<SecondOpsSaveResult> {
  try {
    const resp = await apiClient.put<SecondOpsRecord>(
      `/api/jobs/${jobId}/second-ops`,
      payload,
    )
    return { kind: 'saved', record: resp.data }
  } catch (err: unknown) {
    if (isApiError(err) && err.response) {
      const { status, data } = err.response
      const detail = (data as { detail?: unknown })?.detail
      if (status === 409) {
        return {
          kind: 'stale',
          message: messageFromDetail(
            detail,
            'This job changed since the audit was opened; it can no longer be edited.',
          ),
        }
      }
      if (status === 422) {
        return {
          kind: 'rejected',
          message: messageFromDetail(detail, 'The audit was rejected by the server.'),
        }
      }
      if (status === 404) {
        return { kind: 'stale', message: 'This job no longer exists.' }
      }
    }
    return {
      kind: 'unreachable',
      message: 'Could not reach the API. The audit was not saved; retry.',
    }
  }
}

function messageFromDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    const message = (detail as Record<string, unknown>).message
    if (typeof message === 'string') return message
  }
  return fallback
}
