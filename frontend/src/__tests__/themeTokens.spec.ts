import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/* No test asserts a rendered colour — jsdom loads no stylesheet and evaluates no
 * `prefers-color-scheme` branch, so a mounted component cannot resolve these.
 * The gate is instead on the source of truth: the token declarations themselves,
 * so a later edit cannot revert the ground colour with a green suite. */

const STYLESHEET = readFileSync(resolve(process.cwd(), 'src/style.css'), 'utf-8')

const DARK_BRANCH = (() => {
  const start = STYLESHEET.indexOf('@media (prefers-color-scheme: dark)')
  expect(start).toBeGreaterThan(-1)
  return STYLESHEET.slice(start)
})()

function declaredIn(block: string, token: string): string | null {
  const match = block.match(new RegExp(`${token}:\s*([^;]+);`))
  return match ? match[1].trim() : null
}

describe('theme tokens', () => {
  it('anchors the dark ground on #111110', () => {
    expect(declaredIn(DARK_BRANCH, '--color-slate-900')).toBe('#111110')
  })

  it('binds the three-tone scale to distinct values in each theme', () => {
    const light = STYLESHEET.slice(0, STYLESHEET.indexOf('@media (prefers-color-scheme: dark)'))
    for (const block of [light, DARK_BRANCH]) {
      const tones = ['--color-surface-base', '--color-surface-raised', '--color-surface-overlay']
        .map(token => declaredIn(block, token))
      expect(tones.every(Boolean)).toBe(true)
      expect(new Set(tones).size).toBe(3)
    }
  })

  it('keeps the dark ground and the raised tone in step', () => {
    expect(declaredIn(DARK_BRANCH, '--color-surface-base'))
      .toBe(declaredIn(DARK_BRANCH, '--color-slate-900'))
  })

  it('declares slate-750, the stop four dark: sites name', () => {
    expect(declaredIn(STYLESHEET, '--color-slate-750')).not.toBeNull()
    expect(declaredIn(DARK_BRANCH, '--color-slate-750')).toBe('#232220')
  })

  it('routes the stock accent stops through the brand ramp', () => {
    for (const stop of ['--color-sky-500', '--color-blue-500']) {
      expect(declaredIn(STYLESHEET, stop)).toBe('var(--color-accent-500)')
    }
    expect(declaredIn(STYLESHEET, '--color-accent-500')).toBe('#0093d0')
  })

  it('composes dark elevation from a lighter top edge, not a darker halo', () => {
    for (const token of ['--shadow-hover', '--shadow-elevated']) {
      expect(declaredIn(DARK_BRANCH, token)).toMatch(/^inset 0 1px 0 rgba\(255,255,255,/)
    }
  })

  it('gives every grid signal a value in both themes', () => {
    const light = STYLESHEET.slice(0, STYLESHEET.indexOf('@media (prefers-color-scheme: dark)'))
    for (const token of ['--color-ship-today', '--color-secondops-text', '--color-secondops-na', '--color-secondops-recorded']) {
      expect(declaredIn(light, token), `${token} light`).not.toBeNull()
      expect(declaredIn(DARK_BRANCH, token), `${token} dark`).not.toBeNull()
      expect(declaredIn(light, token)).not.toBe(declaredIn(DARK_BRANCH, token))
    }
  })

  it('keeps ship-today off the brand value that fails AA as grid prose', () => {
    // #0093D0 is 3.45:1 on white and 3.88:1 on the dark hover row. The token
    // carries the nearest hue-locked stop that clears 4.5 on all four surfaces.
    for (const block of [STYLESHEET, DARK_BRANCH]) {
      expect(declaredIn(block, '--color-ship-today')).not.toBe('#0093d0')
    }
  })
})
