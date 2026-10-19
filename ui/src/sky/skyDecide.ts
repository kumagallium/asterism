// 星図の右レールで「ひとこと決める」を出すための純関数。描画は SkyRail、並びは decide.orderCandidates。
import { orderCandidates } from '../decide/decide'
import type { PickEnd } from '../lineChoice'
import type { SkyTerm } from './skyApi'
import type { SkyNode } from './skyLayout'

/** 選んだ点が種類（星）なら、決める側（主語）。星以外（丸・用語・標準・つながり）は null。 */
export function decideSubjectOf(node: SkyNode | null | undefined): PickEnd | null {
  if (!node || node.type !== 'kind') return null
  return { id: node.iri, iri: node.iri, label: node.label, role: 'dataset', termKind: 'class' }
}

/** 行き先の候補（用語のうち種類のもの）。主語を渡すと、名前が完全一致するものを先頭に並べる。 */
export function decideCandidatesOf(
  terms: readonly SkyTerm[],
  subject?: PickEnd | null,
  lang: 'ja' | 'en' = 'ja',
): PickEnd[] {
  const ends = terms
    .filter((t) => t.kind === 'class')
    .map<PickEnd>((t) => ({
      id: t.iri,
      iri: t.iri,
      label: lang === 'en' && t.label_en ? t.label_en : t.label,
      role: 'shared',
      termKind: 'class',
    }))
  return subject ? orderCandidates(subject, ends) : ends
}
