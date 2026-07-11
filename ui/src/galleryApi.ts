// Data for the dataset-centric Catalog / Home screens.
//
// Two layers are presented separately on purpose (design doc §6.6 / D8):
//
//   - Ontology layer (shared vocabulary = TBox): slow-changing, SHARED, high
//     blast radius — editing it ripples to every downstream consumer.
//   - Mapping layer (dataset → vocabulary binding = ingester + MIE): fast-
//     changing, per-dataset/per-purpose, LOCAL and disposable.
//
// Making that edit-risk difference legible — and surfacing each mapping's
// PURPOSE — is the whole point of the two-layer split (handoff §1).
//
// LIVE-ONLY (no fixtures): every dataset shown here is REAL — the workbench-
// materialized drafts persisted to the api (/api/datasets), with their own
// designed vocabulary (model.yaml → classes), class diagram (diagram.md), and
// the external vocabularies they actually reuse (derived from the real term
// IRIs in each dataset's alignment report). The shared, cross-dataset vocabulary
// is introspected live from the store by SharedVocabView (demo-agent
// /demo/schema). Nothing is fabricated: when a signal is unavailable, the UI
// shows "—" or an explicit empty state rather than a placeholder.

import { fetchProposal } from './api'
import { authHeaders } from './authToken'
import i18n from './i18n'
import { deriveReuses } from './vocab'

// ---- edit-risk (the layer-distinction signal) -----------------------------

export type EditRisk = 'high' | 'low'

// Maps an Ask citation/provenance `kind` to the vocabulary class it is typed
// with, so the UI can link a grounded answer to the ontology that backs it.
// Only kinds that correspond to a class in the seeded TBox are listed.
export const KIND_TO_CLASS: Record<string, string> = {
  curve: 'Curve',
  sample: 'Sample',
  paper: 'Paper',
  ingestion: 'IngestionActivity',
}

// ---- ontology layer -------------------------------------------------------

export interface OntologyEntry {
  id: string
  name: string
  prefix: string
  baseIri: string
  description: string
  /** Own (minted) classes in this vocabulary. */
  classes: string[]
  /** Vocabularies reused instead of re-minting (the "Reuse" story, QUDT-style). */
  reuses: { prefix: string; what: string }[]
  /** Mermaid classDiagram source (rendered with mermaid.js). */
  mermaid: string
  editRisk: EditRisk
}

// ---- mapping layer --------------------------------------------------------

export type MappingArtifactKind = 'ingester' | 'mie' | 'shex' | 'mapping' | 'spec' | 'design'

export interface MappingArtifact {
  kind: MappingArtifactKind
  name: string
  /** i18n key (+ params), resolved at RENDER time so the artifact list follows
   * the active language (a fetch-time `i18n.t()` would bake the language in). */
  summaryKey: string
  summaryParams?: Record<string, string | number>
}

export interface MappingPurpose {
  /** Short tag shown prominently (the showcase signal — handoff §1). */
  tag: string
  /** One-line elaboration of what query/analysis this binding serves. */
  detail: string
}

export interface MappingEntry {
  id: string
  name: string
  dataset: string
  /** Which ontology (by id) this dataset is bound into. */
  targetOntologyId: string
  targetOntologyName: string
  description: string
  /** Purpose tags — WHY this mapping exists. Surfaced first, on purpose. */
  purposes: MappingPurpose[]
  artifacts: MappingArtifact[]
  editRisk: EditRisk
}

// ---- live datasets (materialized via the workbench, persisted to /api) ----
// These are what closes the authoring→catalog loop: a dataset materialized in
// the workbench is persisted (api V1a) and surfaces here. They are DRAFTS (a
// freshly designed TBox + mapping) until promoted into the canonical graph. The
// fetch is best-effort: if the workbench API is absent, the catalog renders an
// empty state (no error, no fixture).

// Workbench API base. Same-origin by default (Vite proxies /api → :8080);
// override with VITE_API_URL to point at a separately-hosted API.
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

/** Reuse/New split of a draft's predicates + classes vs the canonical graph. */
export interface AlignmentReport {
  predicates: { reuse: string[]; new: string[] }
  classes: { reuse: string[]; new: string[] }
}

