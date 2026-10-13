// ⑥「自分の問い」の純関数（閉じた選択 → api に送る形）と、⑦ 公開ダイアログの件数の文言。
import { describe, expect, it } from 'vitest'
import type { QuestionDraft, TrialQueries, UpperItem } from '../api'
import type { DatasetRules } from '../galleryApi'
import en from '../i18n/locales/en/kantan.json'
import ja from '../i18n/locales/ja/kantan.json'
import {
  canSaveQuestion,
  describeUpperQuestions,
  hasAnswer,
  mapSide,
  newQuestion,
  publishWriteCounts,
  questionChoices,
  selectionKey,
  selectionOf,
  selectionOfQuestion,
  selectionReady,
  shortRef,
  skipReasonKind,
  upperTarget,
} from './questions'

const K = 'https://example.org/datasets/xrd/ontology#Peak'
const K2 = 'https://example.org/datasets/xrd/ontology#Sample'
const P = 'https://example.org/datasets/xrd/ontology#intensity'
const P2 = 'https://example.org/datasets/xrd/ontology#name'

const rules = {
  prefixes: {},
  warnings: [],
  labels: { [K]: '回折点', [P]: '強度' },
  maps: [
    {
      id: 'peak',
      subject: { class_iris: [K] },
      properties: [
        { predicate: 'ds:intensity', predicate_iri: P, kind: 'reference', label: 'Intensity' },
        // IRI を作る行・つながりは項目の選択肢に出さない
        { predicate: 'ds:sample', predicate_iri: 'x:sample', kind: 'join' },
        { predicate: 'ds:id', predicate_iri: 'x:id', kind: 'template' },
        { predicate: 'ds:ref', predicate_iri: 'x:ref', kind: 'reference', term_type: 'IRI' },
      ],
    },
    {
      id: 'sample',
      subject: { class_iris: [K2] },
      properties: [{ predicate: 'ds:name', predicate_iri: P2, kind: 'reference' }],
    },
  ],
} as unknown as DatasetRules

describe('questionChoices', () => {
  it('規則が無ければ空', () => {
    expect(questionChoices(null)).toEqual({ kinds: [], properties: [] })
  })

  it('種類と値の項目だけを、model の表示名つきで返す', () => {
    const c = questionChoices(rules)
    expect(c.kinds).toEqual([
      { iri: K, label: '回折点' },
      { iri: K2, label: 'Sample' },
    ])
    expect(c.properties.map((p) => p.iri)).toEqual([P, P2])
    // 表示名: model.yaml の label → 設計の label → IRI の最後の語
    expect(c.properties.find((p) => p.iri === P)?.label).toBe('強度')
    expect(c.properties.find((p) => p.iri === P2)?.label).toBe('name')
    expect(c.properties.find((p) => p.iri === P)?.kinds).toEqual([K])
  })
})

describe('選択 → api に送る形（閉じた選択）', () => {
  it('件数は種類だけ。項目は送らない', () => {
    expect(selectionOf('count', K, P)).toEqual({ op: 'count', kind_iri: K })
  })

  it('範囲・上位の値は項目が要り、種類は絞り込み（任意）', () => {
    expect(selectionOf('range', '', P)).toEqual({ op: 'range', property_iri: P })
    expect(selectionOf('top', K, P)).toEqual({ op: 'top', property_iri: P, kind_iri: K })
  })

  it('組み上がり: count は種類、range/top は項目', () => {
    expect(selectionReady(selectionOf('count', '', P))).toBe(false)
    expect(selectionReady(selectionOf('count', K, ''))).toBe(true)
    expect(selectionReady(selectionOf('range', K, ''))).toBe(false)
    expect(selectionReady(selectionOf('top', '', P))).toBe(true)
  })

  it('題が空なら保存できない', () => {
    const sel = selectionOf('count', K, '')
    expect(canSaveQuestion('  ', sel)).toBe(false)
    expect(canSaveQuestion('回折点は何件？', sel)).toBe(true)
    expect(canSaveQuestion('回折点は何件？', selectionOf('count', '', ''))).toBe(false)
  })

  it('選択の鍵は op・種類・項目で決まる（題は関係しない）', () => {
    const a = selectionKey(selectionOf('range', K, P))
    expect(a).toBe(selectionKey(selectionOf('range', K, P)))
    expect(a).not.toBe(selectionKey(selectionOf('top', K, P)))
    expect(a).not.toBe(selectionKey(selectionOf('range', '', P)))
  })
})

