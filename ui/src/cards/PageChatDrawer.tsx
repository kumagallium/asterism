// ページの中で AI と会話しながら観点（グラフ）を作り・直し・聞く右ドロワー
// （契約メモ contract_pr_f12.md §1）。見た目・開閉の流儀は
// `consult/ConsultDrawer.tsx` に揃える（`pageChat.css` は Asterism 自身の
// デザイントークンを使い、consult 側の CSS はコピーしない）。
//
// consult と違い、このドロワーは呼び出し側（SubjectPage/SetPage/ClassPage —
// ui-page）に完全に制御される: 自分の FAB や履歴一覧は持たない
// （`open`/`onClose` の外部制御・スレッドは `subjectKey` ごとに 1 本）。
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLlmSettings } from '../settings/context'
import { withFieldLabels } from './builtinFields'
import type { CardRunSubject, CardSpec, CardToolResult, ConverseMessage, ConverseProposal, ConverseSubject } from './cardsApi'
import { classSchema, converse, NoLlmKeyError, runCard } from './cardsApi'
import { addCard } from './cardStore'
import { defaultViewFor } from './defaultView'
import { MEASURE_SHAPES, type MeasureSchemaLike, type Translate } from './measureCardFields'
import { NewCardForm } from './NewCardForm'
import './pageChat.css'
import {
  appendPageChatMessage,
  failPageChatAnswer,
  isPageChatThreadBusy,
  labelsFromSchema,
  latestDraft,
  pageChatThreadIdFor,
  proposalCardSpec,
  rememberPageChatThread,
  resolvePageChatAnswer,
  startPageChatThread,
  usePageChatThreads,
  type PageChatThread,
  type PageChatTurn,
} from './pageChatThreads'
import { TableView } from './TableView'
import type { TableSpec, VegaLiteSpec, ViewSpec } from './viewSpec'
import { VegaLiteView } from './VegaLiteView'

const MAX_HISTORY_TURNS = 20
const MEASURE_SHAPE_SET = new Set<string>(MEASURE_SHAPES)
const NO_KEY_TEXT = 'AI を使うには 設定 › AI でキーを入れます。フォームからは今すぐ作れます'

export interface PageChatPageSummary {
  facts: { label: string; value: string }[]
  cards: { title: string; output_kind: string; rows: Record<string, unknown>[] }[]
}

