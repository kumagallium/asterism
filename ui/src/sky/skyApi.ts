// 星図（ホーム）の読み取り。`GET /api/sky`（契約 pr2-contract.md §A・§B、実装は
// `asterism.sky_view`）。公開済みのものだけが来る。draft は含まれない。
import { throwApiError } from '../api'

/** 種類（星）。`links` は同じデータセットの中の別の種類（星座の線）。 */
export interface SkyKind {
  iri: string
  label: string
  count: number
  links: string[]
  /** 種類が標準用語をそのまま使っているときの標準用語の IRI。 */
  standard: string | null
}

export interface SkyDataset {
  id: string
  label: string
  created_at: string
  kinds: SkyKind[]
}

export type SkyRelation = 'subClassOf' | 'equivalentClass' | 'subPropertyOf' | 'equivalentProperty'

export interface SkyTermMember {
  iri: string
  dataset_id: string
  relation: SkyRelation
}

export interface SkyTerm {
  iri: string
  slug: string
  label: string
  label_en: string
  kind: 'class' | 'property'
  members: SkyTermMember[]
  standard: { iri: string; relation: string } | null
  /** この用語を要る問いに答えられるデータセットの数。 */
  answering: number
  /** この用語を要る問いの tool_name。 */
  questions: string[]
}

export interface SkyStandard {
  iri: string
  label: string
  prefix: string
}

export interface SkyHub {
  id: string
  label: string
  /** 参加している種類の IRI。 */
  members: string[]
}

export interface SkyQuestion {
  tool_name: string
  title: string
  theme_id: string | null
  theme_label: string
  answering: number
  total_datasets: number
  /** 要る用語の IRI。 */
  needs: string[]
  shape: string
}

export interface SkyFacts {
  triples: number
  datasets: number
  kinds: number
  terms: number
  questions_answerable: number
  questions_total: number
  published_graphs: number
}

export interface SkyRecent {
  at: string
  kind: 'publish' | 'term' | 'decision'
  text: string
}

export interface SkyData {
  datasets: SkyDataset[]
  terms: SkyTerm[]
  standards: SkyStandard[]
  hubs: SkyHub[]
  questions: SkyQuestion[]
  facts: SkyFacts
  recent: SkyRecent[]
  truncated: boolean
}

export const EMPTY_SKY: SkyData = {
  datasets: [],
  terms: [],
  standards: [],
  hubs: [],
  questions: [],
  facts: {
    triples: 0,
    datasets: 0,
    kinds: 0,
    terms: 0,
    questions_answerable: 0,
    questions_total: 0,
    published_graphs: 0,
  },
  recent: [],
  truncated: false,
}

export async function getSky(): Promise<SkyData> {
  const res = await fetch('/api/sky')
  if (!res.ok) await throwApiError(res, 'sky')
  const body = (await res.json()) as Partial<SkyData>
  // 古いサーバ・欠けた欄に強く: 無い欄は空にする。
  return {
    ...EMPTY_SKY,
    ...body,
    facts: { ...EMPTY_SKY.facts, ...(body.facts ?? {}) },
  }
}