describe('newQuestion', () => {
  it('題は前後の空白を落とし、選択を写す。id は既存と重ならない', () => {
    const first = newQuestion(' 回折点は何件？ ', selectionOf('count', K, ''), [])
    expect(first).toEqual({ id: 'q1', title: '回折点は何件？', op: 'count', kind_iri: K })
    const second = newQuestion('強度の範囲は？', selectionOf('range', '', P), [first])
    expect(second).toEqual({ id: 'q2', title: '強度の範囲は？', op: 'range', property_iri: P })
    // 途中を消した後でも重ならない
    const third = newQuestion('x', selectionOf('count', K, ''), [{ ...first, id: 'q2' }])
    expect(third.id).toBe('q3')
  })

  it('下書き → 選択に戻せる（lint_error や題は含めない）', () => {
    const q: QuestionDraft = {
      id: 'q1',
      title: '題',
      op: 'top',
      kind_iri: K,
      property_iri: P,
      lint_error: 'x',
    }
    expect(selectionOfQuestion(q)).toEqual({ op: 'top', kind_iri: K, property_iri: P })
  })
})

describe('「ことばへ写す」の行き先', () => {
  const upper = {
    classes: { [K]: 'https://example.org/sv/sample', [K2]: K2 },
    properties: { [P]: 'https://example.org/sv/strength' },
  }

  it('件数は種類の側、範囲・上位の値は項目の側で畳む', () => {
    expect(mapSide('count')).toBe('classes')
    expect(mapSide('range')).toBe('properties')
    expect(mapSide('top')).toBe('properties')
  })

  it('上位に畳めたらその語。自分のままなら null（鋳造フォームへ案内）', () => {
    expect(upperTarget({ id: 'q', title: 't', op: 'count', kind_iri: K }, upper)).toBe(
      'https://example.org/sv/sample',
    )
    expect(upperTarget({ id: 'q', title: 't', op: 'count', kind_iri: K2 }, upper)).toBeNull()
    expect(upperTarget({ id: 'q', title: 't', op: 'range', property_iri: P }, upper)).toBe(
      'https://example.org/sv/strength',
    )
    expect(upperTarget({ id: 'q', title: 't', op: 'top', property_iri: P2 }, upper)).toBeNull()
  })
})

describe('hasAnswer', () => {
  const base = {
    dataset_id: 'd',
    available: true,
    classes: [],
    count_sparql: null,
    entities: null,
    range: null,
    top: null,
    samples: null,
  } as TrialQueries

  it('問いに当たる 1 つが入っていれば答えあり', () => {
    expect(hasAnswer(base)).toBe(false)
    expect(hasAnswer({ ...base, classes: [{ iri: K, n: 3 }] })).toBe(true)
    expect(hasAnswer({ ...base, available: false, classes: [{ iri: K, n: 3 }] })).toBe(false)
  })
})

describe('⑦ 公開ダイアログ「線 N 本・問い M 件を書きます」', () => {
  const line = (applied_at: string | null = null): UpperItem => ({
    subject: K,
    term: 'https://example.org/sv/sample',
    relation: 'subClassOf',
    applied_at,
  })
  const q = (id: string, lint_error?: string): QuestionDraft => ({
    id,
    title: id,
    op: 'count',
    kind_iri: K,
    lint_error,
  })

  it('0 件なら出さない（null）', () => {
    expect(publishWriteCounts([], [])).toBeNull()
    // 消費済みの線は書かない
    expect(publishWriteCounts([line('2026-10-09T00:00:00Z')], [])).toBeNull()
  })

  it('N = 未消費の線、M = ツールにできる問い（lint を通らないものは数えない）', () => {
    expect(
      publishWriteCounts([line(), line('2026-10-09T00:00:00Z')], [q('a'), q('b', 'broken')]),
    ).toEqual({ lines: 1, questions: 1 })
    expect(publishWriteCounts([], [q('a'), q('b')])).toEqual({ lines: 0, questions: 2 })
  })

  for (const [name, locale] of [
    ['ja', ja],
    ['en', en],
  ] as const) {
    it(`${name}: 文言が 3 通りあり、件数の差し込みがある`, () => {
      const s8 = locale.s8
      expect(s8.writesBoth).toContain('{{lines}}')
      expect(s8.writesBoth).toContain('{{questions}}')
      expect(s8.writesLines).toContain('{{lines}}')
      expect(s8.writesLines).not.toContain('{{questions}}')
      expect(s8.writesQuestions).toContain('{{questions}}')
      expect(s8.writesQuestions).not.toContain('{{lines}}')
    })
  }

  it('skipped 済みの線は数えない（書けないと分かっている線を「書きます」と言わない）', () => {
    expect(publishWriteCounts([{ ...line(), skipped: 'predicate missing' }], [])).toBeNull()
    expect(
      publishWriteCounts([line(), { ...line(), skipped: 'predicate missing' }], []),
    ).toEqual({ lines: 1, questions: 0 })
  })

  it('ja の文言は「線 N 本・問い M 件を書きます」', () => {
    expect(ja.s8.writesBoth).toBe('線 {{lines}} 本・問い {{questions}} 件を書きます')
  })
})

