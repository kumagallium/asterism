// Turning a discovered candidate into words a person can act on. Pure functions, no
// i18n calls: they return KEYS the caller resolves, so the wording lives in the locale
// files and this stays unit-testable (the `kantan/errorMessages.ts` shape).
//
// No domain dictionary here on purpose. The concept name comes from the data's own
// column names, and the only domain words on screen are the values themselves.
import type { DiscoverCandidate } from './crosswalkApi'

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
 * are real). Returned as {key, values} so the caller resolves them in its language. */
export function askQuestionsFor(
  candidate: DiscoverCandidate,
): { key: string; values: Record<string, string> }[] {
  const label = conceptLabel(candidate.concept)
  const value = candidate.samples[0]?.key ?? ''
  const names = candidate.participants.map((p) => p.name)
  const out: { key: string; values: Record<string, string> }[] = []
  if (value) out.push({ key: 'crosswalk:create.done.askQ1', values: { value } })
  if (names.length >= 2) {
    out.push({
      key: 'crosswalk:create.done.askQ2',
      values: { a: names[0], b: names[1], label },
    })
  }
  out.push({ key: 'crosswalk:create.done.askQ3', values: { label } })
  return out
}
