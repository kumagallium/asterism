import { describe, expect, it } from 'vitest'
import type { SkyTerm } from './skyApi'
import { decideCandidatesOf, decideSubjectOf } from './skyDecide'
import type { SkyNode } from './skyLayout'

const term = (iri: string, label: string, kind: 'class' | 'property' = 'class'): SkyTerm => ({
  iri,
  slug: iri,
  label,
  label_en: '',
  kind,
  members: [],
  standard: null,
  answering: 0,
  questions: [],
})

describe('decideSubjectOf', () => {
  it('種類（星）だけが主語になる', () => {
    const kind = { type: 'kind', id: 'k|a', iri: 'urn:a', label: '試料', datasetId: 'd', count: 1, r: 3, x: 0, y: 0 } as SkyNode
    expect(decideSubjectOf(kind)).toEqual({ id: 'urn:a', iri: 'urn:a', label: '試料', role: 'dataset', termKind: 'class' })
  })
  it('種類以外と未選択は null', () => {
    for (const type of ['dataset', 'term', 'std', 'hub']) {
      expect(decideSubjectOf({ type, id: 'x', label: 'x', x: 0, y: 0 } as unknown as SkyNode)).toBeNull()
    }
    expect(decideSubjectOf(undefined)).toBeNull()
    expect(decideSubjectOf(null)).toBeNull()
  })
})

describe('decideCandidatesOf', () => {
  const terms = [term('urn:t1', '合金'), term('urn:t2', '試料'), term('urn:p', '温度', 'property'), term('urn:t3', 'Sample')]
  it('種類の用語だけを共有の候補にする', () => {
    const out = decideCandidatesOf(terms)
    expect(out.map((c) => c.iri)).toEqual(['urn:t1', 'urn:t2', 'urn:t3'])
    expect(out.every((c) => c.role === 'shared' && c.termKind === 'class' && c.id === c.iri)).toBe(true)
  })
  it('名前が完全一致するものが先頭', () => {
    const subject = decideSubjectOf({ type: 'kind', id: 'k|a', iri: 'urn:a', label: '試料', datasetId: 'd', count: 1, r: 3, x: 0, y: 0 } as SkyNode)
    expect(decideCandidatesOf(terms, subject)[0].iri).toBe('urn:t2')
  })
  it('用語が無ければ空', () => {
    expect(decideCandidatesOf([])).toEqual([])
  })
})
