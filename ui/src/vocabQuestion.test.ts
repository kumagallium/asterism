import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { beforeAll, describe, expect, it } from 'vitest'
import { parseHash } from './App'
import type { Alignment } from './crosswalkApi'
import i18n from './i18n'
import type { GroundCandidate } from './groundingApi'
import type { SharedTerm } from './vocabApi'
import { AlignmentRow } from './PerspectiveAlignment'
import { ScopeTabs } from './VocabLinesSection'
import { VocabTermRow } from './VocabTermsSection'
import {
  apiScope,
  buildMintInput,
  bumpVersion,
  cqLabel,
  cqTitleMap,
  defaultCqTitle,
  initialCqTitle,
  isolatedCount,
  LINE_SCOPES,
  orderLines,
  parseVocabHash,
  pickExactStandard,
  showIsolatedNotice,
  slugFromText,
  suggestSlug,
  vocabQuestionHash,
  vocabScopeHash,
} from './vocabQuestion'

// 架空の語だけを使う（分野語ゼロ）。
const tr = (k: string, o: { label: string }) => i18n.t(k, o) as string

beforeAll(async () => {
  await i18n.changeLanguage('ja')
})

function cand(score: number, name = 'Widget'): GroundCandidate {
  return {
    iri: `http://example.org/std#${name}`,
    curie: `ex:${name}`,
    prefix: 'ex:',
    name,
    kind: 'class',
    label: name,
    vocab_title: 'Example',
    domain: 'x',
    score,
    match: 'exact',
  }
}

function term(slug: string, over: Partial<SharedTerm> = {}): SharedTerm {
  return {
    iri: `https://example.org/sv#${slug}`,
    slug,
    kind: 'class',
    label: slug,
    label_en: null,
    comment: null,
    created_at: '2026-10-09T00:00:00Z',
    wired: true,
    answering_datasets: 2,
    narrower: [],
    standards: [],
    cqs: [],
    ...over,
  }
}

describe('既定の問い（ADR §2.3 の表）', () => {
  it('種類は「どのデータセットに何件あるか」', () => {
    expect(defaultCqTitle('class', '試料', tr)).toBe('試料は、どのデータセットに何件あるか')
  })

  it('項目は「値は、どのデータセットにいくつあるか」', () => {
    expect(defaultCqTitle('property', '温度', tr)).toBe('温度の値は、どのデータセットにいくつあるか')
  })

  it('label がまだ無ければ空（語が決まっていない）', () => {
    expect(defaultCqTitle('class', '  ', tr)).toBe('')
  })

  it('題があれば題が先、無ければ既定のテンプレート', () => {
    expect(initialCqTitle('試料は何件？', 'class', '試料', tr)).toBe('試料は何件？')
    expect(initialCqTitle('  ', 'class', '試料', tr)).toBe('試料は、どのデータセットに何件あるか')
  })
})

describe('標準の語が先に出る分岐', () => {
  it('完全一致（100）だけを採る・部分一致は出さない', () => {
    expect(pickExactStandard([cand(90), cand(100, 'Exact'), cand(100, 'Later')])?.name).toBe('Exact')
    expect(pickExactStandard([cand(99), cand(40)])).toBeNull()
    expect(pickExactStandard([])).toBeNull()
  })

  it('標準の語を退けるときは理由が要り、来歴 declined_standard に載る', () => {
    const base = {
      labelJa: '試料',
      labelEn: 'Widget',
      slug: 'widget',
      kind: 'class' as const,
      comment: '',
      cqTitles: ['試料は何件？'],
    }
    expect(buildMintInput({ ...base, declined: { iri: 'http://e/x', reason: '  ' } })).toEqual({
      problem: 'reason',
    })
    const ok = buildMintInput({ ...base, declined: { iri: 'http://e/x', reason: '粒度が違う' } })
    expect('input' in ok && ok.input.declined_standard).toEqual({
      iri: 'http://e/x',
      reason: '粒度が違う',
    })
  })
})

