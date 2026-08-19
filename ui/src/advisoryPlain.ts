import i18n from './i18n'
import { localName } from './vocab'

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
 *
 * Two things the plain sentence must carry, because they ARE the decision: the
 * names of the things involved — under the label the rest of the screen uses,
 * not the mapping's English identifier (K4) — and the actual columns at stake,
 * never just how many there were.
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

/** A numeric column stored as text — SPARQL then compares "9.4" above "100.0",
 *  so max/min/ORDER BY answer WRONGLY with no error. Deterministic post-processing
 *  usually stamps the datatype in, but when one survives it must not read as an
 *  anonymous "other": it is the one advisory that makes an ANSWER wrong. */
const UNTYPED_NUMERIC = 'holds numbers but is mapped as an untyped literal'

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

/** `column 'Seebeck' holds numbers but is mapped as …` → "Seebeck" */
function untypedNumericColumn(advisory: string): string {
  const m = /^column '([^']+)' holds numbers/.exec(advisory)
  return m ? m[1] : ''
}

/**
 * The columns one "never uses" advisory names (`source s has 17 column(s) the
 * mapping never uses: a, b, c. If a column carries meaning …`). The generator
 * prints at most ten and then glues a ` …` onto the last one, so the ellipsis is
 * stripped before splitting. Anchored on the fixed tail, so a column name
 * containing `.` survives (same rule as {@link shellDroppedColumns}).
 */
function unmappedColumns(advisory: string): string[] {
  const m = /column\(s\) the mapping never uses: (.+?)\. If a column carries/.exec(advisory)
  if (!m) return []
  const list = m[1].trim().replace(/\s*…$/, '')
  return list
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s && s !== '…')
}

/** How many columns the advisory DECLARES unused — the truth even when the
 *  printed list is capped at ten. */
function unmappedCount(advisory: string): number {
  const m = /has (\d+) column\(s\) the mapping never uses/.exec(advisory)
  return m ? Number(m[1]) : 0
}

/**
 * Human-readable names for the identifiers an advisory quotes.
 *
 * The generator names things by their ENGLISH identifier (`MaterialSample`,
 * `Sample.hasMeasurement`) because that is what the mapping calls them, while
 * every other surface — the wizard's 項目の意味 table, the catalog's rules tab —
 * shows the model.yaml label for the very same thing. One concept under two
 * names is exactly what ADR K4 forbids, so callers that already hold
 * `rules.labels` pass it in. Keys may be full term IRIs or bare local names.
 * Nothing is invented: an identifier with no label is printed as written.
 */
export type TermLabels = Record<string, string>

type LabelIndex = Map<string, string> | null

function labelIndex(labels?: TermLabels): LabelIndex {
  if (!labels) return null
  const index = new Map<string, string>()
  for (const [key, label] of Object.entries(labels)) {
    if (!label) continue
    index.set(key, label)
    // Advisories quote local names; first label wins if two IRIs collide there.
    const local = localName(key)
    if (!index.has(local)) index.set(local, label)
  }
  return index.size > 0 ? index : null
}

/**
 * The two separators the generator builds a compound identifier from:
 * `Class.predicate` (shape rows) and `A + B` (a disconnected group that holds
 * several maps — `" + ".join(...)` in rml_validate.py). Kept as a capture group
 * so the separators survive the round trip.
 */
const IDENTIFIER_PARTS = /(\s\+\s|\.)/

/** `Sample.hasMeasurement` → 「試料.測定した」, `Sample + Batch` → 「試料 + 群」.
 *  Each segment is looked up on its own, so a compound whose class has a label
 *  and whose property does not still reads better than the bare identifier. */
function labelFor(name: string, index: LabelIndex): string {
  if (!index || !name) return name
  const hit = index.get(name)
  if (hit) return hit
  const parts = name.split(IDENTIFIER_PARTS)
  if (parts.length < 2) return name
  let changed = false
  // split() with a capture group interleaves separators at the odd indices.
  const mapped = parts.map((p, i) => {
    if (i % 2 === 1) return p
    const label = index.get(p)
    if (label) changed = true
    return label ?? p
  })
  return changed ? mapped.join('') : name
}

