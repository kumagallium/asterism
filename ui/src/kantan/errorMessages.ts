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
  /** Interpolation values for `body` (e.g. the size limit read out of the
   *  server's own sentence). Callers pass them straight to `t(body, vars)`. */
  vars?: Record<string, string | number>
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
  // "server unreachable" one — and an LLM-provider failure is classified as one
  // BEFORE the generic 401/404 families, whose keywords it would otherwise trip
  // ("model not found" is not a deleted dataset; an invalid provider key is not
  // a missing write token).

  // The AI provider rejected the key.
  if (
    has(
      'x-api-key',
      'invalid_api_key',
      'invalid api key',
      'incorrect api key',
      'authentication_error',
      'authentication error',
      'invalid_request_error: no api key',
    )
  ) {
    return {
      title: 'kantan:s5.plain.llmAuthTitle',
      body: 'kantan:s5.plain.llmAuthBody',
    }
  }
  // The configured model id does not exist for this provider.
  if (has('model not found', 'model_not_found', 'unknown model', 'does not exist or you do not')) {
    return {
      title: 'kantan:s5.plain.modelTitle',
      body: 'kantan:s5.plain.modelBody',
    }
  }
  // The AI provider is throttling / overloaded.
  if (has('rate limit', 'rate_limit', 'http 429', 'too many requests', 'overloaded')) {
    return { title: 'kantan:s5.plain.rateTitle', body: 'kantan:s5.plain.rateBody', hint: 'wait' }
  }
  // Permission — a missing / rejected write token (the 503 gate or a 401/403).
  // Matched on the words the server actually emits (the plain Japanese sentence
  // and the env-var name); a bare 'token' would also swallow "max_tokens".
  if (
    has(
      'asterism_api_token',
      'api token',
      '利用許可コード',
      'unauthorized',
      'forbidden',
      'http 401',
      'http 403',
    )
  ) {
    return { title: 'kantan:s5.plain.tokenTitle', body: 'kantan:s5.plain.tokenBody', hint: 'settings' }
  }
  // The file was too big for the upload gate. The limit is read out of the
  // server's OWN sentence ("exceeds the 1024 MiB limit") rather than duplicated
  // here, so the two can never drift; without a number the sentence simply
  // omits it (BACKEND-TEXT-02).
  if (has('http 413', 'exceeds the', 'too large', 'entity too large')) {
    const mb = /exceeds the (\d+) mib limit/.exec(hay)?.[1]
    return mb
      ? { title: 'kantan:s5.plain.tooLargeTitle', body: 'kantan:s5.plain.tooLargeBody', vars: { mb } }
      : { title: 'kantan:s5.plain.tooLargeTitle', body: 'kantan:s5.plain.tooLargeBodyNoSize' }
  }
  // The characters could not be read: a CSV saved in an encoding this reader
  // does not accept. Named on the server's deterministic sentence and on the
  // Python codec error it wraps.
  if (
    has('ソースをテキストとして読み取れませんでした') ||
    (has('decode', 'decoding') && has('codec', 'utf-8', 'utf8', 'byte', 'encoding'))
  ) {
    return { title: 'kantan:s5.plain.decodeTitle', body: 'kantan:s5.plain.decodeBody' }
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
  // The step did not finish in time. Retry is the primary action: this family
  // also covers ingest/attach (no model involved), and the model choice is not
  // the reader's to make in this tier (ADR K5).
  if (has('timed out', 'timeout', 'time out')) {
    return {
      title: 'kantan:s5.plain.timeoutTitle',
      body: 'kantan:s5.plain.timeoutBody',
      hint: 'wait',
    }
  }
  // The AI answered, but not in the shape the reader step can parse.
  if (
    has(
      'not valid json',
      'is not valid json/yaml',
      'must be a single json object',
      'could not parse',
    )
  ) {
    return { title: 'kantan:s5.plain.parseTitle', body: 'kantan:s5.plain.parseBody' }
  }
  // The AI produced nothing usable (empty answer / reasoning text only).
  if (has('only reasoning', 'reasoning_only', 'empty output', 'returned no output')) {
    return { title: 'kantan:s5.plain.emptyTitle', body: 'kantan:s5.plain.emptyBody' }
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
  // A file the reader could not open at all (unsupported / malformed upload).
  // Last of the specific families, and deliberately narrow: the status alone
  // would also swallow a design 422, so the message must be about a SOURCE.
  if (
    has('http 400', 'http 415', 'http 422', 'unsupported', 'unreadable') &&
    has('inspect', 'source', 'file', 'upload', 'ソース', 'ファイル')
  ) {
    return { title: 'kantan:s5.plain.unreadableTitle', body: 'kantan:s5.plain.unreadableBody' }
  }
  // Anything else: keep the card's per-stage headline, add a gentle nudge.
  return { body: 'kantan:s5.plain.genericBody' }
}
