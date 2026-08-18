// Human-readable names for a dataset's own minted terms, from the reviewed
// model.yaml labels the /rules projection ships (`DatasetRules.labels`).
//
// Lives in its own module so both the 設計 tab's rule tables and the 外部の標準に
// 合わせる panel show the SAME name for the same term — a reader who saw 「ZT」 in
// one place must not meet 「hasZT」 in the other (kantan-mode ADR K8 / K4).

/** A model.yaml label for a term, matched by full IRI first, local name second.
 *  Returns undefined when it would only repeat the CURIE's own tail (noise), so
 *  callers can fall back to the local name without printing it twice. */
export function labelFor(
  labels: Record<string, string>,
  fullIri: string | undefined,
  shown: string,
): string | undefined {
  const tail = shown.split(':').pop()
  let label: string | undefined
  if (fullIri && labels[fullIri]) label = labels[fullIri]
  else {
    const local = (fullIri ?? shown).split(/[#/:]/).pop()
    if (local) {
      for (const [iri, l] of Object.entries(labels)) {
        if (iri.endsWith(`#${local}`) || iri.endsWith(`/${local}`)) {
          label = l
          break
        }
      }
    }
  }
  return label && label !== tail ? label : undefined
}

/** The readable tail of a CURIE or IRI (`zem:hasZT` → `hasZT`). */
export function tailOf(term: string): string {
  return term.split(/[#/:]/).pop() || term
}
