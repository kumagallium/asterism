// ページの中の会話スレッド（契約メモ contract_pr_f12.md §1-1）。保存の仕組みは
// consultThreads.ts と同じ `createThreadStore`（namespace `pagechat` — appdata
// `/api/appdata/pagechat/threads`・hosted は localStorage）。consult と違い、
// ここでのスレッドは「オブジェクトごと」（`subjectKey`）— 同じ主語には常に
// 高々 1 本という想定なので、`subjectKey → threadId` の対応づけをこのファイル
// が別の小さな localStorage キーで持つ（`createThreadStore` 自体はスレッドに
// 主語の情報を持たせられない・生成する id も選べない）。この索引を失っても
// 実害は「次の送信で新しいスレッドが始まる」だけ — 会話そのもの（turns）は
// `createThreadStore` 側にそのまま残る。
//
// このファイルはもう 1 つ、AI の提案（`ConverseProposal`）を F4（`+グラフを
// 足す`）と同じ規則で `CardSpec` に変換する純関数群も持つ（契約メモ §1-3
// 「足す」＝「F4 の toCardSpec と同じ CardSpec・同じ cardId・同じ題名規則」）。
// `cardId`/`titleFor` は `measureCardFields.ts` の同じ関数をそのまま使う ——
// 同じ params からは常に同じ id になる、という保証はハッシュ関数を共有する
// ことそのものから来る（再実装しない）。

import { createThreadStore, type Thread, type ThreadMeta, type Turn } from '../threadStore'
import type { CardSpec, ConverseProposal, ConverseResponse, MeasureAgg, MeasureCardParams, MeasureShape } from './cardsApi'
import { cardId, MEASURE_AGGS, MEASURE_SHAPES, titleFor, type MeasureCardLabels, type MeasureSchemaLike, type Translate } from './measureCardFields'
import type { Row } from './viewSpec'

const STORAGE_KEY = 'asterism.pagechat.threads.v1'
const INDEX_KEY = 'asterism.pagechat.index.v1'
const MAX_THREADS = 200

function normalizeProposal(raw: unknown): ConverseProposal | null {
  if (raw === null || raw === undefined || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  if (r.params === null || typeof r.params !== 'object') return null
  if (typeof r.output_kind !== 'string' || typeof r.title !== 'string') return null
  const presentation =
    r.presentation !== null && r.presentation !== undefined && typeof r.presentation === 'object'
      ? (r.presentation as Record<string, unknown>)
      : null
  return { params: r.params as Record<string, unknown>, presentation, output_kind: r.output_kind, title: r.title }
}

/** localStorage/appdata から復元した `result` が `ConverseResponse` の形を
 *  していなければ落とす（`createThreadStore` の流儀 — 壊れた保存は無視する）。 */
function normalizeResult(raw: unknown): ConverseResponse | null {
  if (raw === null || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  if (typeof r.reply !== 'string') return null
  return { reply: r.reply, proposal: normalizeProposal(r.proposal) }
}

const store = createThreadStore<ConverseResponse>({
  namespace: 'pagechat',
  storageKey: STORAGE_KEY,
  maxThreads: MAX_THREADS,
  normalizeResult,
})

export const usePageChatThreads = store.useThreads
export const getPageChatThread = store.getThread
export const isPageChatThreadBusy = store.isThreadBusy
export const resolvePageChatAnswer = store.resolveAnswer
export const failPageChatAnswer = store.failAnswer
export const appendPageChatMessage = store.appendMessage
export const setPageChatTurnDecision = store.setTurnDecision

export type PageChatThread = Thread<ConverseResponse>
export type PageChatTurn = Turn<ConverseResponse>

// ---------------------------------------------------------------------------
// subject_key → threadId（この会話専用の小さな索引）
// ---------------------------------------------------------------------------

function loadIndex(): Record<string, string> {
  if (typeof localStorage === 'undefined') return {}
  try {
    const raw = localStorage.getItem(INDEX_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, string>) : {}
  } catch {
    return {}
  }
}

function saveIndex(index: Record<string, string>): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(INDEX_KEY, JSON.stringify(index))
  } catch {
    /* private mode 等 — 諦める（次の送信で新しいスレッドが始まるだけ） */
  }
}

/** この `subjectKey` に紐づくスレッド id。まだ存在する（削除されていない）
 *  ときだけ返す。 */
export function pageChatThreadIdFor(subjectKey: string): string | null {
  const id = loadIndex()[subjectKey]
  return id && store.getThread(id) ? id : null
}

/** 新しく（または既存の）スレッドをこの `subjectKey` に結びつける。 */
export function rememberPageChatThread(subjectKey: string, threadId: string): void {
  const index = loadIndex()
  if (index[subjectKey] === threadId) return
  saveIndex({ ...index, [subjectKey]: threadId })
}

