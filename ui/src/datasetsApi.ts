// Data-source kinds for the "データを追加" source switcher. Only CSV is wired to
// a backend today; JSON/API/DB are shown but disabled ("近日対応") until the
// connect flow lands. (The dataset-centric Catalog/Home/Shared-vocab data lives
// in galleryApi.ts and is sourced ONLY from real backend data — no fixtures.)

export type SourceKind = 'csv' | 'api' | 'json' | 'db'

export const SOURCE_LABEL: Record<SourceKind, string> = {
  csv: '表計算 / CSV',
  api: 'API 連携',
  json: 'JSON',
  db: 'DB',
}
