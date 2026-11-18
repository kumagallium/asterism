// 手で決める答え方の一覧（HandForm の選択肢）。
/** 答え方の 18 の方法（AI 無しで決められる形）。`id` は `op` か `op:direction`。 */
export const WAYS = [
  { id: 'factors:lowers', mode: 'knowledge', op: 'factors', direction: 'lowers', label: 'answer.option.lowers', need: 'any' },
  { id: 'factors:raises', mode: 'knowledge', op: 'factors', direction: 'raises', label: 'answer.option.raises', need: 'any' },
  { id: 'factors:affects', mode: 'knowledge', op: 'factors', direction: 'affects', label: 'answer.option.affects', need: 'any' },
  { id: 'parts', mode: 'knowledge', op: 'parts', direction: '', label: 'answer.option.parts', need: 'any' },
  { id: 'kinds', mode: 'knowledge', op: 'kinds', direction: '', label: 'answer.option.kinds', need: 'any' },
  { id: 'makers', mode: 'knowledge', op: 'makers', direction: '', label: 'answer.option.makers', need: 'class' },
  { id: 'products', mode: 'knowledge', op: 'products', direction: '', label: 'answer.option.products', need: 'class' },
  { id: 'measurers', mode: 'knowledge', op: 'measurers', direction: '', label: 'answer.option.measurers', need: 'any' },
  { id: 'measured', mode: 'knowledge', op: 'measured', direction: '', label: 'answer.option.measured', need: 'class' },
  { id: 'sources', mode: 'knowledge', op: 'sources', direction: '', label: 'answer.option.sources', need: 'class' },
  { id: 'outcomes', mode: 'knowledge', op: 'outcomes', direction: '', label: 'answer.option.outcomes', need: 'class' },
  { id: 'competitors', mode: 'knowledge', op: 'competitors', direction: '', label: 'answer.option.competitors', need: 'class' },
  { id: 'properties', mode: 'knowledge', op: 'properties', direction: '', label: 'answer.option.properties', need: 'class' },
  { id: 'bearers', mode: 'knowledge', op: 'bearers', direction: '', label: 'answer.option.bearers', need: 'property' },
  { id: 'missing', mode: 'knowledge', op: 'missing', direction: '', label: 'answer.option.missing', need: 'class' },
  { id: 'count', mode: 'data', op: 'count', direction: '', label: 'answer.option.count', need: 'class' },
  { id: 'range', mode: 'data', op: 'range', direction: '', label: 'answer.option.range', need: 'property' },
  { id: 'top', mode: 'data', op: 'top', direction: '', label: 'answer.option.top', need: 'property' },
] as const

/** その方法で対象に選べる概念（need が any なら全部）。 */
export function targetsFor<C extends { kind: string }>(concepts: C[], need: string): C[] {
  return concepts.filter((c) => need === 'any' || c.kind === need)
}
