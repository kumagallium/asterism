import { describe, expect, it } from 'vitest'
import {
  type AnswerRow,
  type ConceptRow,
  type RelationRow,
  type ThemeDraft,
  addConcept,
  addQuestion,
  answerComplete,
  answerState,
  applyDraftResponse,
  applyMatches,
  buildCommitBody,
  confirmCounts,
  conceptDismissKey,
  defaultLabel,
  dismissEndKey,
  hasConceptNamed,
  idList,
  knowledgeEstimate,
  matchSignature,
  normalizeDraft,
  parseQuestionLines,
  placeBlocker,
  reachableKeys,
  relationPredicate,
  removeConcept,
  removeRelation,
  rowProblems,
  sentConcepts,
  sentRelations,
  setAnswerTarget,
  setWay,
  standardIri,
  startManual,
  syncAnswers,
  unlinkedKeys,
  validateDraftResponse,
  wayOf,
} from './themeDraft'

const concept = (key: string, label: string, kind: 'class' | 'property', extra: Partial<ConceptRow> = {}): ConceptRow => ({
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
  ...extra,
})
const rel = (from: string, r: RelationRow['rel'], to: string, extra: Partial<RelationRow> = {}): RelationRow => ({
  from,
  rel: r,
  to,
  reason: '',
  problem: null,
  by: 'ai',
  ...extra,
})
const ans = (q: string, extra: Partial<AnswerRow> = {}): AnswerRow => ({
  question: q,
  mode: 'memo',
  op: '',
  target: '',
  direction: '',
  kind: '',
  property: '',
  value: '',
  memo: 'none',
  wanted_data: '',
  reason: '',
  by: 'ai',
  ...extra,
})

const Q = '熱伝導率を下げる要素になり得るのは？'
/** スタブ AI と同じ熱伝導率の例。 */
function thermal(): ThemeDraft {
  return {
    id: 'thermo',
    label: '熱電材料',
    step: 2,
    questions: [Q],
    concepts: [
      concept('c1', '熱伝導率', 'property', { use: 'standard:quantitykind:ThermalConductivity', slug: 'thermal_conductivity' }),
      concept('c2', '格子熱伝導率', 'property'),
      concept('c3', '電子熱伝導率', 'property'),
      concept('c4', '粒界散乱', 'class'),
      concept('c5', '点欠陥散乱', 'class'),
      concept('c6', 'キャリア濃度', 'property'),
      concept('c7', '試料', 'class', { use: 'existing:sample', slug: 'sample' }),
    ],
    relations: [
      rel('c2', 'part', 'c1'),
      rel('c3', 'part', 'c1'),
      rel('c4', 'lowers', 'c2'),
      rel('c5', 'lowers', 'c2'),
      rel('c6', 'raises', 'c3'),
      rel('c7', 'affects', 'c1'),
    ],
    answers: [
      ans(Q, { mode: 'knowledge', op: 'factors', target: 'c1', direction: 'lowers', memo: null, wanted_data: '試料ごとの 粒径・熱伝導率 の表' }),
    ],
    suggested_questions: [],
    standards: {},
    updated_at: '',
  }
}

describe('questions', () => {
  it('splits lines, trims and drops blanks and duplicates', () => {
    expect(parseQuestionLines('  A?  \n\nB?\nＡ?\n')).toEqual(['A?', 'B?'])
    expect(addQuestion(['A?'], 'A?')).toEqual(['A?'])
    expect(addQuestion(['A?'], 'B?')).toEqual(['A?', 'B?'])
  })
  it('names an empty theme after the first 20 characters of the first question', () => {
    expect(defaultLabel([Q, 'x'])).toBe('熱伝導率を下げる要素になり得るのは？')
    expect(defaultLabel(['あ'.repeat(30)])).toBe('あ'.repeat(20))
    expect(defaultLabel([])).toBe('')
  })
})

