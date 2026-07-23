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

export function plainAdvisories(advisories: string[]): PlainAdvisory[] {
  const t = i18n.t.bind(i18n)
  const disconnected = advisories.filter((a) => a.includes(DISCONNECTED))
  const duplicate = advisories.filter((a) => a.includes(DUPLICATE_COLUMN))
  const unmapped = advisories.filter((a) => a.includes(UNMAPPED_COLUMN))
  const known = new Set([...disconnected, ...duplicate, ...unmapped])
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
  if (duplicate.length > 0) {
    out.push({ text: t('gallery:advisory.duplicateColumn', { count: duplicate.length }), raw: duplicate })
  }
  if (unmapped.length > 0) {
    out.push({ text: t('gallery:advisory.unmapped', { count: unmapped.length }), raw: unmapped })
  }
  if (other.length > 0) {
    out.push({ text: t('gallery:advisory.other', { count: other.length }), raw: other })
  }
  return out
}
