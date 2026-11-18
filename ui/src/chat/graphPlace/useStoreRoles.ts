// ストアの用語の役割の表（IRI → 役割）を、図の元（/api/sky）と同じ合図で読む（PR 14e-1b）。
// 一覧の口は 1 度に全語を返すので、合図ごとに 1 回・走っている読みがあれば古い結果は捨てる（1 本にまとめる）。
// 読めなかったとき（失敗・古いサーバ）は前の表を残す（無ければ空＝台帳の行だけで描く）。
import { useEffect, useRef, useState } from 'react'
import { listTerms } from '../../vocabApi'
import { storeRoleTable } from './placedTerms'

export function useStoreRoles(refreshKey: number, fetcher: typeof listTerms = listTerms): ReadonlyMap<string, string> {
  const [table, setTable] = useState<ReadonlyMap<string, string>>(new Map())
  const seq = useRef(0)
  useEffect(() => {
    const n = ++seq.current
    fetcher().then(
      (terms) => {
        if (n === seq.current) setTable(storeRoleTable(terms))
      },
      () => undefined,
    )
  }, [refreshKey, fetcher])
  return table
}
