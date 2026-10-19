import { describe, expect, it } from 'vitest'
import { slugFromText } from '../vocabQuestion'
import {
  type DraftResponse,
  type RelationRow,
  type SelectionRow,
  type TermRow,
  type ThemeDraft,
  acceptedRelations,
  addGroup,
  addQuestion,
  answeringNow,
  applyDraftResponse,
  applyMatches,
  buildCommitBody,
  canAdvance,
  chooseUse,
  idList,
  implicitRelations,
  isMemo,
  matchSignature,
  memoReason,
  newTermRow,
  parseQuestionLines,
  placeBlocker,
  placeSummary,
  relationApplies,
  relationsReady,
  removeRow,
  renameRow,
  rowProblems,
  selectionComplete,
  selectionFits,
  selectionPayload,
  selectionsReady,
  setRelation,
  setSelection,
  startManual,
  suggestRowSlug,
  syncSelections,
  termMints,
  termUseOptions,
  upperStructure,
  validateDraftResponse,
  visibleGroups,
} from './themeDraft'

// 架空の語だけを使う（分野語ゼロ）。

function term(key: string, over: Partial<TermRow> = {}): TermRow {
  return {
    key,
    label_ja: `語${key}`,
    label_en: `word ${key}`,
    aliases: [],
    kind: 'class',
    use: 'new',
    slug: `word_${key}`,
    reason: '',
    match: null,
    by: 'ai',
    ...over,
  }
}

function rel(subject: string, object: string, over: Partial<RelationRow> = {}): RelationRow {
  return { subject, object, relation: 'sub', problem: null, by: 'ai', ok: null, ...over }
}

function sel(question: string, over: Partial<SelectionRow> = {}): SelectionRow {
  return {
    question,
    op: '',
    kind: '',
    property: '',
    value: '',
    fits: false,
    memo: 'none',
    reason: '',
    by: 'ai',
    ...over,
  }
}

function draft(over: Partial<ThemeDraft> = {}): ThemeDraft {
  return {
    id: 'd1',
    label: 'テーマ',
    step: 1,
    questions: ['問い A', '問い B'],
    ai: null,
    terms: [],
    relations: [],
    selections: [],
    updated_at: '',
    ...over,
  }
}

const CTX = { existingSlugs: new Set<string>() }

describe('① 問いの入力', () => {
  it('1 行 1 問・空と重複（大小・全半角の違い）を落とす', () => {
    expect(parseQuestionLines('  問い A \n\n問い  B\nＡｂｃ\nabc\n')).toEqual(['問い A', '問い B', 'Ａｂｃ'])
    expect(parseQuestionLines('')).toEqual([])
  })

  it('AI の案を足す: すでにあれば足さない', () => {
    expect(addQuestion(['a'], 'b')).toEqual(['a', 'b'])
    expect(addQuestion(['a'], ' A ')).toEqual(['a'])
  })
})

describe('slug の導出（サーバの themes.slug_from_text と同じ例）', () => {
  it.each([
    ['Sample composition', 'sample_composition'],
    ['hasSeebeckCoefficient', 'has_seebeck_coefficient'],
    ['3D shape', 'x_3d_shape'],
    ['試料', ''],
    ['battery cell', 'battery_cell'],
    ['  Cycle  number!! ', 'cycle_number'],
    ['XMLHttpRequest', 'xml_http_request'],
    ['café', 'cafe'],
    ['', ''],
  ])('%s → %s', (text, slug) => {
    expect(slugFromText(text)).toBe(slug)
  })

  it('行の ID の既定: 英語名 → 別名の順・既存と重なれば _2', () => {
    const row = { label_en: 'Battery cell', aliases: ['cell'] }
    expect(suggestRowSlug(row, [])).toBe('battery_cell')
    expect(suggestRowSlug(row, ['battery_cell'])).toBe('battery_cell_2')
    expect(suggestRowSlug({ label_en: '', aliases: ['col_name'] }, [])).toBe('col_name')
    expect(suggestRowSlug({ label_en: '', aliases: [] }, [])).toBe('')
  })
})

