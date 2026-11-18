import { describe, expect, it } from 'vitest'
import { EMPTY_SKY } from '../../sky/skyApi'
import type { DraftSky, SkyData } from '../../sky/skyApi'
import { datasetsShown, hasWords, isDatasetNode, withoutDatasetSide } from './datasetVisibility'

const ledgerDraft = (n: number): DraftSky => ({
  draft_id: 'ledger:s',
  theme_label: '台帳',
  concepts: Array.from({ length: n }, (_, i) => ({ key: `c${i}`, label: `語${i}`, kind: 'class' as const, use: 'new' })),
  relations: [],
  questions: [],
})
const term = { iri: 'urn:t:a', slug: 'a', label: 'あ', label_en: 'a', kind: 'class', members: [], standard: null, answering: 0, questions: [] } as unknown as SkyData['terms'][number]

describe('データセットも出すの既定', () => {
  it('語か関係が 1 つでもあれば既定オフ（台帳の語・ストアの用語・知識の線のどれでも）', () => {
    expect(hasWords(EMPTY_SKY, [ledgerDraft(1)])).toBe(true)
    expect(hasWords({ ...EMPTY_SKY, terms: [term] }, [])).toBe(true)
    expect(hasWords({ ...EMPTY_SKY, edges: [{ from: 'a', to: 'b', relation: 'subClassOf' } as unknown as NonNullable<SkyData['edges']>[number]] }, [])).toBe(true)
    expect(datasetsShown(null, true)).toBe(false)
  })
  it('語も関係も 1 つも無いときはオン（取り込みをしていない利用者の図を変えない）', () => {
    expect(hasWords(EMPTY_SKY, [])).toBe(false)
    expect(hasWords(EMPTY_SKY, [ledgerDraft(0)])).toBe(false)
    expect(datasetsShown(null, false)).toBe(true)
  })
  it('利用者の選択が既定より優先される', () => {
    expect(datasetsShown(true, true)).toBe(true)
    expect(datasetsShown(false, false)).toBe(false)
  })
})

describe('データセット側の点', () => {
  it('データセット d|・種類 k|・視点 h| がデータセット側。用語・台帳の語は違う', () => {
    expect(['d|x', 'k|x|y', 'h|v'].every(isDatasetNode)).toBe(true)
    expect(isDatasetNode('t|urn:a')).toBe(false)
    expect(isDatasetNode('draft|ledger:s|c')).toBe(false)
    expect(isDatasetNode(null)).toBe(false)
  })
  it('データセットと視点だけを除く（用語・線は残す）', () => {
    const sky = { ...EMPTY_SKY, terms: [term], datasets: [{ id: 'w', label: 'w', created_at: '', kinds: [] }], hubs: [{ id: 'h', label: 'h', members: [] }] } as unknown as SkyData
    const out = withoutDatasetSide(sky)
    expect(out.datasets).toEqual([])
    expect(out.hubs).toEqual([])
    expect(out.terms).toHaveLength(1)
  })
})
