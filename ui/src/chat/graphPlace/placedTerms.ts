// ストアに置いた語（台帳の行の `term_iri`）の純関数（PR 14e-1b・契約 §4）。
// `/api/sky` の用語に役割は載らない。台帳の行から「用語の IRI → 役割」の表を引き、ストアの用語の点の色に使う。
import type { SkyData } from '../../sky/skyApi'
import type { Ledger, LedgerRow, TermCard } from './knowledgeRelationsApi'

export interface PlacedTerm {
  iri: string
  /** 台帳（取り込み元）の鍵と、その台帳での語の id（図で押した点から「用語」のタブを開くのに使う）。 */
  source: string
  termId: string
  role: string | null
  kind: string | null
}

const ends = (r: LedgerRow): TermCard[] => [r.from, r.to]

/** 置いた語の表（IRI → 先に見つかった 1 件）。台帳の順（取り込み元の順・行の順）で決まるので毎回同じ。
 *  絞り込みの前の全行から作る（絞り込みで行が隠れても、ストアの用語の色は変わらない）。 */
export function placedIndex(ledgers: readonly Ledger[]): Map<string, PlacedTerm> {
  const m = new Map<string, PlacedTerm>()
  for (const l of ledgers) {
    for (const r of l.rows) {
      for (const c of ends(r)) {
        if (c.termIri && !m.has(c.termIri)) m.set(c.termIri, { iri: c.termIri, source: l.source.source, termId: c.id, role: c.role, kind: c.kind })
      }
    }
  }
  return m
}

/** 台帳の語 → ストアの用語の IRI（置いていなければ null）。 */
export function placedIriOf(ledgers: readonly Ledger[], source: string, termId: string): string | null {
  for (const l of ledgers) {
    if (l.source.source !== source) continue
    for (const r of l.rows) for (const c of ends(r)) if (c.id === termId && c.termIri) return c.termIri
  }
  return null
}

/** ストアの共有語（`GET /api/vocab/shared`）→ IRI → 役割の表。役割の札がある語だけ。 */
export function storeRoleTable(terms: readonly { iri: string; role?: string | null }[]): Map<string, string> {
  const m = new Map<string, string>()
  for (const t of terms) if (t.role) m.set(t.iri, t.role)
  return m
}

/** 図に渡す sky の用語に役割を足す。ストアの札が正（取り込みと関係のないチャットの用語も 4 色になる）・
 *  ストアに札が無い語は台帳の行の役割で補う・どちらにも無い用語はそのまま（今までの 2 色）。 */
export function withTermRoles<S extends Pick<SkyData, 'terms'>>(sky: S, ledgers: readonly Ledger[], store?: ReadonlyMap<string, string> | null): S {
  const idx = placedIndex(ledgers)
  if (idx.size === 0 && !store?.size) return sky
  return {
    ...sky,
    terms: sky.terms.map((t) => {
      const role = store?.get(t.iri) ?? idx.get(t.iri)?.role
      return role ? { ...t, role } : t
    }),
  }
}
