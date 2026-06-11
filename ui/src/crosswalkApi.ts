// Client for the crosswalk HUB (ADR crosswalk-hub.md productize ①④).
//
// The hub is a thin, growing bridge that joins datasets on a shared concept (e.g.
// composition): one shared entity per normalized value reported by >=2 datasets. It
// is authored by multi-selecting promoted datasets and declaring each one's
// concept-bearing predicate (the human-vetted mapping claim — AI-assisted), then
// built. These calls go through the same /api proxy as galleryApi (so they are LIVE
// even under the preview's mock demo mode), and carry the write-auth token for the
// mutating routes (build/propose).
import { authHeaders } from './authToken'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export interface CrosswalkParticipant {
  dataset_id: string
  label: string
  predicate: string
}

export interface CrosswalkConcept {
  name: string
  class_iri?: string
  link_predicate?: string
  normalizer?: string
  participants: CrosswalkParticipant[]
}

export interface CrosswalkConfig {
  min_datasets: number
  concepts: CrosswalkConcept[]
}

/** The hub's registry meta facets (a subset; the catalog reads these). */
export interface CrosswalkMeta {
  crosswalk_participants?: string[]
  crosswalk_shared_compositions?: number
  crosswalk_built_at?: string
  crosswalk_concepts?: string[]
  triple_count?: number
}

export interface CrosswalkInfo {
  exists: boolean
  config: CrosswalkConfig | null
  dataset: CrosswalkMeta | null
}

export interface BuildResult {
  hub_graph: string
  built_at: string
  triple_count: number
  shared_total: number
  shared: Record<string, string[]>
  links: Record<string, Record<string, number>>
  participants_used: { dataset_id: string; label: string }[]
  participants_skipped: { dataset_id: string; label: string; reason: string }[]
  dataset: CrosswalkMeta | null
}

/** One dataset's literal-valued predicate candidate, with a sample value. */
export interface PredicateCandidate {
  iri: string
  sample: string
}

export interface ProposeCandidate {
  dataset_id: string
  label: string
  predicates: PredicateCandidate[]
}

export interface ProposeResult {
  concept: string
  participants: { dataset_id: string; predicate: string; why: string }[]
  candidates: ProposeCandidate[]
  skipped: { dataset_id: string; reason: string }[]
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
  return new Error(`${op}失敗 (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
}

/** The persisted crosswalk config + the hub's stats (exists:false when no hub yet). */
export async function getCrosswalk(): Promise<CrosswalkInfo> {
  const res = await fetch(`${API_BASE}/api/crosswalk`)
  if (!res.ok) throw await asError(res, 'クロスウォークの取得')
  return (await res.json()) as CrosswalkInfo
}

/** Build (or rebuild) the hub. With a config = author/replace; without = rebuild. */
export async function buildCrosswalk(config?: CrosswalkConfig): Promise<BuildResult> {
  const res = await fetch(`${API_BASE}/api/crosswalk/build`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(config ? { config } : {}),
  })
  if (!res.ok) throw await asError(res, 'クロスウォークの構築')
  return (await res.json()) as BuildResult
}

/**
 * AI-assist: suggest each selected dataset's concept-bearing predicate (a DRAFT for
 * human review, never built). Needs the Anthropic key (LLM) + the write-auth token.
 */
export async function proposeCrosswalkMapping(
  datasetIds: string[],
  concept: string,
  apiKey: string,
): Promise<ProposeResult> {
  const res = await fetch(`${API_BASE}/api/crosswalk/propose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey, ...authHeaders() },
    body: JSON.stringify({ dataset_ids: datasetIds, concept }),
  })
  if (!res.ok) throw await asError(res, 'AI 提案')
  return (await res.json()) as ProposeResult
}
