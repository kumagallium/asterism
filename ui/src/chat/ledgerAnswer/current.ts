// 保存された答えの「未確認の鍵」を、今の台帳で数え直す（話を開き直したとき・ほかで確認したとき）。
import { fetchLedger, KnowledgeApiError, type ImportSource, type LedgerRow } from '../graphPlace/knowledgeRelationsApi'
import { relKey, type LedgerAnswer } from './types'

type Row = Pick<LedgerRow, 'key' | 'confirmed' | 'rejected' | 'state' | 'rel'> & { from: { id: string }; to: { id: string } }
/** 取り込み元 → 今の行。読めなかったら null・外されていたら（404）'removed'。 */
export type RowsBySource = ReadonlyMap<string, readonly Row[] | null | 'removed'>

export interface CurrentUse {
  /** 今も未確認の鍵（読めなかった取り込み元の鍵は、保存された数のまま入る）。 */
  pending: LedgerAnswer['unconfirmed']
  /** もう確認済みの鍵。 */
  resolved: ReadonlySet<string>
  /** 後で「違う」とされた鍵。 */
  rejected: ReadonlySet<string>
  /** 今の台帳に無い鍵（取り込み直しで変わった）。 */
  gone: ReadonlySet<string>
  /** 取り込み元ごと外されている鍵。 */
  removed: ReadonlySet<string>
}

/** 取り込み元の行の鍵 `o:<from>|<rel>|<to>` を分ける。違う形は null。 */
export function parseOriginKey(key: string): { from: string; rel: string; to: string } | null {
  if (!key.startsWith('o:')) return null
  const p = key.slice(2).split('|')
  return p.length === 3 && p.every((x) => x !== '') ? { from: p[0], rel: p[1], to: p[2] } : null
}

/** 純関数: 保存された `unconfirmed` ＋ 今の行 → 今も未確認／確認済み／「違う」／消えた／外された。
 *  取り込み元の行（`o:` の鍵）は、確認すると一覧から消え、同じ 2 語の組の件が確認済みになる。
 *  同じ関係・同じ向きで確認済みの件があれば確認済みと数える。 */
export function currentUse(unconfirmed: LedgerAnswer['unconfirmed'], rows: RowsBySource | null): CurrentUse {
  const out = { resolved: new Set<string>(), rejected: new Set<string>(), gone: new Set<string>(), removed: new Set<string>() }
  const pending: { source: string; key: string }[] = []
  const index = new Map<string, Map<string, Row>>()
  for (const u of unconfirmed) {
    const list = rows?.get(u.source)
    const id = relKey(u.source, u.key)
    if (list === 'removed') {
      out.removed.add(id)
      continue
    }
    if (!list) {
      pending.push(u) // 読めなかった・まだ読んでいない: 保存されたまま
      continue
    }
    let m = index.get(u.source)
    if (!m) {
      m = new Map()
      for (const r of list) if (r.key) m.set(r.key, r)
      index.set(u.source, m)
    }
    const r = m.get(u.key)
    if (r) {
      if (r.rejected) out.rejected.add(id)
      else if (r.confirmed) out.resolved.add(id)
      else pending.push(u)
      continue
    }
    const o = parseOriginKey(u.key)
    if (o && list.some((x) => x.state === 'relation' && x.confirmed && !x.rejected && x.rel === o.rel && x.from.id === o.from && x.to.id === o.to)) {
      out.resolved.add(id)
    } else out.gone.add(id)
  }
  return { ...out, pending }
}

export interface Band {
  /** 今も未確認で、確認ボタンで確認できる数。 */
  remaining: number
  /** 確認済み（この画面で確認した分も含む）。 */
  confirmed: number
  rejected: number
  gone: number
  removed: number
  /** 確認ボタンでは確認できない数。 */
  conflict: number
}

/** 純関数: 帯に出す数。`done` はこの画面で確認が通った鍵。 */
export function bandOf(ledger: Pick<LedgerAnswer, 'unconfirmed' | 'conflict_count'>, cur: CurrentUse, done: ReadonlySet<string>): Band {
  const remaining = cur.pending.filter((u) => !done.has(relKey(u.source, u.key))).length
  const confirmed = new Set([...done, ...cur.resolved]).size
  return { remaining, confirmed, rejected: cur.rejected.size, gone: cur.gone.size, removed: cur.removed.size, conflict: ledger.conflict_count }
}

// ── 読み（取り込み元ごとに 1 回・短い間キャッシュ） ──

const TTL_MS = 2000
const cache = new Map<string, { at: number; p: Promise<LedgerRow[] | null | 'removed'> }>()

/** 確認が通ったあとなど、すぐ読み直したいとき。 */
export function clearLedgerRowsCache(): void {
  cache.clear()
}

/** 取り込み元ごとの今の行。読めなかった取り込み元は null（描画は止めない）・外されていたら 'removed'。 */
export async function readCurrentRows(sources: string[], lang: 'ja' | 'en'): Promise<Map<string, LedgerRow[] | null | 'removed'>> {
  const out = new Map<string, LedgerRow[] | null | 'removed'>()
  await Promise.all(
    [...new Set(sources)].map(async (s) => {
      const hit = cache.get(s)
      let p = hit && Date.now() - hit.at < TTL_MS ? hit.p : null
      if (!p) {
        p = fetchLedger({ source: s } as ImportSource, lang)
          .then((l): LedgerRow[] | null | 'removed' => l.rows)
          .catch((e): null | 'removed' => (e instanceof KnowledgeApiError && e.status === 404 ? 'removed' : null))
        cache.set(s, { at: Date.now(), p })
      }
      out.set(s, await p)
    }),
  )
  return out
}