export interface PageChatDrawerProps {
  /** `converse`/表示に使う主語。種類のページ（`kind: 'class'`）は会話専用の
   *  形で、`runCard`/`NewCardForm` に渡す前に {@link toRunSubject} が
   *  `set`（`where: []`）へ変換する。 */
  subject: ConverseSubject
  /** 契約メモ §1-1 の文字列表現。種類のページは `k:<class_iri>`。 */
  subjectKey: string
  classIri?: string
  /** `NewCardForm` の型合わせ用（現時点ではフォーム内部で使わない — 詳細は
   *  `NewCardForm.tsx` のコメント参照）。無指定は空文字で渡す。 */
  datasetId?: string
  pageSummary: PageChatPageSummary
  open: boolean
  onClose: () => void
  /** ページ下部の入力欄から送られた文面。ドロワーが開いた時点で 1 度だけ
   *  自動送信する。 */
  initialMessage?: string
  onCardAdded: (card: CardSpec) => void
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

/** presentation（F3 の見せ方切替と同じ語彙）の `mark` だけを既定ビューへ
 *  上書きする最小の適用。F3 の `viewFor`/`presentation.ts` はこの作業ツリーに
 *  まだ無い（deviations 参照）— この場しのぎの局所実装で、共有モジュールが
 *  入り次第そちらに差し替える。 */
function applyPresentation(view: ViewSpec, presentation: Record<string, unknown> | null): ViewSpec {
  if (!presentation || view.lang !== 'vega-lite') return view
  const mark = presentation.mark
  if (mark === undefined) return view
  return { ...view, spec: { ...(view.spec as VegaLiteSpec), mark } }
}

export function PageChatDrawer({
  subject,
  subjectKey,
  datasetId,
  pageSummary,
  open,
  onClose,
  initialMessage,
  onCardAdded,
}: PageChatDrawerProps) {
  const { t, i18n } = useTranslation('cards')
  const { isReady, getActiveCredentials } = useLlmSettings()
  const runSubject = toRunSubject(subject)

  const threads = usePageChatThreads()
  const [threadId, setThreadId] = useState<string | null>(() => pageChatThreadIdFor(subjectKey))
  // 主語が変わったら（ドロワーが同じインスタンスのまま別のページに使い回され
  // ることは想定していないが、安全側に倒す）その主語のスレッドへ切り替え、
  // 決着（足す/やめる）の記録も忘れる。
  const [threadForKey, setThreadForKey] = useState(subjectKey)
  const [decidedTurnIds, setDecidedTurnIds] = useState<Record<string, 'added' | 'discarded'>>({})
  if (threadForKey !== subjectKey) {
    setThreadForKey(subjectKey)
    setThreadId(pageChatThreadIdFor(subjectKey))
    setDecidedTurnIds({})
  }
  const thread = threads.find((th) => th.id === threadId)
  const busy = isPageChatThreadBusy(thread)

  const [draftText, setDraftText] = useState('')
  const [noKeyForced, setNoKeyForced] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const noKey = !isReady || noKeyForced

  const decidedSet = new Set(Object.keys(decidedTurnIds))
  const currentDraft = latestDraft(thread?.turns ?? [], decidedSet)

  const scrollRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [thread?.turns.length, open])

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  async function send(overrideText?: string) {
    const text = (overrideText ?? draftText).trim()
    if (!text || busy || noKey) return
    if (overrideText === undefined) setDraftText('')
    const priorMessages = historyOf(thread)
    let activeId = threadId
    let assistantTurnId: string | undefined
    if (activeId) {
      const appended = appendPageChatMessage(activeId, text)
      assistantTurnId = appended?.assistantTurnId
    } else {
      const started = startPageChatThread(text)
      activeId = started.thread.id
      assistantTurnId = started.assistantTurnId
      setThreadId(activeId)
      rememberPageChatThread(subjectKey, activeId)
    }
    if (!activeId || !assistantTurnId) return
    try {
      const res = await converse(
        {
          subject,
          messages: [...priorMessages, { role: 'user', content: text }],
          draft: currentDraft ? { params: currentDraft.params, presentation: currentDraft.presentation } : null,
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
  // （契約メモ §1-1「その質問がスレッドの 1 通目になる」）。
  const sentInitialRef = useRef<string | null>(null)
  useEffect(() => {
    if (!open || !initialMessage || noKey) return
    if (sentInitialRef.current === initialMessage) return
    sentInitialRef.current = initialMessage
    void send(initialMessage)
    // send は draftText/thread など毎レンダー変わる値を読むので、依存は
    // 「いつ 1 回だけ送るか」を決める open/initialMessage/noKey だけに絞る
    // （NewCardForm.tsx の linkingKinds 取得 effect と同じ流儀）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialMessage, noKey])

  function decide(turnId: string, outcome: 'added' | 'discarded') {
    setDecidedTurnIds((prev) => ({ ...prev, [turnId]: outcome }))
  }

  if (!open) return null

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

        <div className="pagechat-scroll" ref={scrollRef}>
          {!thread || thread.turns.length === 0 ? (
            <p className="pagechat-empty">
              {t('pagechat.placeholder', { defaultValue: '聞きたいこと、出したいグラフ（例: 人口の推移を出して）' })}
            </p>
          ) : (
            thread.turns.map((turn) => (
              <PageChatBubble
                key={turn.id}
                turn={turn}
                subject={runSubject}
                subjectKey={subjectKey}
                decision={decidedTurnIds[turn.id]}
                t={t}
                onAdd={(card) => {
                  decide(turn.id, 'added')
                  onCardAdded(card)
                }}
                onDiscard={() => decide(turn.id, 'discarded')}
              />
            ))
          )}
        </div>

        <div className="pagechat-foot">
          {noKey ? (
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
              <form
                className="pagechat-composer"
                onSubmit={(e) => {
                  e.preventDefault()
                  void send()
                }}
              >
                <textarea
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
              <button type="button" className="pagechat-open-form" onClick={() => setShowForm((v) => !v)}>
                {t('pagechat.open_form', { defaultValue: '詳しく指定' })}
              </button>
              {showForm && (
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
              )}
            </>
          )}
        </div>
      </aside>
    </>
  )
}

function PageChatBubble({
  turn,
  subject,
  subjectKey,
  decision,
  t,
  onAdd,
  onDiscard,
}: {
  turn: PageChatTurn
  subject: CardRunSubject
  subjectKey: string
  decision: 'added' | 'discarded' | undefined
  t: Translate
  onAdd: (card: CardSpec) => void
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
  return (
    <div className="pagechat-msg pagechat-msg--assistant">
      <div className="pagechat-msg-col">
        <div className="pagechat-bubble">{reply}</div>
        {proposal && !decision && (
          <ProposalPreview subject={subject} subjectKey={subjectKey} proposal={proposal} t={t} onAdd={onAdd} onDiscard={onDiscard} />
        )}
        {proposal && decision === 'added' && (
          <p className="pagechat-added-note">{t('pagechat.added', { defaultValue: '足しました' })}</p>
        )}
      </div>
    </div>
  )
}

function ProposalPreview({
  subject,
  subjectKey,
  proposal,
  t,
  onAdd,
  onDiscard,
}: {
  subject: CardRunSubject
  subjectKey: string
  proposal: ConverseProposal
  t: Translate
  onAdd: (card: CardSpec) => void
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

  function handleAdd() {
    if (!ready || !result) return
    const labels = labelsFromSchema(schema, proposal.params)
    const spec = proposalCardSpec(proposal, labels, subjectKey, t)
    if (!spec) return
    addCard(spec)
    onAdd(spec)
  }

  return (
    <div className="pagechat-proposal">
      <p className="pagechat-proposal-title">{t('pagechat.proposal_title', { defaultValue: 'この観点を足しますか？' })}</p>
      {error && <p className="ds-empty-note">{t('render_error')}</p>}
      {!error && !ready && <p className="ds-empty-note">{t('page.loading')}</p>}
      {result && ready && <ProposalView proposal={proposal} result={result} t={t} />}
      <div className="pagechat-proposal-actions">
        <button type="button" className="btn btn--ghost btn--sm" onClick={onDiscard}>
          {t('pagechat.proposal_discard', { defaultValue: 'やめる' })}
        </button>
        <button type="button" className="btn btn--soft btn--sm" disabled={!ready} onClick={handleAdd}>
          {t('pagechat.proposal_add', { defaultValue: '足す' })}
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