interface DatasetMeta {
  id: string
  name: string
  created_at: string
  classes?: string[]
  class_count?: number
  complete?: boolean
  exit_code?: number
  has_ingester?: boolean
  has_mie?: boolean
  // Phase 5: declarative RML presence + draft-graph ingest status.
  has_rml?: boolean
  // Whether the reviewed §9 Mapping IR spec (mapping.yaml) is persisted — the
  // human-readable source the RML was deterministically compiled from.
  has_mapping_ir?: boolean
  // Redesign: whether the design (propose/refine Markdown) was persisted, so the
  // catalog can offer a "見直す" action that reopens it in the workbench.
  has_proposal?: boolean
  ingested?: boolean
  ingested_at?: string
  triple_count?: number
  graph_iri?: string
  // Task E: design-time source files persisted server-side (lets the catalog
  // ingest a design-stage dataset with no re-attach). source_kind (#19) labels the
  // source and picks the right file picker; "xml" marks a document dataset (its
  // accumulation feed is documents, not CSV/JSON batches).
  has_source?: boolean
  source_files?: string[]
  source_kind?: 'csv' | 'json' | 'xml'
  // S4: whether the draft was promoted into the canonical (default) graph.
  promoted?: boolean
  promoted_at?: string
  triples_promoted?: number
  // #20 P3 lifecycle: "active" | "retracted" (tombstoned) | "deleted"; version bumps on re-promote.
  status?: string
  version?: number
  // Reuse/New term split computed against canonical at ingest/promote time. Its
  // full term IRIs are the truthful source for "which external vocabularies does
  // this dataset actually reuse" (deriveReuses) — no extra query, no UI minting.
  alignment?: AlignmentReport
  // crosswalk-hub.md ④: the hub registry dataset is a bridge, not a normal dataset —
  // it carries these facets and is surfaced as the crosswalk view, not a list card.
  is_crosswalk?: boolean
  crosswalk_participants?: string[]
  crosswalk_shared_compositions?: number
  // incremental-ingest.md: a live "feed" dataset that has had batches appended
  // (the device-feed path). append_seq counts appends; appends is the per-batch log.
  feed?: boolean
  append_seq?: number
  triples_appended?: number
  appends?: {
    seq: number
    batch_files: string[]
    triples_in_batch: number
    appended_at: string
  }[]
}

// ---- ingest-rules transparency (the rules viewer) ---------------------------
// The rules that produce the citable facts are themselves reviewable: a
// deterministic, LLM-free projection of the persisted RML (GET /rules), the raw
// artifact contents (GET /api/datasets/{id} — already served, now surfaced), and
// the redesign history with server-side diffs (GET /history).

/** One term-map value: how a subject/object/argument gets its value. */
export interface RuleTerm {
  kind?: 'reference' | 'template' | 'constant' | 'function' | 'join' | 'unknown'
  reference?: string
  template?: string
  constant?: string
  constant_is_iri?: boolean
  function?: string
  function_iri?: string
  args?: (RuleTerm & { param: string })[]
  parent_map?: string
  conditions?: { child: string; parent: string }[]
  datatype?: string
  language?: string
  term_type?: string
}

export interface RuleProperty extends RuleTerm {
  predicate: string
  predicate_iri: string
}

export interface RuleMap {
  id: string
  source?: string
  iterator?: string
  formulation?: string
  subject: RuleTerm & { classes?: string[]; class_iris?: string[] }
  properties: RuleProperty[]
}

export interface DatasetRules {
  maps: RuleMap[]
  prefixes: Record<string, string>
  warnings: string[]
  /** model.yaml labels keyed by full term IRI (same projection promote uses). */
  labels: Record<string, string>
}

/** The human-readable projection of a dataset's persisted mapping. */
export async function getDatasetRules(datasetId: string): Promise<DatasetRules> {
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/rules`)
  if (!res.ok) throw new Error(await _errText(res, 'rules'))
  return (await res.json()) as DatasetRules
}

/** Raw artifact contents (file name → text), incl. proposal.md when stored.
 * The detail endpoint already ships every artifact; this surfaces them. */
export async function getDatasetArtifactContents(
  datasetId: string,
): Promise<Record<string, string>> {
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}`)
  if (!res.ok) throw new Error(await _errText(res, 'artifacts'))
  const body = (await res.json()) as { artifacts?: Record<string, string> }
  const contents: Record<string, string> = { ...(body.artifacts ?? {}) }
  try {
    const p = await fetchProposal(datasetId)
    if (p.has_proposal) contents['proposal.md'] = p.proposal_md
  } catch {
    // proposal is optional enrichment — the artifact files still show.
  }
  return contents
}

