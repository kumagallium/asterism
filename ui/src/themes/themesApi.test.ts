import { describe, expect, it } from 'vitest'
import { themeLabel } from './themesApi'

const t = (key: string) => (key === 'themes:firstThemeLabel' ? 'First questions' : key)

describe('themeLabel', () => {
  it('translates only the migration default of the first theme', () => {
    expect(themeLabel({ id: 'first', label: 'はじめの問い' }, t)).toBe('First questions')
  })
  it('keeps a label a person wrote', () => {
    expect(themeLabel({ id: 'first', label: '電池' }, t)).toBe('電池')
    expect(themeLabel({ id: 'other', label: 'はじめの問い' }, t)).toBe('はじめの問い')
  })
})
