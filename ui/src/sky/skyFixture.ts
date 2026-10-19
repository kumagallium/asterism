// 星図の試験に使う作り物（架空の分野）。種つき（`hash01` と同じ決定論）で、時刻・乱数は使わない。
// テストのファイルから import すると、そのファイルのテストがもう一度走るので、ここに置く。
import type { SkyData, SkyDataset, SkyHub, SkyKind, SkyStandard, SkyTerm } from './skyApi'
import { hash01 } from './skyLayout'

const NS = 'https://example.org/sky#'
const pad = (n: number, w = 3): string => String(n).padStart(w, '0')

export interface FixtureSize {
  datasets: number
  kinds: number
}
/** 規模の 4 段（契約 pr2-contract.md §F）。 */
export const SCALES: Record<'S' | 'M' | 'L' | 'XL', FixtureSize> = {
  S: { datasets: 3, kinds: 10 },
  M: { datasets: 8, kinds: 40 },
  L: { datasets: 20, kinds: 100 },
  XL: { datasets: 50, kinds: 250 },
}

/**
 * データセット `datasets` 個・種類が全部で `kinds` 個の作り物。
 * 用語は種類の 1/3、標準用語は用語の 1/6、ハブは データセットの 1/4（最低 1）。
 */
export function makeSky(datasets: number, kinds: number, salt = 'sky'): SkyData {
  const dss: SkyDataset[] = []
  const termMembers = new Map<number, { dataset_id: string; iri: string }[]>()
  const nTerms = Math.max(1, Math.round(kinds / 3))
  const nStd = Math.max(1, Math.round(nTerms / 6))
  for (let d = 0; d < datasets; d++) {
    const id = `d${pad(d)}`
    const n = Math.floor(kinds / datasets) + (d < kinds % datasets ? 1 : 0)
    const ks: SkyKind[] = []
    for (let k = 0; k < n; k++) {
      const iri = `${NS}${id}/K${pad(k)}`
      const links: string[] = []
      if (k > 0) links.push(`${NS}${id}/K${pad(k - 1)}`)
      if (k > 3 && hash01(iri, salt + 'l') < 0.4) links.push(`${NS}${id}/K${pad(Math.floor(hash01(iri, salt + 'm') * k))}`)
      const std = hash01(iri, salt + 's') < 0.15 ? `${NS}std/S${pad(Math.floor(hash01(iri, salt + 'n') * nStd), 2)}` : null
      ks.push({
        iri,
        // 長さを散らす（星のラベルの重なりを試す）
        label: `種類${pad(d)}-${pad(k)}${'あ'.repeat(Math.floor(hash01(iri, salt + 'w') * 4))}`,
        count: Math.round(10 ** (hash01(iri, salt + 'c') * 5)),
        links,
        standard: std,
      })
      // 用語に入れる（3 割の種類は用語なし）
      if (hash01(iri, salt + 't') < 0.7) {
        const t = Math.floor(hash01(iri, salt + 'u') * nTerms)
        termMembers.set(t, [...(termMembers.get(t) ?? []), { dataset_id: id, iri }])
      }
    }
    dss.push({
      id,
      label: `データセット${pad(d)}`,
      created_at: `2026-01-${pad((d % 28) + 1, 2)}T00:${pad(Math.floor(d / 28), 2)}:00Z`,
      kinds: ks,
    })
  }
  const stds: SkyStandard[] = Array.from({ length: nStd }, (_, i) => ({
    iri: `${NS}std/S${pad(i, 2)}`,
    label: `Std${pad(i, 2)}`,
    prefix: 'ex',
  }))
  const terms: SkyTerm[] = Array.from({ length: nTerms }, (_, t) => {
    const prop = hash01(`t${t}`, salt + 'p') < 0.25
    const ms = termMembers.get(t) ?? []
    const std = hash01(`t${t}`, salt + 'q') < 0.25 ? stds[Math.floor(hash01(`t${t}`, salt + 'r') * nStd)] : null
    return {
      iri: `${NS}term/T${pad(t)}`,
      slug: `term-${pad(t)}`,
      label: `用語${pad(t)}${'い'.repeat(Math.floor(hash01(`t${t}`, salt + 'x') * 5))}`,
      label_en: `Term ${pad(t)}`,
      kind: prop ? 'property' : 'class',
      members: ms.map((m) => ({
        iri: m.iri,
        dataset_id: m.dataset_id,
        relation: hash01(m.iri, salt + 'e') < 0.2 ? (prop ? 'equivalentProperty' : 'equivalentClass') : prop ? 'subPropertyOf' : 'subClassOf',
      })),
      standard: std ? { iri: std.iri, relation: 'equivalentClass' } : null,
      answering: ms.length > 0 ? 1 : 0,
      questions: [],
    } as SkyTerm
  })
  const nHubs = Math.max(1, Math.round(datasets / 4))
  const hubs: SkyHub[] = Array.from({ length: nHubs }, (_, h) => {
    const members: string[] = []
    for (let j = 0; j < 3; j++) {
      const d = dss[Math.floor(hash01(`h${h}${j}`, salt + 'h') * dss.length)]
      if (d && d.kinds.length) members.push(d.kinds[Math.floor(hash01(`h${h}${j}`, salt + 'k') * d.kinds.length)].iri)
    }
    return { id: `hub${pad(h, 2)}`, label: `つながり${pad(h, 2)}`, members: [...new Set(members)] }
  })
  return {
    datasets: dss,
    terms,
    standards: stds,
    hubs,
    questions: [],
    facts: {
      triples: 0,
      datasets,
      kinds,
      terms: nTerms,
      questions_answerable: 0,
      questions_total: 0,
      published_graphs: datasets,
    },
    recent: [],
    truncated: false,
  }
}