/** One redesign snapshot's directory metadata (contents fetched by id). */
export interface DatasetHistoryEntry {
  id: string
  saved_at: string
  artifacts: string[]
}

export async function getDatasetHistory(datasetId: string): Promise<DatasetHistoryEntry[]> {
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/history`)
  if (!res.ok) throw new Error(await _errText(res, 'history'))
  const body = (await res.json()) as { snapshots?: DatasetHistoryEntry[] }
  return body.snapshots ?? []
}

export interface DatasetHistorySnapshot {
  snapshot: { id: string; saved_at: string; artifacts: Record<string, string> }
  /** Unified diffs (snapshot → current), only for files that actually changed. */
  diffs: Record<string, string>
}

export async function getDatasetHistorySnapshot(
  datasetId: string,
  snapshotId: string,
): Promise<DatasetHistorySnapshot> {
  const res = await fetch(
    `${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/history/${encodeURIComponent(snapshotId)}`,
  )
  if (!res.ok) throw new Error(await _errText(res, 'history'))
  return (await res.json()) as DatasetHistorySnapshot
}

/** Preview which draft terms are Reuse (in canonical) vs New, before promoting. */
export async function getAlignment(datasetId: string): Promise<AlignmentReport> {
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/alignment`)
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`alignment failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return ((await res.json()) as { alignment: AlignmentReport }).alignment
}

/** Human-gated promotion: MOVE the draft graph into canonical so Ask can cite it. */
export async function promoteDataset(
  datasetId: string,
): Promise<{ triples_promoted: number; alignment: AlignmentReport }> {
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/promote`, {
    method: 'POST',
    headers: authHeaders(),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`promote failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return (await res.json()) as { triples_promoted: number; alignment: AlignmentReport }
}

async function _errText(res: Response, op: string): Promise<string> {
  const detail = await res.text().catch(() => '')
  return `${op} failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`
}

/** #20 P3: withdraw a promoted dataset from the citable corpus (tombstone, not
 * delete — data + IRIs stay so existing citations keep resolving). */
export async function retractDataset(datasetId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/retract`, {
    method: 'POST',
    headers: authHeaders(),
  })
  if (!res.ok) throw new Error(await _errText(res, 'retract'))
}

/** #20 P3: undo a retraction — the dataset re-enters the citable scope. */
export async function reinstateDataset(datasetId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/reinstate`, {
    method: 'POST',
    headers: authHeaders(),
  })
  if (!res.ok) throw new Error(await _errText(res, 'reinstate'))
}

/** #20 P3: hard-delete a dataset. A promoted (citable) dataset needs force=true
 * (the backend returns 409 otherwise and steers you to retract). */
export async function deleteDataset(datasetId: string, force = false): Promise<void> {
  const q = force ? '?force=true' : ''
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}${q}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok) throw new Error(await _errText(res, 'delete'))
}

/** Change a dataset's DISPLAY name only — the id (the IRI seed / data identity) is
 * immutable, so graphs, IRIs and existing citations are untouched. */
export async function renameDataset(datasetId: string, name: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/rename`, {
    method: 'POST',
    headers: { ...authHeaders(), 'content-type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!res.ok) throw new Error(await _errText(res, 'rename'))
}

/** Result of an incremental append (ADR incremental-ingest.md): a new batch was
 * POST-merged into the dataset's live canonical graph (it grew; existing IRIs stay;
 * re-emitted rows dedupe). */
export interface AppendResult {
  dataset_id: string
  live_graph: string
  triples_in_batch: number
  append_seq: number
  crosswalk_stale: boolean
  dataset: DatasetMeta
}

/**
 * Incremental append: grow a *promoted* dataset's live feed with a new batch
 * (the device-feed path). Materializes ONLY the batch (O(new)) and merges it into
 * the live graph, so the new facts are immediately citable while existing
 * triples/IRIs are untouched (re-emitted rows dedupe). The batch filename(s) must
 * match the dataset's rml:source. 200 with the result (no SSE — batches are small).
 */
export async function appendToDataset(datasetId: string, files: File[]): Promise<AppendResult> {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/append`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  })
  if (!res.ok) throw new Error(await _errText(res, 'append'))
  return (await res.json()) as AppendResult
}

export interface DocumentAppendResult {
  dataset_id: string
  live_graph: string
  paper_iri: string
  triples_in_batch: number
  append_seq: number
  dataset: DatasetMeta
}

/**
 * Add ONE document to an existing, promoted document dataset (the "定例ミーティング"
 * path). Structures just the new doc (Word→JATS server-side if needed) and merges it
 * into the live graph, so search_text / quote_with_citation then span every document
 * added. 200 with the result (no SSE — one document structures in milliseconds).
 */
export async function appendDocument(
  datasetId: string,
  file: File,
): Promise<DocumentAppendResult> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/documents`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  })
  if (!res.ok) throw new Error(await _errText(res, 'append document'))
  return (await res.json()) as DocumentAppendResult
}

