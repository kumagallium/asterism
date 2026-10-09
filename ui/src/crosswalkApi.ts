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
  /** DISPLAY ONLY, added by the server when it reads the config back: the dataset's
   * current name and the field's human label. Never part of the stored config — the
   * saved claim is the id + the predicate IRI. */
  name?: string
  predicate_label?: string
  /** The kind (class IRI) whose entities carry the value — what makes a shared
   * naming predicate (rdfs:label on every value catalog) a field of ONE kind
   * (crosswalk-kind-scoped-fields.md). Absent = any subject (legacy). */
  subject_class?: string
  /** Read-time enrichment: the kind's own name, for 「Composition › 試料化学組成」. */
  subject_class_label?: string
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
  /** DISPLAY ONLY (see {@link CrosswalkParticipant}): the human label for what this
   * connects on, resolved when the config is read back. Absent → the UI falls back to
   * the key, or to "the value found in both" when the key is a minted placeholder. */
  concept_label?: string
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
  /** true when this perspective was created or grown by the server itself from
   * matching ☑ ticks (F15), not authored by a human picking candidates. */
  auto_linked?: boolean
  /** The dataset ids whose ☑ ticks contributed to the auto-link, when
   * {@link auto_linked} is true. */
  auto_linked_from?: string[]
}

export interface CrosswalkInfo {
  perspective_id?: string
  exists: boolean
  config: CrosswalkConfig | null
  dataset: CrosswalkMeta | null
  /** R1（契約メモ contract_b2_hub_names.md B2-2）— このつながりの表示名。人が
   * 付けた名前が無ければ参加している項目の表示名から組み立てた名前、それも
   * 無ければ「名前のないつながり」。 `perspectiveDisplayName` はこれを最優先
   * で読む。 */
  display_name?: string
}

/** One crosswalk PERSPECTIVE (multi-perspective ADR): a distinct lens with its own
 * config + graph + stats. The upper ontology is plural — a set of these. */
export interface CrosswalkPerspective {
  perspective_id: string
  config: CrosswalkConfig | null
  dataset: CrosswalkMeta | null
  /** R1（契約メモ contract_b2_hub_names.md B2-2）— {@link CrosswalkInfo.display_name}
   * と同じ。 */
  display_name?: string
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
  /** The kind the sampled values sit on; null/absent for untyped subjects. */
  subject_class?: string | null
  subject_class_label?: string | null
  /** The design's word for this kind's field (K8), when it has one. */
  label?: string | null
}

export interface ProposeCandidate {
  dataset_id: string
  label: string
  predicates: PredicateCandidate[]
}

export interface ProposeResult {
  concept: string
  participants: { dataset_id: string; predicate: string; subject_class?: string; why: string }[]
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
  /** 以下の 6 つは api が読み取り時に必ず付ける（古い行にも。ADR upper-structure-shared-terms.md
   * §2.3）。型が省略可なのは、これらが無かった頃の手組みデータ（テストの fixture など）を
   * 壊さないため。画面は `?? 'unknown'` / `?? false` で読む。 */
  source_kind?: AlignEndKind
  target_kind?: AlignEndKind
  /** 端が `dataset` のとき、そのデータセットの id。それ以外は null。 */
  source_dataset?: string | null
  target_dataset?: string | null
  /** この線を引いた理由の問い（CQ）。無ければ null。 */
  cq?: string | null
  /** 端が `dataset`／`shared` として記録されたのに、今は存在しない。切れている線。 */
  broken?: boolean
}

/** 線の端の種類（鋳造済みの共有語＝shared、標準語＝standard、素性不明＝unknown）。 */
export type AlignEndKind = 'dataset' | 'perspective' | 'shared' | 'standard' | 'unknown'

/** `getAlignments(scope)` の絞り込み。省略＝全部。 */
export type AlignmentScope = 'perspective' | 'standard' | 'shared' | 'dataset'

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

/** R6 (契約 contract_d_discover_existing.md): 既にある concept に、まだ参加して
 * いないデータセットだけを足す。合流先（perspectiveId・concept）は discover の
 * `existing` が決めたもの — ここは足すだけ。返り値は build 系と同じ形
 * + `participants_added`。 */
export interface JoinResult extends BuildResult {
  participants_added: { dataset_id: string; predicate: string }[]
}

export async function joinExistingConcept(
  perspectiveId: string,
  concept: string,
  participants: {
    dataset_id: string
    label: string
    predicate: string
    subject_class?: string | null
  }[],
): Promise<JoinResult> {
  const res = await fetch(
    `${API_BASE}/api/crosswalks/${encodeURIComponent(perspectiveId)}/join`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ concept, participants }),
    },
  )
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.build'))
  return (await res.json()) as JoinResult
}

/** つながり（perspective）を削除する。消えるのは hub グラフ（参加データの投影）と
 *  登録だけで、元のデータセットには触れない — 同じ設定でいつでも作り直せる。
 *  視点間の対応（alignment）は独立した事実なので残る。 */
export async function deletePerspective(perspectiveId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/crosswalk/${encodeURIComponent(perspectiveId)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.delete'))
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

/** The fields one promoted dataset can connect on, per kind, sampled from the store
 * and named with the design's words — the dropdown's options without an AI
 * (crosswalk-kind-scoped-fields.md). Empty for a dataset that is not promoted. */
export async function getCrosswalkFields(datasetId: string): Promise<PredicateCandidate[]> {
  const res = await fetch(`${API_BASE}/api/crosswalk/fields/${encodeURIComponent(datasetId)}`)
  if (!res.ok) throw await asError(res, i18n.t('crosswalk:error.ops.fetch'))
  return ((await res.json()) as { fields?: PredicateCandidate[] }).fields ?? []
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
  /** The kind this field sits on (null for untyped subjects) and its name. */
  subject_class?: string | null
  subject_class_label?: string | null
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

/** One participant slot, as it appears in {@link DiscoverExisting}'s `linked`/`new`. */
export interface DiscoverExistingSlot {
  dataset_id: string
  predicate: string
  subject_class: string | null
}

/** 契約 contract_d_discover_existing.md R4: このスロットの組が、すでにある
 * つながり（concept）と重なっているときだけ、候補に付く。合流先は名前ではなく
 * 参加者そのもの（`match_existing`）で決まる — この情報を使って足すのが R6。 */
export interface DiscoverExisting {
  perspective_id: string
  concept: string
  /** 候補の参加者のうち、その concept にそのデータセットがもう参加しているもの。 */
  linked: DiscoverExistingSlot[]
  /** まだ参加していないデータセットの参加者 — R6 の API で足せるもの。 */
  new: DiscoverExistingSlot[]
  /** `new` が空 = 足せるものが無い（もう全員つながっている）。 */
  already_linked: boolean
}

export interface DiscoverCandidate {
  id: string
  concept: string
  /** The human label for what this connects on, when the server could resolve one
   * from the participants' designs (K8). The ascii `concept` key stays internal. */
  concept_label?: string
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
  /** Present only when this candidate's slots overlap an already-built concept
   * (R4). Absent = a fresh candidate — nothing joins it. */
  existing?: DiscoverExisting
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
    /** `existing`.`already_linked` が真の候補の数（契約 R4）。 */
    already_linked: number
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
export async function getAlignments(scope?: AlignmentScope): Promise<AlignmentsResult> {
  const qs = scope ? `?scope=${encodeURIComponent(scope)}` : ''
  const res = await fetch(`${API_BASE}/api/crosswalk/alignments${qs}`)
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
  /** この線を引く理由の問い（CQ）。共有語への線では必要。 */
  cq?: string,
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
      ...(cq ? { cq } : {}),
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
