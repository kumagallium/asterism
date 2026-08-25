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
}

/** Find and parse an `asterism-suggestions` block in an assistant reply. A
 *  missing or malformed block is not an error — `displayText` still comes
 *  back (unmodified) and both lists are empty, so a parse failure degrades
 *  to "just show the reply", never to a broken bubble. */
export function parseSuggestionsBlock(text: string): ParsedSuggestions {
  const m = text.match(SUGGESTIONS_BLOCK_RE)
  if (!m || m.index === undefined) return { displayText: text, suggestions: [], kinds: [] }
  const displayText = (text.slice(0, m.index) + text.slice(m.index + m[0].length)).trim()
  try {
    const parsed = JSON.parse(m[1]) as { suggestions?: unknown; kinds?: unknown }
    const suggestions: ConsultSuggestion[] = []
    if (Array.isArray(parsed.suggestions)) {
      for (const raw of parsed.suggestions) {
        if (!raw || typeof raw !== 'object') continue
        const r = raw as Record<string, unknown>
        const column = typeof r.column === 'string' ? r.column.trim() : ''
        if (!column) continue
        suggestions.push({
          column,
          meaning:
            typeof r.meaning === 'string' && r.meaning.trim() ? r.meaning.trim() : undefined,
          unit: typeof r.unit === 'string' && r.unit.trim() ? r.unit.trim() : undefined,
        })
      }
    }
    const kinds: ConsultKindSuggestion[] = []
    if (Array.isArray(parsed.kinds)) {
      for (const raw of parsed.kinds) {
        if (!raw || typeof raw !== 'object') continue
        const r = raw as Record<string, unknown>
        const map = typeof r.map === 'string' ? r.map.trim() : ''
        const name = typeof r.name === 'string' ? r.name.trim() : ''
        if (!map || !name) continue
        kinds.push({ map, name })
      }
    }
    return { displayText, suggestions, kinds }
  } catch {
    return { displayText, suggestions: [], kinds: [] }
  }
}

export interface ApplyPayload {
  suggestions: ConsultSuggestion[]
  kinds: ConsultKindSuggestion[]
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
