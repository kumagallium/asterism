// 「言えなかった関係の種類」を読む hook（R2）。台帳が読み直されたとき（確認・取り込みのあと）と、取り込み元の絞り込み・言語が変わったときに引き直す。
// 引き直している間は前の群を残す（ちらつかせない）のは、同じ取り込み元・同じ言語の読み直しのときだけ。失敗したら群は出さない（1 行だけ）。別の取り込み元・言語に変えたら前の群は捨てる。
import { useEffect, useRef, useState } from 'react'
import { fetchUnstated, type UnstatedGroup } from './knowledgeRelationsApi'

import { ALL } from './ledgerView'
import type { LedgersApi } from './useLedgers'

/** 候補のタブ（`tab`）を開いていて台帳が読めている間だけ読む。取り込み元の絞り込みがあればその鍵を渡す。 */
export function unstatedArgs(api: Pick<LedgersApi, 'status' | 'ledgers' | 'filter'>, tab: string): { source: string | undefined; enabled: boolean } {
  return { source: api.filter.source === ALL ? undefined : api.filter.source, enabled: tab === 'candidates' && api.status === 'ok' && api.ledgers.length > 0 }
}

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
  const [state, setState] = useState<UnstatedLoad & { source: string | undefined; lang: 'ja' | 'en' }>({ status: 'loading', groups: [], source, lang })
  const seq = useRef(0)
  useEffect(() => {
    if (!enabled) return
    const n = ++seq.current
    void fetcher(lang, source).then(
      (r) => {
        if (n === seq.current) setState({ status: 'ok', groups: r.groups, source, lang })
      },
      () => {
        if (n === seq.current) setState(() => ({ status: 'error', groups: [], source, lang }))
      },
    )
  }, [trigger, lang, source, enabled, fetcher])
  // 別の取り込み元・言語の群は、読み直しが済むまで見せない（その取り込み元の物に見えてしまう）
  if (state.source !== source || state.lang !== lang) return { status: 'loading', groups: [] }
  return { status: state.status, groups: state.groups }
}
