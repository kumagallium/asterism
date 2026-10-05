import { describe, expect, it } from 'vitest'
import type { PublishedNames } from '../api'
import enKantan from '../i18n/locales/en/kantan.json'
import jaKantan from '../i18n/locales/ja/kantan.json'
import wizardSource from './KantanWizard.tsx?raw'
import { namesToPublish } from './publishedNames'

const waiting: PublishedNames = {
  dataset_id: 'd',
  available: true,
  changes: [{ iri: 'https://example.org/onto#price', kind: 'property', published: '売値', design: '税込価格' }],
}
const review = { datasetId: 'd', reviewOnly: true, readFrom: 'published' as const }

describe('namesToPublish — 見直しの「ためす」に出す「公開済みの項目名」', () => {
  it('公開した版を読んでいる、作り直していない見直しでだけ出す', () => {
    expect(namesToPublish({ ...review, names: waiting })).toEqual(waiting.changes)
  })

  it('下書きを読んでいるときは出さない（名前はその公開で一緒に出る）', () => {
    expect(namesToPublish({ ...review, readFrom: 'draft', names: waiting })).toEqual([])
  })

  it('初回の流れ・下書きを作り直したあとは出さない', () => {
    expect(namesToPublish({ ...review, reviewOnly: false, names: waiting })).toEqual([])
  })

  it('公開をやめている・まだ読めていないときは出さない', () => {
    expect(namesToPublish({ ...review, readFrom: 'retracted', names: waiting })).toEqual([])
    expect(namesToPublish({ ...review, readFrom: null, names: waiting })).toEqual([])
    expect(namesToPublish({ datasetId: 'd', reviewOnly: true, names: waiting })).toEqual([])
  })

  it('別のデータセットの答えは出さない（前の問い合わせがあとから届いたとき）', () => {
    expect(namesToPublish({ ...review, datasetId: 'other', names: waiting })).toEqual([])
    expect(namesToPublish({ ...review, datasetId: null, names: waiting })).toEqual([])
  })

  it('見くらべられなかった・違いが無いときは出さない', () => {
    expect(namesToPublish({ ...review, names: null })).toEqual([])
    expect(namesToPublish({ ...review, names: { ...waiting, available: false } })).toEqual([])
    expect(namesToPublish({ ...review, names: { ...waiting, changes: [] } })).toEqual([])
  })
})

// 帯は、名前を公開側へ出す道を「その画面に実在するボタンの名前」で言う。ボタンの
// 名前が変わったのに文だけ残ると、無いボタンを案内することになる。
describe('見直しの帯は、項目名を公開するボタンを実際の名前で言う', () => {
  it.each([
    ['ja', jaKantan],
    ['en', enKantan],
  ])('%s', (_lang, kantan) => {
    expect(kantan.redesign.bannerNote).toContain(kantan.s7.namesPublish)
  })
})

describe('ウィザードは、知らせに出すものを namesToPublish に任せる', () => {
  it('動き（goPublish）・言葉（tryWording）と同じ条件（reviewOnly）と、いまのデータセットを渡す', () => {
    expect(wizardSource).toMatch(/namesToPublish\(\{\s*datasetId: kzDatasetId,\s*reviewOnly,/)
    // reviewOnly の定義は 1 つ（tryWording.test.ts が、goPublish と tryWording の側を固定している）。
    expect(wizardSource.match(/const reviewOnly = /g)).toHaveLength(1)
  })

  it('名前を公開している最中は、「ためす」から出るボタンを押せない', () => {
    expect(wizardSource).toContain('disabled={trialLoading || namesBusy}')
    expect(wizardSource).toMatch(/onClick=\{backToMeanings\}\s*disabled=\{namesBusy\}/)
  })

  it('公開した版を読んだときだけ、公開されている名前を取りに行く', () => {
    expect(wizardSource).toMatch(
      /if \(got\.read_from === 'published'\) \{\s*void fetchPublishedNames\(datasetId\)/,
    )
  })
})
