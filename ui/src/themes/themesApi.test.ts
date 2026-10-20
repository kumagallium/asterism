import { describe, expect, it } from 'vitest'
import { themeLabel } from './themesApi'

const t = (key: string) => (key === 'themes:firstThemeLabel' ? 'First questions' : key)

describe('themeLabel', () => {
  it('translates only the migration default of the first theme', () => {
    expect(themeLabel({ id: 'first', label: 'はじめの問い' }, t)).toBe('First questions')
  })
  it('keeps a label a person wrote', () => {
    expect(themeLabel({ id: 'first', label: '熱電材料' }, t)).toBe('熱電材料')
    expect(themeLabel({ id: 'other', label: 'はじめの問い' }, t)).toBe('はじめの問い')
  })
})