describe('the closed JSON v2', () => {
  const raw = {
    theme_label: '熱電材料',
    concepts: [{ key: 'c1', label_ja: '熱伝導率', label_en: 'k', aliases: ['kappa'], kind: 'property', use: 'new', slug: 'k', reason: 'r', match: null, by: 'ai' }],
    relations: [{ from: 'c1', rel: 'lowers', to: 'c1', reason: '', problem: 'same', by: 'ai' }],
    answers: [{ question: Q, mode: 'knowledge', op: 'factors', target: 'c1', direction: 'lowers', kind: null, property: null, memo: null, wanted_data: 'w', reason: '', by: 'ai' }],
    suggested_questions: ['別の問い？'],
    standards: { 'quantitykind:Foo': { label: 'Foo', kind: 'quantity' } },
    dropped: { concepts: [], relations: [] },
  }
  it('accepts the 6a shape and keeps problem marks', () => {
    const r = validateDraftResponse(raw)!
    expect(r.concepts[0].key).toBe('c1')
    expect(r.relations[0].problem).toBe('same')
    expect(r.answers[0].kind).toBe('') // null は空に
    expect(r.standards['quantitykind:Foo'].label).toBe('Foo')
  })
  it('refuses a shape it cannot read (the screen falls back to writing by hand)', () => {
    expect(validateDraftResponse(null)).toBeNull()
    expect(validateDraftResponse({ ...raw, concepts: 'x' })).toBeNull()
    expect(validateDraftResponse({ ...raw, concepts: [{ key: 'c1', label_ja: 'a', kind: 'weird' }] })).toBeNull()
    expect(validateDraftResponse({ ...raw, relations: [{ from: 'a', to: 'b', rel: 'sameAs' }] })).toBeNull()
  })
  it('puts the AI draft into the draft; a name the person typed wins over the AI name', () => {
    const base = { ...thermal(), label: '', concepts: [], relations: [], answers: [] }
    const res = validateDraftResponse(raw)!
    expect(applyDraftResponse(base, res).label).toBe('熱電材料')
    expect(applyDraftResponse({ ...base, label: '自分の名前' }, res).label).toBe('自分の名前')
    expect(applyDraftResponse(base, res).answers).toHaveLength(1)
  })
  it('normalizes a saved draft and keeps one answer row per question', () => {
    const d = normalizeDraft({ id: 'x', label: 'L', step: 9, questions: ['A?', 'B?'], concepts: [{ key: 'c1', label_ja: 'a', kind: 'class' }], relations: [], answers: [] })!
    expect(d.step).toBe(1)
    expect(d.answers.map((a) => a.question)).toEqual(['A?', 'B?'])
    expect(d.answers.every((a) => a.mode === 'memo')).toBe(true)
    expect(normalizeDraft({})).toBeNull()
  })
})

describe('answers', () => {
  it('syncs the answer rows with the questions', () => {
    const rows = syncAnswers([ans('A?', { mode: 'data', op: 'count', kind: 'k1' })], ['B?', 'A?'])
    expect(rows.map((a) => a.question)).toEqual(['B?', 'A?'])
    expect(rows[0].mode).toBe('memo')
    expect(rows[1].op).toBe('count')
  })
  it('knows when a way is decided, and reads the way back', () => {
    const a = thermal().answers[0]
    expect(answerComplete(a)).toBe(true)
    expect(wayOf(a)).toBe('lowers')
    expect(answerState(a)).toBe('knowledge')
    expect(answerState({ ...a, target: '' })).toBe('memo') // 対象が無ければ「まだ」
    expect(answerState(ans('x'))).toBe('memo')
  })
  it('changes the way and the target', () => {
    const a = ans('x')
    const raised = setWay(a, 'raises')
    expect(raised).toMatchObject({ mode: 'knowledge', op: 'factors', direction: 'raises', by: 'human' })
    expect(answerComplete(raised)).toBe(false)
    const decided = setAnswerTarget(raised, { target: 'c1' })
    expect(answerComplete(decided)).toBe(true)
    const count = setWay(decided, 'count')
    expect(count).toMatchObject({ mode: 'data', op: 'count', target: '' })
    expect(answerComplete(setAnswerTarget(count, { kind: 'c4' }))).toBe(true)
    expect(setWay(decided, 'memo')).toMatchObject({ mode: 'memo', memo: 'human', target: '' })
    // 範囲は項目が要る（種類は任意）
    expect(answerComplete(setAnswerTarget(setWay(a, 'range'), { property: 'c2' }))).toBe(true)
    expect(answerComplete(setAnswerTarget(setWay(a, 'top'), { kind: 'c4' }))).toBe(false)
  })
  it('estimates how many lines answer a knowledge question (one step through the parts)', () => {
    const d = thermal()
    // 熱伝導率を下げる: 格子・電子は「一部」なので、格子を下げる 2 本（粒界・点欠陥）が数に入る
    expect(knowledgeEstimate(d, d.answers[0])).toBe(2)
    // 変える: 下げる・上げる・変える の 3 述語すべて = 2（格子を下げる）+1（電子を上げる）+1（試料が熱伝導率を変える）
    expect(knowledgeEstimate(d, { ...d.answers[0], direction: 'affects' })).toBe(4)
    expect(knowledgeEstimate(d, { ...d.answers[0], op: 'parts', direction: '' })).toBe(2)
    expect(knowledgeEstimate(d, { ...d.answers[0], op: 'kinds', direction: '' })).toBe(0)
    expect(knowledgeEstimate(d, ans('x'))).toBe(0)
  })
})