/** Whether the locale actually carries a sentence for this key. A finding whose
 *  sentence is not (yet) translated folds into the counted "other" line — the
 *  same fail-closed rule ADR §5.1 sets for unknown trap ids, so a missing key can
 *  never surface as a raw `gallery:advisory.…` in front of a researcher. */
function hasSentence(key: string, opts?: Record<string, unknown>): boolean {
  return i18n.exists(key, opts)
}

/** One line per shape finding — each names the class.predicate it is about, which
 * is what lets the reader go straight to that row in the design. */
function shapeLines(
  advisories: string[],
  marker: string,
  key: string,
  out: PlainAdvisory[],
  labels: LabelIndex,
): string[] {
  const t = i18n.t.bind(i18n)
  const hits = advisories.filter((a) => a.includes(marker))
  for (const a of hits) {
    out.push({ text: t(key, { subject: labelFor(subjectOf(a), labels) }), raw: [a] })
  }
  return hits
}

/**
 * @param labels optional identifier → human label table (`rules.labels`), so the
 * plain sentences name things the way the rest of the screen names them. Omitted
 * ⇒ identifiers are printed exactly as the generator wrote them (today's text).
 */
export function plainAdvisories(advisories: string[], labels?: TermLabels): PlainAdvisory[] {
  const t = i18n.t.bind(i18n)
  const names = labelIndex(labels)
  const disconnected = advisories.filter((a) => a.includes(DISCONNECTED))
  const duplicate = advisories.filter((a) => a.includes(DUPLICATE_COLUMN))
  const unmapped = advisories.filter((a) => a.includes(UNMAPPED_COLUMN))
  const shells = advisories.filter((a) => a.includes(EMPTY_SHELL))
  const untyped = hasSentence('gallery:advisory.untypedNumeric')
    ? advisories.filter((a) => a.includes(UNTYPED_NUMERIC))
    : []
  const shape: PlainAdvisory[] = []
  const shapeHits = [
    ...shapeLines(advisories, SHAPE_MISSING, 'gallery:advisory.shapeMissing', shape, names),
    ...shapeLines(advisories, SHAPE_DANGLING, 'gallery:advisory.shapeDangling', shape, names),
    ...shapeLines(advisories, SHAPE_WRONG_CLASS, 'gallery:advisory.shapeWrongClass', shape, names),
    ...shapeLines(advisories, SHAPE_DATATYPE, 'gallery:advisory.shapeDatatype', shape, names),
  ]
  const known = new Set([
    ...disconnected,
    ...duplicate,
    ...unmapped,
    ...shells,
    ...untyped,
    ...shapeHits,
  ])
  const other = advisories.filter((a) => !known.has(a))

  const out: PlainAdvisory[] = []
  for (const a of disconnected) {
    const groups = disconnectedGroups(a).map((g) => labelFor(g, names))
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
    const cls = labelFor(shellMap(a), names)
    out.push({
      text:
        cols.length > 0
          ? t('gallery:advisory.emptyShellColumns', { cls, columns: cols.join(', ') })
          : t('gallery:advisory.emptyShell', { cls }),
      raw: [a],
    })
  }
  if (duplicate.length > 0) {
    out.push({ text: t('gallery:advisory.duplicateColumn', { count: duplicate.length }), raw: duplicate })
  }
  if (unmapped.length > 0) {
    // Which columns were dropped is the whole decision here — a weak model that
    // maps 3 of 20 columns is obvious the moment the names are on screen, and
    // invisible when only a count is (K11: never leave the reader with a number).
    // `count` counts COLUMNS, not advisories: one advisory per source file can
    // carry seventeen of them.
    // Never show a 0: if the generator ever reworded the sentence the count is
    // read from, fall back to the names we did extract, then to today's number.
    const cols = unmapped.flatMap(unmappedColumns)
    const count =
      unmapped.reduce((n, a) => n + unmappedCount(a), 0) || cols.length || unmapped.length
    const opts = { count, columns: cols.join(', ') }
    out.push({
      text:
        cols.length > 0 && hasSentence('gallery:advisory.unmappedColumns', opts)
          ? t('gallery:advisory.unmappedColumns', opts)
          : t('gallery:advisory.unmapped', opts),
      raw: unmapped,
    })
  }
  for (const a of untyped) {
    out.push({
      text: t('gallery:advisory.untypedNumeric', { column: untypedNumericColumn(a) }),
      raw: [a],
    })
  }
  // Data findings last: the design lines above are about something the user can
  // still edit, these are about data already ingested.
  out.push(...shape)
  if (other.length > 0) {
    out.push({ text: t('gallery:advisory.other', { count: other.length }), raw: other })
  }
  return out
}

