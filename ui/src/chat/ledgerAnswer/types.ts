// 印つきの答え（14e-2）: `reply.answer.ledger` の形と、壊れた返りの整え。
// 台帳の関係（未確認も確認済みも）を使った答えの、項目の一覧・使った鍵・未確認の鍵。

export interface LedgerUse {
  source: string
  key: string
  confirmed: boolean
}

export interface LedgerEvidence {
  title: string
  source_url: string | null
}

export interface LedgerItem {
  label: string
  sentence: string
  confirmed: boolean
  tentative: boolean
  context: string[]
  evidence_total: number
  evidence: LedgerEvidence[]
  uses: LedgerUse[]
}

export interface LedgerQuestionRef {
  source: string
  name: string
  text: string
  count: number
}

export interface LedgerAnswer {
  questions: LedgerQuestionRef[]
  items: LedgerItem[]
  /** 全数（`items` は先頭 50 件まで）。 */
  items_total: number
  unconfirmed: { source: string; key: string }[]
  unconfirmed_count: number
  confirmed_count: number
}

type Obj = Record<string, unknown>
const isObj = (x: unknown): x is Obj => !!x && typeof x === 'object' && !Array.isArray(x)
const str = (x: unknown, d = ''): string => (typeof x === 'string' ? x : d)
const num = (x: unknown): number => (typeof x === 'number' && Number.isFinite(x) ? x : 0)
const arr = (x: unknown): unknown[] => (Array.isArray(x) ? x : [])

/** 壊れた欄は無いものとして扱う（描かない）。 */
export function ledgerOf(v: unknown): LedgerAnswer | null {
  if (!isObj(v) || !Array.isArray(v.items)) return null
  const items = arr(v.items)
    .filter(isObj)
    .filter((i) => typeof i.sentence === 'string')
    .map(
      (i): LedgerItem => ({
        label: str(i.label),
        sentence: str(i.sentence),
        confirmed: i.confirmed === true,
        tentative: i.tentative === true,
        context: arr(i.context).filter((c): c is string => typeof c === 'string' && c !== ''),
        evidence_total: num(i.evidence_total),
        evidence: arr(i.evidence)
          .filter(isObj)
          .map((e) => ({ title: str(e.title), source_url: typeof e.source_url === 'string' && e.source_url !== '' ? e.source_url : null })),
        uses: arr(i.uses)
          .filter(isObj)
          .filter((u) => typeof u.key === 'string' && typeof u.source === 'string')
          .map((u) => ({ source: str(u.source), key: str(u.key), confirmed: u.confirmed === true })),
      }),
    )
  return {
    questions: arr(v.questions)
      .filter(isObj)
      .map((q) => ({ source: str(q.source), name: str(q.name), text: str(q.text), count: num(q.count) })),
    items,
    items_total: Math.max(num(v.items_total), items.length),
    unconfirmed: arr(v.unconfirmed)
      .filter(isObj)
      .filter((u) => typeof u.key === 'string' && typeof u.source === 'string')
      .map((u) => ({ source: str(u.source), key: str(u.key) })),
    unconfirmed_count: num(v.unconfirmed_count),
    confirmed_count: num(v.confirmed_count),
  }
}

/** 鍵の同一性（取り込み元＋鍵）。 */
export const relKey = (source: string, key: string): string => `${source}\u0000${key}`

/** 項目がまだ未確認か（この画面で確認が通った分は確認済みとして扱う）。 */
export function itemPending(item: LedgerItem, done: ReadonlySet<string>): boolean {
  if (item.uses.length === 0) return !item.confirmed
  return item.uses.some((u) => !u.confirmed && !done.has(relKey(u.source, u.key)))
}