/** A materialized dataset adapted to both layers (ontology + mapping). */
export interface LiveDataset {
  meta: DatasetMeta
  ontology: OntologyEntry
  mapping: MappingEntry
}

// All term IRIs a dataset actually uses, from its alignment report (classes +
// predicates, reuse + new). Empty when no alignment has been computed yet.
function alignmentTermIris(a?: AlignmentReport): string[] {
  if (!a) return []
  return [...a.classes.reuse, ...a.classes.new, ...a.predicates.reuse, ...a.predicates.new]
}

// Structural predicates carry no vocabulary signal in the per-dataset view.
const STRUCTURAL_NS = [
  'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
  'http://www.w3.org/2000/01/rdf-schema#',
  'http://www.w3.org/2002/07/owl#',
]

/** Real predicate IRIs the dataset uses (from alignment), minus structural ones. */
function datasetPredicateIris(a?: AlignmentReport): string[] {
  if (!a) return []
  return [...a.predicates.reuse, ...a.predicates.new].filter(
    (p) => !STRUCTURAL_NS.some((ns) => p.startsWith(ns)),
  )
}

/** Real class IRIs the dataset uses (from alignment), minus structural ones. */
function datasetClassIris(a?: AlignmentReport): string[] {
  if (!a) return []
  return [...a.classes.reuse, ...a.classes.new].filter(
    (c) => !STRUCTURAL_NS.some((ns) => c.startsWith(ns)),
  )
}

function toOntology(meta: DatasetMeta, mermaid: string): OntologyEntry {
  return {
    id: `live-${meta.id}`,
    name: meta.name,
    prefix: '(draft)',
    baseIri: meta.id,
    description: i18n.t('gallery:api.ontologyDescription', { date: meta.created_at.slice(0, 10) }),
    classes: meta.classes ?? [],
    // Reuse story straight from the real term IRIs this dataset uses (no fixture).
    reuses: deriveReuses(alignmentTermIris(meta.alignment)),
    mermaid,
    // A draft TBox is local/disposable until promoted — low risk to edit.
    editRisk: 'low',
  }
}

/** The lifecycle stage of a workbench-built dataset (Phase 5 #15). */
export type DatasetStage = 'design' | 'ingested' | 'promoted'

export function datasetStage(meta: { ingested?: boolean; promoted?: boolean }): DatasetStage {
  if (meta.promoted) return 'promoted'
  if (meta.ingested) return 'ingested'
  return 'design'
}

/** Short label + tone for each lifecycle stage. Resolved at call time so the
 *  badge text follows the active language (do not hoist to a module constant). */
export function stageInfo(): Record<DatasetStage, { badge: string; tone: 'design' | 'ingested' | 'promoted' }> {
  return {
    design: { badge: i18n.t('gallery:api.stage.designBadge'), tone: 'design' },
    ingested: { badge: i18n.t('gallery:api.stage.ingestedBadge'), tone: 'ingested' },
    promoted: { badge: i18n.t('gallery:api.stage.promotedBadge'), tone: 'promoted' },
  }
}