describe('公開の応答（upper_questions）を画面の言葉にする', () => {
  it('何も無い・応答が無いときは empty', () => {
    expect(describeUpperQuestions(null).empty).toBe(true)
    expect(describeUpperQuestions({}).empty).toBe(true)
    expect(
      describeUpperQuestions({
        upper: { applied: 0, skipped: [] },
        questions: { written: [], lint_errors: [] },
        warnings: [],
      }).empty,
    ).toBe(true)
  })

  it('書けた件数・書けなかった線の理由・検査で落ちた問い・警告を拾う', () => {
    const notes = describeUpperQuestions({
      upper: {
        applied: 2,
        skipped: [{ subject: 'property:peak/温度', term: 'https://std/t', reason: 'predicate missing' }],
      },
      questions: { written: ['q_a1'], lint_errors: [{ id: 'q2', errors: ['bad kind'] }] },
      warnings: ['線を 1 本書けませんでした', '  '],
    })
    expect(notes.empty).toBe(false)
    expect(notes.applied).toBe(2)
    expect(notes.skipped).toHaveLength(1)
    expect(notes.written).toBe(1)
    expect(notes.lintErrors).toEqual([{ id: 'q2', errors: ['bad kind'] }])
    expect(notes.warnings).toEqual(['線を 1 本書けませんでした'])
  })

  it('書けなかった線だけでも empty ではない', () => {
    expect(
      describeUpperQuestions({
        upper: { skipped: [{ subject: 's', term: 't', reason: 'subject missing' }] },
      }).empty,
    ).toBe(false)
  })

  it('shortRef: property: は接頭辞を外し、IRI は最後の語', () => {
    expect(shortRef('property:peak/温度')).toBe('peak/温度')
    expect(shortRef('https://example.org/sv/temp')).toBe('temp')
    expect(shortRef('https://example.org/onto#Peak')).toBe('Peak')
  })

  it('skipReasonKind: api が決まった言い方で返す理由だけ言い換える', () => {
    expect(skipReasonKind('predicate missing')).toBe('predicateMissing')
    expect(skipReasonKind('subject missing')).toBe('subjectMissing')
    expect(skipReasonKind('something else')).toBe('other')
  })

  for (const [name, locale] of [
    ['ja', ja],
    ['en', en],
  ] as const) {
    it(`${name}: 結果の文言と名前だけの公開の入口の文言が揃っている`, () => {
      expect(locale.publishResult.applied).toContain('{{n}}')
      expect(locale.publishResult.skippedItem).toContain('{{reason}}')
      expect(locale.publishResult.reason.predicateMissing).toBeTruthy()
      expect(locale.publishResult.lintItem).toContain('{{title}}')
      expect(locale.s7.writesBoth).toContain('{{lines}}')
      expect(locale.s7.writesBoth).toContain('{{questions}}')
      expect(locale.s7.own.mapExisted).toContain('{{term}}')
    })
  }

  it('ja の入口の文言は「線 N 本・問い M 件を公開に書き込みます」', () => {
    expect(ja.s7.writesBoth).toBe('線 {{lines}} 本・問い {{questions}} 件を公開に書き込みます')
  })
})
