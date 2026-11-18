import { describe, expect, it } from 'vitest'
import { chatHash, legacyHashRewrite, parseChatHash, placeOf } from './chatRoute'

describe('chatRoute', () => {
  it('hash は 場所 3 つ＋設定＋root', () => {
    expect(parseChatHash('')).toEqual({ kind: 'root' })
    expect(parseChatHash('#/')).toEqual({ kind: 'root' })
    expect(parseChatHash('#/import')).toEqual({ kind: 'import' })
    expect(parseChatHash('#/graph')).toEqual({ kind: 'graph' })
    expect(parseChatHash('#/chat')).toEqual({ kind: 'new' })
    expect(parseChatHash('#/chat/cabc12345x')).toEqual({ kind: 'session', id: 'cabc12345x' })
    expect(parseChatHash('#/settings')).toEqual({ kind: 'settings' })
  })
  it('?add=1 は取り込みだけが持つ', () => {
    expect(parseChatHash('#/import?add=1')).toEqual({ kind: 'import', add: true })
    expect(parseChatHash('#/import?add=0')).toEqual({ kind: 'import' })
    expect(parseChatHash('#/graph?add=1')).toEqual({ kind: 'graph' })
  })
  it('壊れた id は新しいチャット', () => {
    expect(parseChatHash('#/chat/')).toEqual({ kind: 'new' })
    expect(parseChatHash('#/chat/XYZ')).toEqual({ kind: 'new' })
    expect(parseChatHash('#/chat/%E0%A4%A')).toEqual({ kind: 'new' })
  })
  it('知らない hash・奥のある hash は root', () => {
    expect(parseChatHash('#/unknown')).toEqual({ kind: 'root' })
    expect(parseChatHash('#/import/x')).toEqual({ kind: 'root' })
    expect(parseChatHash('#/homepage')).toEqual({ kind: 'root' })
  })
  it('chatHash は parse と往復できる', () => {
    for (const h of ['#/', '#/import', '#/import?add=1', '#/graph', '#/chat', '#/chat/cabc12345x', '#/settings']) {
      expect(chatHash(parseChatHash(h)), h).toBe(h)
    }
  })
  it('旧 hash の転送: #/t/<id> → #/chat/<id>・壊れた id → #/chat', () => {
    expect(legacyHashRewrite('#/t/cabc12345x')).toBe('#/chat/cabc12345x')
    expect(legacyHashRewrite('#/t/XYZ')).toBe('#/chat')
    expect(legacyHashRewrite('#/t')).toBe('#/chat')
  })
  it('旧 hash の転送: ?add=1 は取り込みへ', () => {
    expect(legacyHashRewrite('#/?add=1')).toBe('#/import?add=1')
    expect(legacyHashRewrite('#/t/cabc12345x?add=1')).toBe('#/import?add=1')
  })
  it('旧 hash の転送: #/home 系は知識グラフへ', () => {
    expect(legacyHashRewrite('#/home')).toBe('#/graph')
    expect(legacyHashRewrite('#/home?new=x')).toBe('#/graph')
  })
  it('旧名の一覧は #/ へ・#/homepage は読み替えない', () => {
    const olds = [
      '#/questions', '#/questions/term/x', '#/vocab?q=1', '#/data', '#/data?tab=activity', '#/datasets', '#/datasets/abc/tools',
      '#/browse', '#/cards', '#/cards/i/x', '#/ask', '#/ask/th1', '#/decisions', '#/decisions?filter=perspective',
      '#/crosswalk', '#/crosswalk/new', '#/map', '#/network', '#/sparql', '#/workbench', '#/workbench?returnTo=x', '#/jobs', '#/cardsdemo',
    ]
    for (const h of olds) {
      expect(legacyHashRewrite(h), h).toBe('#/')
      expect(parseChatHash(h), h).toEqual({ kind: 'root' })
    }
    expect(legacyHashRewrite('#/homepage')).toBeNull()
    expect(legacyHashRewrite('#/home2')).toBeNull()
  })
  it('新しい hash は読み替えない', () => {
    for (const h of ['#/', '#/import', '#/import?add=1', '#/graph', '#/chat', '#/chat/cabc12345x', '#/settings']) {
      expect(legacyHashRewrite(h), h).toBeNull()
    }
  })
  it('placeOf: 場所を持つのは 3 つだけ', () => {
    expect(placeOf({ kind: 'import' })).toBe('import')
    expect(placeOf({ kind: 'graph' })).toBe('graph')
    expect(placeOf({ kind: 'new' })).toBe('chat')
    expect(placeOf({ kind: 'session', id: 'cabc12345x' })).toBe('chat')
    expect(placeOf({ kind: 'root' })).toBeNull()
    expect(placeOf({ kind: 'settings' })).toBeNull()
  })
})