function toMapping(meta: DatasetMeta): MappingEntry {
  const stage = datasetStage(meta)
  const n = meta.triples_promoted ?? meta.triple_count ?? '?'
  const artifacts: MappingArtifact[] = []
  if (meta.has_rml) {
    const rmlKey =
      stage === 'promoted'
        ? 'gallery:api.rmlSummary.promoted'
        : stage === 'ingested'
          ? 'gallery:api.rmlSummary.ingested'
          : 'gallery:api.rmlSummary.design'
    artifacts.push({
      kind: 'mapping',
      name: 'mapping.rml.ttl',
      summaryKey: rmlKey,
      summaryParams: { n: String(n) },
    })
  }
  if (meta.has_mapping_ir) {
    artifacts.push({ kind: 'spec', name: 'mapping.yaml', summaryKey: 'gallery:api.mappingIrSummary' })
  }
  if (meta.has_mie) {
    artifacts.push({ kind: 'mie', name: 'mie.yaml', summaryKey: 'gallery:api.mieSummary' })
  }
  if (meta.has_ingester) {
    artifacts.push({ kind: 'ingester', name: 'ingester.py', summaryKey: 'gallery:api.ingesterSummary' })
  }
  if (meta.has_proposal) {
    artifacts.push({ kind: 'design', name: 'proposal.md', summaryKey: 'gallery:api.proposalSummary' })
  }
  const description =
    stage === 'promoted'
      ? i18n.t('gallery:api.mappingDescription.promoted', { n })
      : stage === 'ingested'
        ? i18n.t('gallery:api.mappingDescription.ingested', { n })
        : i18n.t('gallery:api.mappingDescription.design')
  return {
    id: `live-${meta.id}`,
    name: meta.name,
    dataset: i18n.t('gallery:api.mappingDataset', { date: meta.created_at.slice(0, 10) }),
    targetOntologyId: `live-${meta.id}`,
    targetOntologyName: meta.name,
    description,
    purposes: [],
    artifacts,
    editRisk: 'low',
  }
}

/**
 * Fetch datasets the user has materialized. API 障害（接続不可・非200）は throw
 * する — 従来は空配列に丸めていたため、障害時にカタログ/ホームが「まだデータ
 * セットがありません＋データを追加」という事実に反する空状態を表示し、
 * GalleryView に用意されたエラー分岐が到達不能になっていた。
 */
