// Turning a discovered candidate into words a person can act on. Pure functions, no
// i18n calls: they return KEYS the caller resolves, so the wording lives in the locale
// files and this stays unit-testable (the `kantan/errorMessages.ts` shape).
//
// No domain dictionary here on purpose. The concept name comes from the data's own
// column names, and the only domain words on screen are the values themselves.
import type { DiscoverCandidate } from './crosswalkApi'
import { plainError } from './kantan/errorMessages'

/** Explanations for the closed normalizer set. An unknown id (a server newer than
 * this build) falls back rather than showing a raw identifier. */
const NORMALIZER_KEYS = new Set([
  'identity',
  'casefold',
  'whitespace',
  'nfkc',
  'loose_text',
  'composition',
  'element_canonical',
  'recipe',
])

/** The closed caution set the discovery emits. Same fallback rule. */
const FLAG_KEYS = new Set([
  'single_value_overlap',
  'low_cardinality',
  'high_fanout',
  'values_truncated',
  'predicates_truncated',
  'numeric_like',
  'fold_only_match',
  'asymmetric_coverage',
])

/** i18n key for "what counts as the same value here", or the generic fallback. */
export function sameAsKey(normalizer: string): string {
  return `crosswalk:create.sameAs.${NORMALIZER_KEYS.has(normalizer) ? normalizer : 'unknown'}`
}

/** i18n key for a caution flag, or undefined when this build does not know it (an
 * unknown id is skipped rather than rendered raw — an untranslated token would read
 * as breakage, and the candidate is still usable). */
export function flagKey(flag: string): string | undefined {
  return FLAG_KEYS.has(flag) ? `crosswalk:create.flag.${flag}` : undefined
}

/** A concept key as a heading: `crystal_system` → `crystal system`. Nothing clever —
 * the words come from the data, so anything smarter would be a guess. */
export function conceptLabel(concept: string): string {
  return concept.replace(/_/g, ' ').trim() || concept
}

/** Keys the discovery mints when the joined fields share no word of their own
 * (`shared_value_1`). They name nothing a person would recognise, so a heading built
 * from one is worse than no heading at all. */
const PLACEHOLDER_KEY = /^shared[_ ]value(?:[_ ]?\d+)?$/i

/** What a concept should be CALLED on screen, or `undefined` when the only thing on
 * hand is a minted placeholder (the caller then says "the value found in both").
 *
 * Order: the human label the server resolved for it (K8 — the field's own label from
 * the design), then the field label every participant agrees on, then the concept key
 * humanised. The raw key belongs in a `title` attribute, never in the heading. */
export function conceptDisplay(
  candidate: Pick<DiscoverCandidate, 'concept' | 'concept_label' | 'participants'>,
): string | undefined {
  const given = (candidate.concept_label ?? '').trim()
  if (given) return given
  const fields = new Set(
    candidate.participants.map((p) => (p.predicate_label ?? '').trim()).filter(Boolean),
  )
  if (fields.size === 1) {
    const only = [...fields][0]
    if (!PLACEHOLDER_KEY.test(only)) return conceptLabel(only)
  }
  return PLACEHOLDER_KEY.test(candidate.concept) ? undefined : conceptLabel(candidate.concept)
}

/** Names older servers minted for a crosswalk nobody named ("crosswalk hub (…)",
 * "crosswalk: crystal"). They are stored data, so they still arrive from the api —
 * but they are an implementation word plus a slug, which is not a name. */
const IMPLEMENTATION_NAME = /^crosswalk(\s|:)/i

/** What to call a crosswalk in the tab strip / the overview: its name, or `undefined`
 * when it has none worth showing (the caller then says "unnamed connection" and keeps
 * the id in a `title`). */
export function perspectiveDisplayName(p: {
  dataset?: { name?: string } | null
  perspective_id?: string
}): string | undefined {
  const name = (p.dataset?.name ?? '').trim()
  return !name || IMPLEMENTATION_NAME.test(name) ? undefined : name
}

/** The same rule for a SAVED crosswalk's concept (a stored config has the key; the
 * label arrives with the read when the server can resolve one). */
export function conceptName(name: string, label?: string): string | undefined {
  const given = (label ?? '').trim()
  if (given) return given
  return PLACEHOLDER_KEY.test(name) ? undefined : conceptLabel(name)
}