describe('what the problem relations and the lonely concepts do', () => {
  it('reaches every concept of the thermal example from the question target', () => {
    const d = thermal()
    expect([...reachableKeys(d)].sort()).toEqual(['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c7'])
    expect(unlinkedKeys(d).size).toBe(0)
    expect(confirmCounts(d)).toEqual({ concepts: 6, relations: 6, total: 12 }) // 試料は既にある用語なので作らない
  })
  it('marks a concept that no relation joins to the question, and does not send it', () => {
    const d = { ...thermal(), concepts: [...thermal().concepts, concept('c8', '孤立した概念', 'class')] }
    expect([...unlinkedKeys(d)]).toEqual(['c8'])
    expect(sentConcepts(d).map((c) => c.key)).not.toContain('c8')
    expect(buildCommitBody(d, 'ja').concepts.map((c) => c.key)).not.toContain('c8')
  })
  it('does not send a relation the machine stopped, and what hangs on it is lonely', () => {
    const d = thermal()
    d.relations[2] = rel('c4', 'lowers', 'c2', { problem: 'target_not_property' })
    d.relations[4] = rel('c6', 'raises', 'c3', { problem: 'cycle' })
    expect(unlinkedKeys(d)).toEqual(new Set(['c4', 'c6'])) // c5 は別の線でつながる
    expect(sentRelations(d)).toHaveLength(4)
    expect(buildCommitBody(d, 'ja').relations).toHaveLength(4)
  })
  it('starts from a data question: a hand-added kind that a count points at is connected by itself', () => {
    let d: ThemeDraft = { ...thermal(), concepts: [], relations: [], answers: [ans('件数は？')] }
    d = addConcept(d, '記録', 'class')
    expect(unlinkedKeys(d)).toEqual(new Set(['h1']))
    d = { ...d, answers: [setAnswerTarget(setWay(d.answers[0], 'count'), { kind: 'h1' })] }
    expect(unlinkedKeys(d).size).toBe(0)
  })
  it('ties two concepts that share a standard term (the api counts that line too)', () => {
    const d: ThemeDraft = {
      ...thermal(),
      concepts: [
        concept('a', '主', 'property', { use: 'standard:quantitykind:X' }),
        concept('b', '従', 'property', { use: 'standard:quantitykind:X' }),
      ],
      relations: [],
      answers: [ans(Q, { mode: 'knowledge', op: 'parts', target: 'a', memo: null })],
    }
    expect(unlinkedKeys(d).size).toBe(0)
  })
})

describe('editing the map', () => {
  it('removes a concept with its relations and sends the answers that pointed at it back to "not yet"', () => {
    const next = removeConcept(thermal(), 'c1')
    expect(next.concepts.some((c) => c.key === 'c1')).toBe(false)
    expect(next.relations.every((r) => r.from !== 'c1' && r.to !== 'c1')).toBe(true)
    expect(next.answers[0].mode).toBe('memo')
    expect(next.answers[0].wanted_data).toBe('試料ごとの 粒径・熱伝導率 の表')
  })
  it('removes one relation', () => {
    expect(removeRelation(thermal(), 0).relations).toHaveLength(5)
  })
  it('adds a hand concept once per name', () => {
    const d = addConcept(thermal(), '  新しい概念 ', 'class')
    expect(d.concepts.at(-1)).toMatchObject({ key: 'h1', label_ja: '新しい概念', by: 'human', use: 'new' })
    expect(addConcept(d, '新しい概念', 'property')).toBe(d)
    expect(hasConceptNamed(d.concepts, '熱伝導率')).toBe(true)
    expect(addConcept(d, '   ', 'class')).toBe(d)
  })
  it('applies the machine match to a hand row, but never over a choice the person made', () => {
    const row = concept('h1', '記録', 'class', { by: 'human', slug: '', use: 'new' })
    const sig = { h1: matchSignature(row) }
    const res = [{ key: 'h1', match: { kind: 'existing' as const, ref: 'record', label: '記録', matched_by: 'label' as const }, use: 'existing:record', slug: 'record' }]
    const [m] = applyMatches([row], res, sig)
    expect(m).toMatchObject({ use: 'existing:record', slug: 'record', use_by: 'machine' })
    const [kept] = applyMatches([{ ...row, use_by: 'human' }], res, sig)
    expect(kept.use).toBe('new')
    // 打ち直された行は反映しない
    const [typed] = applyMatches([{ ...row, label_ja: '別' }], res, sig)
    expect(typed.use).toBe('new')
  })
})

