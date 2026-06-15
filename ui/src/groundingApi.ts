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