export async function getLiveDatasets(): Promise<LiveDataset[]> {
  const res = await fetch(`${API_BASE}/api/datasets`)
  if (!res.ok) throw new Error(`datasets: HTTP ${res.status}`)
  const body = (await res.json()) as { datasets?: DatasetMeta[] }
  const metas: DatasetMeta[] = Array.isArray(body.datasets) ? body.datasets : []
  // Pull each dataset's detail for its Mermaid diagram + the richer meta (which
  // carries the alignment report) — best-effort per item.
  return Promise.all(
    metas.map(async (meta) => {
      let mermaid = ''
      let full = meta
      try {
        const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(meta.id)}`)
        if (res.ok) {
          const detail = (await res.json()) as {
            meta?: DatasetMeta
            artifacts?: { 'diagram.md'?: string }
          }
          mermaid = extractMermaid(detail.artifacts?.['diagram.md'] ?? '')
          if (detail.meta) full = { ...meta, ...detail.meta }
        }
      } catch {
        // leave mermaid empty + use the list meta; the card still renders
      }
      return { meta: full, ontology: toOntology(full, mermaid), mapping: toMapping(full) }
    }),
  )
}

// Pull the ```mermaid fenced block out of a diagram.md (mirror of the api side).
function extractMermaid(diagramMd: string): string {
  const m = /```mermaid\s*\n([\s\S]*?)```/.exec(diagramMd)
  return (m ? m[1] : diagramMd).trim()
}

// ---- dataset-centric catalog (real data) ----------------------------------
// The Catalog/Home/Shared-vocab screens are dataset-first. To stay truthful (no
// fabricated datasets/counts), datasets come ONLY from the workbench-materialized
// drafts persisted to /api/datasets. No demo placeholders, no static canonical.

export type CatalogStatusKind = 'pub' | 'draft' | 'design'

export interface CatalogDataset {
  id: string
  name: string
  sub: string
  statusKind: CatalogStatusKind
  /** `key` is a locale-independent id ('fact' | 'class') for data matching; `label` is display-only. */
  counts: { key?: string; value: string; label: string }[]
  purposes: { tag: string; detail: string }[]
  classes: string[]
  reuses: { prefix: string; what: string }[]
  /** Real predicate IRIs the dataset uses (from alignment); structural ones dropped. */
  predicates: string[]
  /** Real class IRIs the dataset uses (from alignment); structural ones dropped. The
   * grounding/接地 UI grounds the dataset's OWN minted terms to external standards. */
  classIris: string[]
  artifacts: {
    kind: string
    name: string
    /** i18n key + params — translated at render (language-reactive). */
    detailKey: string
    detailParams?: Record<string, string | number>
  }[]
  mermaid?: string
  /** Present for materialized drafts; carries the backend handle for promote. */
  live?: LiveDataset
  /** The crosswalk hub bridge (crosswalk-hub.md ④): surfaced as its own view, not a
   * normal list card, and excluded from being a crosswalk participant. */
  isCrosswalk: boolean
}

const ARTIFACT_KIND_LABEL: Record<MappingArtifactKind, string> = {
  ingester: 'CODE',
  mie: 'MIE',
  shex: 'ShEx',
  mapping: 'RML',
  spec: 'IR',
  design: 'MD',
}

function liveToCatalog(l: LiveDataset): CatalogDataset {
  const stage = datasetStage(l.meta)
  const statusKind: CatalogStatusKind =
    stage === 'promoted' ? 'pub' : stage === 'ingested' ? 'draft' : 'design'
  const n = l.meta.triples_promoted ?? l.meta.triple_count
  const counts = [
    { key: 'class', value: String(l.ontology.classes.length), label: i18n.t('gallery:api.count.class') },
  ]
  if (n != null)
    counts.unshift({ key: 'fact', value: n.toLocaleString(), label: i18n.t('gallery:api.count.fact') })
  // 直近のライフサイクルイベントを表す（常に「設計を保存」だと公開済みでも
  // 設計日しか見えず誤解を招く）。日時はイベントに対応するものへ。
  const sub =
    stage === 'promoted'
      ? i18n.t('gallery:api.catalogSubPublished', {
          date: (l.meta.promoted_at ?? l.meta.created_at).slice(0, 10),
        })
      : stage === 'ingested'
        ? i18n.t('gallery:api.catalogSubDraft', {
            date: (l.meta.ingested_at ?? l.meta.created_at).slice(0, 10),
          })
        : i18n.t('gallery:api.catalogSub', { date: l.meta.created_at.slice(0, 10) })
  return {
    id: l.ontology.id,
    name: l.meta.name,
    sub,
    statusKind,
    counts,
    purposes: [],
    classes: l.ontology.classes,
    reuses: l.ontology.reuses,
    predicates: datasetPredicateIris(l.meta.alignment),
    classIris: datasetClassIris(l.meta.alignment),
    artifacts: l.mapping.artifacts.map((a) => ({
      kind: ARTIFACT_KIND_LABEL[a.kind],
      name: a.name,
      detailKey: a.summaryKey,
      detailParams: a.summaryParams,
    })),
    mermaid: l.ontology.mermaid || undefined,
    live: l,
    isCrosswalk: l.meta.is_crosswalk === true,
  }
}

/** All catalogued datasets: the workbench-materialized drafts. Real, live-only. */
export async function getCatalogDatasets(): Promise<CatalogDataset[]> {
  const live = await getLiveDatasets()
  return live.map(liveToCatalog)
}

// Run a scalar-returning SPARQL aggregate against the read-only endpoint.
// Best-effort: any failure (no API, empty store, parse error) resolves to null
// so the UI shows "—" rather than a fabricated number.
async function sparqlScalar(query: string): Promise<number | null> {
  try {
    const res = await fetch(`${API_BASE}/api/sparql`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ query }),
    })
    if (!res.ok) return null
    const json = (await res.json()) as {
      results?: { bindings?: Array<Record<string, { value?: string }>> }
    }
    const row = json.results?.bindings?.[0]
    const first = row ? Object.values(row)[0]?.value : undefined
    if (first == null) return null
    const n = Number(first)
    return Number.isFinite(n) ? n : null
  } catch {
    return null
  }
}

/** Graph-wide stats for Home, measured from the store (null when unavailable). */
export async function getGraphStats(): Promise<{
  facts: number | null
  classes: number | null
  datasets: number
}> {
  const [facts, classes, datasets] = await Promise.all([
    sparqlScalar('SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }'),
    sparqlScalar('SELECT (COUNT(DISTINCT ?c) AS ?n) WHERE { ?s a ?c }'),
    // The crosswalk hub is a bridge, not a dataset — exclude it from the count so
    // Home matches the Catalog (which also hides it).
    getCatalogDatasets().then((d) => d.filter((x) => !x.isCrosswalk).length),
  ])
  return { facts, classes, datasets }
}
