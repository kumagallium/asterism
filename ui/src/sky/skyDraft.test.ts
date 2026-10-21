import { describe, expect, it } from 'vitest'
import type { GraphOp } from '../themes/consultGraphApi'
import type { ConceptRow, RelationRow, ThemeDraft } from '../themes/themeDraft'
import { NO_PENDING, buildCanvas, pendingOf, relId } from '../workspace/workspaceState'
import { draftSkyOf, edgeIndexOf, NEW_DRAFT_ID, relOfSkyEdge, retargetKeyOf, skyEdgeId } from './skyDraft'
import { EMPTY_SKY } from './skyApi'
import { draftNodeId, layoutSky, termId } from './skyLayout'
import { makeSky } from './skyFixture'

const concept = (key: string, label: string, kind: 'class' | 'property' = 'property', use = 'new'): ConceptRow => ({
  key,
  label_ja: label,
  label_en: '',
  aliases: [],
  kind,
  use,
  slug: `s_${key}`,
  reason: '',
  match: null,
  by: 'ai',
})
const rel = (from: string, r: RelationRow['rel'], to: string): RelationRow => ({ from, rel: r, to, reason: '', problem: null, by: 'ai' })

// 熱伝導率の例: 格子は熱伝導率の一部・散乱因子 2 つが熱伝導率を下げる
const draft: ThemeDraft = {
  id: 'd1',
  label: '熱電',
  step: 2,
  questions: ['熱伝導率を下げる要素は？'],
  concepts: [concept('c1', '熱伝導率'), concept('c2', '格子熱伝導率'), concept('c4', '粒界散乱', 'class'), concept('c5', '点欠陥散乱', 'class')],
  relations: [rel('c2', 'part', 'c1'), rel('c4', 'lowers', 'c1'), rel('c5', 'lowers', 'c1')],
  answers: [],
  suggested_questions: [],
  standards: {},
  updated_at: '',
}
const op = (o: GraphOp): GraphOp => o

describe('draftSkyOf — 作業中の下書きを提案の札・提案の線にする', () => {
  it('全部が提案（薄い）なら、札も線も thin', () => {
    const d = draftSkyOf(draft, buildCanvas(draft, pendingOf(draft), []), 'ja')
    expect(d.draft_id).toBe('d1')
    expect(d.theme_label).toBe('熱電')
    expect(d.concepts.map((c) => c.state)).toEqual(['thin', 'thin', 'thin', 'thin'])
    expect(d.relations.map((r) => r.state)).toEqual(['thin', 'thin', 'thin'])
    expect(d.relations.map((r) => r.index)).toEqual([0, 1, 2])
  })

  it('図に入れたものは solid（本物の線の両端も solid）。薄いまま残る行は thin', () => {
    const pending = { concepts: ['c5'], relations: [relId(draft.relations[2])] }
    const d = draftSkyOf(draft, buildCanvas(draft, pending, []), 'ja')
    expect(Object.fromEntries(d.concepts.map((c) => [c.key, c.state]))).toEqual({ c1: 'solid', c2: 'solid', c4: 'solid', c5: 'thin' })
    expect(d.relations.map((r) => r.state)).toEqual(['solid', 'solid', 'thin'])
  })

  it('返事を待つ提案カードの差分が、薄い札・薄い線・付け替え・外す提案として重なる', () => {
    const ghost: GraphOp[] = [
      op({ op: 'add_concept', key: 'n1', label_ja: 'ラットリング', kind: 'class', use: 'new', reason: '' }),
      op({ op: 'add_relation', from: 'n1', rel: 'lowers', to: 'c2', reason: '' }),
      op({ op: 'retarget', from: 'c4', rel: 'lowers', to: 'c1', new_to: 'c2', reason: '' }),
      op({ op: 'remove', target: 'concept', key: 'c5' }),
    ]
    const d = draftSkyOf(draft, buildCanvas(draft, NO_PENDING, ghost), 'ja')
    const n1 = d.concepts.find((c) => c.key === 'n1')
    expect(n1).toMatchObject({ state: 'thin', proposed: true, label: 'ラットリング' })
    expect(d.concepts.find((c) => c.key === 'c5')?.state).toBe('remove')
    const by = (from: string, to: string) => d.relations.filter((r) => r.from === from && r.to === to)
    expect(by('n1', 'c2')[0]).toMatchObject({ state: 'thin', index: null })
    expect(by('c4', 'c1')[0].state).toBe('moved')
    expect(by('c4', 'c2')[0]).toMatchObject({ state: 'retarget', index: null })
    // 外す概念につながる線も外す提案
    expect(by('c5', 'c1')[0].state).toBe('remove')
  })

  it('保存の前（id が無い）は NEW_DRAFT_ID・英語なら英語名', () => {
    const en = { ...draft, id: '', concepts: draft.concepts.map((c) => ({ ...c, label_en: `${c.key} en` })) }
    const d = draftSkyOf(en, buildCanvas(en, NO_PENDING, []), 'en')
    expect(d.draft_id).toBe(NEW_DRAFT_ID)
    expect(d.concepts[0].label).toBe('c1 en')
  })

  it('星図の配置に載せると、提案の札と線になる（薄い→濃い）', () => {
    const thin = layoutSky(EMPTY_SKY, { width: 960, drafts: [draftSkyOf(draft, buildCanvas(draft, pendingOf(draft), []), 'ja')] })
    const solid = layoutSky(EMPTY_SKY, { width: 960, drafts: [draftSkyOf(draft, buildCanvas(draft, NO_PENDING, []), 'ja')] })
    expect(thin.edges.filter((e) => e.kind === 'draft')).toHaveLength(3)
    expect(solid.edges.filter((e) => e.kind === 'draft').every((e) => e.draft?.state === 'solid')).toBe(true)
    expect((thin.nodes.get(draftNodeId('d1', 'c1')) as { state: string }).state).toBe('thin')
    expect((solid.nodes.get(draftNodeId('d1', 'c1')) as { state: string }).state).toBe('solid')
  })
})

