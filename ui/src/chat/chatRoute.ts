// hash は場所 3 つ＋設定（PR 14d-1・design.md §23.11）。hash が唯一の真実源。
//   #/               最初に開く場所を決めて置き換える（firstPlace.ts）
//   #/import         取り込み（`?add=1`＝AI 未設定のとき、かんたんを中央に出す）
//   #/graph          知識グラフ
//   #/chat           新しいチャット（まだ保存していない）
//   #/chat/<id>      保存したチャット
//   #/settings       設定（モーダルを開く）
// 旧い hash は `legacyHashRewrite` で新しい場所へ読み替える（`#/t/<id>` → `#/chat/<id>` など）。

/** 左のナビの 3 つの場所（仕事の順）。 */
export type Place = 'import' | 'graph' | 'chat'
export const PLACES: readonly Place[] = ['import', 'graph', 'chat']

/** チャットの場所の中身（新しいチャットか、保存したチャットか）。 */
export type ChatView = { kind: 'new' } | { kind: 'session'; id: string }

export type ChatRoute =
  | { kind: 'root' }
  | { kind: 'import'; add?: boolean }
  | { kind: 'graph' }
  | ChatView
  | { kind: 'settings' }

/** サーバが振る話の id（`c` + base36 の 8〜12 文字）。 */
export const SESSION_ID_RE = /^c[a-z0-9]{8,12}$/

/** ルートの場所（`#/` と設定は場所を持たない）。 */
export function placeOf(route: ChatRoute): Place | null {
  if (route.kind === 'import' || route.kind === 'graph') return route.kind
  if (route.kind === 'new' || route.kind === 'session') return 'chat'
  return null
}

/** 同じチャット（どちらも新しいチャット、または同じ id）か。 */
export function sameView(a: ChatView, b: ChatView): boolean {
  return a.kind === 'session' ? b.kind === 'session' && a.id === b.id : b.kind === 'new'
}

/** 今の場所のルート（設定を閉じたときに戻る先）。場所が決まる前（null）は `#/`。 */
export function placeRouteOf(place: Place | null, view: ChatView, importAdd: boolean): ChatRoute {
  if (place === 'chat') return view
  if (place === 'import') return importAdd ? { kind: 'import', add: true } : { kind: 'import' }
  if (place === 'graph') return { kind: 'graph' }
  return { kind: 'root' }
}

function sessionId(raw: string | undefined): string | null {
  if (!raw) return null
  let id = raw
  try {
    id = decodeURIComponent(id)
  } catch {
    return null
  }
  return SESSION_ID_RE.test(id) ? id : null
}

export function parseChatHash(hash: string): ChatRoute {
  const [path, query = ''] = hash.replace(/^#\/?/, '').split('?')
  const add = /(?:^|&)add=1(?:&|$)/.test(query)
  const parts = path.split('/').filter(Boolean)
  if (parts[0] === 'settings') return { kind: 'settings' }
  if (parts[0] === 'import' && parts.length === 1) return add ? { kind: 'import', add } : { kind: 'import' }
  if (parts[0] === 'graph' && parts.length === 1) return { kind: 'graph' }
  if (parts[0] === 'chat') {
    if (parts.length === 1) return { kind: 'new' }
    const id = sessionId(parts[1])
    // 壊れた id は新しいチャット（チャットの場所には居る）
    return id ? { kind: 'session', id } : { kind: 'new' }
  }
  return { kind: 'root' }
}

export function chatHash(route: ChatRoute): string {
  if (route.kind === 'session') return `#/chat/${encodeURIComponent(route.id)}`
  if (route.kind === 'new') return '#/chat'
  if (route.kind === 'import') return route.add ? '#/import?add=1' : '#/import'
  if (route.kind === 'graph') return '#/graph'
  if (route.kind === 'settings') return '#/settings'
  return '#/'
}

const LEGACY_RE =
  /^#\/(questions|vocab|data|datasets|browse|cards|ask|decisions|crosswalk|map|network|sparql|workbench|jobs|cardsdemo)(?=[?/]|$)/

/**
 * 旧い hash なら置き換える先、そうでなければ null。
 *   #/t/<id>              → #/chat/<id>（v0.57〜v0.67 のチャット）
 *   #/?add=1・#/t/…?add=1 → #/import?add=1（かんたんは取り込みの中央に出す）
 *   #/home…               → #/graph（かんたんの完了「知識グラフで見る」が出す）
 *   そのほかの旧名         → #/（最初に開く場所の規則で決まる）
 */
export function legacyHashRewrite(hash: string): string | null {
  const [path, query = ''] = hash.split('?')
  const add = /(?:^|&)add=1(?:&|$)/.test(query)
  if (/^#\/?$/.test(path)) return add ? '#/import?add=1' : null
  const t = /^#\/t(?:\/([^/]*))?(?:\/.*)?$/.exec(path)
  if (t) {
    if (add) return '#/import?add=1'
    const id = sessionId(t[1])
    return id ? `#/chat/${encodeURIComponent(id)}` : '#/chat'
  }
  if (/^#\/home(?=[?/]|$)/.test(hash)) return '#/graph'
  return LEGACY_RE.test(hash) ? '#/' : null
}
