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

import { authHeaders } from './authToken'
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

export type MappingArtifactKind = 'ingester' | 'mie' | 'shex' | 'mapping'

export interface MappingArtifact {
  kind: MappingArtifactKind
  name: string
  summary: string
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
  ingested?: boolean
  triple_count?: number
  graph_iri?: string
  // Task E: design-time source files persisted server-side (lets the catalog
  // ingest a design-stage dataset with no re-attach). source_kind (#19) is
  // "csv" | "json" so the UI labels the source and picks the right file picker.
  has_source?: boolean
  source_files?: string[]
  source_kind?: 'csv' | 'json'
  // S4: whether the draft was promoted into the canonical (default) graph.
  promoted?: boolean
  triples_promoted?: number
  // #20 P3 lifecycle: "active" | "retracted" (tombstoned) | "deleted"; version bumps on re-promote.
  status?: string
  version?: number
  // Reuse/New term split computed against canonical at ingest/promote time. Its
  // full term IRIs are the truthful source for "which external vocabularies does
  // this dataset actually reuse" (deriveReuses) — no extra query, no UI minting.
  alignment?: AlignmentReport
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

function toOntology(meta: DatasetMeta, mermaid: string): OntologyEntry {
  return {
    id: `live-${meta.id}`,
    name: meta.name,
    prefix: '(draft)',
    baseIri: meta.id,
    description: `ワークベンチで materialize した設計（${meta.created_at.slice(0, 10)}）。共有語彙への昇格前のドラフト。`,
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

/** Short Japanese label + plain description for each lifecycle stage. */
export const STAGE_INFO: Record<DatasetStage, { badge: string; tone: 'design' | 'ingested' | 'promoted' }> = {
  design: { badge: '設計のみ（未取り込み）', tone: 'design' },
  ingested: { badge: '下書きに取り込み済み', tone: 'ingested' },
  promoted: { badge: '共有データ（Ask 利用可）', tone: 'promoted' },
}

function toMapping(meta: DatasetMeta): MappingEntry {
  const stage = datasetStage(meta)
  const n = meta.triples_promoted ?? meta.triple_count ?? '?'
  const artifacts: MappingArtifact[] = []
  if (meta.has_rml) {
    const rmlSummary =
      stage === 'promoted'
        ? `宣言 RML（共有データに取り込み済み・${n} 件）`
        : stage === 'ingested'
          ? `宣言 RML（下書きグラフに取り込み済み・${n} 件）`
          : '宣言 RML（未取り込み。ワークベンチの人間ゲートで取り込める）'
    artifacts.push({ kind: 'mapping', name: 'mapping.rml.ttl', summary: rmlSummary })
  }
  if (meta.has_ingester) {
    artifacts.push({ kind: 'ingester', name: 'ingester.py', summary: '生成された取り込みスクリプト（未実行）' })
  }
  if (meta.has_mie) {
    artifacts.push({ kind: 'mie', name: 'mie.yaml', summary: '生成された AI 探索メタ' })
  }
  const description =
    stage === 'promoted'
      ? `共有データ（Ask が引用する正式グラフ）に取り込み済み・${n} 件。SPARQL で問い合わせ可能。`
      : stage === 'ingested'
        ? `下書きグラフに取り込み済み・${n} 件。確認できたら「共有データに昇格」すると Ask の引用対象になる。`
        : '設計のみ＝まだ RDF を生成していない。宣言 RML があればワークベンチの人間ゲートで取り込める。'
  return {
    id: `live-${meta.id}`,
    name: meta.name,
    dataset: `設計を保存（${meta.created_at.slice(0, 10)}）`,
    targetOntologyId: `live-${meta.id}`,
    targetOntologyName: meta.name,
    description,
    purposes: [],
    artifacts,
    editRisk: 'low',
  }
}

/**
 * Fetch datasets the user has materialized. Best-effort: any failure (no API,
 * network error, non-200) resolves to an empty list so the catalog degrades to
 * an empty state rather than erroring.
 */
export async function getLiveDatasets(): Promise<LiveDataset[]> {
  let metas: DatasetMeta[]
  try {
    const res = await fetch(`${API_BASE}/api/datasets`)
    if (!res.ok) return []
    const body = (await res.json()) as { datasets?: DatasetMeta[] }
    metas = Array.isArray(body.datasets) ? body.datasets : []
  } catch {
    return []
  }
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
  counts: { value: string; label: string }[]
  purposes: { tag: string; detail: string }[]
  classes: string[]
  reuses: { prefix: string; what: string }[]
  /** Real predicate IRIs the dataset uses (from alignment); structural ones dropped. */
  predicates: string[]
  artifacts: { kind: string; name: string; detail: string }[]
  mermaid?: string
  /** Present for materialized drafts; carries the backend handle for promote. */
  live?: LiveDataset
}

const ARTIFACT_KIND_LABEL: Record<MappingArtifactKind, string> = {
  ingester: 'CODE',
  mie: 'MIE',
  shex: 'ShEx',
  mapping: 'RML',
}

function liveToCatalog(l: LiveDataset): CatalogDataset {
  const stage = datasetStage(l.meta)
  const statusKind: CatalogStatusKind =
    stage === 'promoted' ? 'pub' : stage === 'ingested' ? 'draft' : 'design'
  const n = l.meta.triples_promoted ?? l.meta.triple_count
  const counts = [{ value: String(l.ontology.classes.length), label: 'クラス' }]
  if (n != null) counts.unshift({ value: n.toLocaleString(), label: '事実' })
  return {
    id: l.ontology.id,
    name: l.meta.name,
    sub: `設計を保存 · ${l.meta.created_at.slice(0, 10)}`,
    statusKind,
    counts,
    purposes: [],
    classes: l.ontology.classes,
    reuses: l.ontology.reuses,
    predicates: datasetPredicateIris(l.meta.alignment),
    artifacts: l.mapping.artifacts.map((a) => ({
      kind: ARTIFACT_KIND_LABEL[a.kind],
      name: a.name,
      detail: a.summary,
    })),
    mermaid: l.ontology.mermaid || undefined,
    live: l,
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
    getCatalogDatasets().then((d) => d.length),
  ])
  return { facts, classes, datasets }
}
