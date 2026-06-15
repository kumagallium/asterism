// Preset domain-hint snippets (案 A). Each checkbox the user ticks appends its
// `text` to the domain hint sent to propose. These encode the pitfalls that
// past dogfooding showed actually change proposal quality (composite keys,
// PROV-O, bnode-free, unit normalization, JSON columns) — so a non-expert can
// produce a useful hint by ticking boxes instead of writing prose.

export interface PresetHint {
  id: string
  // i18n key (workbench namespace) for the checkbox label, resolved by the
  // consumer (WorkbenchView.tsx) via `t(hint.label)`.
  label: string
  text: string // appended to the domain hint (Markdown, for the LLM)
}

// NOTE: `text` is the snippet appended to the domain hint sent to the LLM, so it
// is left as fixed English prose (NOT translated). Only `label` (the on-screen
// checkbox text) is i18n-keyed.
export const PRESET_HINTS: PresetHint[] = [
  {
    id: 'composite-key',
    label: 'workbench:hints.compositeKey',
    text: 'ID columns (e.g. sample_id) may repeat across papers/files, so IRIs likely need a composite key (e.g. include the paper/source ID).',
  },
  {
    id: 'prov',
    label: 'workbench:hints.prov',
    text: 'PROV-O is required: every entity is a prov:Entity and every ingest run a prov:Activity.',
  },
  {
    id: 'no-bnode',
    label: 'workbench:hints.noBnode',
    text: 'Do NOT use blank nodes; mint stable IRIs so re-ingest is idempotent.',
  },
  {
    id: 'units',
    label: 'workbench:hints.units',
    text: 'Normalize physical quantities and units (reuse a shared vocabulary like QUDT; unify synonyms).',
  },
  {
    id: 'json-columns',
    label: 'workbench:hints.jsonColumns',
    text: 'Some columns embed JSON (arrays/objects); decide whether to expand into nodes or keep as JSON literals.',
  },
]
