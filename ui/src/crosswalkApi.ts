// Client for the crosswalk HUB (ADR crosswalk-hub.md productize ①④).
//
// The hub is a thin, growing bridge that joins datasets on a shared concept (e.g.
// composition): one shared entity per normalized value reported by >=2 datasets. It
// is authored by multi-selecting promoted datasets and declaring each one's
// concept-bearing predicate (the human-vetted mapping claim — AI-assisted), then
// built. These calls go through the same /api proxy as galleryApi (so they are LIVE
// even under the preview's mock demo mode), and carry the write-auth token for the
// mutating routes (build/propose).
import { type JobHandle, subscribeJob } from './api'
import { authHeaders } from './authToken'
import i18n from './i18n'
import { type LlmCredentials, llmHeaders } from './settings/store'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export interface CrosswalkParticipant {
  dataset_id: string
  label: string
  predicate?: string
  /** Compound key: one predicate per key part, keyed by part name (the participant maps
   * each part to a predicate). Used instead of ``predicate`` (compound-keys ADR). */
  predicates?: Record<string, string>
}

/** One part of a (possibly compound) join key: a name + its normalizer (named or a
 * recipe). compound-keys ADR. */
export interface CrosswalkKeyPart {
  name: string
  normalizer?: string
  normalizer_recipe?: string[]
}

export interface CrosswalkConcept {
  name: string
  class_iri?: string
  link_predicate?: string
  normalizer?: string
  /** A declarative recipe (ordered closed-primitive ids) — when set, it IS the join
   * key (normalizer-recipes ADR). Built/saved with the perspective (no code). */
  normalizer_recipe?: string[]
  /** Compound key: the join key is the TUPLE of these parts' normalized values (every
   * part must match). Empty/absent = a single value from ``normalizer`` (compound-keys
   * ADR). */
  key_parts?: CrosswalkKeyPart[]
  participants: CrosswalkParticipant[]
}

export interface CrosswalkConfig {
  min_datasets: number
  concepts: CrosswalkConcept[]
}

/** The hub's registry meta facets (a subset; the catalog reads these). */
export interface CrosswalkMeta {
  id?: string
  name?: string
  crosswalk_perspective_id?: string
  crosswalk_participants?: string[]
  crosswalk_shared_compositions?: number
  crosswalk_built_at?: string
  crosswalk_concepts?: string[]
  triple_count?: number
}

export interface CrosswalkInfo {
  perspective_id?: string
  exists: boolean
  config: CrosswalkConfig | null
  dataset: CrosswalkMeta | null
}

/** One crosswalk PERSPECTIVE (multi-perspective ADR): a distinct lens with its own
 * config + graph + stats. The upper ontology is plural — a set of these. */
export interface CrosswalkPerspective {
  perspective_id: string
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

/** One asserted schema alignment BETWEEN two perspectives' terms (multi-perspective
 * ADR §Phase 2 — "視点をつなぐ"). A human-vetted, citable, reversible claim; never
 * auto-reasoned (Oxigraph runs no OWL reasoner). */
export interface Alignment {
  alignment_iri: string
  source: string
  target: string
  relation: string
  from_perspective: string
  to_perspective: string
  at: string
}

/** The asserted alignments + the CLOSED set of relations a human may assert. */
export interface AlignmentsResult {
  alignments: Alignment[]
  relations: string[]
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
    i18n.t('crosswalk:error.failed', {
      op,
      status: res.status,
      detail: detail ? `: ${detail}` : '',
    }),
  )
}

/** The persisted crosswalk config + the hub's stats (exists:false when no hub yet). */
export async function getCrosswalk(): Promise<CrosswalkInfo> {
  const res = await fetch(`${API_BASE}/api/crosswalk`)
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.fetch'))
  return (await res.json()) as CrosswalkInfo
}

/** List every crosswalk PERSPECTIVE (the upper ontology is plural). */
export async function getCrosswalks(): Promise<CrosswalkPerspective[]> {
  const res = await fetch(`${API_BASE}/api/crosswalks`)
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.fetchList'))
  return ((await res.json()) as { perspectives?: CrosswalkPerspective[] }).perspectives ?? []
}

/** Build (or rebuild) the DEFAULT (composition) perspective. With a config =
 * author/replace; without = rebuild. */
export async function buildCrosswalk(config?: CrosswalkConfig): Promise<BuildResult> {
  const res = await fetch(`${API_BASE}/api/crosswalk/build`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(config ? { config } : {}),
  })
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.build'))
  return (await res.json()) as BuildResult
}

/** Build (or rebuild) a NAMED perspective — author a new lens, or (with no config)
 * rebuild it from its persisted config (multi-perspective ADR). ``name`` is the human
 * label; ``perspectiveId`` is its slug. */
export async function buildPerspective(
  perspectiveId: string,
  config?: CrosswalkConfig,
  name?: string,
): Promise<BuildResult> {
  const res = await fetch(`${API_BASE}/api/crosswalk/${encodeURIComponent(perspectiveId)}/build`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ ...(config ? { config } : {}), name: name ?? '' }),
  })
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.build'))
  return (await res.json()) as BuildResult
}

/**
 * AI-assist: suggest each selected dataset's concept-bearing predicate (a DRAFT for
 * human review, never built). Needs the Anthropic key (LLM) + the write-auth token.
 */
