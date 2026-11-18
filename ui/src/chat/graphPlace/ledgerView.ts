// 台帳の行を「絞り込む・並べる・語ごとにまとめる」純関数（PR 14e-1）。図（ledgerDrafts.ts）と右の一覧（GraphPlace）が共有する。
import type { Ledger, LedgerRow } from './knowledgeRelationsApi'
import { toneOf, type Tone } from '../map/mapModel'

export type StatusFilter = 'all' | 'unconfirmed' | 'confirmed' | 'rejected'

export interface LedgerFilter {
  /** 取り込み元の鍵。`all`＝すべて。 */
  source: string
  status: StatusFilter
  /** 関係の種類（下書き側の名前: lowers など）。`all`＝すべて。 */
  rel: string
  /** 関係の無い組（候補）を図に出す。 */
  candidates: boolean
}

export const ALL = 'all'
export const DEFAULT_FILTER: LedgerFilter = { source: ALL, status: 'all', rel: ALL, candidates: false }

/** 1 度に出す行数（「もっと見る」で増やす）。 */
export const PAGE_SIZE = 50

/** 関係の行（見せる関係＝未確認か確認済み）。 */
export const isShown = (r: LedgerRow): boolean => r.state === 'relation' && !r.rejected
export const isCandidate = (r: LedgerRow): boolean => r.state === 'none'

const bySource = (ledgers: readonly Ledger[], source: string): LedgerRow[] =>
  ledgers.filter((l) => source === ALL || l.source.source === source).flatMap((l) => l.rows)

/** 並び: 未確認 → 確認済み、根拠の多い順。同じなら文の順（毎回同じ並びになる）。 */
export function compareRows(a: LedgerRow, b: LedgerRow): number {
  if (a.confirmed !== b.confirmed) return a.confirmed ? 1 : -1
  if (a.evidence.length !== b.evidence.length) return b.evidence.length - a.evidence.length
  const sa = `${a.source}|${a.key ?? a.sentence ?? ''}`
  const sb = `${b.source}|${b.key ?? b.sentence ?? ''}`
  return sa < sb ? -1 : sa > sb ? 1 : 0
}

/** 絞り込んだ関係の行（候補は含まない）。状態が「すべて」なら未確認＋確認済み。「違うとしたもの」だけが rejected の行を返す。 */
export function filterRelations(ledgers: readonly Ledger[], f: LedgerFilter): LedgerRow[] {
  const rows = bySource(ledgers, f.source).filter((r) => (f.rel === ALL || r.rel === f.rel))
  const out =
    f.status === 'rejected'
      ? rows.filter((r) => r.rejected)
      : rows.filter((r) => isShown(r) && (f.status === 'all' || (f.status === 'confirmed') === r.confirmed))
  return out.sort(compareRows)
}

/** 候補（関係の無い組）。組（`pair`）ごとに 1 行: AI の「言えない」の行を主にし、同じ組の取り込み元の食い違いはその言い分を添える。
 *  取り込み元の絞り込みだけ効く（関係の種類・状態は無い）。根拠の多い順。 */
export function filterCandidates(ledgers: readonly Ledger[], f: Pick<LedgerFilter, 'source'>): LedgerRow[] {
  const byPair = new Map<string, LedgerRow>()
  const origins: LedgerRow[] = []
  for (const r of bySource(ledgers, f.source).filter(isCandidate)) {
    if (r.by === 'origin') origins.push(r)
    else if (!byPair.has(`${r.source}|${r.pair}`)) byPair.set(`${r.source}|${r.pair}`, r)
  }
  for (const o of origins) {
    const k = `${o.source}|${o.pair}`
    const cur = byPair.get(k)
    if (cur) byPair.set(k, { ...cur, originSentence: cur.originSentence ?? o.sentence })
    else byPair.set(k, { ...o, originSentence: o.sentence })
  }
  return [...byPair.values()].sort(compareRows)
}

/** 絞り込みの選択肢に出す関係の種類（台帳に実際にある名前だけ・名前の順）。状態が「違うとしたもの」のときは「違う」とした行から作る。 */
export function relKinds(ledgers: readonly Ledger[], status: StatusFilter = 'all'): string[] {
  const rows = ledgers.flatMap((l) => l.rows).filter(status === 'rejected' ? (r: LedgerRow) => r.rejected : isShown)
  return [...new Set(rows.map((r) => r.rel).filter((x): x is string => !!x))].sort()
}

/** 選んでいる値が選択肢から無くなったら「すべて」に戻した絞り込み（見た目と状態をずらさない）。 */
export function effectiveFilter(ledgers: readonly Ledger[], f: LedgerFilter): LedgerFilter {
  const source = f.source === ALL || ledgers.some((l) => l.source.source === f.source) ? f.source : ALL
  const rel = f.rel === ALL || relKinds(ledgers, f.status).includes(f.rel) ? f.rel : ALL
  return source === f.source && rel === f.rel ? f : { ...f, source, rel }
}

export interface TermSummary {
  /** `<取り込み元>|<語の id>`。 */
  id: string
  source: string
  termId: string
  name: string
  role: string | null
  tone: Tone
  /** 見せている関係の数・未確認の数。 */
  relations: number
  unconfirmed: number
  rows: LedgerRow[]
}

/** 見せる関係を語ごとにまとめる（両端の語それぞれに入れる）。未確認の多い順 → 関係の多い順 → 名前。 */
export function summarizeTerms(rows: readonly LedgerRow[]): TermSummary[] {
  const m = new Map<string, TermSummary>()
  const add = (r: LedgerRow, c: LedgerRow['from']) => {
    const id = `${r.source}|${c.id}`
    let t = m.get(id)
    if (!t) {
      t = { id, source: r.source, termId: c.id, name: c.name, role: c.role, tone: toneOf(c.role, c.kind), relations: 0, unconfirmed: 0, rows: [] }
      m.set(id, t)
    }
    t.relations++
    if (!r.confirmed) t.unconfirmed++
    t.rows.push(r)
  }
  for (const r of rows) {
    if (!isShown(r)) continue
    add(r, r.from)
    add(r, r.to)
  }
  return [...m.values()].sort((a, b) => b.unconfirmed - a.unconfirmed || b.relations - a.relations || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
}

/** その語の「未確認」の鍵を取り込み元ごとにまとめる（「この語の関係を確認」で送る分）。鍵の無い行（取り込み元の関係）は含まない。 */
export function unconfirmedKeys(term: TermSummary): string[] {
  return term.rows.filter((r) => !r.confirmed && r.key).map((r) => r.key as string)
}

/** 先頭から n 行（「もっと見る」の分）。 */
export const firstRows = <T,>(rows: readonly T[], n: number): T[] => rows.slice(0, n)
