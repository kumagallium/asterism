import { describe, expect, it } from 'vitest'
import { EMPTY_SKY } from '../sky/skyApi'
import { datasetNodeId, draftNodeId, hubNodeId, kindId, termId } from '../sky/skyLayout'
import { contentsKeyOf, UNPUBLISHED } from './contentsKey'
import type { SkyLoad } from './useSky'

const sky = {
  ...EMPTY_SKY,
  terms: [{ iri: 'http://t/zt', slug: 'zt', label: 'ZT', label_en: 'ZT', kind: 'class' as const, members: [], standard: null, answering: 0, questions: [] }],
  hubs: [{ id: 'hub1', label: '視点', members: ['http://k/a', 'http://k/b'] }],
  datasets: [{ id: 'pub', label: '公開', created_at: '', kinds: [] }],
}
const ok: Extract<SkyLoad, { status: 'ok' }> = {
  status: 'ok',
  sky,
  born: [],
  proposedDatasets: [{ id: 'thin', label: '添えた', created_at: '', kinds: [{ iri: 'http://k/new', label: '新しい種類', count: 3, links: [], standard: null }], proposed: true }],
  proposed: [
    {
      draft_id: 'dr',
      theme_label: 't',
      relations: [],
      questions: [],
      concepts: [
        { key: 'a', label: '新しい種類', kind: 'class', use: 'new', state: 'thin' },
        { key: 'b', label: '別名', kind: 'class', use: 'existing:zt' },
        { key: 'c', label: '未知', kind: 'property', use: 'new', state: 'thin' },
      ],
    },
  ],
}

describe('contentsKeyOf', () => {
  it('何も押していない: session / 話が無ければ null', () => {
    expect(contentsKeyOf(null, ok, 's1')).toEqual({ session: 's1' })
    expect(contentsKeyOf(null, ok, null)).toBeNull()
  })
  it('用語・種類・データセット', () => {
    expect(contentsKeyOf(termId('http://t/zt'), ok, 's')).toEqual({ term: 'http://t/zt' })
    expect(contentsKeyOf(kindId('pub', 'http://k/x'), ok, 's')).toEqual({ kind: 'http://k/x' })
    expect(contentsKeyOf(datasetNodeId('pub'), ok, 's')).toEqual({ dataset: 'pub' })
  })
  it('未公開のデータセットの種類は、そのデータセット', () => {
    expect(contentsKeyOf(kindId('thin', 'http://k/new'), ok, 's')).toEqual({ dataset: 'thin' })
  })
  it('視点は最初の member の種類', () => {
    expect(contentsKeyOf(hubNodeId('hub1'), ok, 's')).toEqual({ kind: 'http://k/a' })
    expect(contentsKeyOf(hubNodeId('none'), ok, 's')).toBeNull()
  })
  it('提案: 添えた表・既にある用語・引けないもの', () => {
    expect(contentsKeyOf(draftNodeId('dr', 'a'), ok, 's')).toEqual({ dataset: 'thin' })
    expect(contentsKeyOf(draftNodeId('dr', 'b'), ok, 's')).toEqual({ term: 'http://t/zt' })
    expect(contentsKeyOf(draftNodeId('dr', 'c'), ok, 's')).toBe(UNPUBLISHED)
    expect(contentsKeyOf(draftNodeId('zz', 'a'), ok, 's')).toBe(UNPUBLISHED)
  })
})
