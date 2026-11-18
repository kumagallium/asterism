// 台帳（取り込み元ごとの関係）を読む・書く・絞り込みを持つ hook（PR 14e-1・契約 §2.2）。ChatApp は 1 行で呼ぶ。
// 絞り込みはここが持つ（図と右の一覧の両方に効くので、ChatApp の外の 1 か所にまとめる）。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DraftSky } from '../../sky/skyApi'
import { fetchImports, fetchLedger, KnowledgeApiError, postAssign, reviewInChunks, type ImportSource, type Ledger, type ReviewAction, type ReviewResult } from './knowledgeRelationsApi'
import { ledgerDrafts } from './ledgerDrafts'
import { DEFAULT_FILTER, effectiveFilter, type LedgerFilter } from './ledgerView'

export interface LedgersLoad {
  status: 'loading' | 'ok' | 'error'
  ledgers: Ledger[]
  /** 読めなかった取り込み元（飛ばして残りを出す）。 */
  failed: string[]
}

/** 取り込み元の一覧と、取り込み元ごとの台帳を読む。1 件の失敗は飛ばして `failed` に積む（一覧が読めないときだけ error）。 */
export async function loadLedgers(lang: 'ja' | 'en', fetchers: { imports: typeof fetchImports; ledger: typeof fetchLedger } = { imports: fetchImports, ledger: fetchLedger }): Promise<LedgersLoad> {
  let sources: ImportSource[]
  try {
    sources = await fetchers.imports()
  } catch {
    return { status: 'error', ledgers: [], failed: [] }
  }
  const got = await Promise.all(sources.map((s) => fetchers.ledger(s, lang).then((l) => ({ l, s }), () => ({ l: null, s }))))
  return {
    status: 'ok',
    ledgers: got.flatMap((g) => (g.l ? [g.l] : [])),
    failed: got.filter((g) => !g.l).map((g) => g.s.title),
  }
}

/** 書き込みの失敗を 1 行の種類に直す。409＝取り込み中・422＝検査に落ちた（理由つき）。 */
export type WriteFailure = { kind: 'busy' } | { kind: 'rejected'; reason: string } | { kind: 'other'; message: string }
export function writeFailureOf(e: unknown): WriteFailure {
  if (e instanceof KnowledgeApiError) {
    if (e.status === 409) return { kind: 'busy' }
    if (e.status === 422) return { kind: 'rejected', reason: e.message }
    return { kind: 'other', message: e.message }
  }
  return { kind: 'other', message: e instanceof Error ? e.message : String(e) }
}

export interface LedgersApi extends LedgersLoad {
  filter: LedgerFilter
  setFilter: (f: LedgerFilter) => void
  /** 図に足す合成の下書き（絞り込み済み）。 */
  drafts: DraftSky[]
  reload: () => void
  /** 確認・取り消し・違う・戻す。書いたあとは台帳を読み直す。 */
  review: (source: string, action: ReviewAction, keys: string[]) => Promise<ReviewResult | void>
  /** `pair` は組の鍵（`pair` を `|` で分けた先の語が a）。 */
  assign: (source: string, pair: string, rel: string, subject: 'a' | 'b') => Promise<void>
}

export function useLedgers(tick: number, lang: 'ja' | 'en'): LedgersApi {
  const [load, setLoad] = useState<LedgersLoad>({ status: 'loading', ledgers: [], failed: [] })
  const [rawFilter, setFilter] = useState<LedgerFilter>(DEFAULT_FILTER)
  const [own, setOwn] = useState(0)
  const seq = useRef(0)
  useEffect(() => {
    const n = ++seq.current
    void loadLedgers(lang).then((r) => {
      if (n === seq.current) setLoad(r)
    })
  }, [tick, own, lang])
  const reload = useCallback(() => setOwn((n) => n + 1), [])
  const review = useCallback(
    async (source: string, action: ReviewAction, keys: string[]) => {
      try {
        return await reviewInChunks(source, action, keys)
      } finally {
        reload() // 途中で止まっても、済んだ分は台帳に残る。読み直して合わせる
      }
    },
    [reload],
  )
  const assign = useCallback(
    async (source: string, pair: string, rel: string, subject: 'a' | 'b') => {
      await postAssign(source, pair, rel, subject, lang)
      reload()
    },
    [reload, lang],
  )
  // 選んでいる値が選択肢から消えたら「すべて」に戻す（取り込み元・関係の種類）
  const filter = useMemo(() => effectiveFilter(load.ledgers, rawFilter), [load.ledgers, rawFilter])
  const drafts = useMemo(() => ledgerDrafts(load.ledgers, filter), [load.ledgers, filter])
  return { ...load, filter, setFilter, drafts, reload, review, assign }
}