describe('retargetKeyOf — 線の端を落とした札が、下書きのどの概念か', () => {
  const sky = makeSky(2, 8)
  const t = sky.terms[0]
  const withExisting: ThemeDraft = { ...draft, concepts: [...draft.concepts, concept('e1', t.label, t.kind, `existing:${t.slug}`)] }
  const model = buildCanvas(withExisting, NO_PENDING, [op({ op: 'add_concept', key: 'n1', label_ja: '提案', kind: 'class', use: 'new', reason: '' })])
  const l = layoutSky(sky, { width: 1000, drafts: [draftSkyOf(withExisting, model, 'ja')] })

  it('提案の札は自分の key', () => {
    expect(retargetKeyOf(l.nodes.get(draftNodeId('d1', 'c2')), withExisting, sky.terms, 'd1')).toBe('c2')
  })
  it('既にある用語の札は、下書きがその用語を使う概念の key（使っていなければ null）', () => {
    expect(retargetKeyOf(l.nodes.get(termId(t.iri)), withExisting, sky.terms, 'd1')).toBe('e1')
    expect(retargetKeyOf(l.nodes.get(termId(sky.terms[1].iri)), withExisting, sky.terms, 'd1')).toBeNull()
  })
  it('返事を待つ提案の札・ほかの下書きの札・星・無い点は落とし先にしない', () => {
    expect(retargetKeyOf(l.nodes.get(draftNodeId('d1', 'n1')), withExisting, sky.terms, 'd1')).toBeNull()
    expect(retargetKeyOf(l.nodes.get(draftNodeId('d1', 'c2')), withExisting, sky.terms, 'other')).toBeNull()
    const star = [...l.nodes.values()].find((n) => n.type === 'kind')
    expect(retargetKeyOf(star, withExisting, sky.terms, 'd1')).toBeNull()
    expect(retargetKeyOf(undefined, withExisting, sky.terms, 'd1')).toBeNull()
    expect(retargetKeyOf(l.nodes.get(draftNodeId('d1', 'c2')), null, sky.terms, 'd1')).toBeNull()
  })
})

describe('edgeIndexOf — 選んだ線の関係の添字', () => {
  it('draftId|from|rel|to で引く。直して無くなった線・何も選んでいないときは -1', () => {
    expect(edgeIndexOf(draft, 'd1|c4|lowers|c1', 'd1')).toBe(1)
    expect(edgeIndexOf(draft, 'd1|c4|lowers|c2', 'd1')).toBe(-1)
    expect(edgeIndexOf(draft, null, 'd1')).toBe(-1)
    expect(edgeIndexOf(null, 'd1|c4|lowers|c1', 'd1')).toBe(-1)
  })
  it('開いていない下書きの線は、同じ from|rel|to でも開いている下書きの関係にしない', () => {
    expect(edgeIndexOf(draft, 'old|c4|lowers|c1', 'd1')).toBe(-1)
    expect(edgeIndexOf(draft, 'd1|c4|lowers|c1', '')).toBe(-1)
    expect(relOfSkyEdge('d1|c4|lowers|c1', 'd1')).toBe('c4|lowers|c1')
    expect(relOfSkyEdge('old|c4|lowers|c1', 'd1')).toBeNull()
    expect(skyEdgeId('d1', 'c4|lowers|c1')).toBe('d1|c4|lowers|c1')
  })
})