describe('閉じた JSON の検証', () => {
  const good = {
    ai: { terms: [] },
    terms: [
      { key: 't1', label_ja: '語', label_en: 'word', aliases: ['w'], kind: 'class', use: 'new', slug: 'word', reason: '', match: null },
    ],
    relations: [{ subject: 't1', object: 'cmso:Material', relation: 'sub', problem: null }],
    selections: [{ question: 'q', op: 'top', kind: 't1', property: '', value: '', fits: true, memo: null, reason: '' }],
    suggested_questions: ['次の問い'],
    standards: { 'cmso:Material': { label: 'Material', kind: 'class' } },
    groups: [{ label_ja: 'まとめ', children: ['t1', 't2'] }],
  }

  it('決まった形ならそのまま読める（関係は未決・AI 下書きの印）', () => {
    const res = validateDraftResponse(good) as DraftResponse
    expect(res.terms[0]).toMatchObject({ key: 't1', by: 'ai', aliases: ['w'] })
    expect(res.relations[0]).toMatchObject({ ok: null, by: 'ai' })
    expect(res.selections[0].fits).toBe(true)
    expect(res.standards['cmso:Material'].label).toBe('Material')
    expect(res.groups).toHaveLength(1)
  })

  it.each([
    ['配列でない', { ...good, terms: 'x' }],
    ['kind が列挙の外', { ...good, terms: [{ ...good.terms[0], kind: 'thing' }] }],
    ['relation が列挙の外', { ...good, relations: [{ ...good.relations[0], relation: 'partOf' }] }],
    ['use が文字列でない', { ...good, terms: [{ ...good.terms[0], use: 3 }] }],
    ['object でない', null],
  ])('形が合わなければ null（手入力へ）: %s', (_name, raw) => {
    expect(validateDraftResponse(raw)).toBeNull()
  })

  it('数え方が列挙の外なら空にして、型に合うとは言わない', () => {
    const res = validateDraftResponse({
      ...good,
      selections: [{ ...good.selections[0], op: 'delete', fits: true }],
    }) as DraftResponse
    expect(res.selections[0]).toMatchObject({ op: '', fits: false, memo: 'none' })
  })

  it('今の型（件数・範囲・上位の値）に無い数え方は fits にしない', () => {
    const res = validateDraftResponse({
      ...good,
      selections: [{ ...good.selections[0], op: 'trend', fits: true }],
    }) as DraftResponse
    expect(res.selections[0].fits).toBe(false)
  })

  it('下書きに取り込む: 関係は未決（機械が止めたものは最初から「違う」）', () => {
    const res = validateDraftResponse({
      ...good,
      relations: [
        { subject: 't1', object: 't2', relation: 'sub', problem: null },
        { subject: 't2', object: 't1', relation: 'sub', problem: 'cycle' },
      ],
    }) as DraftResponse
    const out = applyDraftResponse(draft(), res)
    expect(out.relations.map((r) => r.ok)).toEqual([null, false])
    expect(out.ai?.suggested_questions).toEqual(['次の問い'])
    expect(out.terms).toHaveLength(1)
  })
})

describe('手で書く・問いが変わったとき', () => {
  it('問いの数だけ空の行を作る（用語は③で足す）', () => {
    const out = startManual(draft())
    expect(out.ai).toBeNull()
    expect(out.selections.map((s) => [s.question, s.op, s.by])).toEqual([
      ['問い A', '', 'human'],
      ['問い B', '', 'human'],
    ])
  })

  it('消えた問いの行は落とし、増えた問いは空の行を足す（残る行はそのまま）', () => {
    const kept = sel('問い A', { op: 'count', kind: 't1', memo: null })
    const out = syncSelections([kept, sel('古い問い')], ['問い A', '新しい問い'])
    expect(out[0]).toBe(kept)
    expect(out.map((s) => s.question)).toEqual(['問い A', '新しい問い'])
  })
})

