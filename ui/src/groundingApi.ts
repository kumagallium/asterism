// Client for the external-standard GROUNDING search (external-standard-alignment.md §8).
//
// Given a class/predicate name, the backend returns CANDIDATE real term IRIs from the
// curated, closed catalog (CMSO / QUDT / schema.org / PROV …) — never fabricated. The
// human picks one and confirms; adopting it asserts an alignment via crosswalkApi.align.
// These calls go through the same /api proxy as galleryApi (so they are LIVE even under
// the preview's mock demo mode), and are read-only (no auth needed).
import i18n from './i18n'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

/** One curated external vocabulary (metadata) Asterism recognizes + can ground to. */
export interface GroundVocabulary {
  prefix: string
  title: string
  namespace: string
  domain: string
  homepage: string
  term_count: number
}

/** One MINTED schema term + the external-standard candidates it could reuse/align to
 * (propose-time grounding suggestions). */
export interface SchemaTermGrounding {
  name: string
  kind: 'class' | 'property'
  source_curie: string
  candidates: GroundCandidate[]
}

/** A grounding candidate: a real external term + how strongly it matched the query. */
export interface GroundCandidate {
  iri: string
  curie: string
  prefix: string
  name: string
  kind: 'class' | 'property'
  label: string
  vocab_title: string
  domain: string
  score: number
  match: string
}

async function asError(res: Response, op: string): Promise<Error> {
  const text = await res.text().catch(() => '')
  let detail = text
  try {
    const j = JSON.parse(text) as { detail?: unknown }
    if (j && typeof j.detail === 'string') detail = j.detail
  } catch {
    /* not JSON — keep raw text */
  }
  return new Error(
    i18n.t('grounding:error.failed', { op, status: res.status, detail: detail ? `: ${detail}` : '' }),
  )
}

/** The curated known vocabularies (the recognized standards). Read-only. */
export async function getVocabularies(): Promise<GroundVocabulary[]> {
  const res = await fetch(`${API_BASE}/api/vocabularies`)
  if (!res.ok) throw await asError(res, i18n.t('grounding:op.vocabularies'))
  return ((await res.json()) as { vocabularies?: GroundVocabulary[] }).vocabularies ?? []
}

/**
 * Candidate external-standard terms for a class/predicate name, best first. Closed-set:
 * every candidate is a real catalog IRI (never invented); the human confirms the pick.
 */
export async function groundTerms(
  query: string,
  opts: { kind?: 'class' | 'property'; domain?: string; limit?: number } = {},
): Promise<GroundCandidate[]> {
  const params = new URLSearchParams({ q: query })
  if (opts.kind) params.set('kind', opts.kind)
  if (opts.domain) params.set('domain', opts.domain)
  if (opts.limit) params.set('limit', String(opts.limit))
  const res = await fetch(`${API_BASE}/api/ground?${params.toString()}`)
  if (!res.ok) throw await asError(res, i18n.t('grounding:op.search'))
  return ((await res.json()) as { candidates?: GroundCandidate[] }).candidates ?? []
}

/** Batch grounding for the 共通のことばの地図: many term names, ONE round trip
 *  (`POST /api/ground/terms`). The reply maps each sent name to its near-exact
 *  candidates (score >= 90 server-side) — names with no strong match are absent.
 *  Same closed catalog + determinism as `groundTerms`. Read-only. */
export async function groundTermsBatch(
  terms: { name: string; kind?: 'class' | 'property' }[],
): Promise<Record<string, GroundCandidate[]>> {
  if (terms.length === 0) return {}
  const res = await fetch(`${API_BASE}/api/ground/terms`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ terms }),
  })
  if (!res.ok) throw await asError(res, i18n.t('grounding:op.search'))
  return ((await res.json()) as { terms?: Record<string, GroundCandidate[]> }).terms ?? {}
}

