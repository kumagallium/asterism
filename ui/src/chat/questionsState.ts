// 問いのタブの純粋な計算（PR 11l）。
import type { SkyData, SkyQuestion } from '../sky/skyApi'

/** 要る用語 → 結ばれたデータセット名（重複なし）。`union`＝全部の用語に結ばれたものが無く、和集合に落ちた。 */
export function datasetNamesOf(q: SkyQuestion, sky: SkyData): { names: string[]; union: boolean } {
  const label = new Map(sky.datasets.map((d) => [d.id, d.label]))
  const sets = q.needs.map((iri) => new Set((sky.terms.find((x) => x.iri === iri)?.members ?? []).map((m) => m.dataset_id)))
  if (sets.length === 0) return { names: [], union: false }
  const all = [...new Set(sets.flatMap((s) => [...s]))]
  const both = all.filter((id) => sets.every((s) => s.has(id)))
  const union = both.length === 0
  const names = (union ? all : both).map((id) => label.get(id)).filter((x): x is string => !!x)
  return { names: [...new Set(names)], union }
}

/** 問いの文の同一判定: 前後の空白と全角・半角の「？」の差を無視する。 */
export function normalizeQuestion(s: string): string {
  return s.trim().replace(/[？?]+$/, '').trim()
}
