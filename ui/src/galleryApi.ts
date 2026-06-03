// Data for the M4 galleries (ontologies vs mappings).
//
// Two layers are presented separately on purpose (design doc §6.6 / D8):
//
//   - Ontology layer (shared vocabulary = TBox): slow-changing, SHARED, high
//     blast radius — editing it ripples to every downstream consumer.
//   - Mapping layer (dataset → vocabulary binding = ingester + MIE): fast-
//     changing, per-dataset/per-purpose, LOCAL and disposable.
//
// Making that edit-risk difference legible — and surfacing each mapping's
// PURPOSE — is the whole point of the two-gallery split (handoff §1).
//
// Fixture-first, like demoApi.ts: the content below is the REAL committed
// starrydata ontology/mapping (docs/ontology/*, ingest/.../starrydata.py,
// data/togomcp/mie/starrydata.yaml), captured statically so the UI renders
// before a backend gallery endpoint exists. A later `live` mode can fetch the
// same shapes from the API without touching the views.

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

// The canonical starrydata TBox (docs/ontology/diagram.md + starrydata.ttl).
const STARRYDATA_MERMAID = `classDiagram
    direction LR

    class Paper {
        +dcterms:identifier (SID)
        +schema:identifier (DOI)
        +schema:name (title)
        +schema:datePublished
        +bibo:volume / issue / pages
    }
    class Sample {
        +dcterms:identifier (sample_id)
        +schema:name (sample_name)
        +sd:compositionString
        +sd:compositionDetails
    }
    class Curve {
        +dcterms:identifier (figure_id)
        +sd:propertyX / propertyY
        +sd:unitXString / unitYString
        +sd:xValuesJSON / yValuesJSON
        +sd:xMin / xMax / yMin / yMax
        +sd:pointCount
    }
    class Descriptor {
        +sd:descriptorName
        +sd:descriptorCategory
        +sd:descriptorExtracted
    }
    class IngestionActivity {
        +prov:atTime
        +prov:used (CSV source)
        +prov:wasAssociatedWith (agent)
    }

    Sample "1" --> "1" Paper : fromPaper
    Sample "1" --> "0..n" Descriptor : hasDescriptor
    Curve "1" --> "1" Sample : ofSample
    Paper ..> IngestionActivity : wasGeneratedBy
    Sample ..> IngestionActivity : wasGeneratedBy
    Curve ..> IngestionActivity : wasGeneratedBy

    note for Curve "subClassOf prov-Entity. x/y are JSON literal plus aggregates"
    note for IngestionActivity "subClassOf prov-Activity. One per ingest run"`

const ONTOLOGIES: OntologyEntry[] = [
  {
    id: 'starrydata',
    name: 'Starrydata Ontology',
    prefix: 'sd:',
    baseIri: 'https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#',
    description:
      '材料測定データ (熱電・電池・磁性) の共有語彙。Paper / Sample / Curve を中心に、すべて prov:Entity として来歴を担保する。物性名・単位は生文字列に加えて QUDT IRI に正規化 (sd:propertyYQuantity → qudt:SeebeckCoefficient 等) し、表記ゆれを横断できる。',
    classes: ['Paper', 'Sample', 'Curve', 'Descriptor', 'IngestionActivity'],
    reuses: [
      {
        prefix: 'qudt:',
        what: 'QuantityKind / Unit（"Seebeck coefficient"→qudt:SeebeckCoefficient、単位→QUDT。物性名・単位の共有語彙）',
      },
      { prefix: 'schema:', what: 'Person / Periodical / 論文メタdata (schema.org)' },
      { prefix: 'prov:', what: 'Entity / Activity / Agent (PROV-O)' },
      { prefix: 'dcterms:', what: 'identifier / created / modified' },
      { prefix: 'bibo:', what: 'volume / issue / pages' },
    ],
    mermaid: STARRYDATA_MERMAID,
    // Shared vocabulary: slow-changing, breaking it ripples to all consumers.
    editRisk: 'high',
  },
]

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

// The real starrydata binding: ingest/.../starrydata.py + the MIE yaml. Purpose
// tags are drawn from the MIE's sparql_query_examples (ZT/Seebeck ranking,
// composition search, QUDT unit normalization, provenance, paper coverage).
const MAPPINGS: MappingEntry[] = [
  {
    id: 'starrydata-ingest',
    name: 'Starrydata 取り込みマッピング',
    dataset: 'Starrydata CSV（papers / samples / curves）',
    targetOntologyId: 'starrydata',
    targetOntologyName: 'Starrydata Ontology',
    description:
      '3 種の CSV を複合キーで IRI 化し、sd: 語彙へ束縛する。どの目的（問い）に応えるための束縛かを目的タグで示す。',
    purposes: [
      {
        tag: '熱電性能の探索',
        detail: 'Curve の propertyY（ZT / Seebeck）と xMin/xMax/yMin/yMax 集約で範囲フィルタ・ランキング',
      },
      {
        tag: '組成検索',
        detail: 'Sample.compositionString の部分一致で試料を引く（Bi2Te3 など）',
      },
      {
        tag: '単位の正規化（QUDT）',
        detail: 'Seebeck の表記ゆれ・単位（V/K 等）を QUDT 共有語彙で横断',
      },
      {
        tag: '来歴トレース',
        detail: 'curve → sample → paper → IngestionActivity / デジタル化を辿る',
      },
      {
        tag: '論文メタデータ参照',
        detail: 'DOI / 著者 / 雑誌（schema.org 再利用）で網羅的研究を特定',
      },
    ],
    artifacts: [
      {
        kind: 'ingester',
        name: 'ingest/.../starrydata.py',
        summary: 'CSV 3 種 → triples。複合キーで IRI を生成し sd: 語彙へ束縛',
      },
      {
        kind: 'mie',
        name: 'data/togomcp/mie/starrydata.yaml',
        summary: 'AI 探索メタ + SPARQL 例 + answer_grounding（回答の接地ルール）',
      },
      {
        kind: 'shex',
        name: 'shape_expressions（MIE 内）',
        summary: 'Paper / Sample / Curve の ShEx 形状制約',
      },
    ],
    // Dataset-local binding: fast-changing, disposable, safe to edit.
    editRisk: 'low',
  },
]

