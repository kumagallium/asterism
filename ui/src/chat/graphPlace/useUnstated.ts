// 「言えなかった関係の種類」を読む hook（R2）。台帳が読み直されたとき（確認・取り込みのあと）と、取り込み元の絞り込み・言語が変わったときに引き直す。
// 引き直している間は前の群を残す（ちらつかせない）。
import { useEffect, useRef, useState } from 'react'
import { fetchUnstated, type UnstatedGroup } from './knowledgeRelationsApi'

export interface UnstatedLoad {
  status: 'loading' | 'ok' | 'error'
  groups: UnstatedGroup[]
}

/**
 * @param trigger 台帳を読み直すたびに変わる値（`api.ledgers`）
 * @param source 取り込み元の鍵。`undefined`＝すべて
 * @param enabled false の間は読まない（候補のタブを開いていない・台帳がまだ）
 */
export function useUnstated(trigger: unknown, lang: 'ja' | 'en', source: string | undefined, enabled: boolean, fetcher: typeof fetchUnstated = fetchUnstated): UnstatedLoad {
  const [load, setLoad] = useState<UnstatedLoad>({ status: 'loading', groups: [] })
  const seq = useRef(0)
  useEffect(() => {
    if (!enabled) return
    const n = ++seq.current
    void fetcher(lang, source).then(
      (r) => {
        if (n === seq.current) setLoad({ status: 'ok', groups: r.groups })
      },
      () => {
        if (n === seq.current) setLoad((cur) => ({ status: 'error', groups: cur.groups }))
      },
    )
  }, [trigger, lang, source, enabled, fetcher])
  return load
}
