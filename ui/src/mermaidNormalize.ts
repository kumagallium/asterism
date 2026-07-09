// AI models keep emitting a classDiagram relation dialect that Mermaid does not
// accept: `A -- label --> B` (label between the dashes and the arrow; observed
// live 2026-07-08 across an AI-designed dataset's diagram.md). The legal form is
// `A --> B : label`. The rewrite is deliberately narrow — only lines that
// contain BOTH `--` and `-->` with a single identifier between them are touched,
// so legal `A -- B` links and labeled `A --> B : x` relations pass through
// unchanged. Callers render the normalized source; on-error fallbacks must keep
// showing the ORIGINAL source (what is stored is what the user sees).
const DIALECT_RELATION = /^(\s*)([A-Za-z_][\w]*)\s+--\s+([A-Za-z_][\w]*)\s+-->\s+([A-Za-z_][\w]*)\s*$/

export function normalizeMermaidDialects(chart: string): string {
  if (!/^\s*classDiagram/m.test(chart)) return chart
  return chart
    .split('\n')
    .map((line) => {
      const m = line.match(DIALECT_RELATION)
      return m ? `${m[1]}${m[2]} --> ${m[4]} : ${m[3]}` : line
    })
    .join('\n')
}
