import { describe, expect, it } from 'vitest'
import enKantan from '../i18n/locales/en/kantan.json'
import jaKantan from '../i18n/locales/ja/kantan.json'
import wizardSource from './KantanWizard.tsx?raw'
import { tryWording, type TryWording } from './tryWording'

type Dict = Record<string, unknown>

/** `kantan:a.b` を辞書から引く。無ければ undefined。 */
function text(dict: Dict, key: string): string | undefined {
  expect(key.startsWith('kantan:')).toBe(true)
  let node: unknown = dict
  for (const part of key.slice('kantan:'.length).split('.')) {
    node = (node as Dict | undefined)?.[part]
  }
  return typeof node === 'string' ? node : undefined
}

const calm = { trialFailed: false, trialEmpty: false }
const firstRun = { reviewOnly: false, ...calm }
const review = { reviewOnly: true, ...calm }

describe('tryWording — 「ためす」の言葉は、その画面が実際にすることに合わせる', () => {
  it('初回の流れ・作り直したあとは、これまでどおり（下書きを読み、公開へ進む）', () => {
    expect(tryWording({ ...firstRun, readFrom: 'draft' })).toEqual({
      lead: 'kantan:s7.lead',
      failed: 'kantan:s7.failed',
      traceNote: 'kantan:s7.traceNote',
      forward: 'kantan:s7.ok',
    })
    expect(tryWording({ ...firstRun, trialFailed: true }).forward).toBe('kantan:s7.okNoTrial')
    expect(tryWording({ ...firstRun, trialEmpty: true }).forward).toBe('kantan:s7.okAnyway')
  })

  it('見直しでまだ作り直していないとき、先へ進むボタンは「見直しを終了」', () => {
    expect(tryWording(review).forward).toBe('kantan:redesign.confirmNoChange')
    expect(tryWording({ ...review, trialFailed: true }).forward).toBe(
      'kantan:redesign.finishNoTrial',
    )
    expect(tryWording({ ...review, trialEmpty: true }).forward).toBe('kantan:redesign.finishAnyway')
    expect(tryWording({ ...review, trialFailed: true }).failed).toBe('kantan:redesign.trialFailed')
  })

  it('見直しでまだ作り直していないとき、冒頭の文はサーバが読んだデータで言い分ける', () => {
    expect(tryWording({ ...review, readFrom: 'published' }).lead).toBe(
      'kantan:s7.leadReviewPublished',
    )
    expect(tryWording({ ...review, readFrom: 'draft' }).lead).toBe('kantan:s7.leadReviewDraft')
    // まだ読めていない・古いサーバ: どちらとも言わない。
    expect(tryWording({ ...review, readFrom: null }).lead).toBe('kantan:s7.leadReview')
    expect(tryWording(review).lead).toBe('kantan:s7.leadReview')
  })

  it('公開をやめているデータセットは「公開済み」と言わない', () => {
    const words = tryWording({ ...review, readFrom: 'retracted' })
    expect(words.lead).toBe('kantan:s7.leadReview')
    expect(words.traceNote).toBe('kantan:s7.traceNote')
  })

  it('「公開すると開けるようになります」は、公開した版を読んだときには言わない', () => {
    expect(tryWording({ ...review, readFrom: 'published' }).traceNote).toBe(
      'kantan:s7.traceNotePublished',
    )
    expect(tryWording({ ...review, readFrom: 'draft' }).traceNote).toBe('kantan:s7.traceNote')
    expect(tryWording(review).traceNote).toBe('kantan:s7.traceNote')
  })
})

