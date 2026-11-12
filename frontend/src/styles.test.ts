/// <reference types="node" />
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

// Text contrast ≥ 4.5:1 in both themes (PRODUCT.md, Accessibility): warm neutrals make this a live
// check, not an assumption. Reads the token values straight from styles.css.
const css = readFileSync(path.resolve(import.meta.dirname, 'styles.css'), 'utf8')

function tokens(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/--([a-z-]+):\s*(#[0-9a-f]{6})/g)].map((m) => [m[1]!, m[2]!]))
}

const light = tokens(css.slice(css.indexOf(':root {'), css.indexOf('@media')))
const dark = tokens(css.slice(css.indexOf(":root[data-theme='dark'] {"), css.indexOf('@theme inline')))

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }) as [number, number, number]
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number]
  return (hi + 0.05) / (lo + 0.05)
}

const PAIRS: [string, string][] = [
  ['foreground', 'background'],
  ['muted-foreground', 'background'],
  ['muted-foreground', 'card'],
  ['muted-foreground', 'muted'],
  ['card-foreground', 'card'],
  ['primary-foreground', 'primary'],
  ['kb-foreground', 'kb'],
  ['kb', 'background'],
  ['kb', 'card'],
  ['warn', 'background'],
  ['destructive', 'background'],
  // comp A surfaces (2026-11-13): panes, current node, chips, code blocks
  ['muted-foreground', 'panel'],
  ['foreground', 'panel'],
  ['foreground', 'kb-soft'],
  ['kb', 'panel'],
  ['destructive', 'warn-soft'],
  ['code-foreground', 'code'],
]

describe.each([
  ['light', light],
  ['dark', dark],
])('%s theme', (_, t) => {
  it.each(PAIRS)('%s on %s is at least 4.5:1', (fg, bg) => {
    expect(t[fg], fg).toBeDefined()
    expect(t[bg], bg).toBeDefined()
    expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5)
  })
})
