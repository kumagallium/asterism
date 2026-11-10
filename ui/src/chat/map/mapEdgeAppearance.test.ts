import { describe, expect, it } from 'vitest'
import { mapEdgeAppearance } from './mapEdgeAppearance'
import { buildMapGraph } from './mapModel'
import { EMPTY_SKY } from '../../sky/skyApi'

const pal = { edge: '#687e9b', edgeProposed: '#8193ab', edgeOn: '#16243b', faded: '#c1cdde' }
const idle = { hovered: false, on: false, faded: false, lang: 'ja' as const }

describe('確定・提案の線の描画', () => {
  it('通常は確定線と提案線を色と太さで区別し、hover外では名前を出さない', () => {
    const fixed = mapEdgeAppearance({ label: '変える' }, pal, idle)
    const proposed = mapEdgeAppearance({ label: '下げる', thin: true, proposed: true }, pal, idle)
    expect(fixed).toMatchObject({ size: 1, color: pal.edge, label: '' })
    expect(proposed).toMatchObject({ size: 0.75, color: pal.edgeProposed, label: '' })
    expect(mapEdgeAppearance({ thin: true, size: 0.5 }, pal, idle).size).toBe(0.6)
    expect(mapEdgeAppearance({ thin: true, size: 2 }, pal, idle).size).toBe(1.5)
  })
  it('根拠の線は提案の縮小規則より太くしない', () => {
    expect(mapEdgeAppearance({ kind: 'evidence', thin: true }, pal, idle).size).toBe(0.6)
    expect(mapEdgeAppearance({ kind: 'evidence', thin: true, size: 3 }, pal, idle).size).toBe(0.6)
  })
  it('hoverの提案印は現在の言語に従い、元の名前を変更しない', () => {
    const data = { thin: true, proposed: true, label: '下げる' }
    expect(mapEdgeAppearance(data, pal, { ...idle, hovered: true })).toMatchObject({ label: '下げる (提案)', color: pal.edgeOn, size: 1.75, forceLabel: true })
    expect(mapEdgeAppearance(data, pal, { ...idle, hovered: true, lang: 'en' }).label).toBe('下げる (proposed)')
    expect(mapEdgeAppearance({ label: '変える' }, pal, { ...idle, hovered: true }).label).toBe('変える')
    expect(data.label).toBe('下げる')
  })
  it('選択・hoverは強調色、近傍外は薄色を優先し、提案の太さは維持する', () => {
    expect(mapEdgeAppearance({ thin: true }, pal, { ...idle, on: true })).toMatchObject({ color: pal.edgeOn, size: 0.75, label: '' })
    expect(mapEdgeAppearance({ thin: true }, pal, { ...idle, faded: true }).color).toBe(pal.faded)
    expect(mapEdgeAppearance({ thin: true }, pal, { ...idle, hovered: true, faded: true }).color).toBe(pal.edgeOn)
  })
})

// thin は確定論文の根拠線にも使われるため、提案状態に読み替えない。
describe('根拠と提案の意味の分離', () => {
  it('確定した根拠線は細くても確定色で、hoverに提案印を付けない', () => {
    const evidence = { kind: 'evidence', thin: true, label: '根拠' }
    expect(mapEdgeAppearance(evidence, pal, idle)).toMatchObject({ color: '#687e9b', size: 0.6 })
    expect(mapEdgeAppearance(evidence, pal, { ...idle, hovered: true }).label).toBe('根拠')
  })
  it('提案色と提案印はproposed属性だけで決まり、線幅と独立する', () => {
    const proposed = { proposed: true, label: '変える' }
    expect(mapEdgeAppearance(proposed, pal, idle)).toMatchObject({ color: '#8193ab', size: 1 })
    expect(mapEdgeAppearance(proposed, pal, { ...idle, hovered: true }).label).toBe('変える (提案)')
    expect(mapEdgeAppearance({ thin: true }, pal, idle).color).toBe('#687e9b')
  })
})

describe('地図モデルからの提案状態の受け渡し', () => {
  it('公開論文の根拠線と未公開論文の根拠線を同じthin属性から区別する', () => {
    const term = { iri: 'urn:term', slug: 'term', label: '用語', label_en: 'Term', kind: 'class' as const, members: [], standard: null, answering: 0, questions: [] }
    const paper = { id: 'confirmed', label: '確定論文', created_at: '2026-11-10', kinds: [], document: true, evidence_terms: ['urn:term'] }
    const graph = buildMapGraph({ ...EMPTY_SKY, terms: [term], datasets: [paper] }, [], [{ ...paper, id: 'proposed', proposed: true }], 'ja')
    const edges = graph.mapEdges((_edge, attrs, source, target) => ({ attrs, ids: [source, target] }))
    const fixed = edges.find((edge) => edge.ids.includes('d|confirmed'))!.attrs
    const proposed = edges.find((edge) => edge.ids.includes('d|proposed'))!.attrs
    expect(fixed.thin).toBe(true)
    expect(fixed.proposed).toBeUndefined()
    expect(proposed).toMatchObject({ thin: true, proposed: true })
    expect(mapEdgeAppearance(fixed, pal, idle).color).toBe('#687e9b')
    expect(mapEdgeAppearance(proposed, pal, idle).color).toBe('#8193ab')
  })
})