/**
 * Propose-time grounding: for each class/predicate the proposed schema would MINT, the
 * matching standard candidates — so AI-assisted design surfaces "your data could lean on
 * cmso:/qudt:/…". Deterministic + closed-set (candidates never come from the LLM); the
 * model.yaml block is extracted from the propose markdown server-side. Read-only.
 */
export async function groundSchema(proposalMd: string): Promise<SchemaTermGrounding[]> {
  const res = await fetch(`${API_BASE}/api/ground/schema`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ proposal_md: proposalMd }),
  })
  if (!res.ok) throw await asError(res, i18n.t('grounding:op.schema'))
  return ((await res.json()) as { terms?: SchemaTermGrounding[] }).terms ?? []
}

// ─── units ───────────────────────────────────────────────────────────────────
// A unit is not one attribute among many: "300" alone is not a citable fact, and no
// RDF datatype carries the unit. So it gets its own closed catalog (a MIRROR of the
// QUDT unit vocabulary) and its own endpoint — the term search above answers a
// different question ("which class/property should this column reuse?").

/** One real QUDT unit a string resolved to. */
export interface UnitMatch {
  name: string
  iri: string
  curie: string
  label: string
  symbol: string | null
  ucum: string[]
  quantity_kinds: string[]
  matched_on: 'symbol' | 'ucum' | 'name' | 'label' | 'alias'
}

/** What a unit string resolved to. `unknown` is a real answer, not an error: it means
 *  the standard does not carry this unit, which is worth SAYING rather than hiding. */
export interface UnitResolution {
  query: string
  status: 'resolved' | 'ambiguous' | 'unknown'
  si_settled: boolean
  exact: UnitMatch[]
  suggestions: UnitMatch[]
  catalog: { source?: string; version?: string; retrieved?: string; license?: string }
}

/** Resolve one unit string against the closed QUDT catalog. Read-only. */
export async function resolveUnit(query: string): Promise<UnitResolution> {
  const res = await fetch(`${API_BASE}/api/units/resolve?${new URLSearchParams({ q: query })}`)
  if (!res.ok) throw await asError(res, i18n.t('grounding:op.unit'))
  return (await res.json()) as UnitResolution
}

// ─── quantity kinds ──────────────────────────────────────────────────────────
// The other half of the unit lookup: units say "in what", this says "of what". A
// dataset whose units reach the standard but whose properties do not is half
// connected — the quantity is what other people search on ("who else measured thermal
// conductivity?"), while the unit only says how it was written down.

/** One real QUDT QuantityKind a column could be measuring. */
export interface QuantityKindCandidate {
  name: string
  iri: string
  curie: string
  label: string
  gloss: string
  symbol: string | null
  units: string[]
  score: number
  /** How it matched: `exact` / `tokens_subset` / … / `unit` (found by unit alone). */
  match: string
  /** The column's unit is one this quantity may be measured in. */
  unit_fits: boolean
}

/**
 * Candidate quantity kinds for one column, best first. Pass the QUDT unit LOCAL NAME
 * already resolved for it (e.g. `V-PER-K`): it ranks name matches higher and, on its
 * own, offers the quantities that unit can express — which is how a column called `S`
 * still reaches Seebeck coefficient. Closed-set + deterministic (no LLM); a human
 * confirms before anything is asserted.
 */
export async function resolveQuantityKind(
  query: string,
  opts: { unit?: string; limit?: number } = {},
): Promise<QuantityKindCandidate[]> {
  const params = new URLSearchParams({ q: query })
  if (opts.unit) params.set('unit', opts.unit)
  if (opts.limit) params.set('limit', String(opts.limit))
  const res = await fetch(`${API_BASE}/api/quantitykinds/resolve?${params.toString()}`)
  if (!res.ok) throw await asError(res, i18n.t('grounding:op.quantityKind'))
  return ((await res.json()) as { candidates?: QuantityKindCandidate[] }).candidates ?? []
}
