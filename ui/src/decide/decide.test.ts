import { beforeAll, describe, expect, it } from 'vitest'
import i18n from '../i18n'
import type { PickEnd } from '../lineChoice'
import {
  NONE,
  type DecidePreview,
  currentPreview,
  decideGate,
  effectAfterPublish,
  previewKeyOf,
  type PreviewState,
  effectText,
  leadText,
  normalizeName,
  optionLabel,
  orderCandidates,
  reasonText,
  relationChoices,
  sentence,
 isSystemKindIri } from './decide'

// 純関数（文・候補の並び・帰結）と DecidePanel の初期表示。テストデータは架空。

beforeAll(async () => {
  await i18n.changeLanguage('ja')
})
const t = (key: string, opts?: Record<string, unknown>) => i18n.t(key, opts) as string

const end = (iri: string, label: string, role: PickEnd['role'], termKind: PickEnd['termKind']): PickEnd => ({
  id: iri,
  iri,
  label,
  role,
  termKind,
})
const kind = end('urn:k:record', '記録', 'dataset', 'class')

describe('orderCandidates — 標準用語 → 既にある用語 → 他のデータセットの種類（完全一致が先頭）', () => {
  const std = end('urn:std:s', '標準の語', 'standard', 'class')
  const word = end('urn:sv:w', '用語', 'shared', 'class')
  const other = end('urn:k:other', 'かんけいない種類', 'dataset', 'class')
  const exactDataset = end('urn:k:exact', '記録', 'dataset', 'class')
  const item = end('urn:p:i', '項目', 'dataset', 'property')

  it('役割の順に並べ、完全一致（正規化後）は役割に関わらず先頭', () => {
    const got = orderCandidates(kind, [other, word, std, exactDataset])
    expect(got.map((c) => c.iri)).toEqual(['urn:k:exact', 'urn:std:s', 'urn:sv:w', 'urn:k:other'])
  })

  it('結べないもの（種類と項目の混在）・自分自身・重複は落とす', () => {
    const got = orderCandidates(kind, [item, kind, word, word])
    expect(got.map((c) => c.iri)).toEqual(['urn:sv:w'])
  })

  it('項目は項目どうし・量の種類へ', () => {
    const prop = end('urn:p:me', '強度', 'dataset', 'property')
    const qk = end('http://qudt.org/vocab/quantitykind/Length', '長さ', 'standard', 'quantity')
    expect(orderCandidates(prop, [word, item, qk]).map((c) => c.iri)).toEqual([
      'http://qudt.org/vocab/quantitykind/Length',
      'urn:p:i',
    ])
  })

  it('名前の正規化は NFKC・大文字小文字・空白', () => {
    expect(normalizeName('  Ｓａｍｐｌｅ   A ')).toBe('sample a')
  })
})

describe('選べる関係と文', () => {
  it('種類どうしは 一種 → 同じもの の順、項目→量は hasQuantityKind、混在は無し', () => {
    const word = end('urn:sv:w', '用語', 'shared', 'class')
    const prop = end('urn:p:me', '強度', 'dataset', 'property')
    const qk = end('urn:q', '長さ', 'standard', 'quantity')
    expect(relationChoices(kind, word)).toEqual(['subClassOf', 'equivalentClass'])
    expect(relationChoices(prop, qk)).toEqual(['hasQuantityKind'])
    expect(relationChoices(kind, prop)).toEqual([])
  })

  it('決めた 1 文（5 つの関係）に「線」の語は入らない', () => {
    const rels = [
      ['subClassOf', '「A」は「B」の一種'],
      ['equivalentClass', '「A」は「B」と同じもの'],
      ['subPropertyOf', '「A」の値は「B」の一種'],
      ['equivalentProperty', '「A」の値は「B」と同じ'],
      ['hasQuantityKind', '「A」の量は「B」'],
    ] as const
    for (const [rel, want] of rels) {
      const s = sentence('A', 'B', rel, t)
      expect(s).toBe(want)
      expect(s).not.toContain('線')
    }
  })

  it('導入の文は種類・項目・量で言い方を変える', () => {
    const prop = end('urn:p:me', '強度', 'dataset', 'property')
    const qk = end('urn:q', '長さ', 'standard', 'quantity')
    expect(leadText(kind, undefined, t)).toBe('「記録」は')
    expect(leadText(prop, undefined, t)).toBe('「強度」の値は')
    expect(leadText(prop, qk, t)).toBe('この項目「強度」の量は')
    expect(optionLabel('subClassOf', t)).toBe('の一種です')
    expect(optionLabel(NONE, t)).toBe('当てはまるものは無い')
  })
})