/** The label to put INSIDE a sentence a person is about to send (a try-it question),
 * or `undefined` when there is none. Stricter than `conceptDisplay` on purpose: a
 * heading may fall back to the humanised key (`composition`), because a heading is
 * read as a caption over evidence — but the same word inside a Japanese question
 * reads as an identifier that leaked. Only the label the SERVER resolved from the
 * design (K8) counts here; without it the caller asks about "values" instead. */
export function conceptSentenceLabel(source: { concept_label?: string }): string | undefined {
  return (source.concept_label ?? '').trim() || undefined
}

/** Did folding buy anything? Returns how many the strictest rung matched vs the chosen
 * one, so a card can say "as they are 12; ignoring case and width, 215". Undefined
 * when the strict rung already found everything (nothing worth saying). */
export function foldingGain(
  candidate: DiscoverCandidate,
): { strict: number; chosen: number } | undefined {
  const strict = candidate.normalizer_trials.find((t) => t.normalizer === 'identity')
  if (!strict || candidate.normalizer === 'identity') return undefined
  if (strict.matched >= candidate.matched) return undefined
  return { strict: strict.matched, chosen: candidate.matched }
}

/** Try-it questions built from the candidate's own data (templates are i18n, values
 * are real). Returned as {key, values} so the caller resolves them in its language.
 * `label` is what the caller already shows as "what they connect on" — passed in so
 * the questions never disagree with the card above them, and `undefined` when there
 * is no such word (the questions then ask about "values", never about a key). */
export function askQuestionsFor(
  candidate: DiscoverCandidate,
  label: string | undefined,
): { key: string; values: Record<string, string> }[] {
  const sample = candidate.samples[0]
  // The card shows the value AS ONE DATASET SPELLS IT; `key` is the folded join key
  // (`Bi₂Te₃` vs `bi2te3`). A question about the folded form would put a string that
  // exists nowhere in the person's data into their question box — and simply swapping
  // the spelling in is not an option either, because the hub labels its values with
  // the folded key, so the answer would come back empty. So this question is offered
  // only when folding changed nothing.
  const raw = sample ? (Object.values(sample.raw)[0] ?? '') : ''
  const key = sample?.key ?? ''
  const spellingKept = candidate.normalizer === 'identity' || (!!raw && raw === key)
  const names = candidate.participants.map((p) => p.name)
  const out: { key: string; values: Record<string, string> }[] = []
  if (key && spellingKept) {
    out.push({ key: 'crosswalk:create.done.askQ1', values: { value: raw || key } })
  }
  if (names.length >= 2) {
    out.push(
      label
        ? { key: 'crosswalk:create.done.askQ2', values: { a: names[0], b: names[1], label } }
        : { key: 'crosswalk:create.done.askQ2Plain', values: { a: names[0], b: names[1] } },
    )
  }
  out.push(
    label
      ? { key: 'crosswalk:create.done.askQ3', values: { label } }
      : { key: 'crosswalk:create.done.askQ3Plain', values: {} },
  )
  return out
}

// --- Failures, said in this screen's words ----------------------------------------

/** Which family a failure belongs to. The kantan stop card classifies the raw string
 * (status prefix + folded detail); this maps its verdict onto wording that fits a
 * screen where **no AI runs** — the kantan timeout line tells people to switch to a
 * faster model, which would be a lie here (comparing values is key-free). */
const FAMILY_BY_BODY: Record<string, string> = {
  'kantan:s5.plain.tokenBody': 'token',
  'kantan:s5.plain.notFoundBody': 'notFound',
  'kantan:s5.plain.timeoutBody': 'timeout',
  'kantan:s5.plain.serverBody': 'server',
}

export interface CrosswalkError {
  /** i18n key for the plain headline. */
  title: string
  /** i18n key for the plain body sentence. */
  body: string
  /** Present only when the way out is the settings screen (a missing access code). */
  hint?: 'settings'
}

/** A raw api error string → the plain sentence this screen should show. The technical
 * string is never thrown away: callers keep it in the folded "詳しい内容（技術情報）". */
export function crosswalkError(raw: string): CrosswalkError {
  const family = FAMILY_BY_BODY[plainError(raw).body] ?? 'generic'
  return {
    title: `crosswalk:error.plain.${family}Title`,
    body: `crosswalk:error.plain.${family}Body`,
    ...(family === 'token' ? { hint: 'settings' as const } : {}),
  }
}
