// 「静かな表示」の表: 星図と同じ内容を文字で出す（読み上げ・コピー用）。純関数。
// 図は配置（座標）を持つが、表は `SkyData` だけから作る。図の点と 1 対 1 になることは
// skyTable.test.ts で固定する。
import type { SkyData, SkyKnowledgeRelation } from './skyApi'
import { termLabel } from './skyLayout'

export interface StarRow {
  dataset: string
  kind: string
  iri: string
  count: number
  terms: string[]
  standard: string | null
}
export interface TermRow {
  iri: string
  label: string
  type: 'class' | 'property'
  kinds: string[]
  standard: string | null
  questions: TableQuestion[]
}
/** 問い 1 つ。`knowledge` は知識の線の数（データの問いは 0）。 */
export interface TableQuestion {
  title: string
  mode: 'data' | 'knowledge'
  knowledge: number
}
/** 語どうしの知識の線（図の線と 1 対 1）。 */
export interface LineRow {
  from: string
  to: string
  relation: SkyKnowledgeRelation
  reason: string | null
}
export interface StandardRow {
  iri: string
  label: string
  bound: string[]
}
export interface HubRow {
  label: string
  members: string[]
}
export interface SkyTables {
  stars: StarRow[]
  terms: TermRow[]
  standards: StandardRow[]
  hubs: HubRow[]
  lines: LineRow[]
}

const stdName = (s: { prefix: string; label: string }): string => `${s.prefix}:${s.label}`

export function buildSkyTables(sky: SkyData, lang: 'ja' | 'en' = 'ja'): SkyTables {
  const stdByIri = new Map(sky.standards.map((s) => [s.iri, s]))
  const dsLabel = new Map(sky.datasets.map((d) => [d.id, d.label]))
  const kindLabel = (dsId: string, iri: string): string =>
    sky.datasets.find((d) => d.id === dsId)?.kinds.find((k) => k.iri === iri)?.label ?? iri

  // 種類（データセット × IRI）→ 入っている用語
  const termsOf = new Map<string, string[]>()
  for (const t of sky.terms) {
    for (const m of t.members) {
      const key = `${m.dataset_id}\u0001${m.iri}`
      const arr = termsOf.get(key) ?? []
      const name = termLabel(t, lang)
      if (!arr.includes(name)) arr.push(name)
      termsOf.set(key, arr)
    }
  }
  const stars: StarRow[] = []
  for (const d of sky.datasets) {
    for (const k of d.kinds) {
      const std = k.standard ? stdByIri.get(k.standard) : undefined
      stars.push({
        dataset: d.label,
        kind: k.label,
        iri: k.iri,
        count: k.count,
        terms: termsOf.get(`${d.id}\u0001${k.iri}`) ?? [],
        standard: std ? stdName(std) : null,
      })
    }
  }

  const qByName = new Map(sky.questions.map((q) => [q.tool_name, q]))
  const terms: TermRow[] = sky.terms.map((t) => {
    const std = t.standard ? stdByIri.get(t.standard.iri) : undefined
    const kinds: string[] = []
    for (const m of t.members) {
      const name = `${kindLabel(m.dataset_id, m.iri)}（${dsLabel.get(m.dataset_id) ?? m.dataset_id}）`
      if (!kinds.includes(name)) kinds.push(name)
    }
    return {
      iri: t.iri,
      label: termLabel(t, lang),
      type: t.kind,
      kinds,
      standard: std ? stdName(std) : null,
      questions: t.questions.map((name) => {
        const q = qByName.get(name)
        return { title: q?.title ?? name, mode: q?.mode ?? 'data', knowledge: q?.knowledge ?? 0 }
      }),
    }
  })

  const standards: StandardRow[] = sky.standards.map((s) => {
    const bound: string[] = []
    for (const t of sky.terms) if (t.standard?.iri === s.iri) bound.push(termLabel(t, lang))
    for (const d of sky.datasets) for (const k of d.kinds) if (k.standard === s.iri) bound.push(`${k.label}（${d.label}）`)
    return { iri: s.iri, label: stdName(s), bound }
  })

  const hubs: HubRow[] = sky.hubs.map((h) => ({
    label: h.label,
    members: h.members.map((iri) => {
      for (const d of sky.datasets) {
        const k = d.kinds.find((x) => x.iri === iri)
        if (k) return `${k.label}（${d.label}）`
      }
      return iri
    }),
  }))

  const labelOf = new Map(sky.terms.map((t) => [t.iri, termLabel(t, lang)]))
  const lines: LineRow[] = (sky.edges ?? [])
    .filter((e) => labelOf.has(e.from) && labelOf.has(e.to))
    .map((e) => ({
      from: labelOf.get(e.from) as string,
      to: labelOf.get(e.to) as string,
      relation: e.relation,
      reason: e.reason,
    }))

  return { stars, terms, standards, hubs, lines }
}
