// ページの中で AI と会話しながら観点（グラフ）を作り・直し・聞く右ドロワー
// （契約メモ contract_pr_f12.md §1）。見た目・開閉の流儀は
// `consult/ConsultDrawer.tsx` に揃える（`pageChat.css` は Asterism 自身の
// デザイントークンを使い、consult 側の CSS はコピーしない）。
//
// consult と違い、このドロワーは呼び出し側（SubjectPage/SetPage/ClassPage —
// ui-page）に完全に制御される: 自分の FAB や履歴一覧は持たない
// （`open`/`onClose` の外部制御・`target` で開き方を指示される）。
//
// PR F18（contract_pr_f18.md §1.2）: 会話の単位を「観点（カード）ごと＋自由な
// 質問」の複数本にする。見出しの下に会話の切り替え（`.pagechat-threads`）を
// 持ち、`target` で「そのカードの会話」「特定の会話」「新しい会話」のどれを
// 開くかを外から指示できる。提案の決着は、会話がカードに結びついているかで
// 「足す」（結びつけ）／「差し替える」（主）・「別のカードとして足す」に分かれる。
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLlmSettings } from '../settings/context'
import { applyPresentation } from './applyPresentation'
import { withFieldLabels } from './builtinFields'
import type {
  CardRunSubject,
  CardSpec,
  CardToolResult,
  CardView,
  ConverseDraft,
  ConverseMessage,
  ConverseProposal,
  ConverseProposalView,
  ConverseSubject,
  MeasureCardParams,
  MeasureShape,
} from './cardsApi'
import { classSchema, converse, NoLlmKeyError, runCard } from './cardsApi'
import { addCard, replaceCard, useCards } from './cardStore'
import { defaultViewFor } from './defaultView'
import { GraphView } from './GraphView'
import { canonicalJson, MEASURE_SHAPES, sha256Hex, type MeasureSchemaLike, type Translate } from './measureCardFields'
import { parseMermaidFlowchart } from './mermaidFlow'
import { NewCardForm } from './NewCardForm'
import './pageChat.css'
import {
  appendPageChatMessage,
  bindPageChatThreadToCard,
  createPageChatThreadForCard,
  failPageChatAnswer,
  isPageChatThreadBusy,
  labelsFromSchema,
  latestDraft,
  pageChatThreadForCard,
  pageChatThreadsFor,
  proposalCardSpec,
  resolvePageChatAnswer,
  setPageChatTurnDecision,
  startPageChatThread,
  usePageChatThreads,
  type PageChatThread,
  type PageChatTurn,
} from './pageChatThreads'
import { TableView } from './TableView'
import type { GraphSpec, Row, TableSpec, VegaLiteSpec, ViewSpec } from './viewSpec'
import { VegaLiteView } from './VegaLiteView'

const MAX_HISTORY_TURNS = 20
const MEASURE_SHAPE_SET = new Set<string>(MEASURE_SHAPES)
const NO_KEY_TEXT = 'AI を使うには 設定 › AI でキーを入れます。フォームからは今すぐ作れます'
// PR F13: `pagechat.view_source_missing`（統合段で ui/src/i18n/locales/{ja,en}/cards.json
// に追加済み）。NO_KEY_TEXT と同じ流儀で defaultValue に持たせる。
const VIEW_SOURCE_MISSING_TEXT = '元になるカードが見つかりませんでした（先にそのカードを足してから頼んでください）'
// PR F14 §1.5: `pagechat.reask_waiting`・`pagechat.reask_note`（cards.json は
// ui-form が唯一の担当・統合段で追加される想定）。ui-drawer は defaultValue で
// 仮置きする。
const REASK_WAITING_TEXT = '足したカードの結果を待っています…'
// もう一度答え始めたことを示す文面（§1.5「足したら、もう一度答える」の
// 明示）。`{{title}}` は足したカードの見出し。
const REASK_NOTE_TEXT = '足したカード「{{title}}」の結果をもとに、もう一度答えます'
// 待ちの上限（契約メモ §1.5「最長 15 秒。過ぎたら送る」）。
const REASK_WAIT_MS = 15000
// PR F18 §1.2（cards.json は ui-page 担当・統合段で追加される想定）。
const THREAD_NEW_TEXT = '＋ 新しい会話'
const THREAD_UNTITLED_TEXT = '新しい会話'
const THREADS_VIEWPOINTS_TEXT = '観点'
const THREADS_QUESTIONS_TEXT = '質問'
const FIX_INTRO_TEXT = '「{{title}}」を直します。どう変えますか？'
const PROPOSAL_REPLACE_TEXT = '差し替える'
const PROPOSAL_ADD_AS_NEW_TEXT = '別のカードとして足す'
const REPLACED_TEXT = '差し替えました'
const DISCARDED_TEXT = 'やめました'

export interface PageChatPageSummary {
  facts: { label: string; value: string }[]
  /** `card_id` は任意 — `SubjectPage.tsx`/`SetPage.tsx` は `summarizeCardForChat`
   *  経由で必ず渡す（統合段で配線済み）。`ClassPage.tsx` は個々のカードを
   *  実行していない画面なので `cards` 自体が常に空のまま（`card_id` は使われない）。
   *  `tool`/`params`（PR F13 穴埋め）は、まだ「足す」を押していない既定カード
   *  を元にした `kind: 'view'` 提案を解決するために使う（`resolveViewSourceCard`
   *  参照）。`params` は送信元で 2KB を超えたら省かれる。 */
  cards: { card_id?: string; title: string; output_kind: string; rows: Record<string, unknown>[]; tool?: string; params?: Record<string, unknown> }[]
}

/** PR F18 契約メモ §1.2: ドロワーの「開き方」の指示。`kind: 'card'` はそのカード
 *  の会話（無ければ新設）、`'thread'` は特定の会話、`'new'` は空の新しい会話。
 *  無指定（`undefined`）は「直近に触った会話（無ければ新しい会話）」
 *  （{@link resolveChatTarget} 参照）。 */
export type PageChatTarget = { kind: 'card'; cardId: string } | { kind: 'thread'; threadId: string } | { kind: 'new' }

/** 提案の決着（契約メモ §1.2・`threadStore.ts` の `AssistantTurn.decision` と
 *  同じ語彙）。`added_as_new` は「結びついていない別のカードとして足す」
 *  （表示上は `added` と同じ「足しました」）。 */
export type ProposalDecision = 'added' | 'replaced' | 'added_as_new' | 'discarded'

