// 最初に開く場所（hash が `#/` のとき）と、最後に居た場所の覚え（PR 14d-1・契約 §1 ④）。
//   データセットもチャットも 0          → 取り込み
//   覚えている最後の場所がある          → そこ
//   覚えが無い → チャットが 1 つ以上ある → チャット（今までの画面に居た人）
//              → 無い                   → 取り込み（配ったばかり＝同梱の見本だけ、も含む）
// 一覧が読めなかった（null）ときは数えられないので、覚えている場所、無ければ取り込み。
import { SESSION_ID_RE, type ChatRoute, type Place } from './chatRoute'

export const LAST_PLACE_KEY = 'asterism.nav.last'

export interface LastPlace {
  place: Place
  /** 最後に開いていたチャット（保存したもの）。 */
  chat?: string
}

/** 覚えを読む。無い・壊れている・読めないときは null。 */
export function readLastPlace(): LastPlace | null {
  try {
    return parseLastPlace(localStorage.getItem(LAST_PLACE_KEY))
  } catch {
    return null
  }
}

export function parseLastPlace(raw: string | null): LastPlace | null {
  if (!raw) return null
  try {
    const v: unknown = JSON.parse(raw)
    if (!v || typeof v !== 'object') return null
    const { place, chat } = v as { place?: unknown; chat?: unknown }
    if (place !== 'import' && place !== 'graph' && place !== 'chat') return null
    return typeof chat === 'string' && SESSION_ID_RE.test(chat) ? { place, chat } : { place }
  } catch {
    return null
  }
}

export function writeLastPlace(last: LastPlace): void {
  try {
    localStorage.setItem(LAST_PLACE_KEY, JSON.stringify(last))
  } catch {
    /* 覚えられなくても今回の表示には効く */
  }
}

export interface FirstPlaceInput {
  last: LastPlace | null
  /** チャットの数（一覧が読めなかったら null）。 */
  sessions: number | null
  /** データセットの数（公開済み＋未公開。図が読めなかったら null）。 */
  datasets: number | null
  /** 覚えているチャットが今も一覧にあるか（一覧が読めなかったら null）。 */
  chatExists?: boolean | null
}

/** `#/` を置き換える先。 */
export function firstPlace({ last, sessions, datasets, chatExists }: FirstPlaceInput): ChatRoute {
  if (sessions === 0 && datasets === 0) return { kind: 'import' }
  if (last) {
    if (last.place !== 'chat') return { kind: last.place }
    return last.chat && chatExists !== false ? { kind: 'session', id: last.chat } : { kind: 'new' }
  }
  return sessions !== null && sessions > 0 ? { kind: 'new' } : { kind: 'import' }
}
