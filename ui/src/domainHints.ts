// Preset domain-hint snippets (案 A). Each checkbox the user ticks appends its
// `text` to the domain hint sent to propose. These encode the pitfalls that
// past dogfooding showed actually change proposal quality (composite keys,
// PROV-O, bnode-free, unit normalization, JSON columns) — so a non-expert can
// produce a useful hint by ticking boxes instead of writing prose.

export interface PresetHint {
  id: string
  label: string // shown next to the checkbox (Japanese, plain)
  text: string // appended to the domain hint (Markdown, for the LLM)
}

export const PRESET_HINTS: PresetHint[] = [
  {
    id: 'composite-key',
    label: 'ID 列が他のファイル/論文を跨ぐと重複しうる（複合キーが必要かも）',
    text: 'ID columns (e.g. sample_id) may repeat across papers/files, so IRIs likely need a composite key (e.g. include the paper/source ID).',
  },
  {
    id: 'prov',
    label: '出典・来歴 (PROV-O) を必ず記録したい',
    text: 'PROV-O is required: every entity is a prov:Entity and every ingest run a prov:Activity.',
  },
  {
    id: 'no-bnode',
    label: '空白ノード (blank node) は使わない',
    text: 'Do NOT use blank nodes; mint stable IRIs so re-ingest is idempotent.',
  },
  {
    id: 'units',
    label: '単位や量を正規化したい（同義語の統一など）',
    text: 'Normalize physical quantities and units (reuse a shared vocabulary like QUDT; unify synonyms).',
  },
  {
    id: 'json-columns',
    label: 'セル内に JSON（配列やオブジェクト）が入っている列がある',
    text: 'Some columns embed JSON (arrays/objects); decide whether to expand into nodes or keep as JSON literals.',
  },
]