describe('③ 用語', () => {
  it('既にある用語を使う行は、新しい用語を作らない', () => {
    expect(termMints(term('1', { use: 'new' }))).toBe(true)
    expect(termMints(term('1', { use: 'standard:cmso:Material' }))).toBe(true)
    expect(termMints(term('1', { use: 'existing:sample' }))).toBe(false)
  })

  it('どうするの候補: 完全一致を見つけた行だけ、その候補が増える', () => {
    expect(termUseOptions(term('1')).map((o) => o.kind)).toEqual(['new'])
    const ex = term('2', { match: { kind: 'existing', ref: 'sample', label: '試料', matched_by: 'label' } })
    expect(termUseOptions(ex)).toEqual([
      { value: 'new', kind: 'new' },
      { value: 'existing:sample', kind: 'existing' },
    ])
    const qk = term('3', { match: { kind: 'quantity', ref: 'quantitykind:Capacity', label: 'Capacity', matched_by: 'label' } })
    expect(termUseOptions(qk)[1]).toEqual({ value: 'standard:quantitykind:Capacity', kind: 'quantity' })
  })

  describe('手で書いた行の照合（applyMatches）', () => {
    const exact = { kind: 'existing' as const, ref: 'record', label: '記録', matched_by: 'label' as const }
    const sentFor = (rows: TermRow[]) => Object.fromEntries(rows.map((r) => [r.key, matchSignature(r)]))

    it('人が触っていない行は、機械の勧め（既にある用語）に置き換わる・ID も寄る', () => {
      const rows = [term('1', { label_ja: '記録', by: 'human' })]
      const out = applyMatches(rows, [{ key: '1', match: exact, use: 'existing:record' }], sentFor(rows))
      expect(out[0]).toMatchObject({ use: 'existing:record', slug: 'record', match: exact, use_by: 'machine' })
      expect(termUseOptions(out[0]).map((o) => o.value)).toEqual(['new', 'existing:record'])
    })

    it('標準用語の完全一致は standard: に置き換わる', () => {
      const m = { kind: 'quantity' as const, ref: 'quantitykind:Temperature', label: 'Temperature', matched_by: 'label' as const }
      const rows = [term('1', { kind: 'property', slug: 'temperature' })]
      const out = applyMatches(rows, [{ key: '1', match: m, use: 'standard:quantitykind:Temperature' }], sentFor(rows))
      expect(out[0]).toMatchObject({ use: 'standard:quantitykind:Temperature', slug: 'temperature' })
    })

    it('人が select を触った行の use は上書きしない（match は更新する）', () => {
      const rows = chooseUse([term('1', { label_ja: '記録' })], '1', 'new')
      expect(rows[0].use_by).toBe('human')
      const out = applyMatches(rows, [{ key: '1', match: exact, use: 'existing:record' }], sentFor(rows))
      expect(out[0]).toMatchObject({ use: 'new', match: exact, use_by: 'human' })
    })

    it('人が選んだ候補が照合から消えたら、機械の勧めに戻り ID も作り直す', () => {
      const rows = chooseUse([term('1', { label_en: 'log' })], '1', 'existing:record')
      const out = applyMatches(rows, [{ key: '1', match: null, use: 'new' }], sentFor(rows), ['record'])
      expect(out[0]).toMatchObject({ use: 'new', match: null, slug: 'log', use_by: 'machine' })
    })

    it('照合に出したあとに打ち直された行・答えに無い行は触らない', () => {
      const rows = [term('1', { label_ja: '記' }), term('2')]
      const sent = sentFor(rows)
      const edited = [{ ...rows[0], label_ja: '記録' }, rows[1]]
      const out = applyMatches(edited, [{ key: '1', match: exact, use: 'existing:record' }], sent)
      expect(out).toEqual(edited)
    })
  })

  it('既にある用語を選ぶと、ID はその用語の ID に寄る', () => {
    const rows = chooseUse([term('1', { slug: 'mine' })], '1', 'existing:sample')
    expect(rows[0]).toMatchObject({ use: 'existing:sample', slug: 'sample' })
    expect(chooseUse(rows, '1', 'new')[0].slug).toBe('sample')
  })

  it('行の問題: 名前が空・ID の形・すでにある・重複（既にある用語を使う行は ID を見ない）', () => {
    const rows = [
      term('1', { label_ja: ' ' }),
      term('2', { slug: 'Bad Slug' }),
      term('3', { slug: 'taken' }),
      term('4', { slug: 'dup' }),
      term('5', { slug: 'dup' }),
      term('6', { use: 'existing:x', slug: 'Bad Slug' }),
      term('7', { slug: 'fine' }),
    ]
    expect(rowProblems(rows, new Set(['taken']))).toEqual({
      '1': 'label',
      '2': 'slug',
      '3': 'taken',
      '5': 'duplicate',
    })
  })

  it('行を外すと、その行を使う関係と選択も外れる', () => {
    const d = draft({
      terms: [term('1'), term('2', { kind: 'property' })],
      relations: [rel('1', '2'), rel('2', '1'), rel('2', 'cmso:Material')],
      selections: [sel('q', { op: 'top', kind: '1', property: '2', memo: null })],
    })
    const out = removeRow(d, '1')
    expect(out.terms.map((r) => r.key)).toEqual(['2'])
    expect(out.relations).toEqual([rel('2', 'cmso:Material')])
    expect(out.selections[0]).toMatchObject({ kind: '', property: '2' })
  })

  it('新しい行と名前の変更', () => {
    const rows = [term('1'), newTermRow([term('1')], 'property')]
    expect(rows[1]).toMatchObject({ key: 't2', kind: 'property', use: 'new', by: 'human' })
    expect(renameRow(rows, '1', { label_ja: '新名' })[0].label_ja).toBe('新名')
  })
})

