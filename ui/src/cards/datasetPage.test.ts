import { describe, expect, it } from 'vitest'
import type { DatasetSummary, SubjectItem } from './cardsApi'
import { kindRows, meaningBandText, originLine, subjectsForDataset } from './DatasetPage'

// 分野語ゼロ（契約メモ §0）: 架空の 2 分野（図書館の貸出・気象観測）で確かめる。
// t() スタブは setTitle.test.ts と同じ流儀（key と options をそのまま素通し）。

const t = (key: string, options?: Record<string, unknown>): string => {
  const opts = options ? JSON.stringify(options) : ''
  return `${key}${opts}`
}

function summary(over: Partial<DatasetSummary> = {}): DatasetSummary {
  return {
    dataset_id: 'weather-open',
    label: '気象観測（見本）',
    origin: 'open',
    stage: 'promoted',
    license: null,
    snapshot: null,
    source_note: null,
    classes: [],
    is_demo: true,
    ...over,
  }
}

describe('meaningBandText', () => {
  it('promoted なら種類・項目・名前あり・単位ありを classes[] から合算する', () => {
    const s = summary({
      stage: 'promoted',
      classes: [
        { class_iri: 'https://example.org/Station', label: '観測所', count: 62, properties: 5, with_label: 5, with_unit: 1 },
        { class_iri: 'https://example.org/Reading', label: '記録', count: 682, properties: 3, with_label: 3, with_unit: 0 },
      ],
    })
    const out = meaningBandText(s, t)
    expect(out).toContain('dataset.meaning_ok')
    expect(out).toContain('"classes":2')
    expect(out).toContain('"properties":8')
    expect(out).toContain('"withLabel":8')
    expect(out).toContain('"withUnit":1')
  })

  it('promoted でなければ「まだ取り込んでいません」の文言キーだけを返す', () => {
    const s = summary({ stage: 'ingested', classes: [{ class_iri: 'x', label: '観測所', count: 1, properties: 1, with_label: 1, with_unit: 0 }] })
    expect(meaningBandText(s, t)).toBe('dataset.meaning_pending')
  })
})

describe('originLine', () => {
  it('出どころだけのとき: ライセンス・版は出さない', () => {
    const out = originLine(summary({ origin: 'open' }), t)
    expect(out).toBe('dataset.origin_open')
  })

  it('出どころの説明・ライセンス・版がすべてあるとき: 「・」でつなぐ（join キー経由）', () => {
    const out = originLine(
      summary({ origin: 'open', source_note: '気象庁（見本）', license: 'CC-BY-4.0', snapshot: 'v1' }),
      t,
    )
    expect(out).toContain('dataset.origin_with_note')
    expect(out).toContain('"origin":"dataset.origin_open"')
    expect(out).toContain('"note":"気象庁（見本）"')
    expect(out).toContain('dataset.license{"license":"CC-BY-4.0"}')
    expect(out).toContain('dataset.version{"snapshot":"v1"}')
    expect(out.split('dataset.join').length).toBe(3) // 2 回つなぐ = join が 2 回出てくる
  })

  it('ライセンス・版が無ければ、その項目は省く（K4: 版が無いのに出さない）', () => {
    const out = originLine(summary({ origin: 'own', license: null, snapshot: null }), t)
    expect(out).toBe('dataset.origin_own')
  })
})

describe('kindRows', () => {
  it('summary.classes の順のまま、種類ごとの label/count を並べる', () => {
    const s = summary({
      classes: [
        { class_iri: 'https://example.org/Station', label: '観測所', count: 62, properties: 5, with_label: 5, with_unit: 1 },
        { class_iri: 'https://example.org/Reading', label: '記録', count: 682, properties: 3, with_label: 3, with_unit: 0 },
      ],
    })
    expect(kindRows(s)).toEqual([
      { classIri: 'https://example.org/Station', label: '観測所', count: 62 },
      { classIri: 'https://example.org/Reading', label: '記録', count: 682 },
    ])
  })

  it('種類が無ければ空配列（呼び出し側が「種類ごと」自体を出し分ける）', () => {
    expect(kindRows(summary({ classes: [] }))).toEqual([])
  })
})

describe('subjectsForDataset', () => {
  function item(over: Partial<SubjectItem> & { id: string }): SubjectItem {
    return {
      kind: 'individual',
      label: null,
      class_label: null,
      source: 'own',
      card_count: null,
      match: null,
      subject_key: `i:https://example.org/${over.id}`,
      created_at: '2026-01-01T00:00:00Z',
      ...over,
    } as SubjectItem
  }

  it('dataset_id が一致するものだけを残す', () => {
    const a = item({ id: 'station-a', dataset_id: 'weather-open' })
    const b = item({ id: 'station-b', dataset_id: 'loan-own' })
    expect(subjectsForDataset([a, b], 'weather-open')).toEqual([a])
  })

  it('dataset_id が無い／一致しないものは空になる（「その他」節はレール側の役目）', () => {
    const a = item({ id: 'station-a' })
    expect(subjectsForDataset([a], 'weather-open')).toEqual([])
  })
})
