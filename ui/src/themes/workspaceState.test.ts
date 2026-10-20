import { describe, expect, it } from 'vitest'
import type { GraphOp } from './consultGraphApi'
import {
  type ConceptRow,
  type RelationRow,
  type ThemeDraft,
  buildCommitBody,
  confirmCounts,
  unlinkedKeys,
} from './themeDraft'
import {
  NO_PENDING,
  acceptRelations,
  buildCanvas,
  dropReason,
  dropsByOriginal,
  expandMessage,
  ghostOpsOf,
  historyOf,
  newProposalItem,
  openRows,
  restoreItems,
  loadPending,
  newProposalId,
  nextConceptKey,
  opKeys,
  pendingOf,
  pendingTotal,
  realDraft,
  relId,
  relationEditSteps,
  savePending,
  settlePending,
  withDependencies,
} from './workspaceState'

const concept = (key: string, label: string, kind: 'class' | 'property' = 'property'): ConceptRow => ({
  key,
  label_ja: label,
  label_en: '',
  aliases: [],
  kind,
  use: 'new',
  slug: `s_${key}`,
  reason: '',
  match: null,
  by: 'ai',
})
const rel = (from: string, r: RelationRow['rel'], to: string): RelationRow => ({
  from,
  rel: r,
  to,
  reason: '',
  problem: null,
  by: 'ai',
})

// 熱伝導率の例: 散乱因子が（まだ）熱伝導率を下げる線 3 本・格子は熱伝導率の一部
const draft: ThemeDraft = {
  id: 'd1',
  label: '熱電',
  step: 2,
  questions: ['熱伝導率を下げる要素は？'],
  concepts: [
    concept('c1', '熱伝導率'),
    concept('c2', '格子熱伝導率'),
    concept('c4', '粒界散乱', 'class'),
    concept('c5', '点欠陥散乱', 'class'),
  ],
  relations: [rel('c2', 'part', 'c1'), rel('c4', 'lowers', 'c1'), rel('c5', 'lowers', 'c1')],
  answers: [
    {
      question: '熱伝導率を下げる要素は？',
      mode: 'knowledge',
      op: 'factors',
      target: 'c1',
      direction: 'lowers',
      kind: '',
      property: '',
      value: '',
      memo: null,
      wanted_data: '',
      reason: '',
      by: 'ai',
    },
  ],
  suggested_questions: [],
  standards: {},
  updated_at: '',
}

const retarget = (from: string, to: string, newTo: string): GraphOp => ({
  op: 'retarget',
  from,
  rel: 'lowers',
  to,
  new_to: newTo,
  reason: '格子の分',
})

