// 足した関係を読む hook（R3c）。`trigger` が変わるたびに引き直す（書いたあと・台帳を読み直したあと）。
// 引き直している間は前の一覧を残す（ちらつかせない）が、`busy` が真になる。画面は busy の間、書き込みのボタンを無効にする
// （古い行を見て直す・消す・やめるを押させない）。失敗は 1 行だけ。
import { useEffect, useRef, useState } from 'react'
import { fetchAdded, type AddedRelation } from './relationAddApi'

export interface AddedLoad {
  status: 'loading' | 'ok' | 'error'
  relations: AddedRelation[]
  /** 401/503 で読めない（この環境では足せない）。 */
  unavailable: boolean
  /** 読み直しの途中（今の一覧は古いかもしれない）。 */
  busy?: boolean
}

export function useAddedRelations(trigger: unknown, enabled: boolean, fetcher: typeof fetchAdded = fetchAdded): AddedLoad {
  const [state, setState] = useState<AddedLoad & { forTrigger: unknown }>({ status: 'loading', relations: [], unavailable: false, forTrigger: undefined })
  const seq = useRef(0)
  useEffect(() => {
    if (!enabled) return
    const n = ++seq.current
    void fetcher().then(
      (relations) => {
        if (n === seq.current) setState({ status: 'ok', relations, unavailable: false, forTrigger: trigger })
      },
      (e: unknown) => {
        const st = (e as { status?: number } | null)?.status
        if (n === seq.current) setState({ status: 'error', relations: [], unavailable: st === 401 || st === 503, forTrigger: trigger })
      },
    )
  }, [trigger, enabled, fetcher])
  return { status: state.status, relations: state.relations, unavailable: state.unavailable, busy: enabled && !Object.is(state.forTrigger, trigger) }
}
