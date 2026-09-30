import { describe, expect, it } from 'vitest'
import { rowKindLabels } from './rowKindLabels'

const label = (file: string) => `${file} の 1 行`

describe('rowKindLabels (K64)', () => {
  it('names each file by its stem, keyed by the name it was given', () => {
    expect(rowKindLabels(['curves.csv', 'samples.csv'], label)).toEqual({
      'curves.csv': 'curves の 1 行',
      'samples.csv': 'samples の 1 行',
    })
  })

  it('keeps a Japanese file name as the person wrote it', () => {
    expect(rowKindLabels(['測定結果.csv'], label)).toEqual({ '測定結果.csv': '測定結果 の 1 行' })
  })

  it('keeps the extension when two files share a stem, so they stay apart', () => {
    expect(rowKindLabels(['data.csv', 'data.json', 'other.csv'], label)).toEqual({
      'data.csv': 'data.csv の 1 行',
      'data.json': 'data.json の 1 行',
      'other.csv': 'other の 1 行',
    })
  })
})
