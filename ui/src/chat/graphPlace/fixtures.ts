// テスト用の作り物の台帳（実在の研究データは入れない）。
import type { Ledger, LedgerRow, TermCard } from './knowledgeRelationsApi'

export const term = (id: string, name: string, role: string | null = 'concept', kind: string | null = 'class'): TermCard => ({ id, name, kind, role })

export const row = (over: Partial<LedgerRow> & { from: TermCard; to: TermCard }): LedgerRow => ({
  source: 'src-a',
  key: `${over.from.id}|${over.to.id}`,
  state: 'relation',
  rel: 'makes',
  sentence: null,
  evidence: [],
  context: [],
  tentative: false,
  note: null,
  reason: null,
  by: 'ai',
  confirmed: false,
  rejected: false,
  ...over,
})

export const ev = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `c${i}`, title: `知見${i}`, sourceUrl: null }))

export const ledger = (source: string, rows: LedgerRow[]): Ledger => ({
  source: { source, title: `題-${source}`, datasetId: null, copiedAt: null, running: false },
  rows: rows.map((r) => ({ ...r, source })),
  summary: { shown: rows.length, terms: 0, candidates: 0 },
})

export const A = term('t-a', 'ゲルマ', 'material')
export const B = term('t-b', '焼結', 'method')
export const C = term('t-c', '導電率', 'concept', 'property')
export const D = term('t-d', '粒界', 'concept')
