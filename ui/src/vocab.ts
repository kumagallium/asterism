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
 * Well-known EXTERNAL vocabularies. When the live data uses a term under one of
 * these namespaces, that vocabulary is being "reused" rather than re-minted.
 * Structural namespaces (rdf/rdfs/owl/xsd) are deliberately not surfaced.
 */
export const KNOWN_VOCABS: { ns: string; prefix: string; what: string }[] = [
  { ns: 'https://schema.org/', prefix: 'schema:', what: 'schema.org（人物・出版物などのメタデータ）' },
  { ns: 'http://www.w3.org/ns/prov#', prefix: 'prov:', what: 'PROV-O（来歴 Entity / Activity / Agent）' },
  { ns: 'http://purl.org/dc/terms/', prefix: 'dcterms:', what: 'Dublin Core terms（identifier / created 等）' },
  { ns: 'http://purl.org/ontology/bibo/', prefix: 'bibo:', what: 'BIBO（volume / issue / pages）' },
  { ns: 'http://qudt.org/schema/qudt/', prefix: 'qudt:', what: 'QUDT（物性量・単位の共有語彙）' },
  { ns: 'http://www.w3.org/2004/02/skos/core#', prefix: 'skos:', what: 'SKOS（概念体系）' },
]

/** Reused external vocabularies actually present in the given term IRIs. */
export function deriveReuses(iris: Iterable<string>): { prefix: string; what: string }[] {
  const present = new Set<string>()
  for (const iri of iris) {
    if (iri) present.add(namespaceOf(iri))
  }
  return KNOWN_VOCABS.filter((v) => present.has(v.ns)).map(({ prefix, what }) => ({ prefix, what }))
}