describe('proposals (thin) versus real rows', () => {
  it('starts with every concept and relation of the first draft as a proposal, and counts nothing as confirmed', () => {
    const p = pendingOf(draft)
    expect(pendingTotal(p)).toBe(4 + 3)
    const real = realDraft(draft, p)
    expect(real.concepts).toEqual([])
    expect(real.relations).toEqual([])
    expect(confirmCounts(real).total).toBe(0)
  })

  it('"そう（全部）" makes everything real: the confirm count is the whole draft', () => {
    const real = realDraft(draft, NO_PENDING)
    expect(real).toBe(draft)
    expect(confirmCounts(real).total).toBe(4 + 3)
  })

  it('accepting a relation makes both of its ends real (a real line never has a thin end)', () => {
    const p = acceptRelations(pendingOf(draft), [relId(draft.relations[1])], draft)
    expect(p.relations).not.toContain('c4|lowers|c1')
    expect(p.concepts).not.toContain('c4')
    expect(p.concepts).not.toContain('c1')
    const real = realDraft(draft, p)
    expect(real.concepts.map((c) => c.key).sort()).toEqual(['c1', 'c4'])
    expect(real.relations.map(relId)).toEqual(['c4|lowers|c1'])
    // 送るのは本物だけ（c2・c5 は薄いまま）
    expect(buildCommitBody(real, 'ja').concepts.map((c) => c.key).sort()).toEqual(['c1', 'c4'])
  })

  it('a relation between a real and a still-thin concept stays out of the count', () => {
    const p = { concepts: ['c2'], relations: [] }
    const real = realDraft(draft, p)
    expect(real.relations.map(relId)).toEqual(['c4|lowers|c1', 'c5|lowers|c1'])
    expect(real.concepts.map((c) => c.key)).not.toContain('c2')
  })

  it('applying retargets makes the touched concepts real and forgets lines that are gone', () => {
    const applied = [retarget('c4', 'c1', 'c2'), retarget('c5', 'c1', 'c2')]
    const after: ThemeDraft = {
      ...draft,
      relations: [rel('c2', 'part', 'c1'), rel('c4', 'lowers', 'c2'), rel('c5', 'lowers', 'c2')],
    }
    const p = settlePending(pendingOf(draft), applied, after)
    expect(p.concepts).toEqual([]) // c1・c2・c4・c5 はどれも差分が触れた → 本物
    expect(p.relations).toEqual([]) // 触れた 2 つ（c1・c2）の間の線も本物
    expect(realDraft(after, p).relations.map(relId)).toEqual(['c2|part|c1', 'c4|lowers|c2', 'c5|lowers|c2'])
    // 触れていない概念の線は薄いまま
    const q = settlePending(pendingOf(draft), [retarget('c4', 'c1', 'c2')], after)
    expect(q.concepts).toEqual(['c5'])
    expect(realDraft(after, q).relations.map(relId)).toEqual(['c2|part|c1', 'c4|lowers|c2'])
  })

  it('removing a concept drops it from the proposal state', () => {
    const after: ThemeDraft = { ...draft, concepts: draft.concepts.filter((c) => c.key !== 'c5'), relations: draft.relations.slice(0, 2) }
    const p = settlePending(pendingOf(draft), [{ op: 'remove', target: 'concept', key: 'c5', reason: '' }], after)
    expect(p.concepts).not.toContain('c5')
    expect(p.relations).not.toContain('c5|lowers|c1')
  })

  it('unlinked marks only real concepts', () => {
    const real = realDraft(draft, { concepts: ['c5'], relations: [] })
    expect(unlinkedKeys(real).has('c5')).toBe(false)
  })
})

describe('proposal state is kept per draft on this device', () => {
  const mem = () => {
    const m = new Map<string, string>()
    return {
      getItem: (k: string) => m.get(k) ?? null,
      setItem: (k: string, v: string) => void m.set(k, v),
      removeItem: (k: string) => void m.delete(k),
      raw: m,
    }
  }
  it('round-trips, removes the key when nothing is thin, and survives a broken value', () => {
    const s = mem()
    savePending('d1', { concepts: ['c1'], relations: ['a|sub|b'] }, s)
    expect(loadPending('d1', s)).toEqual({ concepts: ['c1'], relations: ['a|sub|b'] })
    expect(loadPending('other', s)).toEqual(NO_PENDING)
    savePending('d1', NO_PENDING, s)
    expect(s.raw.size).toBe(0)
    s.setItem('asterism.themePending.d2', '{broken')
    expect(loadPending('d2', s)).toEqual(NO_PENDING)
    expect(loadPending('', s)).toEqual(NO_PENDING)
  })
})