// 見直しでまだ作り直していないときの「ためす」は、公開の画面に進まない。言葉が
// 「公開へ」「取り込めました」のまま残ると、画面が起きないことを言うことになる
// （実例: 公開済みのデータセットを見直しで開くと「公開前の下書きに取り込めました」
// 「問題なさそう — 公開へ」と出て、押すとデータセットのページへ戻った）。
describe('見直しでまだ作り直していないときの言葉は、公開も取り込みも約束しない', () => {
  const states: [string, TryWording][] = [
    ['答えが出た（公開した版）', tryWording({ ...review, readFrom: 'published' })],
    ['答えが出た（下書き）', tryWording({ ...review, readFrom: 'draft' })],
    ['まだ読めていない', tryWording(review)],
    ['問いを実行できなかった', tryWording({ ...review, trialFailed: true })],
    ['1 件も返らなかった', tryWording({ ...review, trialEmpty: true, readFrom: 'published' })],
  ]

  it.each(states)('%s — ja・en の辞書にある', (_name, words) => {
    for (const key of Object.values(words)) {
      expect(text(jaKantan, key), key).toBeTruthy()
      expect(text(enKantan, key), key).toBeTruthy()
    }
  })

  it.each(states)('%s — 先へ進むボタンと失敗の知らせは「公開」を言わない', (_name, words) => {
    for (const key of [words.forward, words.failed]) {
      expect(text(jaKantan, key), key).not.toContain('公開')
      expect(text(enKantan, key)?.toLowerCase(), key).not.toContain('publish')
    }
  })

  it.each(states)('%s — 冒頭の文は「取り込めました」と言わない', (_name, words) => {
    expect(text(jaKantan, words.lead)).not.toContain('取り込めました')
    expect(text(enKantan, words.lead)).not.toContain('is in the unpublished draft')
  })

  it('公開した版を読んだときは「公開済み」と言い、「公開前の下書き」と言わない', () => {
    const words = tryWording({ ...review, readFrom: 'published' })
    expect(text(jaKantan, words.lead)).toContain('公開済み')
    expect(text(jaKantan, words.lead)).not.toContain('下書き')
    expect(text(jaKantan, words.traceNote)).not.toContain('公開すると')
    expect(text(enKantan, words.lead)).not.toContain('draft')
    expect(text(enKantan, words.traceNote)).not.toContain('Once published')
  })
})

// 失敗の知らせは、その画面に実在するボタンの名前で次の手を言う。
describe('問いを実行できなかったときの知らせは、やり直すボタンの名前を引く', () => {
  it.each([
    ['ja', jaKantan],
    ['en', enKantan],
  ])('%s', (_lang, kantan) => {
    expect(kantan.s7.failed).toContain(kantan.s7.retry)
    expect(kantan.redesign.trialFailed).toContain(kantan.s7.retry)
  })
})

// 言葉を選ぶ場所は tryWording の 1 か所。ウィザードが「ためす」の言葉をじかに
// 書くと、動き（goPublish）と言葉がまた別々に変わる。
describe('ウィザードは「ためす」の言葉を tryWording に任せる', () => {
  it('動きと言葉が、同じ条件（reviewOnly）を見る', () => {
    expect(wizardSource).toContain('const reviewOnly = redesigning && !reingested')
    expect(wizardSource).toMatch(/function goPublish\(\) \{[^}]*?if \(reviewOnly\) \{\s*exitRedesign\(\)/)
    expect(wizardSource).toMatch(/tryWording\(\{\s*reviewOnly,/)
  })

  it('tryWording が選ぶキーを、ウィザードはじかに書かない', () => {
    const chosen = new Set<string>()
    for (const reviewOnly of [false, true]) {
      for (const [trialFailed, trialEmpty] of [
        [false, false],
        [true, false],
        [false, true],
      ]) {
        for (const readFrom of ['draft', 'published', 'retracted', null] as const) {
          const words = tryWording({ reviewOnly, trialFailed, trialEmpty, readFrom })
          for (const key of Object.values(words)) chosen.add(key)
        }
      }
    }
    expect(chosen.size).toBeGreaterThan(10)
    for (const key of chosen) expect(wizardSource, key).not.toContain(`'${key}'`)
  })
})
