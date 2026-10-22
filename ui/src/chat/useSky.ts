// `GET /api/sky`（公開済み＋未確認の提案）の読み取り（PR 11h で ChartPane から切り出し）。
// 地図・中身・上の 1 行が同じ読み取りを見る。`refreshKey` が変わるたびに取り直す。
// `tick` は読み込みに成功した回数（中身の読み直しの合図）。
import { useCallback, useEffect, useState } from 'react'
import type { SkyData, SkyDataset } from '../sky/skyApi'
import { getSkyWithProposed, type SkyDraft } from './chatApi'

export type SkyLoad =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ok'; sky: SkyData; proposed: SkyDraft[]; proposedDatasets: SkyDataset[] }

export function useSky(refreshKey: number, lang: 'ja' | 'en'): { data: SkyLoad; retry: () => void; tick: number } {
  const [data, setData] = useState<SkyLoad>({ status: 'loading' })
  const [retryTick, setRetryTick] = useState(0)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let off = false
    getSkyWithProposed(lang)
      .then(({ sky, proposed, proposedDatasets }) => {
        if (off) return
        setData({ status: 'ok', sky, proposed, proposedDatasets })
        setTick((n) => n + 1)
      })
      // 取り直しの失敗では、いま描いている図を消さない（最初の読み込みだけエラーにする）
      .catch(() => !off && setData((cur) => (cur.status === 'ok' ? cur : { status: 'error' })))
    return () => {
      off = true
    }
  }, [lang, refreshKey, retryTick])

  const retry = useCallback(() => setRetryTick((n) => n + 1), [])
  return { data, retry, tick }
}
