// 公開前の列・種類（まだ store に無い）の帰結。api の試算は「store にあるデータ」を数えるので、
// 公開前のものは数えられない。かわりに、結ぶ先の用語の問いと、今それに答えるデータセットの数
// （GET /api/vocab/shared）を読み、公開すると 1 つ増える見込みを言う（decide.effectAfterPublish）。
import { type SharedTerm, listTerms } from '../vocabApi'

/** 問い（題・今答えるデータセット数）。 */
export interface PublishQuestion {
  title: string
  answering_datasets: number
}

// 用語の一覧は、行ごとに何度も引かない（短い間は 1 回）。語を足して戻ってきたとき古いままに
// ならないよう、少しで捨てる。
const TTL_MS = 30_000
let cache: { at: number; value: Promise<SharedTerm[]> } | null = null
function terms(): Promise<SharedTerm[]> {
  const now = Date.now()
  if (!cache || now - cache.at > TTL_MS) cache = { at: now, value: listTerms().catch(() => []) }
  return cache.value
}

/** 用語 `termIri` に掛かる問い。共有の用語でなければ（標準・他データの種類）空。 */
export async function questionsOfTerm(termIri: string): Promise<PublishQuestion[]> {
  const term = (await terms()).find((x) => x.iri === termIri)
  return (term?.cqs ?? []).map((c) => ({ title: c.title, answering_datasets: c.answering_datasets }))
}

/** 読み直したいとき（語を作った・関係を決めたあと）に捨てる。 */
export function resetPublishEffectCache(): void {
  cache = null
}