describe('帰結の文', () => {
  const base: DecidePreview = { valid: true, reason: null, terms: [], questions: [] }

  it('問いごとに 「0 → 1」・変わらないものは（変わりません）', () => {
    const p: DecidePreview = {
      ...base,
      questions: [
        { tool_name: 'a', title: '回折点は何件？', before: 0, after: 1 },
        { tool_name: 'b', title: '強度は？', before: 2, after: 2 },
      ],
    }
    expect(effectText(p, t)).toBe(
      '問い「回折点は何件？」に答えるデータセット 0 → 1 / 問い「強度は？」に答えるデータセット 2（変わりません）',
    )
  })

  it('問いが無ければ、数は変わらないと言う', () => {
    expect(effectText(base, t)).toContain('変わりません')
  })

  it('公開前は「増える見込み」と今の数だけを言い、「1 → 2」と断定しない', () => {
    const s = effectAfterPublish([{ title: '組成は？', answering_datasets: 1 }], t)
    expect(s).toContain('増える見込みです（今は 1')
    expect(s).not.toContain('→')
    expect(effectAfterPublish([], t)).toContain('公開のときに決まります')
  })

  it('結べない理由は日本語で（新しい文言に「線」を入れない）', () => {
    expect(reasonText('mixed', t)).toBe('種類と項目はつなげません。')
    expect(reasonText('cycle', t)).toBe('循環になります。')
    expect(reasonText('unminted', t)).toBe('まだ作っていない用語です。')
    for (const r of ['mixed', 'cycle', 'unminted', 'same', null] as const) {
      expect(reasonText(r, t)).not.toContain('線')
    }
  })
})

describe('試算の状態と「そう決める」の可否', () => {
  const okPreview: DecidePreview = { valid: true, reason: null, questions: [], terms: [] }
  const ng: PreviewState = {
    key: 'a|b|subClassOf',
    status: 'ok',
    preview: { valid: false, reason: 'cycle', questions: [], terms: [] },
  }
  const ok: PreviewState = { key: 'a|b|subClassOf', status: 'ok', preview: okPreview }

  it('選ぶたびに鍵が変わる。「当てはまるものは無い」と未選択は試算しない', () => {
    expect(previewKeyOf('a', 'b', 'subClassOf')).toBe('a|b|subClassOf')
    expect(previewKeyOf('a', 'b', 'equivalentClass')).toBe('a|b|equivalentClass')
    expect(previewKeyOf('a', 'c', 'subClassOf')).toBe('a|c|subClassOf')
    expect(previewKeyOf('a', 'b', NONE)).toBe('')
    expect(previewKeyOf('a', 'b', null)).toBe('')
    expect(previewKeyOf('a', undefined, 'subClassOf')).toBe('')
  })

  it('古い鍵の結果は採らない', () => {
    expect(currentPreview(ok, 'a|b|equivalentClass')).toBeNull()
    expect(currentPreview(ok, '')).toBeNull()
    expect(currentPreview(ok, 'a|b|subClassOf')).toBe(ok)
    expect(currentPreview(null, 'a|b|subClassOf')).toBeNull()
  })

  it('未選択・結ぶ先なし・処理中・結べない試算では押せない', () => {
    const base = { busy: false, hasTarget: true, picked: 'subClassOf' as const, current: ok }
    expect(decideGate(base)).toEqual({ blocked: false, canDecide: true })
    expect(decideGate({ ...base, picked: null }).canDecide).toBe(false)
    expect(decideGate({ ...base, hasTarget: false }).canDecide).toBe(false)
    expect(decideGate({ ...base, busy: true }).canDecide).toBe(false)
    expect(decideGate({ ...base, current: ng })).toEqual({ blocked: true, canDecide: false })
  })

  it('試算がまだ・失敗でも決められる。「当てはまるものは無い」は試算なしで押せる', () => {
    const base = { busy: false, hasTarget: true, picked: 'subClassOf' as const }
    expect(decideGate({ ...base, current: null }).canDecide).toBe(true)
    expect(decideGate({ ...base, current: { key: 'k', status: 'error' } }).canDecide).toBe(true)
    expect(decideGate({ ...base, picked: NONE, current: null }).canDecide).toBe(true)
  })
})

describe('isSystemKindIri — 系統用の型は候補にしない', () => {
  it('つながりの内部・来歴・用語そのもの・RDF の配管は true', () => {
    expect(isSystemKindIri('https://kumagallium.github.io/asterism/crosswalk/ontology#CrosswalkLink')).toBe(true)
    expect(isSystemKindIri('http://www.w3.org/ns/prov#Activity')).toBe(true)
    expect(isSystemKindIri('https://kumagallium.github.io/asterism/vocab/shared#record')).toBe(true)
    expect(isSystemKindIri('http://www.w3.org/2002/07/owl#Class')).toBe(true)
  })
  it('データセットの種類は false', () => {
    expect(isSystemKindIri('https://asterism.invalid/datasets/prices/ontology#Food')).toBe(false)
    expect(isSystemKindIri('https://kumagallium.github.io/asterism/world/ontology#Country')).toBe(false)
  })
})
