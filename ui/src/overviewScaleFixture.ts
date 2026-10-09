// 全体図の規模の試験に使う作り物（架空の分野）。テストのファイルどうしで共有する
// （テストのファイルから import すると、そのファイルのテストがもう一度走るため、ここに置く）。
import type { CrosswalkPerspective } from './crosswalkApi'
import type { DatasetRules, RuleMap } from './galleryApi'
import type { OverviewInput } from './kindOverview'

// 架空の分野の作り物。規模の試験にも同じ作り方を使う。
export const NS = 'https://example.org/scale#'
const rmap = (id: string): RuleMap => ({
  id,
  subject: { template: `o:${id}/{k}`, classes: [`o:${id}`], class_iris: [`${NS}${id}`] },
  properties: [],
})
export const mkDs = (i: number, kinds: number) => ({
  id: `live-d${i}`,
  apiId: `d${i}`,
  name: `データセット${i}`,
  rules: {
    maps: Array.from({ length: kinds }, (_, k) => rmap(`d${i}K${k}`)),
    prefixes: {},
    warnings: [],
    labels: {},
  } as DatasetRules,
})

/** 種つきの擬似乱数（時刻・Math.random は使わない）。 */
function lcg(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0
    return s / 2 ** 32
  }
}

export function makeScale(nDs: number, nKinds: number, nHubs: number, perHub: number): OverviewInput {
  const rnd = lcg(42)
  const datasets = Array.from({ length: nDs }, (_, i) => mkDs(i, nKinds))
  const counts: Record<string, Record<string, number>> = {}
  for (const d of datasets) {
    counts[d.id] = {}
    for (const m of d.rules.maps) counts[d.id][m.subject.class_iris![0]] = Math.round(10 ** (1 + rnd() * 4))
  }
  const crosswalks: CrosswalkPerspective[] = Array.from({ length: nHubs }, (_, h) => {
    const picked = new Set<number>()
    while (picked.size < Math.min(perHub, nDs)) picked.add(Math.floor(rnd() * nDs))
    return {
      perspective_id: `p${h}`,
      display_name: `つながり${String(h).padStart(2, '0')}`,
      config: {
        min_datasets: 2,
        concepts: [
          {
            name: `c${h}`,
            concept_label: `概念${String(h).padStart(2, '0')}`,
            class_iri: `${NS}Hub${h}`,
            participants: [...picked].map((i) => ({
              dataset_id: `d${i}`,
              subject_class: `${NS}d${i}K${Math.floor(rnd() * nKinds)}`,
              label: 'x',
            })),
          },
        ],
      },
      dataset: null,
    } as unknown as CrosswalkPerspective
  })
  return { datasets, classCountsByDataset: counts, crosswalks, unnamedHub: '名前なし' }
}