export interface PageChatDrawerProps {
  /** `converse`/表示に使う主語。種類のページ（`kind: 'class'`）は会話専用の
   *  形で、`runCard`/`NewCardForm` に渡す前に {@link toRunSubject} が
   *  `set`（`where: []`）へ変換する。 */
  subject: ConverseSubject
  /** 契約メモ §1-1 の文字列表現。種類のページは `k:<class_iri>`。新しい会話・
   *  そのページの既定の会話の結びつけ先（`bindPageChatThreadToCard`/
   *  `createPageChatThreadForCard`/`startPageChatThread` の第 1 引数）はこちら。 */
  subjectKey: string
  /** PR F18 §1.2: 会話の一覧（`.pagechat-threads`）・「そのカードの会話を探す」
   *  が見る主語の集合。無ければ `[subjectKey]`（F19 でハブがメンバーのキーも
   *  渡す）。 */
  subjectKeys?: string[]
  /** PR F18 §1.2: ドロワーの開き方（{@link PageChatTarget}）。 */
  target?: PageChatTarget
  classIri?: string
  /** `NewCardForm` の型合わせ用（現時点ではフォーム内部で使わない — 詳細は
   *  `NewCardForm.tsx` のコメント参照）。無指定は空文字で渡す。 */
  datasetId?: string
  pageSummary: PageChatPageSummary
  open: boolean
  onClose: () => void
  /** ページ下部の入力欄から送られた文面。ドロワーが開いた時点で 1 度だけ
   *  自動送信する。契約メモ §1.2「新しい会話として始める（結びついた会話を
   *  汚さない）」— `target` が指す会話がどれであっても新しい会話で始まる。 */
  initialMessage?: string
  onCardAdded: (card: CardSpec) => void
  /** PR F18 §1.2: 結びついている会話で「差し替える」が決着したときに呼ぶ
   *  （`replaceCard` 後）。cardStore の購読で表示は自動反映されるので、
   *  呼び出し側は通知として受け取るだけでよい。 */
  onCardReplaced?: (oldCardId: string, card: CardSpec) => void
}

/** 会話専用の `class` 主語を、`runCard`/`NewCardForm` が読める形へ落とす:
 *  種類のページ＝「その種類の全件」なので、条件の無い `set` として扱う
 *  （F4 の「条件で集めた一覧」と同じ形）。 */
function toRunSubject(subject: ConverseSubject): CardRunSubject {
  if (subject.kind === 'class') {
    return { kind: 'set', spec: { class: subject.class_iri, where: [], order_by: null, limit: 20, source_scope: 'all' } }
  }
  return subject
}

/** 完了した user/assistant の組だけを、サーバへ送る `messages` の形にする
 *  （`ConsultDrawer.tsx` の `historyOf` と同じ考え方 — 提案 JSON は積まず、
 *  文章だけを履歴として送る）。 */
function historyOf(thread: PageChatThread | undefined): ConverseMessage[] {
  if (!thread) return []
  const out: ConverseMessage[] = []
  const turns = thread.turns
  for (let i = 0; i < turns.length; i++) {
    const turn = turns[i]
    if (turn.role !== 'user') continue
    const answer = turns[i + 1]
    if (!answer || answer.role !== 'assistant' || !answer.result) continue
    out.push({ role: 'user', content: turn.text })
    out.push({ role: 'assistant', content: answer.result.reply })
  }
  return out.slice(-MAX_HISTORY_TURNS)
}

// ---------------------------------------------------------------------------
// PR F18 §1.2: 開き方の決定・下書きの決定・決着のボタンの出し分け（純関数）
// ---------------------------------------------------------------------------

/** `target` が指定されていればそのまま、無ければ「直近に触った会話」
 *  （`recentThreadId`）、それも無ければ新しい会話（契約メモ §1.2「`target` が
 *  無いとき → 直近に触った会話（無ければ新しい会話）」）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同ファイル既存の純関数群と同じ理由）
export function resolveChatTarget(target: PageChatTarget | undefined, recentThreadId: string | null): PageChatTarget {
  if (target) return target
  return recentThreadId ? { kind: 'thread', threadId: recentThreadId } : { kind: 'new' }
}

/** `CardSpec.view`（保存形・`custom` 付き）を `ConverseDraft.view`（ワイヤ形）へ
 *  剥がす。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同上）
export function draftViewFromCardView(view: CardView | undefined): ConverseDraft['view'] {
  if (!view) return undefined
  return { lang: view.lang, spec: view.spec, text: view.text, source_card_id: view.source_card_id }
}

/** 送るときの `draft`（契約メモ §1.2「draft」）: 未決着の直近の提案があれば
 *  それ、無く会話がカードに結びついていればそのカードの
 *  `{params, presentation, view}`。どちらも無ければ `null`。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同上）
export function resolveSendDraft(pendingProposal: ConverseProposal | null, boundCard: CardSpec | undefined): ConverseDraft | null {
  if (pendingProposal) return { params: pendingProposal.params, presentation: pendingProposal.presentation }
  if (boundCard) return { params: boundCard.params, presentation: boundCard.presentation ?? null, view: draftViewFromCardView(boundCard.view) }
  return null
}

/** 提案の決着ボタンの出し分け（契約メモ §1.2）: すでに決着している（`decision`
 *  が付いている — ローカルでたった今決着したか、スレッドに永続化済みか、
 *  どちらでも）提案は `null`（ボタンを出さない・呼び出し側は決着の表示に
 *  回す）。未決着なら、会話がカードに結びついていなければ主ボタンは「足す」
 *  だけ、結びついていれば主ボタンは「差し替える」で副ボタン「別のカードと
 *  して足す」も出す。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同上）
export function proposalButtonsFor(
  boundCardId: string | null | undefined,
  decision?: ProposalDecision,
): { primary: 'add' } | { primary: 'replace'; secondary: 'add_as_new' } | null {
  if (decision) return null
  return boundCardId ? { primary: 'replace', secondary: 'add_as_new' } : { primary: 'add' }
}

/** 決着（{@link ProposalDecision}）に対応する表示文言の i18n キー（契約メモ
 *  §1.2「決着の表示は「差し替えました」「足しました」「やめました」」）。
 *  `added`／`added_as_new` はどちらも「足しました」——結びつけの有無は
 *  ユーザーへの見せ方としては区別しない。未決着なら `null`。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同上）
export function proposalOutcomeKey(decision: ProposalDecision | undefined): 'pagechat.added' | 'pagechat.replaced' | 'pagechat.discarded' | null {
  if (decision === 'added' || decision === 'added_as_new') return 'pagechat.added'
  if (decision === 'replaced') return 'pagechat.replaced'
  if (decision === 'discarded') return 'pagechat.discarded'
  return null
}

const OUTCOME_FALLBACK_TEXT: Record<'pagechat.added' | 'pagechat.replaced' | 'pagechat.discarded', string> = {
  'pagechat.added': '足しました',
  'pagechat.replaced': REPLACED_TEXT,
  'pagechat.discarded': DISCARDED_TEXT,
}

/** 決着の 1 行（{@link proposalOutcomeKey} 参照）。未決着なら何も出さない。 */
function renderOutcomeNote(decision: ProposalDecision | undefined, t: Translate) {
  const key = proposalOutcomeKey(decision)
  if (!key) return null
  return <p className="pagechat-added-note">{t(key, { defaultValue: OUTCOME_FALLBACK_TEXT[key] })}</p>
}

