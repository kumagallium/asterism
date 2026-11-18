// 台帳の行 → 図に足す合成の下書き（PR 14e-1・契約 §2.2）。`useSky`・`okSky.proposed` には混ぜず、図に渡す所だけで足す。
// `draft_id` は `ledger:<取り込み元>`。語の点の id は `draft|ledger:<取り込み元>|<語の id>`（mapModel の台帳の枝が描く）。
// 未確認＝薄い・確認済み＝濃い。候補（関係の無い組）は「候補も出す」がオンのときだけ、名前の無い薄い線で。
import type { DraftSky, DraftSkyConcept, DraftSkyRelation } from '../../sky/skyApi'
import { LEDGER_PREFIX, type LedgerConceptExtra, type LedgerRelationExtra } from '../map/mapModel'
import type { Ledger, LedgerRow } from './knowledgeRelationsApi'
import { filterCandidates, filterRelations, isShown, type LedgerFilter } from './ledgerView'

export type LedgerConcept = DraftSkyConcept & LedgerConceptExtra
export type LedgerRelation = DraftSkyRelation & LedgerRelationExtra

/** 台帳の絞り込み → 図の下書き（取り込み元ごとに 1 つ。語も線も無い取り込み元は作らない）。 */
export function ledgerDrafts(ledgers: readonly Ledger[], filter: LedgerFilter): DraftSky[] {
  const shown = filterRelations(ledgers, filter)
  const cands = filter.candidates ? filterCandidates(ledgers, filter) : []
  const out: DraftSky[] = []
  for (const l of ledgers) {
    const src = l.source.source
    const rels = shown.filter((r) => r.source === src && r.rel && isShown(r)) // 「違う」とした行は図に描かない
    const cs = cands.filter((r) => r.source === src)
    if (rels.length === 0 && cs.length === 0) continue
    const concepts = new Map<string, LedgerConcept>()
    const touch = (c: LedgerRow['from'], confirmed: boolean) => {
      const cur = concepts.get(c.id)
      if (cur) {
        if (confirmed) cur.confirmed = true
        return
      }
      concepts.set(c.id, { key: c.id, label: c.name, kind: c.kind === 'property' ? 'property' : 'class', use: 'new', state: 'solid', role: c.role, confirmed })
    }
    const relations: LedgerRelation[] = []
    for (const r of rels) {
      touch(r.from, r.confirmed)
      touch(r.to, r.confirmed)
      relations.push({ from: r.from.id, to: r.to.id, rel: r.rel as string, state: r.confirmed ? 'solid' : 'thin', confirmed: r.confirmed })
    }
    for (const r of cs) {
      touch(r.from, false)
      touch(r.to, false)
      relations.push({ from: r.from.id, to: r.to.id, rel: 'candidate', state: 'thin', candidate: true })
    }
    out.push({ draft_id: `${LEDGER_PREFIX}${src}`, theme_label: l.source.title, concepts: [...concepts.values()], relations, questions: [] })
  }
  return out
}

/** 点の id（`draft|ledger:<取り込み元>|<語の id>`）→ 取り込み元と語の id。台帳の点でなければ null。 */
export function parseLedgerNode(nodeId: string | null): { source: string; termId: string } | null {
  if (!nodeId || !nodeId.startsWith(`draft|${LEDGER_PREFIX}`)) return null
  const rest = nodeId.slice(`draft|${LEDGER_PREFIX}`.length)
  const i = rest.lastIndexOf('|')
  if (i <= 0) return null
  return { source: rest.slice(0, i), termId: rest.slice(i + 1) }
}

export const ledgerNodeId = (source: string, termId: string): string => `draft|${LEDGER_PREFIX}${source}|${termId}`

/** 知識の写しのデータセット（取り込み元の `datasetId`）を図の元から除く。写しは台帳の語として見せるので、種類・項目の点は出さない。 */
export function withoutCopyDatasets<D extends { id: string }>(datasets: readonly D[], ledgers: readonly Ledger[]): D[] {
  const ids = new Set(ledgers.map((l) => l.source.datasetId).filter((x): x is string => !!x))
  return ids.size === 0 ? [...datasets] : datasets.filter((d) => !ids.has(d.id))
}
