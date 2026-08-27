// 「ほかの AI から使う」タブが出す、クライアント別の貼り付け内容。
// 表示から切り離してあるのは、ここが間違うと画面は正しく見えたまま繋がらない
// ——目視で気づけない種類の誤りだから、テストで固定するため。

export type ClientId = 'claude-code' | 'claude-desktop' | 'cursor' | 'vscode' | 'other'

export const CLIENTS: ClientId[] = [
  'claude-code',
  'claude-desktop',
  'cursor',
  'vscode',
  'other',
]

/** 貼るもの。`url` は「URL そのものを入力欄に入れる」＝別の文字列は要らない。 */
export type Recipe = { kind: 'command' | 'json'; text: string } | { kind: 'url' }

export function recipeFor(client: ClientId, url: string): Recipe {
  switch (client) {
    case 'claude-code':
      return { kind: 'command', text: `claude mcp add --transport http asterism ${url}` }
    case 'cursor':
      return { kind: 'json', text: JSON.stringify({ mcpServers: { asterism: { url } } }, null, 2) }
    case 'vscode':
      return {
        kind: 'json',
        text: JSON.stringify({ servers: { asterism: { type: 'http', url } } }, null, 2),
      }
    // Claude Desktop の claude_desktop_config.json は stdio しか検証しないので、
    // JSON を配ってはいけない（貼っても無言で無視される）。URL を渡して
    // 「カスタムコネクタ」から入れてもらうのが唯一通る道。
    case 'claude-desktop':
    case 'other':
      return { kind: 'url' }
  }
}
