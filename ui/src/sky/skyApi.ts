// 星図（ホーム）の読み取り。`GET /api/sky`（契約 pr2-contract.md §A・§B、実装は
// `asterism.sky_view`）。公開済みのものだけが来る。draft は含まれない。
import { throwApiError } from '../api'
import { authHeaders } from '../authToken'

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

/** 語どうしの知識の線の関係（一部・下げる・上げる・変える）。 */
export type SkyKnowledgeRelation = 'isPartOf' | 'lowers' | 'raises' | 'affects'

/** 語（用語）どうしの知識の線。`from` が `to` を下げる／上げる／変える、`from` は `to` の一部。 */
export interface SkyLine {
  from: string
  to: string
  relation: SkyKnowledgeRelation
  /** 確定したときの理由 1 行（無ければ null）。 */
  reason: string | null
}

export interface SkyQuestion {
  tool_name: string
  title: string
  /** `data`＝データで答える問い。`knowledge`＝確定した知識の線で答える問い（`kq_…`）。 */
  mode?: 'data' | 'knowledge'
  theme_id: string | null
  theme_label: string
  /** 答えるデータセットの数（輪はこの数）。 */
  answering: number
  /** 対象の語の確定した知識の線の数（データの問いは 0）。 */
  knowledge?: number
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
  /** 主たる名前（データセット名・用語・決めたことの左側）。 */
  a: string
  /** 決めたことの相手の名前（それ以外は空）。 */
  b: string
  /** 決めたことの関係（equivalentClass など。それ以外は空）。 */
  relation: string
}

export interface SkyData {
  datasets: SkyDataset[]
  terms: SkyTerm[]
  standards: SkyStandard[]
  hubs: SkyHub[]
  questions: SkyQuestion[]
  /** 語どうしの知識の線（古いサーバでは無い）。 */
  edges?: SkyLine[]
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
  edges: [],
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

/** 問いが答えられるか = データ ≥ 1 または 知識 ≥ 1（`sky_view` と同じ定義）。 */
export function isAnswerable(q: Pick<SkyQuestion, 'answering' | 'knowledge'>): boolean {
  return q.answering >= 1 || (q.knowledge ?? 0) >= 1
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

/** 下書きの札 1 つ分の概念（`GET /api/themes/drafts/sky`）。`use` は `new` / `existing:<slug>` / `standard:<curie>`。 */
export interface DraftSkyConcept {
  key: string
  label: string
  kind: 'class' | 'property'
  use: string
}

/** まだ確かめていない下書き 1 件（テーマ）。星図の上の帯に、枠だけの札＋「下書き」の印で描く。 */
export interface DraftSky {
  draft_id: string
  theme_label: string
  concepts: DraftSkyConcept[]
  relations: { from: string; to: string; rel: string }[]
  questions: string[]
}

/**
 * 下書きの札（認証つきの別口）。`GET /api/sky` は公開済みだけのまま、下書きは書き込み用の鍵を持つ
 * 人にだけ返る。鍵が無い・読めない・古いサーバのときは空（札を出さないだけ — 星図は止めない）。
 */
export async function fetchDraftSky(lang: 'ja' | 'en'): Promise<DraftSky[]> {
  try {
    const res = await fetch(`/api/themes/drafts/sky?lang=${lang}`, { headers: authHeaders() })
    if (!res.ok) return []
    const body = (await res.json()) as { drafts?: DraftSky[] }
    return (body.drafts ?? []).filter(
      (d) => d && typeof d.draft_id === 'string' && Array.isArray(d.concepts),
    )
  } catch {
    return []
  }
}
