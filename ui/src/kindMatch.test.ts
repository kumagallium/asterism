import { describe, expect, it } from 'vitest'
import { catalogClassNames, datasetHasKind, kindDisplayName, vocabClassFor } from './galleryApi'

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

describe('kindDisplayName', () => {
  const labels = { 'https://example.org/ontology#Country': '国' }
  it('表示名はそのまま', () => {
    expect(kindDisplayName(ds, '年ごとの記録')).toBe('年ごとの記録')
  })
  it('ローカル名は、その種類の IRI の名前に引き直す', () => {
    expect(kindDisplayName(ds, 'Country', labels)).toBe('国')
  })
  it('名前が分からないときは出さない（生の識別子を返さない）', () => {
    expect(kindDisplayName(ds, 'Observation', labels)).toBeUndefined()
    expect(kindDisplayName(ds, 'Observation', { 'https://example.org/ontology#Observation': 'Observation' })).toBeUndefined()
    expect(kindDisplayName(ds, 'Paper', labels)).toBeUndefined()
  })
})