describe('placing (step 3)', () => {
  it('lists the IDs of the concepts that will be made, and finds bad ones', () => {
    const d = thermal()
    expect(idList(d).map((l) => l.key)).toEqual(['c1', 'c2', 'c3', 'c4', 'c5', 'c6']) // 試料(既存)は作らない
    expect(placeBlocker(d, new Set())).toBeNull()
    d.concepts[1] = { ...d.concepts[1], slug: 'Bad Slug' }
    expect(placeBlocker(d, new Set())).toBe('slug')
    d.concepts[1] = { ...d.concepts[1], slug: 'thermal_conductivity' }
    expect(rowProblems(sentConcepts(d), new Set())).toEqual({ c2: 'duplicate' })
    expect(rowProblems(sentConcepts(d), new Set(['thermal_conductivity']))).toMatchObject({ c1: 'taken' })
  })
  it('builds the commit body the api asks for (6a)', () => {
    const body = buildCommitBody(thermal(), 'ja')
    expect(body).toMatchObject({ label: '熱電材料', lang: 'ja', ai_drafted: true, questions: [Q] })
    expect(body.concepts[0]).toEqual({
      key: 'c1', label_ja: '熱伝導率', label_en: '', aliases: [], kind: 'property',
      use: 'standard:quantitykind:ThermalConductivity', slug: 'thermal_conductivity', by: 'ai',
    })
    // 既にある用語の行は ID を送らない
    expect(body.concepts.find((c) => c.key === 'c7')).toMatchObject({ use: 'existing:sample', slug: '' })
    expect(body.relations[2]).toEqual({ from: 'c4', rel: 'lowers', to: 'c2', reason: '', by: 'ai' })
    expect(body.answers[0]).toMatchObject({ mode: 'knowledge', op: 'factors', target: 'c1', direction: 'lowers', memo: null })
    expect('batch' in body).toBe(false)
  })
  it('sends an undecided answer as a memo question, not dropped', () => {
    const d = { ...thermal(), answers: [ans(Q, { mode: 'knowledge', op: 'factors', direction: 'lowers' })] }
    expect(buildCommitBody(d, 'ja').answers[0]).toMatchObject({ mode: 'memo', memo: 'none' })
  })
  it('falls back to the first 20 characters when no name was given, and carries extend', () => {
    const body = buildCommitBody({ ...thermal(), label: ' ', extend: 'first' }, 'en')
    expect(body.label).toBe(defaultLabel([Q]))
    expect(body.extend).toBe('first')
    expect(body.lang).toBe('en')
  })
  it('manual mode keeps one "not yet" row per question and a default name', () => {
    const d = startManual({ ...thermal(), label: '', concepts: [], relations: [], answers: [] })
    expect(d.label).toBe(defaultLabel([Q]))
    expect(d.answers).toHaveLength(1)
    expect(d.answers[0].mode).toBe('memo')
  })
})

describe('dismissal keys (the ✕ is kept for the next draft)', () => {
  it('uses the same rule as the api: concept:<normalized name>, IRI for existing and standard ends', () => {
    expect(conceptDismissKey('  粒界　散乱 ')).toBe('concept:粒界 散乱')
    const d = thermal()
    expect(dismissEndKey(d.concepts[3], 'c4')).toBe('concept:粒界散乱')
    expect(dismissEndKey(d.concepts[6], 'c7')).toBe('https://kumagallium.github.io/asterism/vocab/shared#sample')
    expect(standardIri('quantitykind:ThermalConductivity')).toBe('http://qudt.org/vocab/quantitykind/ThermalConductivity')
    expect(standardIri('sosa:Sample')).toBe('http://www.w3.org/ns/sosa/Sample')
    expect(standardIri('nope:x')).toBeNull()
    // 概念でない端（標準の語の curie）は IRI に
    expect(dismissEndKey(undefined, 'sosa:Sample')).toBe('http://www.w3.org/ns/sosa/Sample')
    // 標準の語に一致した新しい概念は標準の語の IRI
    expect(
      dismissEndKey(concept('c', 'x', 'class', { match: { kind: 'standard', ref: 'sosa:Sample', label: 'Sample', matched_by: 'label' } }), 'c'),
    ).toBe('http://www.w3.org/ns/sosa/Sample')
  })
  it('maps the draft relations to the predicate names of the api', () => {
    expect(relationPredicate('sub', 'class')).toBe('subClassOf')
    expect(relationPredicate('sub', 'property')).toBe('subPropertyOf')
    expect(relationPredicate('eq', 'class')).toBe('equivalentClass')
    expect(relationPredicate('eq', 'property')).toBe('equivalentProperty')
    expect(relationPredicate('quantity', 'property')).toBe('hasQuantityKind')
    expect(relationPredicate('part', 'class')).toBe('isPartOf')
    expect(relationPredicate('lowers', 'class')).toBe('lowers')
    expect(relationPredicate('raises', 'property')).toBe('raises')
    expect(relationPredicate('affects', 'property')).toBe('affects')
  })
})
