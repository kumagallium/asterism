// hash は 3 つだけ（PR 11a）。hash が唯一の真実源。
//   #/              新しい話（まだ保存していない）
//   #/t/<id>        保存した話
//   #/settings      設定（モーダルを開く）
// 旧い hash（#/home・#/questions …）は全部 #/ に読み替える。

export type ChatRoute = { kind: 'new' } | { kind: 'session'; id: string } | { kind: 'settings' }

/** サーバが振る話の id（`c` + base36 の 8〜12 文字）。 */
export const SESSION_ID_RE = /^c[a-z0-9]{8,12}$/

export function parseChatHash(hash: string): ChatRoute {
  const parts = hash
    .replace(/^#\/?/, '')
    .split('?')[0]
    .split('/')
    .filter(Boolean)
  if (parts[0] === 'settings') return { kind: 'settings' }
  if (parts[0] === 't' && parts[1]) {
    let id = parts[1]
    try {
      id = decodeURIComponent(id)
    } catch {
      return { kind: 'new' }
    }
    if (SESSION_ID_RE.test(id)) return { kind: 'session', id }
  }
  return { kind: 'new' }
}

export function chatHash(route: ChatRoute): string {
  if (route.kind === 'session') return `#/t/${encodeURIComponent(route.id)}`
  if (route.kind === 'settings') return '#/settings'
  return '#/'
}

const LEGACY_RE =
  /^#\/(home|questions|vocab|data|datasets|browse|cards|ask|decisions|crosswalk|map|network|sparql|workbench|jobs|cardsdemo)(?=[?/]|$)/

/** 旧い hash なら `#/`（置き換えるべき文字列）、そうでなければ null。 */
export function legacyHashRewrite(hash: string): string | null {
  return LEGACY_RE.test(hash) ? '#/' : null
}
