// かんたんウィザードのボタンは、実在する画面にしか進まない。
//
// 「数の確認」（内部の step 6）は畳んだ画面で、描画の分岐が無い（ADR
// meaning-before-identity §7-4）。そこへ `setStep` すると、手順バーだけが動いて
// 本体は空のカードになり、押せるものが見直しの帯しか残らない。実際に「意味を
// つける」の戻るボタンが 6 を指したまま出荷された（2026-10-05 に実機で確認）。
// 画面を畳むときは、そこへ進むボタンの行き先も一緒に直す — それを落とす検査。
import { describe, expect, it } from 'vitest'
import en from '../i18n/locales/en/kantan.json'
import ja from '../i18n/locales/ja/kantan.json'
import source from './KantanWizard.tsx?raw'

/** 描画の分岐を持つ step。`) : step === N ? (` の連なりから読む。
 *  5 は `showS5`（進行の画面）、4 は連なりの最後の else（⑤の形のゲート）が描く。 */
function renderedSteps(src: string): Set<number> {
  const steps = new Set<number>([4, 5])
  for (const m of src.matchAll(/\) : step === (\d+) \? \(/g)) steps.add(Number(m[1]))
  return steps
}

/** `setStep(...)` に書かれた行き先。三項演算子の枝（`a ? 6 : 2`）も拾う。
 *  変数を渡している呼び出しは対象外（数字が書かれていないので読めない）。 */
function stepTargets(src: string): { step: number; line: number }[] {
  const out: { step: number; line: number }[] = []
  for (const m of src.matchAll(/\bsetStep\(/g)) {
    const start = m.index + m[0].length
    let depth = 1
    let end = start
    while (end < src.length && depth > 0) {
      if (src[end] === '(') depth += 1
      else if (src[end] === ')') depth -= 1
      end += 1
    }
    const arg = src.slice(start, end - 1)
    const line = src.slice(0, m.index).split('\n').length
    // 値の位置にある数字だけ — 先頭か `?` `:` の直後で、末尾か `:` `,` の直前。
    for (const v of arg.matchAll(/(?:^|[?:])\s*(\d+)\s*(?=$|[:,])/g)) {
      out.push({ step: Number(v[1]), line })
    }
  }
  return out
}

describe('ウィザードの行き先', () => {
  const rendered = renderedSteps(source)
  const targets = stepTargets(source)

  it('描画の分岐と行き先を、ソースから読めている', () => {
    // 書き方が変わって何も拾えなくなったら、下の検査は黙って通ってしまう。
    for (const step of [1, 2, 3, 7, 8, 9, 10, 11, 12]) expect(rendered).toContain(step)
    expect(targets.length).toBeGreaterThan(20)
  })

  it('setStep の行き先は、どれも描画の分岐を持つ', () => {
    const deadEnds = targets
      .filter(({ step }) => !rendered.has(step))
      .map(({ step, line }) => `KantanWizard.tsx:${line} → step ${step}`)
    expect(deadEnds).toEqual([])
  })
})

describe('「ためす」へ戻るボタンの名前', () => {
  // 「意味をつける」と「公開する」の両方から同じ画面へ戻る。名前が違うと、
  // 別の場所へ行くボタンに読める。
  for (const [name, locale] of [
    ['ja', ja],
    ['en', en],
  ] as const) {
    it(`${name}: meanings.backToTry と s8.back は同じ言葉`, () => {
      expect(locale.meanings.backToTry).toBe(locale.s8.back)
    })
  }
})

describe('畳んだ「数の確認」の言葉', () => {
  // 画面を畳んだら、その名前とボタンの文言も辞書から消す。残っていると、
  // マニュアルの照合テスト（api/tests/test_design_consult.py）は「辞書のどこかに
  // 在る」で通すので、無い画面・無いボタンの案内が素通りする（2026-10-05 に
  // 実例: getting-started.md の「まだ取り込んでいない項目」「すべて取り込む」）。
  // 生きている文言が、消したボタンを名指していないことも同じ検査で見る
  // （実例: 「ためす」の失敗の案内が「この意味で確定」で進めと言っていた）。
  const retired = {
    ja: [
      '数の確認',
      'この意味で確定',
      'まだ取り込んでいない項目',
      'すべて取り込む',
      'すべて取り込まない',
      '空の意味に元の列名を使う',
    ],
    en: [
      'Check the counts',
      'Confirm these meanings',
      'Items not yet included',
      'Include all',
      'Leave all out',
      'Use the original column name for empty meanings',
    ],
  }

  /** 辞書の文言を、キーの道筋つきで全部ならべる。 */
  function strings(value: unknown, path = ''): { key: string; text: string }[] {
    if (typeof value === 'string') return [{ key: path, text: value }]
    if (value === null || typeof value !== 'object') return []
    return Object.entries(value).flatMap(([k, v]) => strings(v, path ? `${path}.${k}` : k))
  }

  for (const [name, locale] of [
    ['ja', ja],
    ['en', en],
  ] as const) {
    it(`${name}: 辞書のどの文言にも残っていない`, () => {
      const hits = strings(locale)
        .filter(({ text }) => retired[name].some((word) => text.includes(word)))
        .map(({ key, text }) => `${key}: ${text}`)
      expect(hits).toEqual([])
    })
  }
})

describe('④を畳むときの行き先（ADR upper-structure-shared-terms §2.5.2）', () => {
  /** 関数の本体（最初の `{` から対応する `}` まで）。 */
  function bodyOf(src: string, head: string): string {
    const at = src.indexOf(head)
    expect(at).toBeGreaterThan(-1)
    const open = src.indexOf('{', at)
    let depth = 0
    for (let i = open; i < src.length; i += 1) {
      if (src[i] === '{') depth += 1
      else if (src[i] === '}' && --depth === 0) return src.slice(open, i + 1)
    }
    return ''
  }

  it('③の「この意味で進む」は、④が畳まれていれば runAssemble を直接呼ぶ', () => {
    const body = bodyOf(source, 'function onMeaningsSettled()')
    expect(body).toContain('numberStepMode')
    expect(body).toContain('runAssemble()')
    expect(body).toContain('setStep(11)')
  })

  it('⑤の戻るは、④が畳まれていれば③（10）へ、出ていれば④（11）へ', () => {
    const at = source.indexOf('onBackToLinks={() => {')
    const body = source.slice(at, at + 400)
    expect(body).toContain("numberStepMode(state.rows, state.excluded) === 'ask' ? 11 : 10")
  })
})