describe('④ 関係', () => {
  const rows = [term('1'), term('2'), term('3', { use: 'existing:sample', slug: 'sample' })]

  it('置けるのは、主語が新しく作る用語で、機械が止めていない関係だけ', () => {
    expect(relationApplies(rel('1', '2'), rows)).toBe(true)
    expect(relationApplies(rel('3', '1'), rows)).toBe(false)
    expect(relationApplies(rel('1', '2', { problem: 'cycle' }), rows)).toBe(false)
    expect(relationApplies(rel('9', '1'), rows)).toBe(false)
  })

  it('初期選択なし: 置ける関係がすべて「そう／違う」になるまで進めない', () => {
    const rels = [rel('1', '2'), rel('3', '1'), rel('2', '1', { problem: 'cycle', ok: false })]
    expect(relationsReady(rels, rows)).toBe(false)
    const answered = setRelation(rels, 0, true)
    expect(relationsReady(answered, rows)).toBe(true) // 置けない関係・止められた関係は聞かない
    expect(acceptedRelations(answered, rows).map((r) => r.object)).toEqual(['2'])
    expect(relationsReady([], rows)).toBe(true)
  })

  it('標準の語と同じ／量の種類と結ぶ関係は、③の選択から決まる', () => {
    const rs = [
      term('1', { use: 'standard:cmso:Material', match: { kind: 'standard', ref: 'cmso:Material', label: 'Material', matched_by: 'label' } }),
      term('2', { kind: 'property', use: 'standard:quantitykind:Capacity' }),
      term('3'),
    ]
    expect(implicitRelations(rs)).toEqual([
      { subject: '1', curie: 'cmso:Material', quantity: false },
      { subject: '2', curie: 'quantitykind:Capacity', quantity: true },
    ])
  })

  it('まとめ名の chip: 同じ親を要る用語が 2 つ以上のときだけ・足すと関係は「そう」', () => {
    const groups = [{ label_ja: '蓄電デバイス', children: ['1', '2'] }, { label_ja: '文書', children: ['1'] }]
    expect(visibleGroups(groups, rows)).toEqual([groups[0]])
    const out = addGroup(draft({ terms: rows }), groups[0])
    const added = out.terms.at(-1) as TermRow
    expect(added).toMatchObject({ label_ja: '蓄電デバイス', kind: 'class', use: 'new', by: 'human' })
    expect(out.relations.map((r) => [r.subject, r.object, r.ok])).toEqual([
      ['1', added.key, true],
      ['2', added.key, true],
    ])
    // 足したあとは、もう提案しない
    expect(visibleGroups(groups, out.terms)).toEqual([])
  })

  it('上位構造: 「そう」の関係だけを、親 → 子の字下げで', () => {
    const rs = [
      term('1', { label_ja: '子' }),
      term('2', { label_ja: '親' }),
      term('3', { label_ja: '孫' }),
      term('4', { label_ja: '外', kind: 'property', use: 'standard:quantitykind:Capacity' }),
    ]
    const d = draft({
      terms: rs,
      relations: [
        rel('1', '2', { ok: true }),
        rel('3', '1', { ok: true }),
        rel('2', '3', { ok: false }),
      ],
      ai: { standards: {} },
    })
    expect(upperStructure(d).map((l) => [l.depth, l.label])).toEqual([
      [0, '親'],
      [1, '子'],
      [2, '孫'],
      [0, '外'],
    ])
    expect(upperStructure(d).at(-1)?.notes).toEqual([{ relation: 'quantity', curie: 'quantitykind:Capacity' }])
    expect(upperStructure(draft({ terms: rs, relations: [rel('1', '2', { ok: null })] }))).toEqual([
      expect.objectContaining({ label: '外' }),
    ])
  })
})

