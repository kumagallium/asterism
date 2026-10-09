// 全体グラフ（値でつなぐ網）の読み取り。`GET /api/network`（契約メモ contract_network_view.md §2）。
import { throwApiError } from './api'
import type { SetSpec } from './cards/cardsApi'

export type NetworkNodeKind = 'entity' | 'value' | 'bundle' | 'hub'

export interface NetworkNode {
  id: string
  kind: NetworkNodeKind
  label: string
  class_iri: string | null
  class_label: string | null
  /** 色の鍵。上位構造（`asterism.shared_vocab.upper_map`）があれば最上位の種類、無ければ自分の種類。
   *  古いサーバは返さないので省略できる（そのときは class_iri で塗る）。 */
  group_iri?: string | null
  dataset_id: string | null
  /** 束なら中の件数。それ以外は 1。 */
  count: number
  degree: number
  /** 値の点・束の「一覧で開く」。サーバが完成形で返す。件・ハブは null。 */
  set_spec: SetSpec | null
}

export interface NetworkEdge {
  source: string
  target: string
  label: string
}

export interface NetworkKind {
  class_iri: string
  class_label: string | null
  count: number
  /** この種類の件が属するデータセット（古いサーバは返さないので省略できる）。 */
  dataset_ids?: string[]
}

export interface NetworkDataset {
  id: string
  label: string
}

export interface NetworkResponse {
  nodes: NetworkNode[]
  edges: NetworkEdge[]
  kinds: NetworkKind[]
  /** 点・種類に出てくるデータセットの名前（古いサーバは返さないので省略できる）。 */
  datasets?: NetworkDataset[]
  stats: {
    entities: number
    nodes: number
    edges: number
    values: number
    bundles: number
    /** 公開済みの graph の数。古いサーバは返さないので省略できる。 */
    published_graphs?: number
  }
  truncated: boolean
}

export async function getNetwork(includeProv: boolean): Promise<NetworkResponse> {
  const res = await fetch(`/api/network?include_prov=${includeProv ? 'true' : 'false'}`)
  if (!res.ok) await throwApiError(res, 'network')
  return (await res.json()) as NetworkResponse
}