/** `target` の識別用カウンタを 1 つ進める（参照が変わったときだけ）。純関数。
 *  内容が同じ `target`（例: 2 回連続の `{kind:'new'}`）でも、呼び出し元が
 *  毎回新しいオブジェクトを渡す限り参照は変わる — 内容の文字列化だけを
 *  署名にすると区別が付かず、2 回目以降の「＋ 観点を足す」で前回（すでに
 *  カードへ結びついた）会話が開いたままになる。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同上）
export function nextTargetChangeCounter(
  prevTarget: PageChatTarget | undefined,
  target: PageChatTarget | undefined,
  counter: number,
): number {
  return target !== prevTarget ? counter + 1 : counter
}

// ---------------------------------------------------------------------------
// PR F14 §1.5: 「足したら、もう一度答える」（提案の `answers: true`）
// ---------------------------------------------------------------------------

/** `assistantTurnId` の応答の直前に置かれた、ユーザーの質問の文面。会話の
 *  turn は「user → assistant」の組で並ぶ（`historyOf` と同じ前提）ので、
 *  1 つ手前が user turn であればその文面、そうでなければ見つからない
 *  （純関数・pageChat.test.ts で確認）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同上）
export function precedingUserText(turns: PageChatTurn[], assistantTurnId: string): string | undefined {
  const idx = turns.findIndex((turn) => turn.id === assistantTurnId)
  if (idx <= 0) return undefined
  const prior = turns[idx - 1]
  return prior.role === 'user' ? prior.text : undefined
}

/** 契約メモ §1.5 の再送の文面: `（足したカード「<title>」を使って）<質問>`。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同上）
export function buildReaskText(cardTitle: string, question: string): string {
  return `（足したカード「${cardTitle}」を使って）${question}`
}

/** `pageSummary.cards` にその `cardId` の結果が載っているか（待ちの判定・純関数）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同上）
export function reaskCardReady(cards: PageChatPageSummary['cards'], cardId: string): boolean {
  return cards.some((c) => c.card_id === cardId)
}

/** 提案の `answers: true` かつ直前の質問が分かるときだけ、再送するべき質問を
 *  返す（契約メモ PR F14 §1.5）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同上）
export function reaskQuestionFor(proposal: ConverseProposal, precedingQuestion: string | undefined): string | undefined {
  const answers = proposal.answers
  if (answers !== true) return undefined
  return precedingQuestion
}

// ---------------------------------------------------------------------------
// PR F13 §1: AI が書いた見せ方（`kind: 'view'`）のプレビュー・「足す」
// ---------------------------------------------------------------------------

/** Vega-Lite の `spec` に `data.values = rows` だけを差し込む（契約メモ
 *  contract_pr_f13.md §1 決定 1「データは AI の JSON に書かせない」）。`data`
 *  以外のキー、`data` の中の `values` 以外のキーには触れない — AI が
 *  `data.url`/`data.format` を書いていたとしても（サーバの許可リストで本来
 *  弾かれるはずだが）ここで必ず上書きされ、rows 以外のデータ源にはならない。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（SubjectPage.tsx の appendAddedCards と同じ理由）
export function injectVegaLiteData(spec: Record<string, unknown>, rows: Row[]): VegaLiteSpec {
  const existingData = spec.data
  const dataRest = existingData !== null && typeof existingData === 'object' ? (existingData as Record<string, unknown>) : {}
  return { ...spec, data: { ...dataRest, values: rows } }
}

/** 「足す」で使う `card_id`: 元のカードの id と view そのものの決定論ハッシュを
 *  つなげたもの（契約メモ §1 実装 (2)「card_id: source_card_id + view の
 *  ハッシュ（canonicalJson + sha256Hex）」）。同じ元カード・同じ view からは
 *  常に同じ id（`measureCardFields.ts` の `cardId` と同じ道具を使うだけで、
 *  桁数・区切りは契約メモの記述に対する実装の選択 — deviations 参照）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同上）
export function viewCardId(sourceCardId: string, view: CardView): string {
  const hash = sha256Hex(canonicalJson(view)).slice(0, 16)
  return `${sourceCardId}-view-${hash}`
}

/** `kind: 'view'` の `source_card_id` が指す、元になったカードの最小限の形
 *  （`CardSpec` そのものではない — まだ「足す」を押していない既定カードは
 *  `CardSpec` を持たないため）。`resolveViewSourceCard` が返す。 */
export interface ResolvedSourceCard {
  card_id: string
  title: string
  tool: string
  params: Record<string, unknown>
  output_kind: string
}

/** `source_card_id` から、元になったカードの作り方（`tool`/`params`）を
 *  解決する（穴埋め: 既定カードを元にした view 提案）。まず
 *  `pageSummary.cards`（既定カードも足したカードも両方含む・PR F13 穴埋めで
 *  `tool`/`params` を持つようになった）から探し、そこに無い、または
 *  `params` が省かれている（2KB 超で送信元が落とした）場合は `cardStore` の
 *  「足したカード」から探す。どちらにも無ければ `undefined`
 *  （呼び出し側は「元になるカードが見つかりませんでした」に倒す）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（同上）
export function resolveViewSourceCard(
  sourceCardId: string,
  pageCards: PageChatPageSummary['cards'],
  addedCards: CardSpec[],
): ResolvedSourceCard | undefined {
  const fromPage = pageCards.find((c) => c.card_id === sourceCardId)
  if (fromPage && fromPage.tool && fromPage.params) {
    return { card_id: sourceCardId, title: fromPage.title, tool: fromPage.tool, params: fromPage.params, output_kind: fromPage.output_kind }
  }
  const fromStore = addedCards.find((c) => c.card_id === sourceCardId)
  if (fromStore) {
    return { card_id: fromStore.card_id, title: fromStore.title, tool: fromStore.tool, params: fromStore.params, output_kind: fromStore.output_kind }
  }
  return undefined
}

/** `view` を描画できる形に変換する（純粋）。`renderableCustomView`
 *  （`CardTile.tsx`/`CardDetail.tsx`・ui-page 担当）と同じ考え方 — vega-lite は
 *  `rows` を `data.values` に差し込むだけ、table は `spec`/`rows` をそのまま
 *  `TableView` に渡す、mermaid は `mermaidFlow.ts` の部分集合パーサで
 *  `GraphSpec` にする。差し込み以外で spec を書き換えない。読めない形は
 *  `null`（呼び出し側は「見せ方を描けません」に倒す）。 */
function renderCustomView(
  view: ConverseProposalView,
  rows: Row[],
): { kind: 'view'; view: ViewSpec } | { kind: 'graph'; graph: GraphSpec } | null {
  if (view.lang === 'vega-lite') {
    if (typeof view.spec !== 'object' || view.spec === null) return null
    return { kind: 'view', view: { lang: 'vega-lite', spec: injectVegaLiteData(view.spec, rows), custom: true } }
  }
  if (view.lang === 'table') {
    if (typeof view.spec !== 'object' || view.spec === null) return null
    return { kind: 'view', view: { lang: 'table', spec: view.spec as unknown as TableSpec, custom: true } }
  }
  if (typeof view.text !== 'string') return null
  return { kind: 'graph', graph: parseMermaidFlowchart(view.text).graph }
}

