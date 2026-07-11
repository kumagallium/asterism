// Data-source kinds for the "データを追加" source switcher. CSV and JSON (#19) are
// wired to a backend (Morph-KGC reads both via the RML's referenceFormulation);
// API/DB are shown but disabled ("近日対応") until their connect flow lands.
// (The dataset-centric Catalog/Home/Shared-vocab data lives in galleryApi.ts and
// is sourced ONLY from real backend data — no fixtures.)

export type SourceKind = 'csv' | 'api' | 'json' | 'db' | 'document'

// Values are i18n keys (workbench namespace), resolved by the consumer via
// `t(SOURCE_LABEL[kind])`. The sole consumer is WorkbenchView.tsx.
export const SOURCE_LABEL: Record<SourceKind, string> = {
  csv: 'workbench:source.csv',
  api: 'workbench:source.api',
  json: 'workbench:source.json',
  db: 'workbench:source.db',
  // A document is also data — it lives alongside the other source kinds. "Word /
  // XML" is the plain-language label (the .xml is the publisher's structured full
  // text; we avoid the jargon "JATS" in the UI).
  document: 'workbench:source.document',
}

// Which source kinds are actually wired end-to-end today (clickable pills + a
// working picker). The rest render disabled with a "近日" badge.
export const SUPPORTED_SOURCES: readonly SourceKind[] = ['csv', 'json', 'document']

// Tabular uploads also accept legacy instrument text exports (.tsv/.txt/.dat/.asc):
// the server sniffs + pins the source dialect at design time and normalizes at
// ingest (docs/architecture/source-dialect.md).
export const TABULAR_ACCEPT = '.csv,.tsv,.txt,.dat,.asc'

// The file picker's `accept` filter per wired source kind.
export const SOURCE_ACCEPT: Partial<Record<SourceKind, string>> = {
  csv: TABULAR_ACCEPT,
  json: '.json,.geojson',
}
