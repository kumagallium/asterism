// The bridge between the design-consult drawer's `asterism-suggestions` code
// blocks (ADR design-consult-chat.md D9) and whichever screen currently knows
// how to apply them — only KantanWizard's S6 today. Same "module-scoped
// store, not React context" posture as `consultContext.ts`: the drawer and
// the wizard are otherwise unrelated trees, and this keeps them decoupled —
// the drawer never imports anything from `kantan/`, it just calls
// `applySuggestions()` and reports whatever comes back.
//
// D5 still holds here: applying a suggestion only fills a BLANK meaning/unit
// field with the AI's wording — it never changes an include/exclude decision
// and never overwrites something the human already typed. The human still
// has to look at what landed and decide whether to keep it.

export interface ConsultSuggestion {
  column: string
  meaning?: string
  unit?: string
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
  /** Parsed candidates, or `[]` when there was no block or it didn't parse —
   *  a malformed block is never an error, it just has no suggestions. */
  suggestions: ConsultSuggestion[]
}

/** Find and parse an `asterism-suggestions` block in an assistant reply. A
 *  missing or malformed block is not an error — `displayText` still comes
 *  back (unmodified) and `suggestions` is empty, so a parse failure degrades
 *  to "just show the reply", never to a broken bubble. */
export function parseSuggestionsBlock(text: string): ParsedSuggestions {
  const m = text.match(SUGGESTIONS_BLOCK_RE)
  if (!m || m.index === undefined) return { displayText: text, suggestions: [] }
  const displayText = (text.slice(0, m.index) + text.slice(m.index + m[0].length)).trim()
  try {
    const parsed = JSON.parse(m[1]) as { suggestions?: unknown }
    if (!Array.isArray(parsed.suggestions)) return { displayText, suggestions: [] }
    const suggestions: ConsultSuggestion[] = []
    for (const raw of parsed.suggestions) {
      if (!raw || typeof raw !== 'object') continue
      const r = raw as Record<string, unknown>
      const column = typeof r.column === 'string' ? r.column.trim() : ''
      if (!column) continue
      suggestions.push({
        column,
        meaning: typeof r.meaning === 'string' && r.meaning.trim() ? r.meaning.trim() : undefined,
        unit: typeof r.unit === 'string' && r.unit.trim() ? r.unit.trim() : undefined,
      })
    }
    return { displayText, suggestions }
  } catch {
    return { displayText, suggestions: [] }
  }
}

export interface ApplySuggestionsResult {
  /** Suggestions that changed at least one blank field. */
  applied: number
  /** Suggestions that matched a column but changed nothing (every field they
   *  offered was already filled in). */
  skipped: number
}

type Applier = (suggestions: ConsultSuggestion[]) => ApplySuggestionsResult

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

/** Apply suggestions via the currently-registered applier. `null` when
 *  nothing is registered (the drawer shows "この画面では反映できません"). */
export function applySuggestions(suggestions: ConsultSuggestion[]): ApplySuggestionsResult | null {
  return applier ? applier(suggestions) : null
}
