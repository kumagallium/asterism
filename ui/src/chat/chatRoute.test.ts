import { describe, expect, it } from 'vitest'
import { chatHash, legacyHashRewrite, parseChatHash } from './chatRoute'

describe('chatRoute', () => {
  it('hash は 3 つ: 新しい話・話・設定', () => {
    expect(parseChatHash('')).toEqual({ kind: 'new' })
    expect(parseChatHash('#/')).toEqual({ kind: 'new' })
    expect(parseChatHash('#/t/cabc12345x')).toEqual({ kind: 'session', id: 'cabc12345x' })
    expect(parseChatHash('#/settings')).toEqual({ kind: 'settings' })
  })
  it('壊れた id・知らない hash は新しい話', () => {
    expect(parseChatHash('#/t/')).toEqual({ kind: 'new' })
    expect(parseChatHash('#/t/XYZ')).toEqual({ kind: 'new' })
    expect(parseChatHash('#/t/%E0%A4%A')).toEqual({ kind: 'new' })
    expect(parseChatHash('#/unknown')).toEqual({ kind: 'new' })
  })
  it('chatHash は parse と往復できる', () => {
    for (const h of ['#/', '#/t/cabc12345x', '#/settings']) expect(chatHash(parseChatHash(h))).toBe(h)
  })
  it('旧 hash は全部 #/ に読み替える', () => {
    const olds = [
      '#/home', '#/home?deep=1', '#/questions', '#/questions/term/x', '#/vocab?q=1', '#/data', '#/data?tab=activity',
      '#/datasets', '#/datasets/abc/tools', '#/browse', '#/cards', '#/cards/i/x', '#/ask', '#/ask/th1', '#/decisions',
      '#/decisions?filter=perspective', '#/crosswalk', '#/crosswalk/new', '#/map', '#/network', '#/sparql',
      '#/workbench', '#/workbench?returnTo=x', '#/jobs', '#/cardsdemo',
    ]
    for (const h of olds) {
      expect(legacyHashRewrite(h), h).toBe('#/')
      expect(parseChatHash(h), h).toEqual({ kind: 'new' })
    }
  })
  it('新しい hash は読み替えない', () => {
    expect(legacyHashRewrite('#/')).toBeNull()
    expect(legacyHashRewrite('#/t/cabc12345x')).toBeNull()
    expect(legacyHashRewrite('#/settings')).toBeNull()
  })
})
