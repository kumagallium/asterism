// Which column of the file a rule reads — the one question every surface that
// shows a design has to answer, and the one each of them used to answer
// differently.
//
// The kantan tier's 「項目の意味」 table asked it as `kind === 'reference'`, which
// is not "reads a column" but "reads a column AND nothing was done to it". A
// weak model wraps almost every numeric column in a Tier-0 cleanup
// (`number_clean`), so live 2026-08-20 a 3,001-row XRD file arrived at that
// screen with its two measured columns — the angle and the intensity, the whole
// point of the file — missing, and its per-peak kind rendered as no table at
// all. They had been swept into the fold labelled 「自動で付く ID・固定値」,
// which is a false description of a measured value.
//
// The rule, stated once here and used everywhere:
//
//   A term TRANSCRIBES a column when it is a literal that reads exactly one
//   source column — directly, or through any depth of single-column conversion.
//
// Two or more columns is a derived value, not a transcription (「A + B」 has no
// single column whose meaning it carries). An IRI is a link or an ID, never a
// value. Both stay out.
import type { RuleTerm } from './galleryApi'

/** The `{…}` placeholders of a template, in order. */
export function templateColumns(template?: string): string[] {
  if (!template) return []
  return [...template.matchAll(/\{([^{}]+)\}/g)].map((m) => m[1].trim()).filter(Boolean)
}

/** Does this term produce an IRI (a link or an ID) rather than a value? */
export function isIriTerm(term: RuleTerm): boolean {
  if (term.term_type && /IRI|URI/i.test(term.term_type)) return true
  if (term.kind === 'join') return true
  if (term.kind === 'constant') return term.constant_is_iri === true
  // A template builds an IRI unless the projection says otherwise: that is what
  // the compiler emits it for, and a literal-typed template says so explicitly.
  return term.kind === 'template'
}

/** Every source column this term reads, at any depth, in order and deduped. */
export function termColumns(term: RuleTerm): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  const walk = (t: RuleTerm) => {
    if (t.reference && !seen.has(t.reference)) {
      seen.add(t.reference)
      out.push(t.reference)
    }
    for (const col of templateColumns(t.template)) {
      if (!seen.has(col)) {
        seen.add(col)
        out.push(col)
      }
    }
    for (const arg of t.args ?? []) walk(arg)
    for (const cond of t.conditions ?? []) {
      if (cond.child && !seen.has(cond.child)) {
        seen.add(cond.child)
        out.push(cond.child)
      }
    }
  }
  walk(term)
  return out
}

/** The one column whose value this term carries, or null when it carries none
 *  (a link, an ID, a constant) or combines several. */
export function transcribedColumn(term: RuleTerm): string | null {
  if (isIriTerm(term)) return null
  const cols = termColumns(term)
  return cols.length === 1 ? cols[0] : null
}
