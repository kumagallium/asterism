// `GET /api/sky`（公開済み＋未確認の提案）の読み取り（PR 11h で ChartPane から切り出し）。
// 地図・局所の図・上の 1 行が同じ読み取りを見る。`refreshKey` が変わるたびに取り直し、
// `bornKey` も変わっていれば、増えた点（IRI）を `born` に入れる（✓ の直後の「生まれた輪」用）。
import { useCallback, useEffect, useRef, useState } from 'react'
import type { SkyData, SkyDataset } from '../sky/skyApi'
import { getSkyWithProposed, type SkyDraft } from './chatApi'

export type SkyLoad =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ok'; sky: SkyData; proposed: SkyDraft[]; proposedDatasets: SkyDataset[]; born: string[] }

/** 星図に出ている種類・用語の IRI（増えたものに輪を出すため）。 */
export function irisOf(sky: SkyData): Set<string> {
  const out = new Set<string>()
  for (const t of sky.terms) out.add(t.iri)
  for (const d of sky.datasets) for (const k of d.kinds) out.add(k.iri)
  return out
}

export function useSky(refreshKey: number, lang: 'ja' | 'en', bornKey = 0): { data: SkyLoad; retry: () => void } {
  const [data, setData] = useState<SkyLoad>({ status: 'loading' })
  const [retryTick, setRetryTick] = useState(0)
  const prevIris = useRef<Set<string> | null>(null)
  const lastBornKey = useRef(bornKey)

  useEffect(() => {
    let off = false
    getSkyWithProposed(lang)
      .then(({ sky, proposed, proposedDatasets }) => {
        if (off) return
        const now = irisOf(sky)
        // 取り直したあとに増えた点（最初の読み込みは除く）に輪を出す
        const ring = bornKey !== lastBornKey.current
        lastBornKey.current = bornKey
        const born = ring && prevIris.current ? [...now].filter((i) => !prevIris.current!.has(i)) : []
        prevIris.current = now
        setData({ status: 'ok', sky, proposed, proposedDatasets, born })
      })
      // 取り直しの失敗では、いま描いている図を消さない（最初の読み込みだけエラーにする）
      .catch(() => !off && setData((cur) => (cur.status === 'ok' ? cur : { status: 'error' })))
    return () => {
      off = true
    }
  }, [lang, refreshKey, bornKey, retryTick])

  const retry = useCallback(() => setRetryTick((n) => n + 1), [])
  return { data, retry }
}
