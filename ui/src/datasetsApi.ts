// Dataset-centric fixtures for the redesigned Catalog / Home / Shared-vocab
// screens (design_handoff_asterism_ux: datasets are the entry point; vocabulary
// + mapping live inside each dataset; the shared part is promoted to its own
// board).
//
// Fixture-first, like demoApi.ts / galleryApi.ts: these illustrate the
// dataset-centric IA before a backend "datasets summary" endpoint exists, so the
// demo renders end-to-end. They are DEMO data (clearly badged in the UI). The
// real Starrydata dataset reuses the committed ontology structure (classes,
// reused vocabularies, mapping rules, purposes) so at least the headline dataset
// is truthful; the others (NIMS Supercon / 実験ノート) are illustrative.
//
// A later `live` mode can replace these with /api aggregates without touching
// the views (same shapes).

export type DatasetStatusKind = 'pub' | 'draft' | 'design'
export type SourceKind = 'csv' | 'api' | 'json' | 'db'

export interface DatasetCount {
  value: string // mono-rendered (e.g. "1.2M", "45k", "—")
  label: string
}

export interface DatasetReuse {
  prefix: string
  what: string
}

export interface DatasetRule {
  source: string // source field(s)
  target: string // vocabulary term (plain JP)
  convert: string // transform (chip)
}

export interface DatasetArtifact {
  kind: string // short uppercase tag (MIE / CODE)
  name: string
  detail: string
}

export interface DatasetEntry {
  id: string
  name: string
  /** "starrydata · CSV 3種" — short provenance/source line (mono). */
  sub: string
  source: SourceKind
  status: DatasetStatusKind
  counts: DatasetCount[]
  /** Questions this dataset can answer (purpose tags). */
  purposes: string[]
  /** Own classes (plain JP + english code). */
  classes: { ja: string; en: string }[]
  /** Vocabularies reused instead of re-minting (the "Reuse" story). */
  reuses: DatasetReuse[]
  /** Mapping rules (source field → vocab term, with transform). */
  rules: DatasetRule[]
  /** Generated mapping artifacts (machine-readable + ingester). */
  artifacts: DatasetArtifact[]
}

export const STATUS_LABEL: Record<DatasetStatusKind, string> = {
  pub: '公開済み',
  draft: '下書き',
  design: '設計中',
}

export const SOURCE_LABEL: Record<SourceKind, string> = {
  csv: '表計算 / CSV',
  api: 'API 連携',
  json: 'JSON',
  db: 'DB',
}

// Headline dataset — mirrors the committed starrydata ontology/mapping
// (docs/ontology, ingest/.../starrydata.py, MIE yaml) so it stays truthful.
const STARRYDATA: DatasetEntry = {
  id: 'starrydata',
  name: 'Starrydata 熱電データ',
  sub: 'starrydata · CSV 3種',
  source: 'csv',
  status: 'pub',
  counts: [
    { value: '1.2M', label: '事実' },
    { value: '45k', label: '試料' },
    { value: '12k', label: '論文' },
  ],
  purposes: ['熱電性能の探索', '組成検索', '単位の正規化 (QUDT)', '来歴トレース', '論文メタ参照'],
  classes: [
    { ja: '論文', en: 'Paper' },
    { ja: '試料', en: 'Sample' },
    { ja: '測定曲線', en: 'Curve' },
    { ja: '記述子', en: 'Descriptor' },
    { ja: '取り込み記録', en: 'IngestionActivity' },
  ],
  reuses: [
    { prefix: 'qudt:', what: '物性名・単位' },
    { prefix: 'schema:', what: '論文メタ' },
    { prefix: 'prov:', what: '来歴' },
    { prefix: 'dcterms:', what: 'ID' },
  ],
  rules: [
    { source: 'SID + sample_id', target: '試料のID', convert: '複合キー' },
    { source: 'composition', target: '組成', convert: 'そのまま' },
    { source: 'Seebeck_coef', target: 'ゼーベック係数', convert: 'QUDT単位' },
    { source: 'Seebeck_unit', target: '単位', convert: '表記ゆれ正規化' },
    { source: 'temperature', target: '測定温度', convert: '°C→K' },
    { source: 'doi', target: '論文', convert: 'schema:同定' },
  ],
  artifacts: [
    { kind: 'MIE', name: 'mapping.json', detail: '機械可読の対応表' },
    { kind: 'CODE', name: 'ingester.py', detail: '実際の取り込み処理' },
  ],
}