describe('slug の既定値と鋳造の入力', () => {
  it('英語 label や camelCase の標準語名から ASCII の slug を作る', () => {
    expect(slugFromText('Sample composition')).toBe('sample_composition')
    expect(slugFromText('hasSeebeckCoefficient')).toBe('has_seebeck_coefficient')
    expect(slugFromText('3D shape')).toBe('x_3d_shape')
  })

  it('日本語だけからは作らない・先に作れた候補を採る', () => {
    expect(slugFromText('試料')).toBe('')
    expect(suggestSlug('', undefined, 'Widget')).toBe('widget')
    expect(suggestSlug('Alpha beta', 'col', 'Widget')).toBe('alpha_beta')
  })

  it('問いが空・slug が不正なら送らない', () => {
    const d = {
      labelJa: '試料',
      labelEn: '',
      slug: 'sample',
      kind: 'class' as const,
      comment: '',
      cqTitles: ['  '],
    }
    expect(buildMintInput(d)).toEqual({ problem: 'cq' })
    expect(buildMintInput({ ...d, cqTitles: ['x'], slug: 'Bad Slug' })).toEqual({ problem: 'slug' })
  })

  it('問いの op は種類で決まる（種類＝count・項目＝values）', () => {
    const d = {
      labelJa: '温度',
      labelEn: '',
      slug: 'temp',
      comment: '',
      cqTitles: ['温度の値は？'],
    }
    const c = buildMintInput({ ...d, kind: 'class' })
    const p = buildMintInput({ ...d, kind: 'property' })
    expect('input' in c && c.input.cqs).toEqual([{ title: '温度の値は？', op: 'count' }])
    expect('input' in p && p.input.cqs).toEqual([{ title: '温度の値は？', op: 'values' }])
  })
})

describe('孤立（まだ線になっていない）の札と注意', () => {
  it('wired=false を数え、20 を超えたときだけ注意を出す', () => {
    const many = Array.from({ length: 21 }, (_, i) => term(`t${i}`, { wired: false }))
    expect(isolatedCount(many)).toBe(21)
    expect(showIsolatedNotice(many)).toBe(true)
    expect(showIsolatedNotice(many.slice(0, 20))).toBe(false)
    expect(showIsolatedNotice([...many.slice(0, 20), term('ok')])).toBe(false)
  })

  it('一覧の行に札が出る（孤立だけ）', () => {
    const noop = () => {}
    const html = (t: SharedTerm) =>
      renderToStaticMarkup(
        createElement(VocabTermRow, {
          term: t,
          confirming: false,
          removing: false,
          onAskRemove: noop,
          onCancelRemove: noop,
          onRemove: noop,
        }),
      )
    expect(html(term('a', { wired: false }))).toContain('まだ線になっていない')
    expect(html(term('b'))).not.toContain('まだ線になっていない')
    expect(html(term('c', { answering_datasets: 3 }))).toContain('3 データセットが答える')
  })
})

describe('線の絞り込み（scope）', () => {
  it('すべて＋ 4 つ。すべては api に scope を送らない', () => {
    expect(LINE_SCOPES).toEqual(['all', 'perspective', 'standard', 'shared', 'dataset'])
    expect(apiScope('all')).toBeUndefined()
    expect(apiScope('shared')).toBe('shared')
  })

  it('切り替えは 5 つ並び、選んだものだけ pressed', () => {
    const html = renderToStaticMarkup(
      createElement(ScopeTabs, { scope: 'shared', onChange: () => {} }),
    )
    expect(html.match(/<button/g)).toHaveLength(5)
    expect(html.match(/aria-pressed="true"/g)).toHaveLength(1)
    expect(html).toMatch(/aria-pressed="true"[^>]*>ことばへ</)
  })

  it('切れている線を先頭に・ほかの順は変えない', () => {
    const a = (id: string, broken?: boolean) => ({ alignment_iri: id, broken }) as Alignment
    expect(orderLines([a('1'), a('2', true), a('3'), a('4', true)]).map((x) => x.alignment_iri)).toEqual([
      '2',
      '4',
      '1',
      '3',
    ])
  })
})

