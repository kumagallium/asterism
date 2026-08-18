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

/** Which recovery action the stop card should surface as its primary button.
 *  `meanings` sends the reader back to the column-meaning step (S6): the only
 *  useful move when a publish is refused because nothing was ingested yet —
 *  retrying the publish itself can never succeed (BACKEND-TEXT-29). */
export type ErrorHint = 'settings' | 'restart' | 'fix' | 'wait' | 'meanings'

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

/** Failures whose CAUSE the server already named. `jobs.py` sends a stable
 *  `code` beside every job error and `ApiError` unwraps `{"detail":{"error":…}}`,
 *  so whenever one reached us there is no need to read English prose at all —
 *  a provider SDK rewording its message can no longer change what the reader is
 *  told (BACKEND-TEXT-06 / -14). Codes not listed here fall through to the
 *  keyword families below, and so does every failure that carries no code. */
const BY_CODE: Record<string, PlainError> = {
  'llm.auth': { title: 'kantan:s5.plain.llmAuthTitle', body: 'kantan:s5.plain.llmAuthBody' },
  'llm.model_not_found': {
    title: 'kantan:s5.plain.modelTitle',
    body: 'kantan:s5.plain.modelBody',
  },
  'llm.rate_limit': {
    title: 'kantan:s5.plain.rateTitle',
    body: 'kantan:s5.plain.rateBody',
    hint: 'wait',
  },
  'llm.timeout': {
    title: 'kantan:s5.plain.timeoutTitle',
    body: 'kantan:s5.plain.timeoutBody',
    hint: 'wait',
  },
  'job.timeout': {
    title: 'kantan:s5.plain.timeoutTitle',
    body: 'kantan:s5.plain.timeoutBody',
    hint: 'wait',
  },
  'llm.truncated': {
    title: 'kantan:s5.plain.budgetTitle',
    body: 'kantan:s5.plain.budgetBody',
    hint: 'wait',
  },
  // Nothing usable came back: an empty answer, or reasoning text with no
  // answer in it. Same advice either way — run it again (WEAK-MODEL-34).
  'llm.empty': {
    title: 'kantan:s5.plain.emptyTitle',
    body: 'kantan:s5.plain.emptyBody',
    hint: 'wait',
  },
  'llm.reasoning_only': {
    title: 'kantan:s5.plain.emptyTitle',
    body: 'kantan:s5.plain.emptyBody',
    hint: 'wait',
  },
  'llm.unreachable': {
    title: 'kantan:s5.plain.serverTitle',
    body: 'kantan:s5.plain.serverBody',
    hint: 'wait',
  },
  'llm.provider_error': {
    title: 'kantan:s5.plain.serverTitle',
    body: 'kantan:s5.plain.serverBody',
    hint: 'wait',
  },
  'ingest.unsafe_rml': {
    title: 'kantan:s5.trap.T9',
    body: 'kantan:s5.plain.designBody',
    hint: 'fix',
  },
  'ingest.materialize_failed': {
    title: 'kantan:s5.plain.materializeTitle',
    body: 'kantan:s5.plain.designBody',
    hint: 'fix',
  },
  'dataset.not_ingested': {
    title: 'kantan:s5.plain.notIngestedTitle',
    body: 'kantan:s5.plain.notIngestedBody',
    hint: 'meanings',
  },
}

/** `api.ts` prefixes every failed call with its own verb, so a raw string of
 *  this shape can only have come from THIS server — never from a model
 *  provider. The 404 family keys on it: "your saved data is gone, start over"
 *  must not be said because some provider answered 404 for a model id
 *  (BACKEND-TEXT-32). Every 404 our api raises really is a missing record
 *  (dataset / staging / job). */
const API_404 = /\bfailed \(http 404\)/

