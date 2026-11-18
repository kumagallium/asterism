// 「知識グラフ」の場所の大きい図: データセットの点（データセット・データの種類・視点）を出すか（PR 14e-1）。
// 語と関係を見る場所なので、語か関係が 1 つでも図にあるときは既定でオフ。1 つも無いときはオン（取り込みをしていない利用者の図を変えない）。
import type { DraftSky, SkyData } from '../../sky/skyApi'

/** 図に語か関係があるか（ストアの用語・知識の線、または台帳の下書きの語）。 */
export function hasWords(sky: Pick<SkyData, 'terms' | 'edges'>, ledgerDrafts: readonly DraftSky[]): boolean {
  return sky.terms.length > 0 || (sky.edges?.length ?? 0) > 0 || ledgerDrafts.some((d) => d.concepts.length > 0 || d.relations.length > 0)
}

/** 出すか。利用者の選択（`pref`）があればそれ、無ければ既定（語か関係があるならオフ）。 */
export const datasetsShown = (pref: boolean | null, words: boolean): boolean => pref ?? !words

/** 点の id がデータセット側の点か（データセット `d|`・データの種類 `k|`・視点 `h|`）。隠している間にこれが選ばれたら自動でオンにする。 */
export function isDatasetNode(nodeId: string | null): boolean {
  const head = nodeId?.split('|')[0]
  return head === 'd' || head === 'k' || head === 'h'
}

/** データセット側の点を図の元から除く（用語・台帳の語と線は残す）。 */
export function withoutDatasetSide<S extends Pick<SkyData, 'datasets' | 'hubs'>>(sky: S): S {
  return { ...sky, datasets: [], hubs: [] }
}