// ---- live datasets (materialized via the workbench, persisted to /api) ----
// These are what closes the authoring→catalog loop: a dataset materialized in
// the workbench is persisted (api V1a) and surfaces here. They are DRAFTS (a
// freshly designed TBox + mapping), distinct from the seeded canonical
// vocabulary above. The fetch is best-effort: if the workbench API is absent,
// the gallery still renders the fixtures (no error).

// Workbench API base. Same-origin by default (Vite proxies /api → :8080);
// override with VITE_API_URL to point at a separately-hosted API.
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

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
}

/** A materialized dataset adapted to both gallery layers (ontology + mapping). */
export interface LiveDataset {
  meta: DatasetMeta
  ontology: OntologyEntry
  mapping: MappingEntry
}

function toOntology(meta: DatasetMeta, mermaid: string): OntologyEntry {
  return {
    id: `live-${meta.id}`,
    name: meta.name,
    prefix: '(draft)',
    baseIri: meta.id,
    description: `ワークベンチで materialize した設計（${meta.created_at.slice(0, 10)}）。共有語彙への昇格前のドラフト。`,
    classes: meta.classes ?? [],
    reuses: [],
    mermaid,
    // A draft TBox is local/disposable until promoted — low risk to edit.
    editRisk: 'low',
  }
}

function toMapping(meta: DatasetMeta): MappingEntry {
  const artifacts: MappingArtifact[] = []
  if (meta.has_rml) {
    artifacts.push({
      kind: 'mapping',
      name: 'mapping.rml.ttl',
      summary: meta.ingested
        ? `宣言 RML（draft グラフに投入済み・${meta.triple_count ?? '?'} triples）`
        : '宣言 RML（未投入。ワークベンチの人間ゲートで投入可能）',
    })
  }
  if (meta.has_ingester) {
    artifacts.push({ kind: 'ingester', name: 'ingester.py', summary: '生成された取り込みスクリプト（未実行）' })
  }
  if (meta.has_mie) {
    artifacts.push({ kind: 'mie', name: 'mie.yaml', summary: '生成された AI 探索メタ' })
  }
  const status = meta.ingested
    ? `宣言 RML を draft グラフに投入済み（${meta.triple_count ?? '?'} triples）。Ask の引用面（canonical）への昇格は別ゲート。`
    : '未投入＝まだ Oxigraph に入っていない。宣言 RML があればワークベンチの人間ゲートで draft グラフへ投入できる。'
  return {
    id: `live-${meta.id}`,
    name: meta.name,
    dataset: `materialize 済み（${meta.created_at.slice(0, 10)})`,
    targetOntologyId: `live-${meta.id}`,
    targetOntologyName: meta.name,
    description: `目的タグは未設定（運用で付与）。${status}`,
    purposes: [],
    artifacts,
    editRisk: 'low',
  }
}

/**
 * Fetch datasets the user has materialized. Best-effort: any failure (no API,
 * network error, non-200) resolves to an empty list so the gallery degrades to
 * fixtures rather than erroring.
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
  // Pull each dataset's diagram for its Mermaid (best-effort per item).
  return Promise.all(
    metas.map(async (meta) => {
      let mermaid = ''
      try {
        const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(meta.id)}`)
        if (res.ok) {
          const detail = (await res.json()) as { artifacts?: { 'diagram.md'?: string } }
          mermaid = extractMermaid(detail.artifacts?.['diagram.md'] ?? '')
        }
      } catch {
        // leave mermaid empty; the card still renders
      }
      return { meta, ontology: toOntology(meta, mermaid), mapping: toMapping(meta) }
    }),
  )
}

// Pull the ```mermaid fenced block out of a diagram.md (mirror of the api side).
function extractMermaid(diagramMd: string): string {
  const m = /```mermaid\s*\n([\s\S]*?)```/.exec(diagramMd)
  return (m ? m[1] : diagramMd).trim()
}

// ---- public API (async so a live backend can drop in later) ---------------

/** List the shared vocabularies (TBox layer). */
export async function getOntologies(): Promise<OntologyEntry[]> {
  await delay(120)
  return ONTOLOGIES
}

/** List the dataset→vocabulary bindings (mapping layer), purpose-tagged. */
export async function getMappings(): Promise<MappingEntry[]> {
  await delay(120)
  return MAPPINGS
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