const NIMS_SUPERCON: DatasetEntry = {
  id: 'nims-supercon',
  name: 'NIMS Supercon',
  sub: '超伝導体 · API 連携',
  source: 'api',
  status: 'draft',
  counts: [
    { value: '8.2k', label: '事実' },
    { value: '320', label: '試料' },
    { value: '88', label: '論文' },
  ],
  purposes: ['臨界温度の探索', '組成検索', '来歴トレース'],
  classes: [
    { ja: '試料', en: 'Sample' },
    { ja: '記述子', en: 'Descriptor' },
    { ja: '臨界温度', en: 'CriticalTemperature' },
  ],
  reuses: [
    { prefix: 'qudt:', what: '温度・単位' },
    { prefix: 'prov:', what: '来歴' },
  ],
  rules: [
    { source: 'material', target: '組成', convert: 'そのまま' },
    { source: 'tc_kelvin', target: '臨界温度', convert: 'QUDT単位' },
    { source: 'reference', target: '論文', convert: 'schema:同定' },
  ],
  artifacts: [{ kind: 'MIE', name: 'mapping.json', detail: '機械可読の対応表（下書き）' }],
}

const LAB_NOTES: DatasetEntry = {
  id: 'lab-notes-2026q1',
  name: '実験ノート 2026Q1',
  sub: 'measurement · JSON',
  source: 'json',
  status: 'design',
  counts: [
    { value: '—', label: '事実' },
    { value: '54', label: '試料' },
    { value: '—', label: '論文' },
  ],
  purposes: ['測定値の整理', '来歴トレース'],
  classes: [
    { ja: '試料', en: 'Sample' },
    { ja: '測定', en: 'Measurement' },
  ],
  reuses: [{ prefix: 'prov:', what: '来歴' }],
  rules: [
    { source: 'specimen_id', target: '試料のID', convert: 'そのまま' },
    { source: 'value', target: '測定値', convert: '単位付与' },
  ],
  artifacts: [],
}

export const DATASETS: DatasetEntry[] = [STARRYDATA, NIMS_SUPERCON, LAB_NOTES]

// Home status band — graph-wide summary.
export interface CatalogStat {
  value: string
  label: string
  tone?: 'fg' | 'primary' | 'entity'
}
export const CATALOG_STATS: CatalogStat[] = [
  { value: '1.2M', label: '事実の数 / triples' },
  { value: '3', label: 'データセット' },
  { value: '5', label: '語彙のクラス', tone: 'primary' },
  { value: '100%', label: '出どころを追える', tone: 'entity' },
]

// ---- shared vocabulary board ----------------------------------------------

export interface SharedClass {
  ja: string
  en: string
  desc: string
  users: number
}
export const SHARED_VOCAB = {
  name: 'materials-core',
  version: 'v1.2',
  classes: [
    { ja: '試料', en: 'Sample', desc: '組成・相をもつ物質サンプル', users: 2 },
    { ja: '測定曲線', en: 'Curve', desc: '温度 vs 物性の一連の測定', users: 1 },
    { ja: '論文', en: 'Paper', desc: '出典の文献メタ', users: 1 },
    { ja: '記述子', en: 'Descriptor', desc: '物性名（QUDT に整合）', users: 2 },
    { ja: '取り込み記録', en: 'IngestionActivity', desc: 'いつ・何から作られたか', users: 2 },
  ] as SharedClass[],
}

// Binding strategy: how a dataset attaches to the shared vocabulary.
export type BindStrategy = 'reuse' | 'extend' | 'map' | 'new'
export const BIND_INFO: Record<BindStrategy, { label: string; en: string; tone: BindStrategy }> = {
  reuse: { label: 'そのまま使う', en: 'reuse', tone: 'reuse' },
  extend: { label: '広げる', en: 'extend', tone: 'extend' },
  map: { label: 'つなぐ', en: 'map-into', tone: 'map' },
  new: { label: '新規', en: 'new', tone: 'new' },
}

export interface VocabUser {
  name: string
  src: string
  binds: { cls: string; strat: BindStrategy }[]
}
export const VOCAB_USERS: VocabUser[] = [
  {
    name: 'Starrydata 熱電データ',
    src: 'CSV · 1.2M 事実',
    binds: [
      { cls: '試料', strat: 'reuse' },
      { cls: '測定曲線', strat: 'reuse' },
      { cls: '論文', strat: 'reuse' },
      { cls: '記述子', strat: 'map' },
    ],
  },
  {
    name: 'NIMS Supercon',
    src: 'API · 8.2k 事実',
    binds: [
      { cls: '試料', strat: 'reuse' },
      { cls: '記述子', strat: 'extend' },
      { cls: '臨界温度Tc', strat: 'new' },
    ],
  },
]

// These dataset fixtures are illustrative (demo). The UI badges them so the
// numbers aren't mistaken for live graph state.
export const isDemoDatasets = true
