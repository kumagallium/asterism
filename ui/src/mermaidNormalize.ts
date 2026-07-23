// AI models keep emitting a classDiagram relation dialect that Mermaid does not
// accept: `A -- label --> B` (label between the dashes and the arrow; observed
// live 2026-07-08 across an AI-designed dataset's diagram.md). The legal form is
// `A --> B : label`. The rewrite is deliberately narrow — only lines that
// contain BOTH `--` and `-->` with a single identifier between them are touched,
// so legal `A -- B` links and labeled `A --> B : x` relations pass through
// unchanged. Callers render the normalized source; on-error fallbacks must keep
// showing the ORIGINAL source (what is stored is what the user sees).
const DIALECT_RELATION = /^(\s*)([A-Za-z_][\w]*)\s+--\s+([A-Za-z_][\w]*)\s+-->\s+([A-Za-z_][\w]*)\s*$/

// A member line is a class-body row: `+temperatureC [℃]`, `+zt xsd_double`,
// `-doStuff(a, b)`. Mermaid treats the spaces inside one as break opportunities
// and wraps the row when the label is as wide as the box it computed for it —
// and since the box is sized BY the widest row, the widest row always trips
// that test, so every class showed its longest attribute wrapped onto two
// centred lines (the "崩れ" reported live 2026-07-23 on ZEM). Joining a member
// line's spaces with NO-BREAK SPACE removes the break opportunity without
// changing a single visible glyph — verified against Mermaid 11.15: the parser
// accepts U+00A0 in member lines and the diagram renders every row on one line.
// Deliberately narrow: only lines whose first non-space character is a UML
// visibility marker, so class/relation/note syntax is never touched.
const MEMBER_LINE = /^(\s*)([+\-#~].*\S)\s*$/
// Escaped, never a literal: a raw U+00A0 here is invisible in diffs and one
// careless "normalize whitespace" pass silently turns this fix back into the
// bug (it happened to a probe copy while this was being developed).
const NBSP = '\u00A0'

export function normalizeMermaidDialects(chart: string): string {
  if (!/^\s*classDiagram/m.test(chart)) return chart
  return chart
    .split('\n')
    .map((line) => {
      const m = line.match(DIALECT_RELATION)
      if (m) return `${m[1]}${m[2]} --> ${m[4]} : ${m[3]}`
      const member = line.match(MEMBER_LINE)
      return member ? `${member[1]}${member[2].replace(/ /g, NBSP)}` : line
    })
    .join('\n')
}