// ---------------------------------------------------------------------------
// 会話の単位＝問い（カード）ごと＋自由な質問（契約メモ §1.1・PR F18）
//
// `meta` を持つスレッドが正本。`meta` の無いスレッドは F18 より前の保存形 —
// 旧索引（上の `subjectKey → threadId`）に載っているものだけを、その
// `subjectKey` の `kind: 'question'` として一覧に混ぜる（読むだけ。実体の
// スレッドの `meta` はここでは書き換えない — 次に何か送る／結びつけるときに
// 初めて `meta` が付く）。
// ---------------------------------------------------------------------------

/** `subjectKeys` のどれかに属する会話の一覧（`updatedAt` 降順）。旧索引だけに
 *  載っていて `meta` が無いスレッドも、その `subjectKey` の `kind: 'question'`
 *  として混ぜる（表示専用の合成 `meta` — 保存はしない）。 */
export function pageChatThreadsFor(subjectKeys: string[]): PageChatThread[] {
  const keys = new Set(subjectKeys)
  const seen = new Set<string>()
  const result: PageChatThread[] = []
  for (const t of store.getAllThreads()) {
    const subjectKey = t.meta?.subject_key
    if (subjectKey && keys.has(subjectKey)) {
      result.push(t)
      seen.add(t.id)
    }
  }
  const index = loadIndex()
  for (const [subjectKey, threadId] of Object.entries(index)) {
    if (!keys.has(subjectKey) || seen.has(threadId)) continue
    const t = store.getThread(threadId)
    if (!t || t.meta) continue
    result.push({ ...t, meta: { subject_key: subjectKey, kind: 'question' } })
    seen.add(t.id)
  }
  return result.sort((a, b) => b.updatedAt - a.updatedAt)
}

/** `subjectKeys` のどれかに属する、`cardId` に結びついた会話（無ければ
 *  `null`）。結びつき（`kind: 'viewpoint'`）は `bindPageChatThreadToCard` が
 *  作る。 */
export function pageChatThreadForCard(subjectKeys: string[], cardId: string): PageChatThread | null {
  const keys = new Set(subjectKeys)
  const found = store
    .getAllThreads()
    .find((t) => t.meta?.card_id === cardId && t.meta?.subject_key && keys.has(t.meta.subject_key))
  return found ?? null
}

/** `subjectKey` の自由な質問として新しい会話を始める（既定 `kind: 'question'`
 *  — `meta` で上書き可）。 */
export function startPageChatThread(
  subjectKey: string,
  message: string,
  meta?: Partial<ThreadMeta>,
): ReturnType<typeof store.startThread> {
  return store.startThread(message, { kind: 'question', ...meta, subject_key: subjectKey })
}

/** カードに結びついた、ターン 0 の会話を作る（契約メモ §1.2 のケース
 *  `target.kind === 'card'` で会話がまだ無いとき）。 */
export function createPageChatThreadForCard(
  subjectKey: string,
  card: { card_id: string; title: string },
): PageChatThread {
  return store.createEmptyThread(card.title, {
    subject_key: subjectKey,
    card_id: card.card_id,
    kind: 'viewpoint',
  })
}

/** この `threadId` に結びついた `subjectKey`。`meta.subject_key` があれば
 *  それ、無ければ旧索引（`subjectKey → threadId`）から引く — F18 より前に
 *  作られたスレッド（`meta` 自体が無い）を後から結びつけても
 *  `subject_key` を失わないため（`pageChatThreadsFor`／`pageChatThreadForCard`
 *  が読むのに必須）。 */
function subjectKeyForThread(threadId: string): string | undefined {
  const existing = store.getThread(threadId)?.meta?.subject_key
  if (existing) return existing
  for (const [subjectKey, id] of Object.entries(loadIndex())) {
    if (id === threadId) return subjectKey
  }
  return undefined
}

/** 既存の会話をカードに結びつける（契約メモ §1.2「足す」の決着）。題名を
 *  カードの題名に揃え、`meta.card_id`／`kind: 'viewpoint'` を立てる。旧索引
 *  にしか `subject_key` が無いスレッド（`meta` 無し）を結びつけるときは、
 *  ここで拾って一緒に書き込む — 書かないと結びついた直後から
 *  `pageChatThreadsFor`／`pageChatThreadForCard` に出てこなくなる。 */
export function bindPageChatThreadToCard(threadId: string, card: { card_id: string; title: string }): void {
  const subjectKey = subjectKeyForThread(threadId)
  store.setThreadMeta(threadId, {
    card_id: card.card_id,
    kind: 'viewpoint',
    ...(subjectKey ? { subject_key: subjectKey } : {}),
  })
  store.setThreadTitle(threadId, card.title)
}

// ---------------------------------------------------------------------------
// 下書き（直前の提案・契約メモ §1-3「直す」）
// ---------------------------------------------------------------------------

