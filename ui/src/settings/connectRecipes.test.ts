import { describe, expect, it } from 'vitest'
import { CLIENTS, recipeFor } from './connectRecipes'

const URL = 'http://127.0.0.1:8765/mcp'

describe('connect recipes', () => {
  it('どのクライアントでも、出す文字列に実際の接続先が入る', () => {
    for (const client of CLIENTS) {
      const r = recipeFor(client, URL)
      if (r.kind !== 'url') expect(r.text).toContain(URL)
    }
  })

  it('Claude Code は 1 行のコマンド', () => {
    const r = recipeFor('claude-code', URL)
    expect(r).toEqual({
      kind: 'command',
      text: `claude mcp add --transport http asterism ${URL}`,
    })
  })

  it('Cursor は mcpServers、VS Code は servers + type:http', () => {
    const cursor = recipeFor('cursor', URL)
    const vscode = recipeFor('vscode', URL)
    if (cursor.kind !== 'json' || vscode.kind !== 'json') throw new Error('expected json')
    expect(JSON.parse(cursor.text)).toEqual({ mcpServers: { asterism: { url: URL } } })
    expect(JSON.parse(vscode.text)).toEqual({
      servers: { asterism: { type: 'http', url: URL } },
    })
  })

  it('Claude Desktop には JSON を配らない（stdio しか検証しないので無言で無視される）', () => {
    expect(recipeFor('claude-desktop', URL)).toEqual({ kind: 'url' })
  })

  it('ポートが 8765 でなくても、そのまま出す', () => {
    const other = 'http://127.0.0.1:8801/mcp'
    const r = recipeFor('claude-code', other)
    if (r.kind === 'url') throw new Error('expected command')
    expect(r.text).toContain(other)
    expect(r.text).not.toContain('8765')
  })
})
