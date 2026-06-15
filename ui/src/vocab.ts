// Shared vocabulary helpers — used by both the Shared-vocabulary board
// (SharedVocabView) and the per-dataset Catalog detail (GalleryView).
//
// These derive the "reuse" story straight from REAL term IRIs (the namespaces
// actually present in the live data), so neither view needs a hardcoded fixture:
// if a dataset's terms live under schema.org / PROV / QUDT etc., that vocabulary
// is being reused rather than re-minted, and we surface it. No fabrication —
// when no known external namespace is present, the result is simply empty.

/** Local name of an IRI (after the last # or /), for a compact code display. */
export function localName(iri: string): string {
  const i = Math.max(iri.lastIndexOf('#'), iri.lastIndexOf('/'))
  return i >= 0 ? iri.slice(i + 1) : iri
}

/** Namespace of an IRI (everything up to and including the last # or /). */
export function namespaceOf(iri: string): string {
  const i = Math.max(iri.lastIndexOf('#'), iri.lastIndexOf('/'))
  return i >= 0 ? iri.slice(0, i + 1) : iri
}

/**
 * Well-known EXTERNAL vocabularies — a CURATED STARTER PACK of famous, foundational
 * ontologies Asterism recognizes (generic metadata + per-domain). When the live data
 * uses a term under one of these namespaces, that vocabulary is being "reused" rather
 * than re-minted, and we surface it (catalog detail, shared-vocabulary board, ontology
 * map). Structural namespaces (rdf/rdfs/owl/xsd) are deliberately not surfaced.
 *
 * The list grows by CURATION (the famous ones per active domain — like QUDT units or the
 * Tier-0 library), NOT by mirroring all of LOV/BioPortal (crosswalk-grounding direction,
 * external-standard-alignment.md). Adding a vocabulary here makes the map RECOGNIZE it;
 * the data only LINKS to it once a dataset's design reuses/aligns its term IRIs — that
 * grounding is the separate retrieval + human-vet workstream.
 *
 * SoT: the backend `ingest/src/asterism/grounding/known_vocabs.yaml` (served at
 * `/api/vocabularies`) is the canonical curated list — it additionally carries the real
 * per-vocabulary TERMS the grounding search (`/api/ground`) returns. The namespaces here
 * MUST stay in sync with it (a UI mirror for sync detection); consolidating the UI onto
 * the backend SoT is the next step.
 *
 * `what` is an i18n KEY (namespace `vocab`), not a literal — consumers resolve it via
 * `t(v.what)`. `ns`/`prefix` are IRIs and are never translated. All namespaces are real
 * (verified), never fabricated.
 */
export const KNOWN_VOCABS: { ns: string; prefix: string; what: string }[] = [
  // Generic / cross-domain (metadata, provenance, people, datasets, observations).
  { ns: 'https://schema.org/', prefix: 'schema:', what: 'vocab:known.schema' },
  { ns: 'http://www.w3.org/ns/prov#', prefix: 'prov:', what: 'vocab:known.prov' },
  { ns: 'http://purl.org/dc/terms/', prefix: 'dcterms:', what: 'vocab:known.dcterms' },
  { ns: 'http://purl.org/ontology/bibo/', prefix: 'bibo:', what: 'vocab:known.bibo' },
  { ns: 'http://www.w3.org/2004/02/skos/core#', prefix: 'skos:', what: 'vocab:known.skos' },
  { ns: 'http://xmlns.com/foaf/0.1/', prefix: 'foaf:', what: 'vocab:known.foaf' },
  { ns: 'http://www.w3.org/ns/dcat#', prefix: 'dcat:', what: 'vocab:known.dcat' },
  { ns: 'http://www.w3.org/ns/sosa/', prefix: 'sosa:', what: 'vocab:known.sosa' },
  // Materials science & engineering (quantities/units + foundational + samples).
  { ns: 'http://qudt.org/schema/qudt/', prefix: 'qudt:', what: 'vocab:known.qudt' },
  { ns: 'https://w3id.org/emmo#', prefix: 'emmo:', what: 'vocab:known.emmo' },
  // CMSO's authoritative term IRIs use http:// (the https:// PURL only redirects to the
  // HTML docs). Match what the ontology actually mints so reuse detection / grounding agree.
  { ns: 'http://purls.helmholtz-metadaten.de/cmso/', prefix: 'cmso:', what: 'vocab:known.cmso' },
]

/** Reused external vocabularies actually present in the given term IRIs. */
export function deriveReuses(iris: Iterable<string>): { prefix: string; what: string }[] {
  const present = new Set<string>()
  for (const iri of iris) {
    if (iri) present.add(namespaceOf(iri))
  }
  return KNOWN_VOCABS.filter((v) => present.has(v.ns)).map(({ prefix, what }) => ({ prefix, what }))
}
