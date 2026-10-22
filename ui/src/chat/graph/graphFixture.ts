// 1 枚の知識の図の試験に使う作り物（熱電材料 8 概念・7 関係・4 つの星座）。時刻・乱数は使わない。
import type { DraftSky, SkyData, SkyDataset, SkyKind, SkyTerm } from '../../sky/skyApi'
import { EMPTY_SKY } from '../../sky/skyApi'

const NS = 'https://example.org/g#'
const kind = (ds: string, name: string, label: string, count: number): SkyKind => ({ iri: `${NS}${ds}/${name}`, label, count, links: [], standard: null })

export const DATASETS: SkyDataset[] = [
  { id: 'sd', label: 'Starrydata', created_at: '2026-01-01T00:00:00Z', kinds: [kind('sd', 'Sample', '試料', 46200), kind('sd', 'Paper', '論文', 8120), kind('sd', 'Curve', '測定曲線', 238000), kind('sd', 'Author', '著者', 21400)] },
  { id: 'mp', label: 'Materials Project', created_at: '2026-01-02T00:00:00Z', kinds: [kind('mp', 'Material', '材料', 154000), kind('mp', 'Bandgap', 'バンドギャップ', 90000)] },
  { id: 'nims', label: 'NIMS', created_at: '2026-01-03T00:00:00Z', kinds: [kind('nims', 'Thermal', '熱伝導率', 3200), kind('nims', 'Seebeck', 'ゼーベック係数', 2900), kind('nims', 'Misc', 'メモ', 12)] },
  { id: 'price', label: '価格表', created_at: '2026-01-04T00:00:00Z', kinds: [kind('price', 'Price', '価格', 640)], proposed: true },
]

const k = (ds: string, name: string): string => `${NS}${ds}/${name}`
const term = (slug: string, label: string, labelEn: string, kindOf: 'class' | 'property', members: [string, string][], std?: string): SkyTerm => ({
  iri: `${NS}t/${slug}`,
  slug,
  label,
  label_en: labelEn,
  kind: kindOf,
  members: members.map(([ds, n]) => ({ iri: k(ds, n), dataset_id: ds, relation: 'subClassOf' as const })),
  standard: std ? { iri: `${NS}std/${std}`, relation: 'equivalentClass' } : null,
  answering: 0,
  questions: [],
})
export const tIri = (slug: string): string => `${NS}t/${slug}`

/** 8 概念・7 関係（一部 2・下げる 2・上げる 1・変える 2）・4 データセット。 */
export const THERMO: SkyData = {
  ...EMPTY_SKY,
  datasets: DATASETS,
  terms: [
    term('material', '材料', 'Material', 'class', [['sd', 'Sample'], ['mp', 'Material']]),
    term('paper', '論文', 'Paper', 'class', [['sd', 'Paper']]),
    term('measurement', '測定', 'Measurement', 'class', [['sd', 'Curve']]),
    term('thermal', '熱伝導率', 'Thermal conductivity', 'property', [['nims', 'Thermal']], 'ThermalConductivity'),
    term('seebeck', 'ゼーベック係数', 'Seebeck coefficient', 'property', [['nims', 'Seebeck']], 'SeebeckCoefficient'),
    term('zt', '性能指数 ZT', 'Figure of merit ZT', 'property', []),
    term('doping', 'ドーピング', 'Doping', 'class', []),
    term('carrier', 'キャリア濃度', 'Carrier concentration', 'property', [['mp', 'Bandgap']]),
  ],
  standards: [
    { iri: `${NS}std/ThermalConductivity`, label: 'ThermalConductivity', prefix: 'qudt' },
    { iri: `${NS}std/SeebeckCoefficient`, label: 'SeebeckCoefficient', prefix: 'qudt' },
  ],
  edges: [
    { from: tIri('measurement'), to: tIri('material'), relation: 'isPartOf', reason: null },
    { from: tIri('paper'), to: tIri('material'), relation: 'isPartOf', reason: null },
    { from: tIri('doping'), to: tIri('carrier'), relation: 'affects', reason: null },
    { from: tIri('thermal'), to: tIri('zt'), relation: 'lowers', reason: null },
    { from: tIri('seebeck'), to: tIri('zt'), relation: 'raises', reason: null },
    { from: tIri('carrier'), to: tIri('seebeck'), relation: 'affects', reason: null },
    { from: tIri('doping'), to: tIri('thermal'), relation: 'lowers', reason: null },
  ],
}

/** 提案の札 2 つ（1 つは既にある用語に結ぶ）。 */
export const DRAFTS: DraftSky[] = [
  {
    draft_id: 'dr1',
    theme_label: '結晶',
    concepts: [
      { key: 'a', label: '結晶構造', kind: 'class', use: 'new' },
      { key: 'b', label: '格子定数', kind: 'property', use: 'new' },
      { key: 'c', label: '材料', kind: 'class', use: 'existing:material' },
    ],
    relations: [
      { from: 'a', to: 'c', rel: 'part' },
      { from: 'b', to: 'a', rel: 'part' },
    ],
    questions: [],
  },
]