export function PageChatDrawer({
  subject,
  subjectKey,
  subjectKeys,
  target,
  datasetId,
  pageSummary,
  open,
  onClose,
  initialMessage,
  onCardAdded,
  onCardReplaced,
}: PageChatDrawerProps) {
  const { t, i18n } = useTranslation('cards')
  const { isReady, getActiveCredentials } = useLlmSettings()
  const runSubject = toRunSubject(subject)
  const subjKeys = subjectKeys ?? [subjectKey]
  // 依存配列に配列そのものを使うと（呼び出し側が毎レンダー新しい配列を渡し
  // うるため）無限に再実行しかねない — 内容を文字列化したものだけを比較する。
  const subjKeysSig = subjKeys.join('\u0000')

  const threads = usePageChatThreads()
  const addedCards = useCards(subjectKey)

  const [threadId, setThreadId] = useState<string | null>(null)
  // 主語が変わったら（ドロワーが同じインスタンスのまま別のページに使い回され
  // ることは想定していないが、安全側に倒す）会話の切り替えを忘れ、開き方の
  // 再解決（下の effect）に委ねる。提案の決着（`AssistantTurn.decision`）は
  // スレッド自身の turn に永続化されている（`setPageChatTurnDecision`）ので、
  // ここで別途忘れさせる state は持たない。
  const [threadForKey, setThreadForKey] = useState(subjectKey)
  const [showThreadList, setShowThreadList] = useState(false)
  if (threadForKey !== subjectKey) {
    setThreadForKey(subjectKey)
    setThreadId(null)
    setShowThreadList(false)
  }
  const thread = threads.find((th) => th.id === threadId)
  const busy = isPageChatThreadBusy(thread)
  // このカードに結びついた会話か（`bindPageChatThreadToCard`/
  // `createPageChatThreadForCard` が立てる `meta.card_id`）。
  const boundCardId = thread?.meta?.card_id ?? null

  const [draftText, setDraftText] = useState('')
  const [noKeyForced, setNoKeyForced] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const noKey = !isReady || noKeyForced

  // PR F14 §1.5: 「足す」で answers: true の提案を確定したあと、その結果が
  // pageSummary.cards に載るのを待ってから、直前の質問をもう一度送る。
  // `reaskFiredRef` は「待ち・再送は 1 回だけ」の番人 — 結果到着とタイムアウトの
  // 2 つの経路が同時に候補になり得るので、実際に送るのはどちらか早い方の 1 回だけ。
  const [reaskPending, setReaskPending] = useState<{ cardId: string; text: string; title: string } | null>(null)
  const reaskFiredRef = useRef(false)
  // 「もう一度答えます」の明示（§1.5）。再送を実際に送った直後の title を
  // 覚えておくだけ — 表示は `reaskNoteTitle && busy` で判定するので、応答が
  // 届いて busy が false に戻れば自然に消える（クリアの effect は持たない）。
  const [reaskNoteTitle, setReaskNoteTitle] = useState<string | null>(null)

  const currentProposalDraft = latestDraft(thread?.turns ?? [])
  const boundCard = boundCardId ? addedCards.find((c) => c.card_id === boundCardId) : undefined
  const sendDraft = resolveSendDraft(currentProposalDraft, boundCard)

  const scrollRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    // `showForm` も依存に含める — 「詳しく指定」で開いたフォームは会話の
    // 最後の塊として `.pagechat-scroll` の末尾に出るので（PR F17 §1 決定
    // 4）、開いた直後は末尾（＝フォーム）まで見える位置にスクロールする。
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [thread?.turns.length, open, showForm])

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 開いたら入力欄にフォーカス（PR F17 §1 決定 6）。noKey のときは composer
  // 自体が無いので何も起きない。
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  // 横幅に余裕がある画面では、開いている間 `.app-main` に右マージンを空けて
  // 重ねずに横へ並べる（CSS 側 `body.pagechat-open` — `ConsultDrawer.tsx` の
  // 同名 effect と同じ流儀。PR F17 §1 決定 2）。閉じる・アンマウントの両方で
  // 外す。
  useEffect(() => {
    document.body.classList.toggle('pagechat-open', open)
    return () => document.body.classList.remove('pagechat-open')
  }, [open])

  // PR F18 §1.2: 開き方（`target`）の解決。`subjectKey`／`target` の組が
  // 変わったときだけ 1 度解決する — render の中で（effect にしない。
  // `threadForKey` と同じ「prop が変わったら state を作り直す」流儀。
  // react-hooks/set-state-in-effect: effect の本体で同期的に setState
  // しない、が守れない相談だったので、既存の CardTile.tsx／SubjectPage.tsx
  // と同じくこちらへ倒した）。ユーザーが会話一覧から手で別の会話へ切り替えた
  // ぶんは、この組が変わらない限り上書きしない。
  // 内容が同じ `target`（例: 2 回目以降の「＋ 観点を足す」はどちらも
  // `{kind:'new'}`）でも、呼び出し元が渡すオブジェクトの参照が変わって
  // いれば別のクリックとして扱う — 内容の文字列化だけだと `kind:'new'` が
  // 何度クリックされても同じ署名になり、前回の会話（すでにカードへ結びついた
  // もの）が開いたままになる事故を防ぐ。
  const [prevTarget, setPrevTarget] = useState<PageChatTarget | undefined>(undefined)
  const [targetChangeCounter, setTargetChangeCounter] = useState(0)
  if (target !== prevTarget) {
    setPrevTarget(target)
    setTargetChangeCounter((c) => nextTargetChangeCounter(prevTarget, target, c))
  }
  const targetSig = target ? `${targetChangeCounter}:${JSON.stringify(target)}` : 'none'
  const resolveSig = open ? `${subjectKey}\u0000${targetSig}\u0000${subjKeysSig}` : ''
  const [resolvedSig, setResolvedSig] = useState('')
  if (open && resolvedSig !== resolveSig) {
    setResolvedSig(resolveSig)
    const recentThreadId = pageChatThreadsFor(subjKeys)[0]?.id ?? null
    const resolved = resolveChatTarget(target, recentThreadId)
    if (resolved.kind === 'new') {
      setThreadId(null)
    } else if (resolved.kind === 'thread') {
      setThreadId(resolved.threadId)
    } else {
      const existing = pageChatThreadForCard(subjKeys, resolved.cardId)
      if (existing) {
        setThreadId(existing.id)
      } else {
        const cardTitle = pageSummary.cards.find((c) => c.card_id === resolved.cardId)?.title ?? ''
        const created = createPageChatThreadForCard(subjectKey, { card_id: resolved.cardId, title: cardTitle })
        setThreadId(created.id)
      }
    }
    setShowThreadList(false)
  }

  async function send(overrideText?: string, forceNewThread?: boolean): Promise<void> {
    const text = (overrideText ?? draftText).trim()
    if (!text || busy || noKey) return
    if (overrideText === undefined) setDraftText('')
    // 契約メモ §1.2: ページ下部の入力欄からの 1 通目は必ず新しい会話（結びつ
    // いた会話を汚さない）。
    const useExisting = !forceNewThread && !!threadId
    const priorMessages = useExisting ? historyOf(thread) : []
    let activeId: string | null = useExisting ? threadId : null
    let assistantTurnId: string | undefined
    const draftForSend = useExisting ? sendDraft : null
    if (activeId) {
      const appended = appendPageChatMessage(activeId, text)
      assistantTurnId = appended?.assistantTurnId
    } else {
      const started = startPageChatThread(subjectKey, text)
      activeId = started.thread.id
      assistantTurnId = started.assistantTurnId
      setThreadId(activeId)
    }
    if (!activeId || !assistantTurnId) return
    try {
      const res = await converse(
        {
          subject,
          messages: [...priorMessages, { role: 'user', content: text }],
          draft: draftForSend,
          page: pageSummary,
          lang: i18n.language.startsWith('en') ? 'en' : 'ja',
        },
        getActiveCredentials(),
      )
      resolvePageChatAnswer(activeId, assistantTurnId, res)
    } catch (e) {
      if (e instanceof NoLlmKeyError) {
        setNoKeyForced(true)
        failPageChatAnswer(activeId, assistantTurnId, t('pagechat.no_key', { defaultValue: NO_KEY_TEXT }))
        return
      }
      failPageChatAnswer(activeId, assistantTurnId, t('pagechat.error', { defaultValue: '返事を作れませんでした' }))
    }
  }

  // ページ下部の入力欄からの文面は、ドロワーが開いた瞬間に 1 度だけ送る
  // （契約メモ §1-1「その質問がスレッドの 1 通目になる」・§1.2「新しい会話と
  // して始める」）。
  const sentInitialRef = useRef<string | null>(null)
  useEffect(() => {
    if (!open || !initialMessage || noKey) return
    if (sentInitialRef.current === initialMessage) return
    sentInitialRef.current = initialMessage
    void send(initialMessage, true)
    // send は draftText/thread など毎レンダー変わる値を読むので、依存は
    // 「いつ 1 回だけ送るか」を決める open/initialMessage/noKey だけに絞る
    // （NewCardForm.tsx の linkingKinds 取得 effect と同じ流儀）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialMessage, noKey])

  /** 提案の決着をスレッド自身の turn に永続化する（PR F18 §1.2）。パネルを
   *  開き直しても・ページを再読み込みしても、決着済みの提案はもう「足す／
   *  差し替える」を出さない — `threadId` が必要なので、決着はいつも「いま
   *  開いている会話」に対して起きる（提案はその会話の中にしかない）。 */
  function decide(turnId: string, outcome: ProposalDecision) {
    if (threadId) setPageChatTurnDecision(threadId, turnId, outcome)
  }

  /** 「足す」で answers: true の提案が確定したときに呼ぶ（`question` は
   *  直前のユーザーの質問・`reaskQuestionFor` が既に answers を確かめている）。 */
  function startReask(cardTitle: string, cardId: string, question: string) {
    reaskFiredRef.current = false
    setReaskPending({ cardId, text: buildReaskText(cardTitle, question), title: cardTitle })
  }

  function fireReask(pending: { text: string; title: string }) {
    if (reaskFiredRef.current) return
    reaskFiredRef.current = true
    setReaskPending(null)
    setReaskNoteTitle(pending.title)
    void send(pending.text)
  }

  // 結果到着の判定: pageSummary（呼び出し側の再実行で更新される）にその
  // card_id の結果が載ったら送る。
  useEffect(() => {
    if (!reaskPending) return
    if (reaskCardReady(pageSummary.cards, reaskPending.cardId)) {
      fireReask(reaskPending)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reaskPending, pageSummary.cards])

  // タイムアウト: 15 秒待っても結果が載らなければ、そのまま送る。
  useEffect(() => {
    if (!reaskPending) return
    const pending = reaskPending
    const timer = window.setTimeout(() => fireReask(pending), REASK_WAIT_MS)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reaskPending?.cardId])

  if (!open) return null

  // PR F18 §1.2: 会話の切り替え一覧（観点＝カードに結びついた会話・質問＝
  // その他）。`updatedAt` 降順は `pageChatThreadsFor` が保証する。
  const chatThreads = pageChatThreadsFor(subjKeys)
  const viewpointThreads = chatThreads.filter((th) => th.meta?.kind === 'viewpoint')
  const questionThreads = chatThreads.filter((th) => th.meta?.kind !== 'viewpoint')

  function openThread(id: string) {
    setThreadId(id)
    setShowThreadList(false)
  }

  function openNewThread() {
    setThreadId(null)
    setShowThreadList(false)
  }

  // 会話がカードに結びついていて、まだターンが 0（`createPageChatThreadForCard`
  // で作ったばかり）のときの案内（契約メモ §1.2「「<題名>」を直します。どう
  // 変えますか？」）。
  const fixIntroTitle = thread && thread.turns.length === 0 && boundCardId ? thread.title : null

  return (
    <>
      <div className="pagechat-backdrop" onClick={onClose} />
      <aside className="pagechat-drawer" role="dialog" aria-label={t('pagechat.title', { defaultValue: 'このページで聞く・作る' })}>
        <div className="pagechat-head">
          <span className="pagechat-head-title">{t('pagechat.title', { defaultValue: 'このページで聞く・作る' })}</span>
          {/* 閉じるボタンの aria-label は §3 に専用キーが無いため直書き（i18n は
              ui-page 担当の cards.json に無い bare キーを参照すると
              check-i18n-refs が赤くなる — 統合時に ui-page がキーを足せば
              t() に差し替える）。 */}
          <button type="button" className="pagechat-icon-btn" onClick={onClose} aria-label="閉じる">
            ×
          </button>
        </div>

        {!noKey && (
          <div className="pagechat-threads">
            <button type="button" className="pagechat-threads-current" onClick={() => setShowThreadList((v) => !v)}>
              {thread?.title || t('pagechat.thread_new', { defaultValue: THREAD_NEW_TEXT })}
            </button>
            {showThreadList && (
              <div className="pagechat-threads-list" role="menu">
                {viewpointThreads.length > 0 && (
                  <div className="pagechat-threads-group">
                    <p className="pagechat-threads-group-label">{t('pagechat.threads_viewpoints', { defaultValue: THREADS_VIEWPOINTS_TEXT })}</p>
                    {viewpointThreads.map((th) => (
                      <button key={th.id} type="button" className="pagechat-threads-item" onClick={() => openThread(th.id)}>
                        {th.title || t('pagechat.thread_untitled', { defaultValue: THREAD_UNTITLED_TEXT })}
                      </button>
                    ))}
                  </div>
                )}
                {questionThreads.length > 0 && (
                  <div className="pagechat-threads-group">
                    <p className="pagechat-threads-group-label">{t('pagechat.threads_questions', { defaultValue: THREADS_QUESTIONS_TEXT })}</p>
                    {questionThreads.map((th) => (
                      <button key={th.id} type="button" className="pagechat-threads-item" onClick={() => openThread(th.id)}>
                        {th.title || t('pagechat.thread_untitled', { defaultValue: THREAD_UNTITLED_TEXT })}
                      </button>
                    ))}
                  </div>
                )}
                <button type="button" className="pagechat-threads-new" onClick={openNewThread}>
                  {t('pagechat.thread_new', { defaultValue: THREAD_NEW_TEXT })}
                </button>
              </div>
            )}
          </div>
        )}

        <div className="pagechat-scroll" ref={scrollRef}>
          {noKey ? (
            // 鍵が無いとき: 案内の 1 文とフォームは会話の領域の中に置く
            // （`.pagechat-foot` は出さない・PR F17 §1 決定 4）。
            <div className="pagechat-nokey">
              <p className="pagechat-nokey-note">{t('pagechat.no_key', { defaultValue: NO_KEY_TEXT })}</p>
              <NewCardForm
                subject={runSubject}
                subjectKey={subjectKey}
                datasetId={datasetId ?? ''}
                onCancel={onClose}
                onCreated={(card) => onCardAdded(card)}
                embedded
              />
            </div>
          ) : (
            <>
              {fixIntroTitle && (
                <p className="pagechat-fix-intro">{t('pagechat.fix_intro', { defaultValue: FIX_INTRO_TEXT, title: fixIntroTitle })}</p>
              )}
              {!thread || thread.turns.length === 0 ? (
                !fixIntroTitle && (
                  <p className="pagechat-empty">
                    {t('pagechat.placeholder', { defaultValue: '聞きたいこと、出したいグラフ（例: 人口の推移を出して）' })}
                  </p>
                )
              ) : (
                thread.turns.map((turn) => (
                  <PageChatBubble
                    key={turn.id}
                    turn={turn}
                    subject={runSubject}
                    subjectKey={subjectKey}
                    pageSummary={pageSummary}
                    precedingQuestion={precedingUserText(thread.turns, turn.id)}
                    decision={turn.role === 'assistant' ? turn.decision : undefined}
                    boundCardId={boundCardId}
                    t={t}
                    onAdd={(card, reaskQuestion) => {
                      decide(turn.id, 'added')
                      addCard(card)
                      if (threadId) bindPageChatThreadToCard(threadId, { card_id: card.card_id, title: card.title })
                      onCardAdded(card)
                      if (reaskQuestion) startReask(card.title, card.card_id, reaskQuestion)
                    }}
                    onAddAsNew={(card, reaskQuestion) => {
                      decide(turn.id, 'added_as_new')
                      addCard(card)
                      onCardAdded(card)
                      if (reaskQuestion) startReask(card.title, card.card_id, reaskQuestion)
                    }}
                    onReplace={(card, reaskQuestion) => {
                      if (!boundCardId || !threadId) return
                      decide(turn.id, 'replaced')
                      replaceCard(subjectKey, boundCardId, card)
                      bindPageChatThreadToCard(threadId, { card_id: card.card_id, title: card.title })
                      onCardReplaced?.(boundCardId, card)
                      if (reaskQuestion) startReask(card.title, card.card_id, reaskQuestion)
                    }}
                    onDiscard={() => decide(turn.id, 'discarded')}
                  />
                ))
              )}
              {/* 「詳しく指定」で開いたフォームは会話の最後の塊として出す
                  （PR F17 §1 決定 4）。 */}
              {showForm && (
                <div className="pagechat-form-block">
                  <NewCardForm
                    subject={runSubject}
                    subjectKey={subjectKey}
                    datasetId={datasetId ?? ''}
                    onCancel={() => setShowForm(false)}
                    onCreated={(card) => {
                      onCardAdded(card)
                      setShowForm(false)
                    }}
                    embedded
                  />
                </div>
              )}
            </>
          )}
        </div>

        {!noKey && (
          <div className="pagechat-foot">
            <div className="pagechat-tools">
              {reaskPending && (
                <p className="pagechat-reask-waiting">{t('pagechat.reask_waiting', { defaultValue: REASK_WAITING_TEXT })}</p>
              )}
              {reaskNoteTitle && busy && (
                <p className="pagechat-reask-note">
                  {t('pagechat.reask_note', { defaultValue: REASK_NOTE_TEXT, title: reaskNoteTitle })}
                </p>
              )}
              <button type="button" className="pagechat-open-form" onClick={() => setShowForm((v) => !v)}>
                {t('pagechat.open_form', { defaultValue: '詳しく指定' })}
              </button>
            </div>
            {/* `.pagechat-foot` の最後の子は必ず composer（PR F17 §1 決定 4）。 */}
            <form
              className="pagechat-composer"
              onSubmit={(e) => {
                e.preventDefault()
                void send()
              }}
            >
              <textarea
                ref={inputRef}
                className="pagechat-input"
                rows={2}
                value={draftText}
                placeholder={t('pagechat.placeholder', { defaultValue: '聞きたいこと、出したいグラフ（例: 人口の推移を出して）' })}
                aria-label={t('pagechat.placeholder', { defaultValue: '聞きたいこと、出したいグラフ（例: 人口の推移を出して）' })}
                onChange={(e) => setDraftText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !e.nativeEvent.isComposing) {
                    e.preventDefault()
                    void send()
                  }
                }}
              />
              <button
                type="submit"
                className="pagechat-send"
                disabled={busy || !draftText.trim()}
                aria-label={t('pagechat.send', { defaultValue: '送る' })}
              >
                {busy ? t('pagechat.thinking', { defaultValue: '考えています…' }) : t('pagechat.send', { defaultValue: '送る' })}
              </button>
            </form>
          </div>
        )}
      </aside>
    </>
  )
}