/** スレッドの中で一番あたらしい「まだ決着していない」提案。`decidedTurnIds`
 *  （呼び出し側がその場で決着させた assistant turn の id — 通常はターン自体の
 *  `decision` で足りるが、保存が反映される前の 1 レンダーぶんの遅れを吸収する
 *  ために引数として残す）に載っている turn、または turn 自体に `decision` が
 *  付いている turn は数えない — 決着済みの提案を下書きとして次の送信に持ち
 *  越さない（開き直し・再読み込み後も同じ）。 */
export function latestDraft(turns: PageChatTurn[], decidedTurnIds: ReadonlySet<string> = new Set()): ConverseProposal | null {
  for (let i = turns.length - 1; i >= 0; i--) {
    const turn = turns[i]
    if (turn.role !== 'assistant' || !turn.result?.proposal) continue
    if (decidedTurnIds.has(turn.id) || turn.decision) continue
    return turn.result.proposal
  }
  return null
}

// ---------------------------------------------------------------------------
// pageSummary の作り方（契約メモ §1-3 の `page`）
// ---------------------------------------------------------------------------

export interface PageCardForSummary {
  title: string
  output_kind: string
  rows: Row[]
}

/** カード 1 枚ぶんの rows を要約用に間引く: 既定は先頭 20 行、`series`（推移）
 *  だけは先頭と末尾の 2 行（横軸の範囲が分かれば足りる・全部渡すと body の
 *  大きさの上限（64KB）に当たりやすい）。 */
export function summarizeCardRows(outputKind: string, rows: Row[]): Row[] {
  if (rows.length === 0) return []
  if (outputKind === 'series') {
    return rows.length === 1 ? [rows[0]] : [rows[0], rows[rows.length - 1]]
  }
  return rows.slice(0, 20)
}

/** ページの読み取った値（facts）と、並んでいるカードの結果（rows は間引き済み）
 *  から、`converse` に送る `page` を組む。`facts` はそのまま通す（呼び出し側
 *  — ui-page — が既に人向けの label/value に整えている前提）。 */
export function buildPageSummary(
  facts: { label: string; value: string }[],
  cards: PageCardForSummary[],
): { facts: { label: string; value: string }[]; cards: PageCardForSummary[] } {
  return {
    facts,
    cards: cards.map((c) => ({
      title: c.title,
      output_kind: c.output_kind,
      rows: summarizeCardRows(c.output_kind, c.rows),
    })),
  }
}

// ---------------------------------------------------------------------------
// 「足す」: 提案 → F4 と同じ規則の CardSpec
// ---------------------------------------------------------------------------

const MEASURE_SHAPE_SET = new Set<string>(MEASURE_SHAPES)
const MEASURE_AGG_SET = new Set<string>(MEASURE_AGGS)

function isMeasureShape(value: string): value is MeasureShape {
  return MEASURE_SHAPE_SET.has(value)
}

/** property IRI → class_schema の人向け label（無ければ IRI そのものへ安全側に
 *  倒す — K4 は呼び出し側の表示で守られる。schema がまだ届いていなければ
 *  IRI をそのまま返す）。`titleFor` に渡す {@link MeasureCardLabels} を組む
 *  ためだけの純関数。 */
export function labelsFromSchema(schema: MeasureSchemaLike | null, params: Record<string, unknown>): MeasureCardLabels {
  function labelOf(value: unknown): string | undefined {
    if (typeof value !== 'string') return undefined
    return schema?.properties.find((p) => p.iri === value)?.label ?? value
  }
  const rawItems = Array.isArray(params.items) ? params.items : undefined
  const items = rawItems?.filter((i): i is string => typeof i === 'string').map((i) => labelOf(i) ?? i)
  const agg = typeof params.agg === 'string' && MEASURE_AGG_SET.has(params.agg) ? (params.agg as MeasureAgg) : undefined
  return {
    x: labelOf(params.x),
    y: labelOf(params.y),
    item: labelOf(params.item),
    category: labelOf(params.category),
    items,
    agg,
  }
}

/** AI の提案 → F4 の「＋ グラフを足す」フォームと同じ規則の `CardSpec`
 *  （契約メモ §1-3「足す」）。`cardId` は params の決定論ハッシュそのもの
 *  （`measureCardFields.ts` と同じ関数）なので、同じ params からは常に同じ
 *  id になる。`output_kind` がサーバの語彙から外れている（想定外の値）ときは
 *  null — 呼び出し側は「足す」を出さない。 */
export function proposalCardSpec(
  proposal: ConverseProposal,
  labels: MeasureCardLabels,
  subjectKey: string,
  t: Translate,
): CardSpec | null {
  if (!isMeasureShape(proposal.output_kind)) return null
  const params = proposal.params as MeasureCardParams
  return {
    card_id: cardId(params),
    subject_key: subjectKey,
    tool: 'set_measure',
    params,
    title: titleFor(proposal.output_kind, labels, t),
    output_kind: proposal.output_kind,
    created_at: new Date().toISOString(),
    presentation: proposal.presentation ?? null,
  }
}
