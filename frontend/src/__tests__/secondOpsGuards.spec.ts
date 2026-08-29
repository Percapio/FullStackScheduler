/**
 * Phase 22 pre-flight greps, executed rather than remembered.
 *
 * Each case here is one line of the plan's manual checklist. A checklist that
 * only exists in a document is a checklist that stops being run.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { readdirSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'

/**
 * Locate frontend/src without depending on the working directory.
 * import.meta.url is an http URL under the jsdom environment, so it cannot be
 * converted to a path; cwd is the repo root or the package root depending on
 * how vitest was invoked.
 */
function resolveSrc(): string {
  let dir = process.cwd()
  for (;;) {
    for (const candidate of [join(dir, 'src'), join(dir, 'frontend', 'src')]) {
      if (existsSync(join(candidate, 'api', 'secondOps.ts'))) return candidate
    }
    const parent = dirname(dir)
    if (parent === dir) throw new Error('frontend/src not found from ' + process.cwd())
    dir = parent
  }
}

const SRC = resolveSrc()

function sourceFiles(): string[] {
  const found: string[] = []
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry)
      if (statSync(full).isDirectory()) {
        walk(full)
        continue
      }
      if (/\.(ts|vue)$/.test(entry)) found.push(full)
    }
  }
  walk(SRC)
  return found
}

/**
 * Read a source file with every comment removed.
 *
 * These guards assert properties of the CODE. Matching prose too would make
 * them trippable by a docblock that merely NAMES the thing it forbids — and the
 * cheapest way to satisfy such a guard is to reword the comment, not to fix the
 * code. Comments explaining these very rules live in the files being scanned.
 */
function read(path: string): string {
  return readFileSync(path, 'utf8')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

/** Read a source file verbatim, comments included. */
function readRaw(path: string): string {
  return readFileSync(path, 'utf8')
}

const FILES = sourceFiles()
const rel = (path: string) => relative(SRC, path).replace(/\\/g, '/')

function filesContaining(needle: string | RegExp): string[] {
  const test = typeof needle === 'string'
    ? (text: string) => text.includes(needle)
    : (text: string) => needle.test(text)
  return FILES.filter((path) => test(read(path))).map(rel)
}

describe('Phase 22 pre-flight greps', () => {
  it('applies renderNotes to no SecondOps component', () => {
    // A hit means v-html reached the pasted BOM data.
    const hits = filesContaining('renderNotes').filter((path) => path.includes('SecondOps'))
    expect(hits).toEqual([])
  })

  it('uses v-html in no SecondOps component', () => {
    const hits = filesContaining('v-html').filter((path) => path.includes('SecondOps'))
    expect(hits).toEqual([])
  })

  it('carries colspan only in HistoryView, at the rendered th count', () => {
    const hits = filesContaining('colspan').filter((path) => !path.includes('__tests__'))
    expect(hits.filter((path) => path.includes('LineageAccordion'))).toEqual([])
    const historyView = readRaw(join(SRC, 'views', 'HistoryView.vue'))
    const thCount = (historyView.match(/<th\b/g) ?? []).length
    expect(historyView).toContain(`:colspan="${thCount}"`)
  })

  it('treats a null summary as absent, never as unaudited', () => {
    for (const path of FILES.filter((p) => rel(p).includes('SecondOps') && !rel(p).includes('__tests__'))) {
      expect(read(path)).not.toMatch(/summary\s*===?\s*null\s*\?\s*['"]unaudited/)
    }
  })

  it('declares no nullable SecondOpsRecord prop', () => {
    // A nullable record prop is the loading-versus-unaudited conflation that
    // SecondOpsFetch replaced.
    const hits = FILES.filter((path) => !rel(path).includes('__tests__'))
      .filter((path) => /SecondOpsRecord\s*\|\s*null/.test(read(path)))
      .map(rel)
    expect(hits).toEqual([])
  })

  it('hardcodes no row cap or max_lines literal', () => {
    // The cap comes from the server or it drifts. The name must not be preceded
    // by a dot: `record.limits.max_lines` is a READ of the server's value, and
    // in a ternary its not-loaded fallback trails a colon that would otherwise
    // read as an assignment.
    const CAP_LITERAL =
      /(?<![.\w])(rowCap|row_cap|maxLines|max_lines|noteMaxChars|note_max_chars)\s*[:=]\s*[1-9]\d*/
    const offenders: string[] = []
    for (const path of FILES) {
      if (rel(path).includes('types.gen.ts') || rel(path).includes('__tests__')) continue
      for (const line of read(path).split('\n')) {
        if (CAP_LITERAL.test(line)) offenders.push(`${rel(path)}: ${line.trim()}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('confines putSecondOps to the api module and the shipping store', () => {
    // A hit in stores/history.ts, HistoryView.vue or SecondOpsRecordModal.vue
    // would mean the read-only surface acquired a write path.
    const hits = filesContaining('putSecondOps').filter((path) => !path.includes('__tests__'))
    expect(hits.sort()).toEqual(['api/secondOps.ts', 'stores/shipping.ts'])
  })

  it('keeps the entry modal out of HistoryView', () => {
    expect(read(join(SRC, 'views', 'HistoryView.vue'))).not.toContain('SecondOpsEntryModal')
  })

  it('keeps the record modal free of any editing surface', () => {
    const recordModal = read(join(SRC, 'components', 'SecondOpsRecordModal.vue'))
    expect(recordModal).not.toContain('<input')
    expect(recordModal).not.toContain('<textarea')
    expect(recordModal).not.toContain('putSecondOps')
    expect(recordModal).not.toContain('accept')
  })
})
