// The query tools Asterism itself ships — attached automatically to a document
// dataset, or resident in the repo's example vocabularies. Their names, titles,
// parameters and result columns are declared in English for the agent that calls
// them; a person reading the same screen gets the wording from
// `tools.json → bundled.<name>` instead. Anything a user authored keeps their own
// naming (only they know what it means) and keeps its 編集 / 削除 buttons — a
// shipped tool is not theirs to edit from the shared screen.
//
// Lives in its own module (not in a component file) so both the panel and the
// runner can import it without breaking react-refresh's component-only rule.
export const BUNDLED_TOOLS = new Set([
  'search_text',
  'quote_with_citation',
  'fetch_passage',
  'measurement_provenance',
])

export function isBundledTool(name: string): boolean {
  return BUNDLED_TOOLS.has(name)
}