function PageChatBubble({
  turn,
  subject,
  subjectKey,
  pageSummary,
  precedingQuestion,
  decision,
  boundCardId,
  t,
  onAdd,
  onAddAsNew,
  onReplace,
  onDiscard,
}: {
  turn: PageChatTurn
  subject: CardRunSubject
  subjectKey: string
  pageSummary: PageChatPageSummary
  /** この応答の直前に置かれたユーザーの質問（`precedingUserText`）。
   *  提案の `answers: true` のとき、「足す」後の再送に使う（契約メモ §1.5）。 */
  precedingQuestion: string | undefined
  decision: ProposalDecision | undefined
  /** 会話が結びついているカードの id（PR F18 §1.2）。あれば主ボタンは
   *  「差し替える」になる。 */
  boundCardId: string | null
  t: Translate
  onAdd: (card: CardSpec, reaskQuestion?: string) => void
  onAddAsNew: (card: CardSpec, reaskQuestion?: string) => void
  onReplace: (card: CardSpec, reaskQuestion?: string) => void
  onDiscard: () => void
}) {
  if (turn.role === 'user') {
    return (
      <div className="pagechat-msg pagechat-msg--user">
        <div className="pagechat-bubble">{turn.text}</div>
      </div>
    )
  }
  if (turn.pending) {
    return (
      <div className="pagechat-msg pagechat-msg--assistant">
        <div className="pagechat-bubble pagechat-bubble--pending">
          <span className="pagechat-spinner" aria-hidden="true" />
          {t('pagechat.thinking', { defaultValue: '考えています…' })}
        </div>
      </div>
    )
  }
  if (turn.error || !turn.result) {
    return (
      <div className="pagechat-msg pagechat-msg--assistant">
        <div className="pagechat-bubble pagechat-bubble--error">
          {turn.error ?? t('pagechat.error', { defaultValue: '返事を作れませんでした' })}
        </div>
      </div>
    )
  }
  const { reply, proposal } = turn.result
  const isViewProposal = proposal?.kind === 'view' && !!proposal.view
  return (
    <div className="pagechat-msg pagechat-msg--assistant">
      <div className="pagechat-msg-col">
        <div className="pagechat-bubble">{reply}</div>
        {proposal && !decision && isViewProposal && (
          <ViewProposalPreview
            subject={subject}
            subjectKey={subjectKey}
            pageSummary={pageSummary}
            proposal={proposal}
            precedingQuestion={precedingQuestion}
            boundCardId={boundCardId}
            decision={decision}
            t={t}
            onAdd={onAdd}
            onAddAsNew={onAddAsNew}
            onReplace={onReplace}
            onDiscard={onDiscard}
          />
        )}
        {proposal && !decision && !isViewProposal && (
          <ProposalPreview
            subject={subject}
            subjectKey={subjectKey}
            proposal={proposal}
            precedingQuestion={precedingQuestion}
            boundCardId={boundCardId}
            decision={decision}
            t={t}
            onAdd={onAdd}
            onAddAsNew={onAddAsNew}
            onReplace={onReplace}
            onDiscard={onDiscard}
          />
        )}
        {proposal && renderOutcomeNote(decision, t)}
      </div>
    </div>
  )
}

