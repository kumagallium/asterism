import { describe, expect, it } from 'vitest'
import { knowledgeQuestionsOf } from './termQuestions'
import type { ThemeQuestion, ThemeSummary } from './themesApi'

const q = (over: Partial<ThemeQuestion>): ThemeQuestion => ({
  tool_name: 'cq_x',
  title: '問い',
  answering_datasets: 0,
  shape: '',
  terms: [],
  mode: 'data',
  ...over,
})
const theme = (id: string, questions: ThemeQuestion[]): ThemeSummary => ({
  id,
  label: id,
  created_at: '',
  questions,
  answerable: 0,
  terms: [],
  memo_questions: [],
})

describe('用語のページの「使う問い」に出す知識の問い', () => {
  const lowers = q({ tool_name: 'kq_tc_lowers', title: '熱伝導率を下げるものは？', mode: 'knowledge', terms: ['tc'], knowledge_count: 3 })
  it('知識の問いのうち、対象がこの用語のものだけ。データの問い・ほかの用語の問いは出さない', () => {
    const themes = [
      theme('a', [lowers, q({ tool_name: 'cq_tc_count', terms: ['tc'] }), q({ tool_name: 'kq_other', mode: 'knowledge', terms: ['other'] })]),
    ]
    expect(knowledgeQuestionsOf(themes, 'tc').map((k) => k.q.tool_name)).toEqual(['kq_tc_lowers'])
    expect(knowledgeQuestionsOf(themes, 'nothing')).toEqual([])
  })
  it('同じ問いが複数のテーマにあれば 1 本・テーマは全部残す', () => {
    const out = knowledgeQuestionsOf([theme('a', [lowers]), theme('b', [lowers])], 'tc')
    expect(out).toHaveLength(1)
    expect(out[0].themes.map((t) => t.id)).toEqual(['a', 'b'])
  })
  it('mode を持たない古いサーバの問いは知識として数えない', () => {
    expect(knowledgeQuestionsOf([theme('a', [q({ mode: undefined, terms: ['tc'] })])], 'tc')).toEqual([])
  })
})
