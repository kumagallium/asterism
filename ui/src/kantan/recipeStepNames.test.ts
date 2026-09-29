// 手順バーの名前と、その段で開く画面の見出しは同じ言葉にする
// （ADR meaning-before-identity §11 K47 の訂正）。同じ 1 画面を指す文言のキーが
// 2 つあり、片方だけ変えると食い違う — 実際に「つながりを選ぶ」（手順バー）と
// 「ID のつけかた」（見出し）が食い違ったまま出荷された。
import { describe, expect, it } from 'vitest'
import en from '../i18n/locales/en/kantan.json'
import ja from '../i18n/locales/ja/kantan.json'

type Locale = typeof ja

/** 手順バーのキー → その段の画面の見出しを持つ節。 */
const SAME_WORDS: [keyof Locale['recipe'], 'meanings' | 'links'][] = [
  ['step3', 'meanings'],
  ['step4', 'links'],
]

describe('手順バーの名前と画面の見出し', () => {
  for (const [name, locale] of [
    ['ja', ja],
    ['en', en],
  ] as const) {
    for (const [step, section] of SAME_WORDS) {
      it(`${name}: recipe.${step} と ${section}.title は同じ言葉`, () => {
        expect(locale[section].title).toBe(locale.recipe[step])
      })
    }
  }
})
