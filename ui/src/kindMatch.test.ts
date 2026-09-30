import { describe, expect, it } from 'vitest'
import { catalogClassNames, datasetHasKind, vocabClassFor } from './galleryApi'

// 種類は表示名（図から・K50）でも、種類のローカル名（Ask の引用の kind に来やすい）でも
// 同じ種類として見つかる（ADR kantan K59）。
const ds = {
  classes: ['国', '年ごとの記録'],
  classIris: ['https://example.org/ontology#Country', 'https://example.org/ontology#Observation'],
}

describe('datasetHasKind', () => {
  it('表示名で見つかる', () => {
    expect(datasetHasKind(ds, '国')).toBe(true)
  })
  it('ローカル名でも見つかる', () => {
    expect(datasetHasKind(ds, 'Observation')).toBe(true)
  })
  it('IRI そのものでも見つかる', () => {
    expect(datasetHasKind(ds, 'https://example.org/ontology#Country')).toBe(true)
  })
  it('無い種類・空は見つからない', () => {
    expect(datasetHasKind(ds, 'Paper')).toBe(false)
    expect(datasetHasKind(ds, '')).toBe(false)
  })
})

describe('catalogClassNames', () => {
  it('ローカル名の kind からも「ことば」へのリンクが出る', () => {
    const known = catalogClassNames([ds as never])
    expect(vocabClassFor('Country', known)).toBe('Country')
    expect(vocabClassFor('国', known)).toBe('国')
    expect(vocabClassFor('Unknown', known)).toBeUndefined()
  })
})
