// 足した関係を読む hook（R3c）。`trigger` が変わるたびに引き直す（書いたあと・台帳を読み直したあと）。
// 引き直している間は前の一覧を残す（ちらつかせない）。失敗は 1 行だけ。
import { useEffect, useRef, useState } from 'react'
import { fetchAdded, type AddedRelation } from './relationAddApi'

export interface AddedLoad {
  status: 'loading' | 'ok' | 'error'
  relations: AddedRelation[]
  /** 401/503 で読めない（この環境では足せない）。 */
  unavailable: boolean
}

export function useAddedRelations(trigger: unknown, enabled: boolean, fetcher: typeof fetchAdded = fetchAdded): AddedLoad {
  const [state, setState] = useState<AddedLoad>({ status: 'loading', relations: [], unavailable: false })
  const seq = useRef(0)
  useEffect(() => {
    if (!enabled) return
    const n = ++seq.current
    void fetcher().then(
      (relations) => {
        if (n === seq.current) setState({ status: 'ok', relations, unavailable: false })
      },
      (e: unknown) => {
        const st = (e as { status?: number } | null)?.status
        if (n === seq.current) setState({ status: 'error', relations: [], unavailable: st === 401 || st === 503 })
      },
    )
  }, [trigger, enabled, fetcher])
  return state
}
