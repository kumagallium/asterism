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
      '材料測定データ (熱電・電池・磁性) の共有語彙。Paper / Sample / Curve を中心に、すべて prov:Entity として来歴を担保する。',
    classes: ['Paper', 'Sample', 'Curve', 'Descriptor', 'IngestionActivity'],
    reuses: [
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

// ---- public API (async so a live backend can drop in later) ---------------

/** List the shared vocabularies (TBox layer). */
export async function getOntologies(): Promise<OntologyEntry[]> {
  await delay(120)
  return ONTOLOGIES
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