function ProposalPreview({
  subject,
  subjectKey,
  proposal,
  precedingQuestion,
  boundCardId,
  decision,
  t,
  onAdd,
  onAddAsNew,
  onReplace,
  onDiscard,
}: {
  subject: CardRunSubject
  subjectKey: string
  proposal: ConverseProposal
  precedingQuestion: string | undefined
  boundCardId: string | null
  /** 呼び出し側（`PageChatBubble`）はすでに決着済みのときこの component
   *  自体を描画しない — ここに来る値は常に `undefined` だが、
   *  {@link proposalButtonsFor} の唯一の決着チェック経路として素通しする
   *  （ボタンの出し分けが decision を二重に判定しない・契約メモ §1.2）。 */
  decision: ProposalDecision | undefined
  t: Translate
  onAdd: (card: CardSpec, reaskQuestion?: string) => void
  onAddAsNew: (card: CardSpec, reaskQuestion?: string) => void
  onReplace: (card: CardSpec, reaskQuestion?: string) => void
  onDiscard: () => void
}) {
  const shapeOk = MEASURE_SHAPE_SET.has(proposal.output_kind)
  const depKey = JSON.stringify(proposal.params)
  const [runState, setRunState] = useState<{ key: string; result: CardToolResult | null; error: boolean }>({
    key: '',
    result: null,
    error: false,
  })
  useEffect(() => {
    if (!shapeOk) return
    let cancelled = false
    runCard(subject, 'set_measure', proposal.params)
      .then((res) => {
        if (!cancelled) setRunState({ key: depKey, result: res, error: false })
      })
      .catch(() => {
        if (!cancelled) setRunState({ key: depKey, result: null, error: true })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depKey, shapeOk])

  const classIri = typeof proposal.params.class === 'string' ? proposal.params.class : null
  const [schemaState, setSchemaState] = useState<{ classIri: string; schema: MeasureSchemaLike | null }>({
    classIri: '',
    schema: null,
  })
  useEffect(() => {
    if (!classIri) return
    let cancelled = false
    classSchema(classIri)
      .then((s) => {
        if (!cancelled) setSchemaState({ classIri, schema: s })
      })
      .catch(() => {
        if (!cancelled) setSchemaState({ classIri, schema: null })
      })
    return () => {
      cancelled = true
    }
  }, [classIri])

  if (!shapeOk) return null
  const result = runState.key === depKey ? runState.result : null
  const error = runState.key === depKey && runState.error
  // classIri がある提案は、class_schema の到着（成功でも失敗でも）を待って
  // からでないと「足す」を許さない — schema が届く前に labelsFromSchema が
  // 生の property IRI へ安全側に倒してしまい、それがそのまま題名に永続化
  // されてしまう（K4 違反）のを防ぐ。runCard と classSchema は別 fetch で
  // 到着順の保証が無いため、この待ち合わせが必要。
  const schemaPending = classIri !== null && schemaState.classIri !== classIri
  const schema = schemaState.classIri === classIri ? schemaState.schema : null
  const ready = !!result && !schemaPending

  function buildSpec(): CardSpec | null {
    if (!ready || !result) return null
    const labels = labelsFromSchema(schema, proposal.params)
    return proposalCardSpec(proposal, labels, subjectKey, t)
  }

  function handlePrimary() {
    const spec = buildSpec()
    if (!spec) return
    const reask = reaskQuestionFor(proposal, precedingQuestion)
    if (boundCardId) onReplace(spec, reask)
    else onAdd(spec, reask)
  }

  function handleAddAsNew() {
    const spec = buildSpec()
    if (!spec) return
    onAddAsNew(spec, reaskQuestionFor(proposal, precedingQuestion))
  }

  const buttons = proposalButtonsFor(boundCardId, decision)
  if (!buttons) return renderOutcomeNote(decision, t)

  return (
    <div className="pagechat-proposal">
      <p className="pagechat-proposal-title">
        {buttons.primary === 'replace'
          ? t('pagechat.proposal_title_replace', { defaultValue: 'この観点に差し替えますか？' })
          : t('pagechat.proposal_title', { defaultValue: 'この観点を足しますか？' })}
      </p>
      {error && <p className="ds-empty-note">{t('render_error')}</p>}
      {!error && !ready && <p className="ds-empty-note">{t('page.loading')}</p>}
      {result && ready && <ProposalView proposal={proposal} result={result} t={t} />}
      <div className="pagechat-proposal-actions">
        <button type="button" className="btn btn--ghost btn--sm" onClick={onDiscard}>
          {t('pagechat.proposal_discard', { defaultValue: 'やめる' })}
        </button>
        {buttons.primary === 'replace' && (
          <button type="button" className="btn btn--ghost btn--sm" disabled={!ready} onClick={handleAddAsNew}>
            {t('pagechat.proposal_add_as_new', { defaultValue: PROPOSAL_ADD_AS_NEW_TEXT })}
          </button>
        )}
        <button type="button" className="btn btn--soft btn--sm" disabled={!ready} onClick={handlePrimary}>
          {buttons.primary === 'replace'
            ? t('pagechat.proposal_replace', { defaultValue: PROPOSAL_REPLACE_TEXT })
            : t('pagechat.proposal_add', { defaultValue: '足す' })}
        </button>
      </div>
      <p className="pagechat-proposal-hint">
        {t('pagechat.proposal_adjust_hint', { defaultValue: '直したいところを続けて書けます（例: 横軸を年に・棒にして）' })}
      </p>
    </div>
  )
}

function ProposalView({ proposal, result, t }: { proposal: ConverseProposal; result: CardToolResult; t: Translate }) {
  const rows = result.items
  const view = applyPresentation(
    defaultViewFor(
      { name: 'set_measure', title: proposal.title, output_kind: result.output_kind, item: withFieldLabels('set_measure', result.item, t) },
      rows,
    ),
    proposal.presentation,
  )
  if (view.lang === 'vega-lite') return <VegaLiteView spec={view.spec as VegaLiteSpec} ariaLabel={proposal.title} height={220} />
  if (view.lang === 'table') return <TableView spec={view.spec as TableSpec} rows={rows} ariaLabel={proposal.title} />
  return null
}

/** `kind: 'view'` の提案のプレビュー（契約メモ contract_pr_f13.md §1 実装 (2)）。
 *  `source_card_id` のカードは、`pageSummary.cards`（既定カードも足したカード
 *  も両方含む・PR F13 穴埋め）→ いま足してあるカード（`cardStore.useCards`）
 *  の順で解決する（`resolveViewSourceCard`）。どちらにも無ければ「見つかりま
 *  せんでした」に倒れる。 */
function ViewProposalPreview({
  subject,
  subjectKey,
  pageSummary,
  proposal,
  precedingQuestion,
  boundCardId,
  decision,
  t,
  onAdd,
  onAddAsNew,
  onReplace,
  onDiscard,
}: {
  subject: CardRunSubject
  subjectKey: string
  pageSummary: PageChatPageSummary
  proposal: ConverseProposal
  precedingQuestion: string | undefined
  boundCardId: string | null
  /** {@link ProposalPreview} と同じ — 呼び出し側がすでに `!decision` で
   *  ガードしているので常に `undefined` だが、`proposalButtonsFor` の唯一の
   *  決着チェック経路として素通しする。 */
  decision: ProposalDecision | undefined
  t: Translate
  onAdd: (card: CardSpec, reaskQuestion?: string) => void
  onAddAsNew: (card: CardSpec, reaskQuestion?: string) => void
  onReplace: (card: CardSpec, reaskQuestion?: string) => void
  onDiscard: () => void
}) {
  const view = proposal.view
  const addedCards = useCards(subjectKey)
  const sourceCard = view ? resolveViewSourceCard(view.source_card_id, pageSummary.cards, addedCards) : undefined

  const depKey = sourceCard ? `${sourceCard.card_id}\u0000${JSON.stringify(sourceCard.params)}` : ''
  const [runState, setRunState] = useState<{ key: string; result: CardToolResult | null; error: boolean }>({
    key: '',
    result: null,
    error: false,
  })
  useEffect(() => {
    if (!sourceCard) return
    let cancelled = false
    runCard(subject, sourceCard.tool, sourceCard.params)
      .then((res) => {
        if (!cancelled) setRunState({ key: depKey, result: res, error: false })
      })
      .catch(() => {
        if (!cancelled) setRunState({ key: depKey, result: null, error: true })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depKey, !!sourceCard])

  if (!view) return null
  if (!sourceCard) {
    return (
      <div className="pagechat-proposal">
        <p className="ds-empty-note">{t('pagechat.view_source_missing', { defaultValue: VIEW_SOURCE_MISSING_TEXT })}</p>
        <div className="pagechat-proposal-actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={onDiscard}>
            {t('pagechat.proposal_discard', { defaultValue: 'やめる' })}
          </button>
        </div>
      </div>
    )
  }

  const result = runState.key === depKey ? runState.result : null
  const error = runState.key === depKey && runState.error
  const rows = result?.items ?? []
  const rendered = result ? renderCustomView(view, rows) : null
  const ready = !!result && !!rendered

  function buildSpec(): CardSpec | null {
    if (!ready || !sourceCard || !view) return null
    // `custom: true` はここで初めて立てる — サーバの `<proposal>` の `view`
    // （{@link ConverseProposalView}）自体は持たない（契約メモ §1 決定 3・4）。
    const savedView: CardView = { ...view, custom: true }
    // 元カードが既定カード（`pageSummary.cards` 由来）のときは `params`/
    // `output_kind` が `MeasureCardParams`/`MeasureShape` の形をしているとは
    // 限らない（宣言ツール／組み込みツールの汎用の形）— `CardSpec` はどんな
    // `tool` の元カードも保存できる契約（穴埋め §1・cardsApi.ts の CardSpec.tool
    // コメント参照）なので、ここでその形へ素通しする。
    return {
      card_id: viewCardId(sourceCard.card_id, savedView),
      subject_key: subjectKey,
      tool: sourceCard.tool,
      params: sourceCard.params as MeasureCardParams,
      title: sourceCard.title,
      output_kind: sourceCard.output_kind as MeasureShape,
      created_at: new Date().toISOString(),
      view: savedView,
    }
  }

  function handlePrimary() {
    const spec = buildSpec()
    if (!spec) return
    const reask = reaskQuestionFor(proposal, precedingQuestion)
    if (boundCardId) onReplace(spec, reask)
    else onAdd(spec, reask)
  }

  function handleAddAsNew() {
    const spec = buildSpec()
    if (!spec) return
    onAddAsNew(spec, reaskQuestionFor(proposal, precedingQuestion))
  }

  const buttons = proposalButtonsFor(boundCardId, decision)
  if (!buttons) return renderOutcomeNote(decision, t)

  return (
    <div className="pagechat-proposal">
      <p className="pagechat-proposal-title">{t('pagechat.view_preview_title', { defaultValue: 'この見せ方を足しますか？' })}</p>
      <p className="pagechat-proposal-hint">{t('pagechat.view_source', { title: sourceCard.title, defaultValue: `元のカード: ${sourceCard.title}` })}</p>
      {error && <p className="ds-empty-note">{t('render_error')}</p>}
      {!error && !ready && <p className="ds-empty-note">{t('page.loading')}</p>}
      {!error && result && !rendered && <p className="ds-empty-note">{t('render_error')}</p>}
      {rendered && <CustomViewRendered rendered={rendered} rows={rows} ariaLabel={sourceCard.title} />}
      <div className="pagechat-proposal-actions">
        <button type="button" className="btn btn--ghost btn--sm" onClick={onDiscard}>
          {t('pagechat.proposal_discard', { defaultValue: 'やめる' })}
        </button>
        {buttons.primary === 'replace' && (
          <button type="button" className="btn btn--ghost btn--sm" disabled={!ready} onClick={handleAddAsNew}>
            {t('pagechat.proposal_add_as_new', { defaultValue: PROPOSAL_ADD_AS_NEW_TEXT })}
          </button>
        )}
        <button type="button" className="btn btn--soft btn--sm" disabled={!ready} onClick={handlePrimary}>
          {buttons.primary === 'replace'
            ? t('pagechat.proposal_replace', { defaultValue: PROPOSAL_REPLACE_TEXT })
            : t('pagechat.proposal_add', { defaultValue: '足す' })}
        </button>
      </div>
    </div>
  )
}

function CustomViewRendered({
  rendered,
  rows,
  ariaLabel,
}: {
  rendered: NonNullable<ReturnType<typeof renderCustomView>>
  rows: Row[]
  ariaLabel: string
}) {
  if (rendered.kind === 'graph') return <GraphView graph={rendered.graph} ariaLabel={ariaLabel} maxHeight={220} compact />
  const { view } = rendered
  if (view.lang === 'vega-lite') return <VegaLiteView spec={view.spec as VegaLiteSpec} ariaLabel={ariaLabel} height={220} />
  if (view.lang === 'table') return <TableView spec={view.spec as TableSpec} rows={rows} ariaLabel={ariaLabel} />
  return null
}