/**
 * Marker phrases from the DETERMINISTIC validators (`asterism.rml_validate`,
 * `asterism_step0.mapping_ir`) → their plain sentence (ADR §5.1).
 *
 * These are NOT model prose, so their fixed phrases are stable enough to
 * translate one by one — and they carry the single most common weak-model
 * failure (a column that is not in the file), for which K11 already has a
 * canonical sentence. Counting them all as "その他" hid exactly that.
 */
const ISSUE_MARKERS: { marker: string; key: string }[] = [
  { marker: 'referenced by the mapping is not in', key: 'kantan:s5.trap.T8' }, // rml_validate
  { marker: "' is not in ", key: 'kantan:s5.trap.T8' }, // mapping_ir compile
  { marker: 'does not accept parameter', key: 'kantan:s5.trap.function' },
  { marker: 'is missing required parameter', key: 'kantan:s5.trap.function' },
  { marker: 'referenced by rml:source does not exist', key: 'kantan:s5.trap.source' },
  { marker: 'no compiled RML mapping', key: 'kantan:s5.trap.uncompiled' },
  { marker: 'is not parseable YAML', key: 'kantan:s5.trap.mieBroken' },
]

/**
 * The plain face of free-form validation / mapping issues — the shared
 * classifier for the wizard's stop card AND the catalog (BACKEND-TEXT-09).
 *
 * One sentence per FAMILY (a design that names four missing columns still reads
 * as one problem), unrecognised lines folded into a single count so a reworded
 * generator degrades to the old behaviour rather than to a wrong sentence.
 * `precededByLines` = other lines are already on the card, which decides
 * between 「このほか、…」 and the standalone wording (ADR §5.1).
 *
 * Nothing here is fed to the AI: callers keep passing the raw English for that
 * (display and AI input stay separate).
 */
export function plainIssues(issues: string[], precededByLines = false): PlainAdvisory[] {
  const t = i18n.t.bind(i18n)
  const out: PlainAdvisory[] = []
  const byKey = new Map<string, PlainAdvisory>()
  const others: string[] = []
  for (const issue of issues) {
    const hit = ISSUE_MARKERS.find((m) => issue.includes(m.marker))
    if (!hit) {
      others.push(issue)
      continue
    }
    const seen = byKey.get(hit.key)
    if (seen) {
      seen.raw.push(issue)
      continue
    }
    const line = { text: t(hit.key), raw: [issue] }
    byKey.set(hit.key, line)
    out.push(line)
  }
  if (others.length > 0) {
    // 「このほか、」 reads right only when a line came before it — either one of
    // ours above, or the trap sentences this list is appended to (ADR §5.1).
    const after = precededByLines || out.length > 0
    out.push({
      text: t(after ? 'kantan:s5.trap.others' : 'kantan:s5.trap.othersOnly', {
        count: others.length,
      }),
      raw: others,
    })
  }
  return out
}