export async function proposeCrosswalkMapping(
  datasetIds: string[],
  concept: string,
  creds: LlmCredentials | null,
): Promise<ProposeResult> {
  const res = await fetch(`${API_BASE}/api/crosswalk/propose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...llmHeaders(creds), ...authHeaders() },
    // The "why" reasons follow the UI language; predicate IRIs stay verbatim.
    body: JSON.stringify({ dataset_ids: datasetIds, concept, language: i18n.language || undefined }),
  })
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.propose'))
  return (await res.json()) as ProposeResult
}

// --- Discovery: the crosswalks that COULD exist, found in the data itself ---------

/** How many values each rung of the normalizer ladder matched — the evidence behind
 * "as they are 12 match; ignoring case and width, 215 do". */
export interface DiscoverTrial {
  normalizer: string
  matched: number
}

export interface DiscoverParticipant {
  dataset_id: string
  /** The crosswalk label (build config); not shown to people. */
  label: string
  /** The dataset's human name — the only identifier the simple tier displays. */
  name: string
  predicate: string
  predicate_label: string
  distinct_values: number
  matched: number
  coverage: number
  statements: number
  values_truncated: boolean
}

/** One shared value, with how each dataset actually spelled it (the evidence). */
export interface DiscoverSample {
  key: string
  raw: Record<string, string>
}

export interface DiscoverCandidate {
  id: string
  concept: string
  name: string
  perspective_id: string
  /** True when building this would REPLACE an existing crosswalk of the same id. */
  perspective_exists: boolean
  class_iri: string
  link_predicate: string
  normalizer: string
  normalizer_trials: DiscoverTrial[]
  matched: number
  score: number
  participants: DiscoverParticipant[]
  samples: DiscoverSample[]
  /** Closed-set caution ids; the wording lives in `crosswalk:create.flag.*`. */
  flags: string[]
  /** Buildable as-is — no assembly on the client (see {@link buildPerspective}). */
  build_config: CrosswalkConfig
}

export interface DiscoverResult {
  candidates: DiscoverCandidate[]
  scanned: {
    datasets: {
      dataset_id: string
      label: string
      name: string
      live_graph: string
      predicates_scanned: number
      predicates_truncated: boolean
      predicates_excluded: { iri: string; reason: string; sample: string; distinct: number }[]
    }[]
    datasets_skipped: { dataset_id: string; reason: string }[]
    datasets_truncated: boolean
    clusters_truncated: boolean
    candidates_truncated: boolean
  }
  limits: Record<string, unknown>
  cancelled: boolean
  queries: number
}

/**
 * Look for crosswalks that could exist, by comparing the promoted datasets' real
 * values. Deterministic and **key-free** (no LLM) — the entrance to connecting data
 * must not be an API-key prompt. Runs as a job because the scan grows with datasets ×
 * columns; subscribe to the returned handle for progress and the result.
 */
export function discoverCrosswalks(
  handlers: {
    onDone: (r: DiscoverResult) => void
    onError: (m: string) => void
    onRunning?: (data: Record<string, unknown>) => void
  },
  options?: { datasetIds?: string[] },
): Promise<JobHandle> {
  return fetch(`${API_BASE}/api/crosswalk/discover`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ dataset_ids: options?.datasetIds ?? [] }),
  }).then(async (res) => {
    if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.discover'))
    const { job_id } = (await res.json()) as { job_id: string }
    return subscribeJob<DiscoverResult>(job_id, handlers)
  })
}

/** The asserted schema alignments between perspectives + the closed relation set
 * (read-only). */
export async function getAlignments(): Promise<AlignmentsResult> {
  const res = await fetch(`${API_BASE}/api/crosswalk/alignments`)
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.fetchAlignments'))
  const j = (await res.json()) as { alignments?: Alignment[]; relations?: string[] }
  return { alignments: j.alignments ?? [], relations: j.relations ?? [] }
}

/**
 * Assert a schema relationship (``relation`` from the closed set) between two
 * perspectives' terms — "視点をつなぐ". Additive, reversible, human-gated; needs the
 * write-auth token. Returns the asserted alignment.
 */
export async function align(
  source: string,
  target: string,
  relation: string,
  fromPerspective?: string,
  toPerspective?: string,
): Promise<Alignment> {
  const res = await fetch(`${API_BASE}/api/crosswalk/align`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      source,
      target,
      relation,
      from_perspective: fromPerspective ?? '',
      to_perspective: toPerspective ?? '',
    }),
  })
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.align'))
  return (await res.json()) as Alignment
}

/** Withdraw a previously asserted alignment (the reversible counterpart of
 * {@link align}). Needs the write-auth token. */
export async function unalign(source: string, target: string, relation: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/crosswalk/align`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ source, target, relation, remove: true }),
  })
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.unalign'))
}

/** Apply a declarative normalizer recipe to sample values — the join keys it would
 * produce, so a human can vet a custom normalizer before building it. Read-only. */
export async function previewNormalizer(
  recipe: string[],
  samples: string[],
): Promise<{ input: string; output: string }[]> {
  const res = await fetch(`${API_BASE}/api/crosswalk/normalizer/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ recipe, samples }),
  })
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.previewNormalizer'))
  return ((await res.json()) as { results?: { input: string; output: string }[] }).results ?? []
}
