// 絞り込みを、置いた語と線（ストアの用語・`sky.edges`）にも効かせる純関数（PR 14e-1b）。
// 規則: 台帳の行に当たる線は、その行が絞り込みに通るときだけ出す。台帳のどの行にも当たらないストアの線
// （チャットで作った関係）は、取り込み元が「すべて」・状態が「すべて」か「確認済み」・関係の種類が合うときだけ。
// 語の点は、出ている線か、通った行（候補を出すなら候補も）に絡むものだけ。絞り込みが全部「すべて」なら何もしない。
import type { SkyData, SkyLine } from '../../sky/skyApi'
import { realRelationOf } from '../../sky/skyLayout'
import type { Ledger, LedgerRow } from './knowledgeRelationsApi'
import { ALL, filterCandidates, filterRelations, isShown, type LedgerFilter } from './ledgerView'

const isDefault = (f: LedgerFilter): boolean => f.source === ALL && f.status === ALL && f.rel === ALL

/** 行 → `sky.edges` の関係名（measures は相手の種別で分かれる）。 */
const realOf = (r: LedgerRow): string | undefined => (r.rel ? realRelationOf(r.rel, r.to.kind === 'property' ? 'property' : 'class') : undefined)

/** 行がこの線に当たるか（両端の `term_iri` と関係。向きの無い関係は向きを問わない）。 */
function rowMatches(r: LedgerRow, e: SkyLine): boolean {
  if (!r.from.termIri || !r.to.termIri || realOf(r) !== e.relation) return false
  if (r.from.termIri === e.from && r.to.termIri === e.to) return true
  return e.relation === 'competesWith' && r.from.termIri === e.to && r.to.termIri === e.from
}

/** 絞り込みの「関係の種類」（下書き側の名前）が、この線の関係に当たるか。 */
const relMatches = (rel: string, edgeRelation: string): boolean =>
  rel === ALL || realRelationOf(rel, 'class') === edgeRelation || realRelationOf(rel, 'property') === edgeRelation

export function filterStoreSky<S extends Pick<SkyData, 'terms' | 'edges'>>(sky: S, ledgers: readonly Ledger[], f: LedgerFilter): S {
  if (isDefault(f)) return sky
  const all = ledgers.flatMap((l) => l.rows)
  const passing = filterRelations(ledgers, f).filter(isShown)
  const passSet = new Set(passing)
  const unmatchedOk = f.source === ALL && (f.status === ALL || f.status === 'confirmed')
  const edges = (sky.edges ?? []).filter((e) => {
    const rows = all.filter((r) => r.confirmed && isShown(r) && rowMatches(r, e))
    return rows.length > 0 ? rows.some((r) => passSet.has(r)) : unmatchedOk && relMatches(f.rel, e.relation)
  })
  const touched = new Set<string>()
  for (const e of edges) {
    touched.add(e.from)
    touched.add(e.to)
  }
  const rows = f.candidates ? [...passing, ...filterCandidates(ledgers, f)] : passing
  for (const r of rows) {
    if (r.from.termIri) touched.add(r.from.termIri)
    if (r.to.termIri) touched.add(r.to.termIri)
  }
  return { ...sky, edges, terms: sky.terms.filter((t) => touched.has(t.iri)) }
}
