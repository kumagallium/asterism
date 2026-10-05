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
 *  5 は `showS5`（進行の画面）、4 は連なりの最後の else（ID のゲート）が描く。 */
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