describe('the thin overlay on the figure', () => {
  it('draws first-draft rows and proposals thin without changing the draft', () => {
    const before = JSON.stringify(draft)
    const m = buildCanvas(draft, { concepts: ['c5'], relations: [] }, [
      { op: 'add_concept', key: 'n1', label_ja: 'ナノ析出物', label_en: '', aliases: [], kind: 'class', use: 'new', reason: '' },
      { op: 'add_relation', from: 'n1', rel: 'lowers', to: 'c2', reason: '' },
    ])
    expect(JSON.stringify(draft)).toBe(before)
    expect(m.concepts.find((c) => c.row.key === 'c5')!.ghost).toBe('add') // 提案のまま
    expect(m.concepts.find((c) => c.row.key === 'c4')!.ghost).toBeUndefined()
    expect(m.concepts.find((c) => c.row.key === 'n1')!.ghost).toBe('add')
    // c5 の線は端が薄いので薄い
    expect(m.relations.find((r) => r.row.from === 'c5')!.ghost).toBe('add')
    const added = m.relations.find((r) => r.row.from === 'n1')!
    expect(added.ghost).toBe('add')
    expect(added.draftIndex).toBeNull()
  })

  it('a retarget shows the old line as moving away and a thin new line', () => {
    const m = buildCanvas(draft, NO_PENDING, [retarget('c4', 'c1', 'c2')])
    const old = m.relations.find((r) => r.draftIndex !== null && relId(r.row) === 'c4|lowers|c1')!
    expect(old.ghost).toBe('moved')
    const next = m.relations.find((r) => r.ghost === 'retarget')!
    expect(relId(next.row)).toBe('c4|lowers|c2')
    expect(m.relations.length).toBe(draft.relations.length + 1)
  })

  it('a removal strikes out the concept and the lines that go with it', () => {
    const m = buildCanvas(draft, NO_PENDING, [{ op: 'remove', target: 'concept', key: 'c4', reason: '' }])
    expect(m.concepts.find((c) => c.row.key === 'c4')!.ghost).toBe('remove')
    expect(m.relations.find((r) => r.row.from === 'c4')!.ghost).toBe('remove')
    expect(m.relations.find((r) => r.row.from === 'c5')!.ghost).toBeUndefined()
    const m2 = buildCanvas(draft, NO_PENDING, [{ op: 'remove', target: 'relation', from: 'c5', rel: 'lowers', to: 'c1', reason: '' }])
    expect(m2.relations.find((r) => r.row.from === 'c5')!.ghost).toBe('remove')
  })

  it('ignores a proposal that points at nothing on the figure', () => {
    const m = buildCanvas(draft, NO_PENDING, [retarget('zz', 'c1', 'c2')])
    expect(m.relations.filter((r) => r.ghost === 'moved')).toEqual([])
  })
})

describe('direct edits become the same closed ops', () => {
  const old = rel('c4', 'lowers', 'c1')
  it('changing only B is one retarget', () => {
    expect(relationEditSteps(old, { from: 'c4', rel: 'lowers', to: 'c2' })).toEqual([
      [{ op: 'retarget', from: 'c4', rel: 'lowers', to: 'c1', new_to: 'c2', reason: '' }],
    ])
  })
  it('changing A or the relation adds the new line first, then removes the old one (a line is never lost)', () => {
    const steps = relationEditSteps(old, { from: 'c5', rel: 'raises', to: 'c1' })
    expect(steps.map((s) => s.map((o) => o.op))).toEqual([['add_relation'], ['remove']])
    expect(steps[1][0]).toMatchObject({ target: 'relation', from: 'c4', rel: 'lowers', to: 'c1' })
  })
  it('nothing changed → nothing to send', () => {
    expect(relationEditSteps(old, { from: 'c4', rel: 'lowers', to: 'c1' })).toEqual([])
  })
  it('proposal ids are unique and in the shape the api accepts', () => {
    const a = newProposalId()
    const b = newProposalId()
    expect(a).not.toBe(b)
    expect(a).toMatch(/^[A-Za-z0-9_.:-]{1,60}$/)
  })
  it('hand-made concept keys avoid the ones in use', () => {
    expect(nextConceptKey(['c1', 'h1', 'h2'])).toBe('h3')
  })
})

