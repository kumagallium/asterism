import type { ImportResult } from './knowledgeImportApi'

export const result = (over: Partial<ImportResult> = {}, rel: Partial<ImportResult['relations']> = {}): ImportResult => ({
  source: 'lab',
  title: '実験ノート',
  dataset_id: 'd1',
  created: true,
  copied: { claims: 12, topics: 3, sources: 2, terms: 5, dropped: {} },
  relations: { shown: 4, terms: 5, candidates: 6, tentative: 4, failed: 0, skipped: null, cancelled: false, aborted: false, ...rel },
  questions: [],
  questions_total: 0,
  ...over,
})