describe('導線の hash', () => {
  it('Ask から題を運ぶ（解釈せずそのまま・エンコードして）', () => {
    expect(vocabQuestionHash('試料は何件？ A/B')).toBe(
      `#/vocab?q=${encodeURIComponent('試料は何件？ A/B')}`,
    )
    expect(vocabQuestionHash('  ')).toBe('#/vocab')
  })

  it('運んだ題を読み戻せる・scope と dataset も読む', () => {
    const q = '試料は何件？ A/B'
    expect(parseVocabHash(vocabQuestionHash(q)).q).toBe(q)
    expect(parseVocabHash('#/vocab?scope=standard&dataset=ds1')).toEqual({
      q: '',
      scope: 'standard',
      dataset: 'ds1',
    })
    expect(parseVocabHash('#/vocab').scope).toBeNull()
    expect(parseVocabHash('#/vocab?scope=bogus').scope).toBeNull()
  })

  it('ルータは #/vocab?… を「ことば」画面として開く', () => {
    expect(parseHash('#/vocab?q=abc')).toEqual({ tab: 'vocab' })
    expect(parseHash('#/vocab?scope=standard&dataset=x')).toEqual({ tab: 'vocab' })
    expect(parseHash('#/vocab')).toEqual({ tab: 'vocab' })
  })
})

describe('節の再読み込み（版の伝播）', () => {
  it('版は 1 ずつ進む（各節へ reloadKey として渡す値）', () => {
    expect(bumpVersion(0)).toBe(1)
    expect(bumpVersion(bumpVersion(0))).toBe(2)
  })

  it('つながり画面から「つながり同士」を開いた線の節へ送る', () => {
    expect(vocabScopeHash('perspective')).toBe('#/vocab?scope=perspective')
    expect(vocabScopeHash('all')).toBe('#/vocab')
    expect(parseVocabHash(vocabScopeHash('perspective')).scope).toBe('perspective')
  })
})

describe('線の問いは題で見せる', () => {
  it('語の問いから tool_name → 題を引く（先に出た題を採る）', () => {
    const titles = cqTitleMap([
      term('a', { cqs: [{ tool_name: 'cq_a_count', title: 'Aは何件？', answering_datasets: 1 }] }),
      term('b', { cqs: [{ tool_name: 'cq_a_count', title: '別の題', answering_datasets: 0 }] }),
    ])
    expect(titles).toEqual({ cq_a_count: 'Aは何件？' })
    expect(cqLabel(titles, 'cq_a_count')).toBe('Aは何件？')
    expect(cqLabel(titles, 'cq_gone')).toBe('cq_gone')
    expect(cqLabel(undefined, 'cq_gone')).toBe('cq_gone')
  })
})

describe('線の一覧の行（両端の kind・問い）', () => {
  const line = (over: Partial<Alignment>): Alignment => ({
    alignment_iri: 'urn:l1',
    source: 'http://example.org/ds#Widget',
    target: 'https://example.org/sv#widget',
    relation: 'subClassOf',
    from_perspective: '',
    to_perspective: '',
    at: '',
    ...over,
  })
  const html = (a: Alignment, cqTitles?: Record<string, string>) =>
    renderToStaticMarkup(
      createElement(AlignmentRow, {
        a,
        relationLabel: (r) => r,
        removing: false,
        onRemove: () => {},
        cqTitles,
      }),
    )

  it('両端の kind を札で出す（データセットは id つき）', () => {
    const out = html(
      line({ source_kind: 'dataset', source_dataset: 'ds1', target_kind: 'shared' }),
    )
    expect(out).toContain('データセット · ds1')
    expect(out).toContain('>ことば<')
  })

  it('unknown と未設定は札なし', () => {
    expect(html(line({ source_kind: 'unknown', target_kind: 'unknown' }))).not.toContain('vocab-line-kind')
    expect(html(line({}))).not.toContain('vocab-line-kind')
  })

  it('問いは題で出し、引けなければ tool_name のまま', () => {
    expect(html(line({ cq: 'cq_w_count' }), { cq_w_count: 'Wは何件？' })).toContain('問い: Wは何件？')
    expect(html(line({ cq: 'cq_w_count' }), {})).toContain('問い: cq_w_count')
  })
})

describe('既存の語に問いを足す', () => {
  const row = (onAddCq?: (cq: { title: string; op: 'count' | 'values' }) => Promise<void>) =>
    renderToStaticMarkup(
      createElement(VocabTermRow, {
        term: term('a'),
        confirming: false,
        removing: false,
        onAskRemove: () => {},
        onCancelRemove: () => {},
        onRemove: () => {},
        onAddCq,
      }),
    )

  it('onAddCq があるときだけ「問いを足す」が出る', () => {
    expect(row(async () => {})).toContain('問いを足す')
    expect(row()).not.toContain('問いを足す')
  })
})
