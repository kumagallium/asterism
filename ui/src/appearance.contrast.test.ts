// @ts-expect-error UI の型に node は入れていない（検査のときだけ node で読む）
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css: string = readFileSync('src/appearance.css', 'utf8')

// ダークの配色の釣り合いを数値で固定する（設定を開いても分からない、を繰り返さない）。
function tokens(): Record<string, string> {
  const block = css.match(/:root\[data-theme="dark"\] \{([\s\S]*?)\n\}/)?.[1] ?? ''
  return Object.fromEntries([...block.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]))
}

type Rgb = [number, number, number]
const hex = (v: string): Rgb => [1, 3, 5].map((i) => parseInt(v.slice(i, i + 2), 16)) as Rgb

function luminance([r, g, b]: Rgb): number {
  const [x, y, z] = [r, g, b].map((c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * x + 0.7152 * y + 0.0722 * z
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((p, q) => q - p)
  return (hi + 0.05) / (lo + 0.05)
}

/** `rgb(7 12 22 / 68%)` の幕を下の色に重ねた結果。 */
function dim(overlay: string, under: Rgb): Rgb {
  const m = overlay.match(/rgb\((\d+) (\d+) (\d+) \/ (\d+)%\)/)
  if (!m) throw new Error(`幕の形が想定外: ${overlay}`)
  const alpha = Number(m[4]) / 100
  return [0, 1, 2].map((i) => Number(m[i + 1]) * alpha + under[i] * (1 - alpha)) as Rgb
}

describe('ダークの配色', () => {
  const t = tokens()
  const c = (name: string) => hex(t[name])

  it('文字はどの面の上でも 4.5:1 以上で読める', () => {
    const surfaces = ['bg', 'paper', 'surface', 'surface-alt', 'surface-raised', 'surface-sink']
    for (const text of ['fg', 'body', 'muted', 'faint', 'primary', 'accent', 'entity', 'activity']) {
      for (const surface of surfaces) {
        expect(contrast(c(text), c(surface)), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  it('状態の文字は自分の地の上で 4.5:1 以上で読める', () => {
    for (const state of ['success', 'error', 'info', 'warn']) {
      expect(contrast(c(`${state}-fg`), c(`${state}-bg`)), state).toBeGreaterThanOrEqual(4.5)
    }
    expect(contrast(c('primary'), c('primary-soft'))).toBeGreaterThanOrEqual(4.5)
    expect(contrast(c('primary-fg'), c('primary'))).toBeGreaterThanOrEqual(4.5)
  })

  it('面は 沈む → 地 → 面 → 浮く の順に明るくなる', () => {
    const order = ['paper', 'bg', 'surface-alt', 'surface', 'surface-raised'].map((n) => luminance(c(n)))
    expect(order).toEqual([...order].sort((a, b) => a - b))
  })

  it('開いたダイアログは、幕で沈んだ画面からはっきり浮く', () => {
    const dimmed = dim(t.overlay, c('surface'))
    expect(contrast(c('surface-raised'), dimmed)).toBeGreaterThanOrEqual(1.5)
    expect(contrast(c('border-raised'), dimmed)).toBeGreaterThanOrEqual(3)
  })

  it('入力欄の枠は面の上で 3:1 以上ある', () => {
    expect(contrast(c('border-strong'), c('surface'))).toBeGreaterThanOrEqual(3)
  })
})
