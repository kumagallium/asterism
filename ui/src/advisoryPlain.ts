import i18n from './i18n'

/**
 * Plain-language face of the design advisories (ADR K11).
 *
 * The advisories the api returns are precise, actionable ENGLISH written for a
 * model to act on — "the mapping's 2 entities split into 2 DISCONNECTED groups:
 * MaterialSample | Measurement. … Declare each link on the CHILD map …". That
 * text is exactly right where it goes (the one-click AI fix hands it over
 * verbatim), and exactly wrong in front of a researcher reading a Japanese UI:
 * shown raw, thirteen of them filled the whole ZEM catalog page with untranslated
 * jargon (observed 2026-07-24).
 *
 * So: classify, count, and say the consequence in the user's language; the raw
 * text stays available in a fold. Classification is by the fixed phrases the
 * DETERMINISTIC generator emits (asterism.rml_validate) — not model output, so
 * the match is stable — and anything unrecognised degrades to a counted "other"
 * line rather than being dropped. Nothing here is fed to the AI; callers pass
 * the raw strings for that.
 */

/** Marker phrases, verbatim from `asterism/rml_validate.py`. */
const DISCONNECTED = 'DISCONNECTED groups'
const DUPLICATE_COLUMN = 'is bound as a plain datatype property by'
const UNMAPPED_COLUMN = 'column(s) the mapping never uses'
const EMPTY_SHELL = 'binds NO value column of its own'

/**
 * Marker phrases from `asterism/shapes.py` (ADR data-shape-checks.md).
 *
 * These describe the INGESTED DATA, not the design: the design advisories above
 * are read from the mapping alone, while these are what the graph actually
 * turned out to be. They arrive on the same `advisories` list on purpose (no
 * second UI surface, ADR §D5), so they get the same treatment — one plain
 * sentence each, raw text in the fold.
 */
const SHAPE_MISSING = 'declared but MISSING in the ingested data'
const SHAPE_DANGLING = 'DANGLING reference'
const SHAPE_WRONG_CLASS = 'WRONG class'
const SHAPE_DATATYPE = 'datatype MISMATCH'

/** `… groups: MaterialSample  |  Measurement.` → ["MaterialSample", "Measurement"] */
function disconnectedGroups(advisory: string): string[] {
  const m = /DISCONNECTED groups:\s*(.+?)\.(?:\s|$)/.exec(advisory)
  if (!m) return []
  return m[1]
    .split('|')
    .map((s) => s.trim())
    .filter(Boolean)
}

export interface PlainAdvisory {
  /** One plain sentence, already translated. */
  text: string
  /** The raw advisories this line stands for (for the fold / the AI fix). */
  raw: string[]
}

/** `map 'XRD-card' mints one entity per row …` → "XRD-card" */
function shellMap(advisory: string): string {
  const m = /^map '([^']+)' mints one entity per row/.exec(advisory)
  return m ? m[1] : ''
}

/** The columns the shell advisory names as dropped (`… bound by no map at all —
 * 2theta, d, I. Put them on 'XRD-card'.`). Only these are quoted in the plain
 * line; a MOVE clause (values parked on the header card) stays in the raw fold.
 * Anchored on the fixed tail so a column name containing `.` survives. */
function shellDroppedColumns(advisory: string): string[] {
  const m = /bound by no map at all — (.+?)\. Put them on '/.exec(advisory)
  if (!m) return []
  return m[1]
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s && s !== '…')
}

/** `Sample.hasMeasurement is a DANGLING reference: …` → "Sample.hasMeasurement" */
function subjectOf(advisory: string): string {
  const m = /^([\w:.-]+)\s/.exec(advisory)
  return m ? m[1] : ''
}

/** One line per shape finding — each names the class.predicate it is about, which
 * is what lets the reader go straight to that row in the design. */
function shapeLines(
  advisories: string[],
  marker: string,
  key: string,
  out: PlainAdvisory[],
): string[] {
  const t = i18n.t.bind(i18n)
  const hits = advisories.filter((a) => a.includes(marker))
  for (const a of hits) {
    out.push({ text: t(key, { subject: subjectOf(a) }), raw: [a] })
  }
  return hits
}

export function plainAdvisories(advisories: string[]): PlainAdvisory[] {
  const t = i18n.t.bind(i18n)
  const disconnected = advisories.filter((a) => a.includes(DISCONNECTED))
  const duplicate = advisories.filter((a) => a.includes(DUPLICATE_COLUMN))
  const unmapped = advisories.filter((a) => a.includes(UNMAPPED_COLUMN))
  const shells = advisories.filter((a) => a.includes(EMPTY_SHELL))
  const shape: PlainAdvisory[] = []
  const shapeHits = [
    ...shapeLines(advisories, SHAPE_MISSING, 'gallery:advisory.shapeMissing', shape),
    ...shapeLines(advisories, SHAPE_DANGLING, 'gallery:advisory.shapeDangling', shape),
    ...shapeLines(advisories, SHAPE_WRONG_CLASS, 'gallery:advisory.shapeWrongClass', shape),
    ...shapeLines(advisories, SHAPE_DATATYPE, 'gallery:advisory.shapeDatatype', shape),
  ]
  const known = new Set([...disconnected, ...duplicate, ...unmapped, ...shells, ...shapeHits])
  const other = advisories.filter((a) => !known.has(a))

  const out: PlainAdvisory[] = []
  for (const a of disconnected) {
    const groups = disconnectedGroups(a)
    out.push({
      // Naming the two boxes is what makes this actionable to a human — they can
      // look at the diagram right above and see the two islands.
      text:
        groups.length === 2
          ? t('gallery:advisory.disconnectedPair', { a: groups[0], b: groups[1] })
          : t('gallery:advisory.disconnected', { count: groups.length || 2 }),
      raw: [a],
    })
  }
  // A per-row kind that records nothing of its own — named per kind, with the
  // columns the data says are its (the human saw those on the gate's card).
  for (const a of shells) {
    const cols = shellDroppedColumns(a)
    out.push({
      text:
        cols.length > 0
          ? t('gallery:advisory.emptyShellColumns', {
              cls: shellMap(a),
              columns: cols.join(', '),
            })
          : t('gallery:advisory.emptyShell', { cls: shellMap(a) }),
      raw: [a],
    })
  }
  if (duplicate.length > 0) {
    out.push({ text: t('gallery:advisory.duplicateColumn', { count: duplicate.length }), raw: duplicate })
  }
  if (unmapped.length > 0) {
    out.push({ text: t('gallery:advisory.unmapped', { count: unmapped.length }), raw: unmapped })
  }
  // Data findings last: the design lines above are about something the user can
  // still edit, these are about data already ingested.
  out.push(...shape)
  if (other.length > 0) {
    out.push({ text: t('gallery:advisory.other', { count: other.length }), raw: other })
  }
  return out
}
