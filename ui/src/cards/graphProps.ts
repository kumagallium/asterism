import type { GraphNode } from './viewSpec'

/** GraphView が節の下に出す「見出し: 値」の行。K4（生の識別子を見せない）
 *  のため、出すのは人向けの `type_label`（見出し「種類」）と `snapshot`
 *  （見出し「版」）だけ — 生の `type`（クラス IRI）や `dataset_id` は
 *  出さない。値の無い行は出さない。 */
export function nodePropLines(
  props: GraphNode['props'],
  t: (key: string) => string,
): string[] {
  if (!props) return []
  const lines: string[] = []
  if (props.type_label) {
    lines.push(`${t('cards:graph.prop_type')}: ${props.type_label}`)
  }
  if (props.snapshot) {
    lines.push(`${t('cards:graph.prop_snapshot')}: ${props.snapshot}`)
  }
  return lines
}
