// 問いのタブの純粋な計算（PR 11l）。
import type { SkyData, SkyQuestion } from '../sky/skyApi'

/** 要る用語 → 結ばれたデータセット名（重複なし）。全部の用語に結ばれたものがあればそれだけ、無ければ和集合。 */
export function datasetNamesOf(q: SkyQuestion, sky: SkyData): string[] {
  const label = new Map(sky.datasets.map((d) => [d.id, d.label]))
  const sets = q.needs.map((iri) => new Set((sky.terms.find((x) => x.iri === iri)?.members ?? []).map((m) => m.dataset_id)))
  if (sets.length === 0) return []
  const all = [...new Set(sets.flatMap((s) => [...s]))]
  const both = all.filter((id) => sets.every((s) => s.has(id)))
  return (both.length > 0 ? both : all).map((id) => label.get(id)).filter((x): x is string => !!x)
}
