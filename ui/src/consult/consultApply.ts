// The bridge between the design-consult drawer's `asterism-suggestions` code
// blocks (ADR design-consult-chat.md D10) and whichever screen currently
// knows how to apply them — KantanWizard's S6 (column meanings/units) and S4
// (kind names) today. Same "module-scoped store, not React context" posture
// as `consultContext.ts`: the drawer and the wizard are otherwise unrelated
// trees, and this keeps them decoupled — the drawer never imports anything
// from `kantan/`, it just calls `applySuggestions()` and reports whatever
// comes back.
//
// D5 still holds here: applying a suggestion only fills a BLANK field with
// the AI's wording (a column's meaning/unit, or a map's empty kind name) —
// it never changes an include/exclude decision, never touches how an ID is
// built, and never overwrites something the human already typed. The human
// still has to look at what landed and decide whether to keep it.

import type { ColumnOwner, IdentifierPick, KindSplit } from '../skeletonKinds'

export interface ConsultSuggestion {
  column: string
  meaning?: string
  unit?: string
}

/** One `kinds[]` entry from a suggestions block — a proposed "1 件が表すもの"
 *  (kind/class) name for a map named in S4's "データの種類" context line. */
export interface ConsultKindSuggestion {
  map: string
  name: string
}

// The fenced-code-block language tag CONSULT_SYSTEM_PROMPT
// (api/src/asterism_api/main.py CONSULT_SUGGESTIONS_FENCE) tells the model to
// use for a suggestions block. Kept as one constant here too so the two
// sides can never silently drift apart.
export const SUGGESTIONS_FENCE = 'asterism-suggestions'

const SUGGESTIONS_BLOCK_RE = new RegExp(
  '```' + SUGGESTIONS_FENCE + '\\s*\\n([\\s\\S]*?)\\n?```',
)

export interface ParsedSuggestions {
  /** The reply with the suggestions block removed (never shown verbatim). */
  displayText: string
  /** Parsed column candidates, or `[]` when there was no block, it didn't
   *  parse, or it had no `suggestions` — a malformed block is never an
   *  error, it just has nothing to offer. */
  suggestions: ConsultSuggestion[]
  /** Parsed kind-name candidates (S4), same never-an-error posture. */
  kinds: ConsultKindSuggestion[]
  /** ADR kind-splitting D3: 同じキーから種類を分ける提案。 */
  splits: KindSplit[]
  /** D3: 帰属の移動（S4 の「載せる種類」と同じ操作）。 */
  owners: ColumnOwner[]
  /** D3: ID を与える候補。`reason` の無いものは**捨てる** — K22 は人が判断
   *  できる材料を出すことを求めていて、理由の無い候補はただ押させるだけ。 */
  identifiers: IdentifierPick[]
}

const EMPTY_SUGGESTIONS: Omit<ParsedSuggestions, 'displayText'> = {
  suggestions: [],
  kinds: [],
  splits: [],
  owners: [],
  identifiers: [],
}

/** 提案ブロックの中の文字列。空文字・非文字列は落とす。 */
function str(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

/** Find and parse an `asterism-suggestions` block in an assistant reply. A
 *  missing or malformed block is not an error — `displayText` still comes
 *  back (unmodified) and both lists are empty, so a parse failure degrades
 *  to "just show the reply", never to a broken bubble. */
export function parseSuggestionsBlock(text: string): ParsedSuggestions {
  const m = text.match(SUGGESTIONS_BLOCK_RE)
  if (!m || m.index === undefined) return { displayText: text, ...EMPTY_SUGGESTIONS }
  const displayText = (text.slice(0, m.index) + text.slice(m.index + m[0].length)).trim()
  try {
    const parsed = JSON.parse(m[1]) as Record<string, unknown>
    const rows = (key: string): Record<string, unknown>[] =>
      Array.isArray(parsed[key])
        ? (parsed[key] as unknown[]).filter(
            (r): r is Record<string, unknown> => !!r && typeof r === 'object',
          )
        : []

    const suggestions: ConsultSuggestion[] = []
    for (const r of rows('suggestions')) {
      const column = str(r.column)
      if (!column) continue
      suggestions.push({
        column,
        meaning: str(r.meaning) || undefined,
        unit: str(r.unit) || undefined,
      })
    }
    const kinds: ConsultKindSuggestion[] = []
    for (const r of rows('kinds')) {
      const map = str(r.map)
      const name = str(r.name)
      if (!map || !name) continue
      kinds.push({ map, name })
    }
    const splits: KindSplit[] = []
    for (const r of rows('splits')) {
      const from = str(r.from)
      const name = str(r.name)
      const columns = Array.isArray(r.columns) ? r.columns.map(str).filter(Boolean) : []
      if (!from || !name || columns.length === 0) continue
      splits.push({ from, name, columns })
    }
    const owners: ColumnOwner[] = []
    for (const r of rows('owners')) {
      const column = str(r.column)
      const map = str(r.map)
      if (!column || !map) continue
      owners.push({ column, map })
    }
    const identifiers: IdentifierPick[] = []
    for (const r of rows('identifiers')) {
      const column = str(r.column)
      // 理由の無い候補は捨てる（K22: 押させるのではなく、選ばせる）。ID を配った
      // あとは動かせないので、根拠なしに 1 押しで足せる形にはしない。
      const reason = str(r.reason)
      if (!column || !reason) continue
      identifiers.push({ column, reason })
    }
    return { displayText, suggestions, kinds, splits, owners, identifiers }
  } catch {
    return { displayText, ...EMPTY_SUGGESTIONS }
  }
}

export interface ApplyPayload {
  suggestions: ConsultSuggestion[]
  kinds: ConsultKindSuggestion[]
  /** D3 の 3 型。適用は決定論（UI のチェック／ドロップダウンを機械が動かすのと
   *  同じ）で、LLM の再実行は要らない。S6 の applier はこれらを無視する。 */
  splits: KindSplit[]
  owners: ColumnOwner[]
  identifiers: IdentifierPick[]
}

export interface ApplySuggestionsResult {
  /** Entries that changed at least one blank field. */
  applied: number
  /** Entries that matched something on screen but changed nothing (every
   *  field they offered was already filled in). */
  skipped: number
}

type Applier = (payload: ApplyPayload) => ApplySuggestionsResult

let applier: Applier | null = null

/** The active screen registers how to apply suggestions here. Returns an
 *  unregister function — call it (as a `useEffect` cleanup) when leaving the
 *  screen, so a stale applier from a screen the user is no longer on can
 *  never run. Registering again (e.g. the same screen's data changed)
 *  replaces the previous applier outright. */
export function registerSuggestionApplier(fn: Applier): () => void {
  applier = fn
  return () => {
    if (applier === fn) applier = null
  }
}

/** Whether some screen is currently able to apply suggestions — the drawer
 *  uses this to decide whether to even show the "反映" button. */
export function hasSuggestionApplier(): boolean {
  return applier !== null
}

/** Apply a payload via the currently-registered applier. `null` when nothing
 *  is registered (the drawer shows "この画面では反映できません"). */
export function applySuggestions(payload: ApplyPayload): ApplySuggestionsResult | null {
  return applier ? applier(payload) : null
}