describe('⑤ 数え方', () => {
  it('数え方ごとに、要る部分が足りているか', () => {
    expect(selectionComplete(sel('q'))).toBe(false)
    expect(selectionComplete(sel('q', { op: 'count' }))).toBe(false)
    expect(selectionComplete(sel('q', { op: 'count', kind: 't1' }))).toBe(true)
    expect(selectionComplete(sel('q', { op: 'top', property: 't2' }))).toBe(true) // 種類は任意
    expect(selectionComplete(sel('q', { op: 'filter', kind: 't1', property: 't2' }))).toBe(false)
    expect(selectionComplete(sel('q', { op: 'filter', kind: 't1', property: 't2', value: 'x' }))).toBe(true)
    expect(selectionComplete(sel('q', { op: 'trend', kind: 't1' }))).toBe(false)
  })

  it('公開できるのは 件数・範囲・上位の値 だけ。残りはメモの問い', () => {
    const top = sel('q', { op: 'top', property: 't2', memo: null })
    expect(selectionFits(top)).toBe(true)
    expect(isMemo(top)).toBe(false)
    const trend = sel('q', { op: 'trend', kind: 't1', property: 't2', memo: null })
    expect(selectionFits(trend)).toBe(false)
    expect(isMemo(trend)).toBe(true)
    expect(memoReason(trend)).toBe('op')
    expect(memoReason(sel('q'))).toBe('none')
    expect(memoReason({ ...top, memo: 'human' })).toBe('human')
  })

  it('選び直すと、メモの印は型に合うかで決まり直す', () => {
    const base = sel('q', { op: 'top', property: 't2', memo: 'ai' })
    const toTrend = setSelection(base, { op: 'trend', kind: 't1' })
    expect(toTrend).toMatchObject({ op: 'trend', memo: 'op', fits: false, by: 'human' })
    const back = setSelection(toTrend, { op: 'count', kind: 't1' })
    expect(back).toMatchObject({ memo: null, fits: true })
    // 人が「メモにする」を選ぶ・外す
    expect(setSelection(back, { memo: 'human' })).toMatchObject({ memo: 'human', fits: false })
    expect(setSelection(setSelection(back, { memo: 'human' }), { memo: null }).fits).toBe(true)
    // まだ決まっていない行を選び直しても、型に合わないとは言わない
    expect(setSelection(sel('q'), { op: 'filter' }).memo).toBeNull()
  })

  it('⑤を進める条件: すべての問いが、決まっているか、メモと決めてある', () => {
    expect(selectionsReady([sel('a'), sel('b')])).toBe(true) // 既定ではメモ（none）
    expect(selectionsReady([sel('a', { memo: null })])).toBe(false)
    expect(selectionsReady([sel('a', { memo: null, op: 'count', kind: 't1' })])).toBe(true)
    expect(selectionsReady([])).toBe(true)
  })

  it('実行文の材料は、行の表示名（言語に合わせる）', () => {
    const rows = [term('1', { label_ja: '電池セル', label_en: 'battery cell' }), term('2', { kind: 'property', label_ja: '容量', label_en: '' })]
    const s = sel('q', { op: 'top', kind: '1', property: '2' })
    expect(selectionPayload(s, rows, 'ja')).toEqual({ op: 'top', kind: '電池セル', property: '容量', value: '' })
    expect(selectionPayload(s, rows, 'en')).toEqual({ op: 'top', kind: 'battery cell', property: '容量', value: '' })
  })

  it('「いまは N データセットが答える」: 新しい用語は 0・既にある用語はその数', () => {
    const rows = [
      term('1'),
      term('2', { use: 'existing:sample', slug: 'sample' }),
      term('3', { kind: 'property', use: 'existing:price', slug: 'price' }),
    ]
    const known = { sample: 4, price: 2 }
    expect(answeringNow(sel('q', { op: 'count', kind: '1' }), rows, known)).toBe(0)
    expect(answeringNow(sel('q', { op: 'count', kind: '2' }), rows, known)).toBe(4)
    expect(answeringNow(sel('q', { op: 'top', kind: '1', property: '3' }), rows, known)).toBe(2)
    expect(answeringNow(sel('q', { op: 'trend', kind: '2', property: '3' }), rows, known)).toBe(0)
  })
})

