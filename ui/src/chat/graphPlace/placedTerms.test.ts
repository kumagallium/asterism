import { describe, expect, it } from 'vitest'
import { EMPTY_SKY, type SkyTerm } from '../../sky/skyApi'
import { ledger, row, term } from './fixtures'
import { placedIndex, placedIriOf, storeRoleTable, withTermRoles } from './placedTerms'

const placed = (id: string, name: string, role: string, iri: string) => ({ ...term(id, name, role), termIri: iri })
const sterm = (iri: string): SkyTerm => ({ iri, slug: iri, label: iri, label_en: iri, kind: 'class', members: [], standard: null, answering: 0, questions: [] })

describe('placedTerms（台帳の行 → 置いた語の表）', () => {
  const l = ledger('s1', [row({ from: placed('t-a', 'ゲルマ', 'material', 'urn:a'), to: term('t-b', '焼結', 'method') }), row({ from: placed('t-a', 'ゲルマ', 'material', 'urn:a'), to: placed('t-c', '導電率', 'concept', 'urn:c') })])

  it('IRI → 取り込み元・語の id・役割。term_iri が無い語は入らない', () => {
    const idx = placedIndex([l])
    expect([...idx.keys()].sort()).toEqual(['urn:a', 'urn:c'])
    expect(idx.get('urn:a')).toMatchObject({ source: 's1', termId: 't-a', role: 'material' })
  })
  it('台帳の語 → IRI（置いていなければ null）', () => {
    expect(placedIriOf([l], 's1', 't-a')).toBe('urn:a')
    expect(placedIriOf([l], 's1', 't-b')).toBeNull()
    expect(placedIriOf([l], 's2', 't-a')).toBeNull()
  })
  it('sky の用語に役割を足す。台帳に出てこない用語はそのまま・置いた語が無ければ同じ物を返す', () => {
    const sky = { ...EMPTY_SKY, terms: [sterm('urn:a'), sterm('urn:x')] }
    const out = withTermRoles(sky, [l])
    expect(out.terms[0].role).toBe('material')
    expect('role' in out.terms[1]).toBe(false)
    expect(withTermRoles(sky, [ledger('s2', [])])).toBe(sky)
  })
  it('並びに依らず同じ（同じ IRI が複数行に出ても先の 1 件）', () => {
    expect(placedIndex([l]).get('urn:a')?.termId).toBe('t-a')
  })
})

describe('ストアの札が正（取り込み元を外した後・チャットで作った用語）', () => {
  const sky = { ...EMPTY_SKY, terms: [sterm('urn:a'), sterm('urn:chat'), sterm('urn:none')] }
  const l = ledger('s1', [row({ from: placed('t-a', 'ゲルマ', 'material', 'urn:a'), to: term('t-b', '焼結', 'method') })])

  it('storeRoleTable: 札のある語だけ IRI → 役割', () => {
    const t = storeRoleTable([{ iri: 'urn:a', role: 'material' }, { iri: 'urn:x', role: null }, { iri: 'urn:y' }])
    expect([...t.entries()]).toEqual([['urn:a', 'material']])
  })
  it('台帳が無くても（外した後）ストアの札で色が付く。チャットで作った用語も', () => {
    const out = withTermRoles(sky, [], new Map([['urn:a', 'material'], ['urn:chat', 'method']]))
    expect(out.terms.map((t) => t.role)).toEqual(['material', 'method', undefined])
  })
  it('両方にあればストアの値・ストアに無ければ台帳で補う・読めなかった（null・空）ときは台帳だけ', () => {
    expect(withTermRoles(sky, [l], new Map([['urn:a', 'method']])).terms[0].role).toBe('method')
    expect(withTermRoles(sky, [l], new Map([['urn:chat', 'method']])).terms[0].role).toBe('material')
    expect(withTermRoles(sky, [l], null).terms[0].role).toBe('material')
    expect(withTermRoles(sky, [], new Map())).toBe(sky)
  })
})
