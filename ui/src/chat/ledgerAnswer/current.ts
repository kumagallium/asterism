// 保存された答えの「未確認の鍵」を、今の台帳で数え直す（話を開き直したとき、確認し終えた答えに帯が出ないように）。
import { fetchLedger, type ImportSource, type LedgerRow } from '../graphPlace/knowledgeRelationsApi'
import { relKey, type LedgerAnswer } from './types'

/** 取り込み元 → 今の行（読めなかったら null）。 */
type Row = Pick<LedgerRow, 'key' | 'confirmed' | 'rejected'>
export type RowsBySource = ReadonlyMap<string, readonly Row[] | null>

export interface CurrentUse {
  /** 今も未確認の鍵（読めなかった取り込み元の鍵は、保存された数のまま入る）。 */
  pending: LedgerAnswer['unconfirmed']
  /** もう確認済みの鍵。 */
  resolved: ReadonlySet<string>
  /** 後で「違う」とされた鍵。 */
  rejected: ReadonlySet<string>
  /** 台帳から消えた鍵。 */
  gone: ReadonlySet<string>
}

/** 純関数: 保存された `unconfirmed` ＋ 今の行 → 今も未確認／確認済み／「違う」／消えた。 */
export function currentUse(unconfirmed: LedgerAnswer['unconfirmed'], rows: RowsBySource | null): CurrentUse {
  const out = { pending: [] as LedgerAnswer['unconfirmed'], resolved: new Set<string>(), rejected: new Set<string>(), gone: new Set<string>() }
  const pending: { source: string; key: string }[] = []
  const index = new Map<string, Map<string, Row>>()
  for (const u of unconfirmed) {
    const list = rows?.get(u.source)
    const id = relKey(u.source, u.key)
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
    if (!r) out.gone.add(id)
    else if (r.rejected) out.rejected.add(id)
    else if (r.confirmed) out.resolved.add(id)
    else pending.push(u)
  }
  return { ...out, pending }
}

// ── 読み（取り込み元ごとに 1 回・短い間キャッシュ） ──

const TTL_MS = 5000
const cache = new Map<string, { at: number; p: Promise<LedgerRow[] | null> }>()

/** 確認が通ったあとなど、すぐ読み直したいとき。 */
export function clearLedgerRowsCache(): void {
  cache.clear()
}

/** 取り込み元ごとの今の行。読めなかった取り込み元は null（描画は止めない）。 */
export async function readCurrentRows(sources: string[], lang: 'ja' | 'en'): Promise<Map<string, LedgerRow[] | null>> {
  const out = new Map<string, LedgerRow[] | null>()
  await Promise.all(
    [...new Set(sources)].map(async (s) => {
      const hit = cache.get(s)
      let p = hit && Date.now() - hit.at < TTL_MS ? hit.p : null
      if (!p) {
        p = fetchLedger({ source: s } as ImportSource, lang)
          .then((l) => l.rows)
          .catch(() => null)
        cache.set(s, { at: Date.now(), p })
      }
      out.set(s, await p)
    }),
  )
  return out
}
