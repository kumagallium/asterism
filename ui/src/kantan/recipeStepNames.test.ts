// 手順バーの名前と、その段で開く画面の見出しは同じ言葉にする
// （ADR meaning-before-identity §11 K47 の訂正）。同じ 1 画面を指す文言のキーが
// 2 つあり、片方だけ変えると食い違う — 実際に手順バーと見出しが別の名前の
// まま出荷された（④の旧名）。④は「番号を選ぶ」で、畳んだときの理由も辞書にある。
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

describe('④を畳んだときの手順バーの言葉', () => {
  for (const [name, locale] of [
    ['ja', ja],
    ['en', en],
  ] as const) {
    it(`${name}: 自動で決まった／仮置きの 2 文言があり、互いに違う`, () => {
      expect(locale.recipe.step4Auto.length).toBeGreaterThan(0)
      expect(locale.recipe.step4Placeholder.length).toBeGreaterThan(0)
      expect(locale.recipe.step4Auto).not.toBe(locale.recipe.step4Placeholder)
    })

    it(`${name}: ④は問①だけ — ②の文言は③へ移って links には残っていない`, () => {
      const links = locale.links as Record<string, unknown>
      const link = locale.meanings.link as Record<string, unknown>
      // [links に在った鍵, ③での鍵]
      const moved: [string, string][] = [
        ['step2Title', 'title'],
        ['step2TitleSolo', 'title'],
        ['lead', 'lead'],
        ['whenYes', 'whenYes'],
        ['colLink', 'colLink'],
        ['askConsultPrefill', 'askConsultPrefill'],
      ]
      for (const [from, to] of moved) {
        expect(links[from]).toBeUndefined()
        expect(link[to]).toBeDefined()
      }
    })
  }

  it('ja: 手順バーの名前は「番号を選ぶ」', () => {
    expect(ja.recipe.step4).toBe('番号を選ぶ')
    expect(en.recipe.step4).toBe('Pick the number')
  })
})