export function plainError(raw: string, code?: string): PlainError {
  const known = code ? BY_CODE[code] : undefined
  if (known) return known
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
  // The configured model id does not exist for this provider. Providers word
  // this differently ("The model `x` does not exist", a bare `not_found_error`)
  // and every wording arrives with a 404 in it — so this family has to win
  // before the deleted-record one below, or a mistyped model name would tell
  // the reader to throw their work away (BACKEND-TEXT-32).
  if (
    has('model not found', 'model_not_found', 'unknown model', 'does not exist or you do not') ||
    // `model.yaml` is one of OUR files: "column … does not exist in model.yaml"
    // is a design complaint, not a provider saying the model id is unknown.
    (has('model') &&
      !has('model.yaml') &&
      has('does not exist', 'no such model', 'not_found_error'))
  ) {
    return {
      title: 'kantan:s5.plain.modelTitle',
      body: 'kantan:s5.plain.modelBody',
    }
  }
  // The AI provider is throttling / overloaded.
  if (has('rate limit', 'rate_limit', 'http 429', 'too many requests', 'overloaded')) {
    return { title: 'kantan:s5.plain.rateTitle', body: 'kantan:s5.plain.rateBody', hint: 'wait' }
  }
  // The step did not finish in time. Evaluated BEFORE the permission family on
  // purpose: the shared job-timeout sentence ends with "…or a lower max-tokens
  // setting", which a token-shaped match would turn into "you lack permission →
  // open Settings" — an access code nobody can find (BACKEND-TEXT-03). Retry is
  // the primary action: this family also covers ingest/attach (no model
  // involved), and the model choice is not the reader's to make (ADR K5).
  if (has('timed out', 'timeout', 'time out')) {
    return {
      title: 'kantan:s5.plain.timeoutTitle',
      body: 'kantan:s5.plain.timeoutBody',
      hint: 'wait',
    }
  }
  // The AI produced nothing usable (empty answer / reasoning text only) — the
  // classic weak-thinking-model failure. Named on the sentences `llm.py`
  // raises ("The model returned an empty response", "…only reasoning text and
  // no answer") as well as the shorter provider phrasings, and running it
  // again is what actually clears it (WEAK-MODEL-34).
  if (
    has(
      'only reasoning',
      'reasoning text',
      'reasoning_only',
      'empty output',
      'empty response',
      'no answer',
      'returned no output',
    )
  ) {
    return {
      title: 'kantan:s5.plain.emptyTitle',
      body: 'kantan:s5.plain.emptyBody',
      hint: 'wait',
    }
  }
  // The model spent its output budget before finishing (a reasoning model that
  // thought until it ran out). Also above the permission family: its sentence
  // names "max output tokens" (WEAK-MODEL-32).
  if (
    has(
      'output budget',
      'reasoning effort',
      'max output tokens',
      'still truncated',
      'truncated after',
    )
  ) {
    return {
      title: 'kantan:s5.plain.budgetTitle',
      body: 'kantan:s5.plain.budgetBody',
      hint: 'wait',
    }
  }
  // Permission — a missing / rejected write token (the 503 gate or a 401/403).
  // Matched on the words the server actually emits (the plain Japanese sentence
  // and the env-var name) plus the HTTP status this module always prefixes; a
  // bare 'token' would swallow "max_tokens", and a bare 'unauthorized' /
  // 'forbidden' would swallow a provider's own wording (KZ-A-21).
  if (
    has(
      'asterism_api_token',
      'x-asterism-token',
      'api token',
      '利用許可コード',
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
  // An Excel workbook the server could not open (its own machine-readable code
  // — the openpyxl wording never reaches the reader).
  if (has('xlsx.convert_failed', 'xlsx.unreadable')) {
    return { title: 'kantan:s5.plain.xlsxTitle', body: 'kantan:s5.plain.xlsxBody' }
  }
  // Publish refused because nothing was ingested yet: retrying the publish can
  // never succeed, so the only exit is back to the column meanings.
  if (has('dataset.not_ingested', 'not ingested', 'no staged graph')) {
    return {
      title: 'kantan:s5.plain.notIngestedTitle',
      body: 'kantan:s5.plain.notIngestedBody',
      hint: 'meanings',
    }
  }
  // The saved design record vanished (deleted in the catalog meanwhile) — a
  // fresh start is the only clean recovery. Keyed on the status AND on the
  // shape only this api produces: a bare 'not found' also appears in "column
  // 'X' not found", which is a design problem (KZ-A-21), and a bare 404 also
  // appears in an LLM provider's "model does not exist" (BACKEND-TEXT-32).
  if (API_404.test(hay)) {
    return {
      title: 'kantan:s5.plain.notFoundTitle',
      body: 'kantan:s5.plain.notFoundBody',
      hint: 'restart',
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
  // The design asked for an operation outside the safety-vetted set. K11 already
  // has the canonical one-liner for this (T9) — reuse it rather than letting the
  // English "unsafe RML mapping: …" fall through to "please try again", which a
  // retry can never clear (BACKEND-TEXT-05).
  if (has('unsafe rml')) {
    return { title: 'kantan:s5.trap.T9', body: 'kantan:s5.plain.designBody', hint: 'fix' }
  }
  // The conversion engine refused the design as written — deterministic, so a
  // retry is pointless; the AI fix is the move.
  if (has('morph-kgc', 'materialization failed')) {
    return {
      title: 'kantan:s5.plain.materializeTitle',
      body: 'kantan:s5.plain.designBody',
      hint: 'fix',
    }
  }
  // The design never became conversion rules at all (a legacy / half-saved
  // record). Same exit: hand it back to the AI.
  if (has('no compiled rml mapping')) {
    return {
      title: 'kantan:s5.plain.uncompiledTitle',
      body: 'kantan:s5.plain.uncompiledBody',
      hint: 'fix',
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
  // A file the reader could not open at all (unsupported / malformed upload).
  // Last of the specific families, and deliberately narrow: the status alone
  // would swallow a design 422, and so would a keyword like "source" — the
  // ingest complaint «column 'ZT' not found in source» is about the DESIGN, not
  // about an unreadable file (KZ-A-21). So the failing operation itself has to
  // be one that reads a source; `api.ts` always prefixes it.
  if (
    (/^(inspect|attach|stage|upload)\b/i.test(raw) &&
      has('http 400', 'http 415', 'http 422', 'unsupported', 'unreadable')) ||
    has('unsupported file', 'unreadable source', 'ソースを読み取れ')
  ) {
    return { title: 'kantan:s5.plain.unreadableTitle', body: 'kantan:s5.plain.unreadableBody' }
  }
  // Anything else: keep the card's per-stage headline, add a gentle nudge.
  return { body: 'kantan:s5.plain.genericBody' }
}
