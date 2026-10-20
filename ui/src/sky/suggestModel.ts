// 「結べそうな線」の純関数（星図に薄い線で重ねる端・まとめて確定の ID・輪を出す点）。
import type { Suggestion, SuggestionKey } from './suggestApi'
import { kindId, termId, type SkyLayout } from './skyLayout'

/** 薄い線の 1 本: 星の点 id → 用語の札の点 id。 */
export interface OverlayEdge {
  from: string
  to: string
}

/**
 * 星図に重ねる候補の線。種類（星）→ 用語の札で、**両端が図にあるものだけ**（項目の候補・丸モード・
 * 図に出ていない用語は重ねない — 一覧には残る）。同じ組は 1 本。
 */
export function overlayEdges(layout: SkyLayout, suggestions: readonly Suggestion[]): OverlayEdge[] {
  const out: OverlayEdge[] = []
  const seen = new Set<string>()
  for (const s of suggestions) {
    const from = kindId(s.dataset_id, s.subject)
    const to = termId(s.term)
    if (layout.nodes.get(from)?.type !== 'kind' || layout.nodes.get(to)?.type !== 'term') continue
    const k = `${from}>${to}`
    if (seen.has(k)) continue
    seen.add(k)
    out.push({ from, to })
  }
  return out
}

/** 「まとめて確定」の ID（来歴）。時刻から作る — 同じ押下は 1 つの ID。 */
export function suggestBatchId(now: Date = new Date()): string {
  const p = (n: number, w = 2) => String(n).padStart(w, '0')
  return (
    `suggest-${now.getUTCFullYear()}${p(now.getUTCMonth() + 1)}${p(now.getUTCDate())}` +
    `T${p(now.getUTCHours())}${p(now.getUTCMinutes())}${p(now.getUTCSeconds())}`
  )
}

/** 線にした候補の端の IRI（星図を取り直して、輪を出す点）。 */
export function irisOfKeys(keys: readonly SuggestionKey[]): string[] {
  return [...new Set(keys.flatMap((k) => [k.subject, k.term]))]
}

/** 一覧から、返事をした候補を外した残り。 */
export function withoutKeys<T extends SuggestionKey>(list: readonly T[], done: readonly SuggestionKey[]): T[] {
  const gone = new Set(done.map((d) => `${d.subject}\u0000${d.term}\u0000${d.relation}`))
  return list.filter((s) => !gone.has(`${s.subject}\u0000${s.term}\u0000${s.relation}`))
}
