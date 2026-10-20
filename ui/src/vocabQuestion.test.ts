import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { beforeAll, describe, expect, it } from 'vitest'
import { parseHash } from './App'
import type { Alignment } from './crosswalkApi'
import i18n from './i18n'
import type { GroundCandidate } from './groundingApi'
import type { SharedTerm } from './vocabApi'
import { AlignmentRow } from './PerspectiveAlignment'
import {
  buildMintInput,
  bumpVersion,
  cqLabel,
  cqTitleMap,
  defaultCqTitle,
  initialCqTitle,
  isolatedCount,
  parseVocabHash,
  pickExactStandard,
  resumeDraftHash,
  showIsolatedNotice,
  slugFromText,
  suggestSlug,
  vocabQuestionHash,
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

describe('孤立（まだ答えるデータがない）の札と注意', () => {
  it('wired=false を数え、20 を超えたときだけ注意を出す', () => {
    const many = Array.from({ length: 21 }, (_, i) => term(`t${i}`, { wired: false }))
    expect(isolatedCount(many)).toBe(21)
    expect(showIsolatedNotice(many)).toBe(true)
    expect(showIsolatedNotice(many.slice(0, 20))).toBe(false)
    expect(showIsolatedNotice([...many.slice(0, 20), term('ok')])).toBe(false)
  })
})

describe('導線の hash', () => {
  it('Ask から題を運ぶ（解釈せずそのまま・エンコードして）', () => {
    expect(vocabQuestionHash('試料は何件？ A/B')).toBe(
      `#/questions?new=1&q=${encodeURIComponent('試料は何件？ A/B')}`,
    )
    // 題が無くても、3 段（①）を開く入口のまま
    expect(vocabQuestionHash('  ')).toBe('#/questions?new=1')
  })

  it('運んだ題を読み戻せる・scope と dataset も読む', () => {
    const q = '試料は何件？ A/B'
    expect(parseVocabHash(vocabQuestionHash(q)).q).toBe(q)
    // 旧 scope / dataset は「決めたこと」へ移った（読まない）
    expect(parseVocabHash('#/vocab?scope=standard&dataset=ds1')).toEqual({ q: '', resume: '' })
    expect(parseVocabHash('#/vocab')).toEqual({ q: '', resume: '' })
  })

  it('ホームの下書きの札から、続き（②）を開く hash を作って読み戻せる', () => {
    expect(parseVocabHash(resumeDraftHash('thermo-1'))).toEqual({ q: '', resume: 'thermo-1' })
    expect(resumeDraftHash('a b')).toBe('#/questions?new=1&resume=a%20b')
    expect(parseHash(resumeDraftHash('thermo-1'))).toEqual({ tab: 'vocab', newQuestion: true })
  })

  it('ルータは #/questions?…（旧 #/vocab?…も）を「知りたいこと」画面として開く', () => {
    expect(parseHash('#/questions?q=abc')).toEqual({ tab: 'vocab' })
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
    expect(out).toContain('>用語<')
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

describe('線の端の名前（表示名・標準は接頭辞つき）', () => {
  it('表示名を優先し、標準の語は prefix:local、無ければ IRI の末尾', async () => {
    const { endLabel } = await import('./vocabQuestion')
    expect(endLabel('https://x.invalid/onto#Record_50577d', 'dataset', 'レシピ')).toBe('レシピ')
    expect(endLabel('http://qudt.org/schema/qudt/hasUnit', 'standard', 'has unit')).toBe('qudt:hasUnit')
    expect(endLabel('https://x.invalid/onto#food', 'dataset', '')).toBe('food')
    expect(endLabel('https://x.invalid/onto#food')).toBe('food')
  })
})
