import type { ThemeQuestion, ThemeSummary } from './themesApi'

/** 用語のページの「使う問い」に足す、知識の問い（確定した関係から答える）。`GET /api/themes` の
 *  テーマの問いのうち `mode === 'knowledge'` で、対象がこの用語（`terms` に slug）のもの。同じ問いが
 *  複数のテーマに入っていても 1 本（テーマ名は `themes` に全部残す）。 */
export interface TermKnowledgeQuestion {
  q: ThemeQuestion
  themes: ThemeSummary[]
}

export function knowledgeQuestionsOf(themes: ThemeSummary[], slug: string): TermKnowledgeQuestion[] {
  const out = new Map<string, TermKnowledgeQuestion>()
  for (const th of themes) {
    for (const q of th.questions) {
      if (q.mode !== 'knowledge' || !q.terms.includes(slug)) continue
      const hit = out.get(q.tool_name)
      if (hit) hit.themes.push(th)
      else out.set(q.tool_name, { q, themes: [th] })
    }
  }
  return [...out.values()]
}
