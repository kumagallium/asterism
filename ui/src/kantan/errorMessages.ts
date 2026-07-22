// Deterministic error-family → plain-language translation for the kantan stop
// card (#7 / K11). The stop card hands the RAW technical string it already
// shows — an api error like `ingest failed (HTTP 404): {"detail":"…"}`, the
// write-token gate `materialize failed (HTTP 503): {"detail":"…ASTERISM_API_
// TOKEN…"}`, or a job error like `Request timed out` — and gets back i18n KEYS
// (the wording lives in kantan.json) plus a hint telling the card which recovery
// action to promote to primary. Pure and side-effect-free (no app state, no i18n
// call) so it is trivially unit-testable; classification runs over the whole raw
// string because the HTTP status sits in the "(HTTP 404)" prefix while the cause
// keywords sit in the folded `{"detail":…}` body.

/** Which recovery action the stop card should surface as its primary button. */
export type ErrorHint = 'settings' | 'restart' | 'fix' | 'wait'

export interface PlainError {
  /** i18n key for the plain headline. Absent → the card keeps its per-stage
   *  headline (the generic "…でエラーが起きました"). */
  title?: string
  /** i18n key for the plain body sentence (always present). */
  body: string
  /** Recovery action to promote; absent → retry stays the primary action. */
  hint?: ErrorHint
}

/** Pull the human sentence out of a FastAPI `{"detail":"…"}` body when the raw
 *  string carries one; otherwise return the raw string unchanged. Used only to
 *  sharpen keyword matching — the card still shows the full raw string in its
 *  folded technical view. Parse failure falls back to the raw string. */
function detailText(raw: string): string {
  const brace = raw.indexOf('{')
  if (brace >= 0) {
    try {
      const parsed = JSON.parse(raw.slice(brace)) as { detail?: unknown }
      if (typeof parsed.detail === 'string') return parsed.detail
    } catch {
      /* not JSON — fall through to the raw string */
    }
  }
  return raw
}

export function plainError(raw: string): PlainError {
  const detail = detailText(raw)
  const hay = `${raw} ${detail}`.toLowerCase()
  const has = (...needles: string[]) => needles.some((n) => hay.includes(n))

  // Order matters: the most specific / most actionable families win first, so a
  // 503 whose body names the write token is a permission problem, not a generic
  // "server unreachable" one.

  // Permission — a missing / rejected write token (the 503 gate or a 401/403).
  if (has('asterism_api_token', 'token', 'unauthorized', 'forbidden', 'http 401', 'http 403')) {
    return { title: 'kantan:s5.plain.tokenTitle', body: 'kantan:s5.plain.tokenBody', hint: 'settings' }
  }
  // The saved design record vanished (deleted in the catalog meanwhile) — a
  // fresh start is the only clean recovery.
  if (has('http 404', 'not found')) {
    return {
      title: 'kantan:s5.plain.notFoundTitle',
      body: 'kantan:s5.plain.notFoundBody',
      hint: 'restart',
    }
  }
  // The model did not answer in time.
  if (has('timed out', 'timeout', 'time out')) {
    return {
      title: 'kantan:s5.plain.timeoutTitle',
      body: 'kantan:s5.plain.timeoutBody',
      hint: 'settings',
    }
  }
  // The AI design still has something that cannot be ingested as-is. (Real trap
  // failures normally arrive as the dedicated `design` stop kind, which keeps
  // its own body + "AI に直してもらう" button; this is the defensive fallback for
  // the same family surfacing through an error kind.) \bt4\b / \bmie\b use word
  // boundaries so a hex dataset id can never trip them.
  if (
    has('truncated', 'incomplete', 'could not be compiled', 'mapping ir', 'mapping_ir') ||
    /\b(t4|mie)\b/i.test(raw)
  ) {
    return { title: 'kantan:s5.plain.designTitle', body: 'kantan:s5.plain.designBody', hint: 'fix' }
  }
  // The server was briefly unreachable (5xx / connection / network).
  if (
    has(
      'http 500',
      'http 502',
      'http 503',
      'http 504',
      'unreachable',
      'connection',
      'econnrefused',
      'failed to fetch',
      'network',
    )
  ) {
    return { title: 'kantan:s5.plain.serverTitle', body: 'kantan:s5.plain.serverBody', hint: 'wait' }
  }
  // Anything else: keep the card's per-stage headline, add a gentle nudge.
  return { body: 'kantan:s5.plain.genericBody' }
}
