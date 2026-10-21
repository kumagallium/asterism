import { describe, expect, it } from 'vitest'
import type { ThemeDraft } from '../themes/themeDraft'
import { nameOfKey, opSentence } from './workspaceText'

// 復元した文に生の key（n1・h2）を出さない。

const t = (key: string, o: Record<string, unknown> = {}) =>
  key === 'themes:chat.goneConcept'
    ? '（外した概念）'
    : key === 'themes:rel.stdName'
      ? `${String(o.name)}（標準）`
      : /^themes:rel\.sentence\./.test(key)
        ? `${String(o.subject)} → ${String(o.object)}`
        : key === 'themes:chat.op.rename'
          ? `rename ${String(o.name)} to ${String(o.to)}`
          : key
const draft: Pick<ThemeDraft, 'concepts' | 'standards'> = {
  concepts: [{ key: 'c2', label_ja: '格子熱伝導率', label_en: '', aliases: [], kind: 'property', use: 'new', slug: 's', reason: '', match: null, by: 'ai' }],
  standards: { 'qudt:Thermal': { label: 'Thermal Conductivity', kind: 'quantity' } },
}

describe('names of keys in a restored sentence', () => {
  it('uses the draft, then standards, then the name the conversation gave, then "a removed concept" — never the key', () => {
    expect(nameOfKey(draft, 'c2', 'ja', t)).toBe('格子熱伝導率')
    expect(nameOfKey(draft, 'qudt:Thermal', 'ja', t)).toBe('Thermal Conductivity（標準）')
    expect(nameOfKey(draft, 'n9', 'ja', t, new Map([['n9', 'ナノ析出物']]))).toBe('ナノ析出物')
    expect(nameOfKey(draft, 'n9', 'ja', t)).toBe('（外した概念）')
    expect(nameOfKey(draft, 'h2', 'ja', t)).not.toContain('h2')
  })

  it('builds a restored relation sentence for a concept that is gone without the raw key', () => {
    const s = opSentence(draft, { op: 'add_relation', from: 'h7', rel: 'lowers', to: 'c2' }, 'ja', t)
    expect(s).toBe('（外した概念） → 格子熱伝導率')
    const named = opSentence(draft, { op: 'add_relation', from: 'h7', rel: 'lowers', to: 'c2' }, 'ja', t, new Map([['h7', '粒界']]))
    expect(named).toBe('粒界 → 格子熱伝導率')
  })
})