describe('reading the apply result', () => {
  it('maps a dropped index of a partial send back to the original op', () => {
    const m = dropsByOriginal([{ index: 1, op: 'retarget', reason: 'cycle', ref: 'x' }], [0, 2, 3])
    expect(m.get(2)?.reason).toBe('cycle')
    expect(m.size).toBe(1)
  })
  it('unknown reasons read as unsupported', () => {
    expect(dropReason('cycle')).toBe('cycle')
    expect(dropReason('???')).toBe('unsupported')
  })
  it('ticking one line also sends the new concept it needs (only if not decided yet)', () => {
    const ops: GraphOp[] = [
      { op: 'add_concept', key: 'n1', label_ja: 'ナノ', label_en: '', aliases: [], kind: 'class', use: 'new', reason: '' },
      { op: 'add_concept', key: 'n2', label_ja: '別', label_en: '', aliases: [], kind: 'class', use: 'new', reason: '' },
      { op: 'add_relation', from: 'n1', rel: 'lowers', to: 'c2', reason: '' },
    ]
    expect(withDependencies(ops, 2, [null, null, null])).toEqual([0, 2])
    expect(withDependencies(ops, 2, ['yes', null, null])).toEqual([2])
  })
  it('opKeys reads the concept keys a op names, not the word "class"', () => {
    expect(opKeys({ op: 'add_concept', key: 'n1', kind: 'class' })).toEqual(['n1'])
    expect(opKeys({ op: 'remove', target: 'concept', key: 'c4' })).toEqual(['c4'])
    expect(opKeys(retarget('c4', 'c1', 'c2'))).toEqual(['c4', 'c1', 'c2'])
  })
  it('the expand message follows the language', () => {
    expect(expandMessage('格子熱伝導率', 'ja')).toBe('格子熱伝導率 の周りを広げて')
    expect(expandMessage('Lattice', 'en')).toBe('Expand around Lattice')
  })
})

describe('the conversation items', () => {
  const op1 = retarget('c4', 'c1', 'c2')
  const op2 = retarget('c5', 'c1', 'c2')

  it('only undecided rows of waiting proposals are drawn thin on the figure', () => {
    const a = newProposalItem({ proposalId: 'p1', ops: [op1, op2], sentences: ['s1', 's2'], by: 'ai' })
    const b = { ...newProposalItem({ proposalId: 'p2', ops: [op1], sentences: ['s'], by: 'ai' }), decided: ['yes' as const] }
    expect(openRows(a)).toEqual([0, 1])
    expect(ghostOpsOf([a, b])).toEqual([op1, op2])
    const half = { ...a, decided: ['yes' as const, null] }
    expect(ghostOpsOf([half])).toEqual([op2])
  })

  it('sends the person’s words and the partner’s prose only (never a proposal card)', () => {
    const items = [
      { kind: 'user' as const, id: '1', text: '散乱は格子に紐づく気がする' },
      { kind: 'ai' as const, id: '2', text: '格子の分に効きます' },
      newProposalItem({ proposalId: 'p', ops: [op1], sentences: ['s'], by: 'ai' }),
      { kind: 'ai' as const, id: '3', text: '  ' },
    ]
    expect(historyOf(items)).toEqual([
      { role: 'user', content: '散乱は格子に紐づく気がする' },
      { role: 'assistant', content: '格子の分に効きます' },
    ])
  })

  it('restores a saved conversation in time order, a person’s words before the proposal they led to', () => {
    const items = restoreItems(
      {
        messages: [
          { role: 'user', content: '一言目', at: '2026-10-20T10:00:00Z' },
          { role: 'user', content: '二言目', at: '2026-10-20T10:05:00Z' },
        ],
        proposals: [
          { proposal_id: 'p1', ops: [op1], at: '2026-10-20T10:00:00Z', by: 'ai' },
          { proposal_id: 'h-1', ops: [op2], at: '2026-10-20T10:06:00Z', by: 'human' },
        ],
      },
      (o) => `sentence of ${String(o.from)}`,
    )
    expect(items.map((i) => (i.kind === 'user' ? i.text : i.kind === 'proposal' ? `P:${i.proposalId}` : '?'))).toEqual([
      '一言目',
      'P:p1',
      '二言目',
      'P:h-1',
    ])
    const first = items[1]
    expect(first.kind === 'proposal' && first.decided).toEqual(['yes'])
    expect(first.kind === 'proposal' && first.sentences).toEqual(['sentence of c4'])
    expect(restoreItems(undefined, () => '')).toEqual([])
  })
})