describe('⑥ 置く', () => {
  it('ID 一覧は、新しく作る用語だけ（既にある用語は出さない）', () => {
    const rows = [term('1'), term('2', { use: 'existing:sample', slug: 'sample' }), term('3', { use: 'standard:cmso:Material', slug: 'material' })]
    expect(idList(rows)).toEqual([
      { key: '1', label: '語1', slug: 'word_1' },
      { key: '3', label: '語3', slug: 'material' },
    ])
  })

  it('保存するものの数と、AI 下書きの印', () => {
    const d = draft({
      ai: {},
      terms: [term('1'), term('2'), term('3', { use: 'existing:sample', slug: 'sample' })],
      relations: [rel('1', '2', { ok: true }), rel('2', '1', { ok: false })],
      selections: [
        sel('a', { op: 'count', kind: '1', memo: null }),
        sel('b', { op: 'trend', kind: '1', property: '2', memo: null }),
      ],
    })
    expect(placeSummary(d)).toEqual({ terms: 2, lines: 1, published: 1, memo: 1, aiDrafted: true })
    expect(placeSummary({ ...d, ai: null }).aiDrafted).toBe(false)
  })

  it('確定の body: 「そう」の関係だけ・型に合わない問いは機械の印つき・足すテーマ', () => {
    const d = draft({
      ai: {},
      extend: 'battery',
      terms: [term('1'), term('2')],
      relations: [rel('1', '2', { ok: true }), rel('2', '1', { ok: false }), rel('1', '2', { ok: null, relation: 'eq' })],
      selections: [
        sel('a', { op: 'count', kind: '1', memo: null }),
        sel('b', { op: 'trend', kind: '1', property: '2', memo: null }),
        sel('c'),
      ],
    })
    const body = buildCommitBody(d, 'ja')
    expect(body).toMatchObject({ label: 'テーマ', extend: 'battery', lang: 'ja', ai_drafted: true })
    expect(body.relations).toEqual([{ subject: '1', object: '2', relation: 'sub' }])
    expect(body.selections.map((s) => s.memo)).toEqual([null, 'op', 'none'])
    expect(buildCommitBody(draft(), 'en')).not.toHaveProperty('extend')
    expect(buildCommitBody(draft(), 'en').ai_drafted).toBe(false)
  })
})

describe('段の進める条件', () => {
  it('① テーマ名と問いが 1 つ以上', () => {
    expect(canAdvance(draft(), 1, CTX)).toBe(true)
    expect(canAdvance(draft({ label: ' ' }), 1, CTX)).toBe(false)
    expect(canAdvance(draft({ questions: [] }), 1, CTX)).toBe(false)
  })

  it('② AI に読ませた（または手で書くと決めた）あと: ⑤の行が問いの数だけある', () => {
    expect(canAdvance(draft(), 2, CTX)).toBe(false)
    expect(canAdvance(startManual(draft()), 2, CTX)).toBe(true)
    expect(canAdvance(draft({ selections: [sel('問い A')] }), 2, CTX)).toBe(false)
  })

  it('③ 名前が空の行があれば進めない（ID の形は⑥の「置く」が止める）', () => {
    expect(canAdvance(draft({ terms: [term('1')] }), 3, CTX)).toBe(true)
    expect(canAdvance(draft({ terms: [term('1', { label_ja: '' })] }), 3, CTX)).toBe(false)
    expect(canAdvance(draft({ terms: [term('1', { slug: '' })] }), 3, CTX)).toBe(true)
  })

  it('④ 置ける関係に答えるまで・⑤ すべての問いが決まるまで', () => {
    const terms = [term('1'), term('2')]
    expect(canAdvance(draft({ terms, relations: [rel('1', '2')] }), 4, CTX)).toBe(false)
    expect(canAdvance(draft({ terms, relations: [rel('1', '2', { ok: false })] }), 4, CTX)).toBe(true)
    expect(canAdvance(draft({ selections: [sel('a', { memo: null })] }), 5, CTX)).toBe(false)
    expect(canAdvance(draft({ selections: [sel('a', { memo: 'human' })] }), 5, CTX)).toBe(true)
  })

  it('⑥ ID に問題が無いとき置ける・最初の問題が理由になる', () => {
    const ok = draft({ terms: [term('1')] })
    expect(canAdvance(ok, 6, CTX)).toBe(true)
    expect(placeBlocker(ok, CTX)).toBeNull()
    const taken = { existingSlugs: new Set(['word_1']) }
    expect(canAdvance(ok, 6, taken)).toBe(false)
    expect(placeBlocker(ok, taken)).toBe('taken')
    expect(placeBlocker(draft({ terms: [term('1', { slug: '' })] }), CTX)).toBe('slug')
  })
})
