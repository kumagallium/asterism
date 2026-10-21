import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { openProposalCount } from '../consult/workspaceChatState'
import { registerWorkspaceChat } from '../consult/workspaceChatActive'
import { useSharedInput } from '../kantan/hostedStage'
import { useLlmSettings } from '../settings/context'
import { conceptName, defaultLabel } from '../themes/themeDraft'
import { GraphChat } from '../workspace/GraphChat'
import { DraftCard, ProposalCard } from '../workspace/ProposalCard'
import { relId } from '../workspace/workspaceState'
import { relationSentenceOf } from '../workspace/workspaceText'
import { SuggestCard } from './QuestionSuggest'
import { startQuestionHash } from './skyRoute'
import { AnswerLines, PlaceBar, ReadStop, SessionTools, WriteForm } from './SessionBlocks'
import type { HomeWorkspace } from './useHomeWorkspace'

// ホームの左の会話（常設・幅 360px・たためる。375px は下からのシート）。契約 pr8b §1・§2。
//   先頭に最初の一手のチップ 2 つ（＋ データを置く／？ 問いを書く）→ 一言 → 提案カード → 「図に入れる」。
//   下書き（テーマ）が開いているあいだは、星図の上の帯に提案が薄く重なり、会話の下に「N 件を置く」が固定される。
//   星や線を選ぶと、会話の先頭に札が差し込まれる（`pinned`。SkyBody が組む）。
//   AI が未設定のときは会話の入力欄を閉じ、チップと札だけで進める。

export function HomeConversation({
  ws,
  lang,
  pinned,
  open,
  onOpen,
  onFold,
  selectedKey,
  selectedEdge,
  onSelectEdge,
  unlinkedKinds,
  starSelected,
  onAddData,
  prefill,
  focusTick,
}: {
  ws: HomeWorkspace
  lang: 'ja' | 'en'
  /** 選んだ星・札・線の札（先頭に差し込む）。 */
  pinned: ReactNode
  open: boolean
  onOpen: () => void
  onFold: () => void
  selectedKey: string | null
  selectedEdge: string | null
  onSelectEdge: (id: string | null) => void
  /** 用語にまだ結ばれていない種類の数（「AI に結ばせる」の件数）。 */
  unlinkedKinds: number
  /** 星（種類）を選んでいる。一言の送り先の既定は「その星について」になり、欄の上に切替が出る。 */
  starSelected: boolean
  onAddData: () => void
  /** 入力欄に入れる文（質問するから持ち込まれた題）。 */
  prefill: { id: number; text: string } | null
  /** 値が増えるたびに入力欄へフォーカスする。 */
  focusTick: number
}) {
  const { t } = useTranslation()
  const tf = t as unknown as (k: string, o?: Record<string, unknown>) => string
  const { isReady, openSettings } = useLlmSettings()
  const [closing, setClosing] = useState(false)
  // 入力欄の下書きは、データを置く間の会話の欄と共有する（左の欄が差し替わっても未送信の文字が残る）
  const [input, setInput] = useSharedInput()
  // ホームは自分の欄に会話を持つ — 右下の「AI に相談」は出さない（会話が 2 つにならない）
  useEffect(() => registerWorkspaceChat(), [])

  const draft = ws.draft
  const idle = !ws.sessionOn
  const waiting = useMemo(() => openProposalCount(ws.items) + ws.thin, [ws.items, ws.thin])

  // 下書きカードの中身（まだ提案のままの概念・関係）
  const draftCardConcepts = draft
    ? ws.pending.concepts.flatMap((k) => {
        const c = draft.concepts.find((r) => r.key === k)
        return c ? [{ key: k, name: conceptName(c, lang), kind: c.kind }] : []
      })
    : []
  const draftCardRelations = draft
    ? ws.pending.relations.flatMap((id) => {
        const index = draft.relations.findIndex((r) => relId(r) === id)
        const r = draft.relations[index]
        return r ? [{ id, index, sentence: relationSentenceOf(draft, r, lang, tf), reason: r.reason }] : []
      })
    : []

  const bubbles = ws.items.map((it) => {
    if (it.kind === 'user') {
      return (
        <div key={it.id} className="consult-msg consult-msg--user">
          <div className="consult-bubble">{it.text}</div>
        </div>
      )
    }
    if (it.kind === 'ai' || it.kind === 'note' || it.kind === 'error') {
      return (
        <div key={it.id} className="consult-msg consult-msg--assistant">
          <div
            className={`consult-bubble${it.kind === 'error' ? ' consult-bubble--error' : ' thm-bubble-ai'}`}
            role={it.kind === 'error' ? 'alert' : undefined}
          >
            {it.text}
          </div>
        </div>
      )
    }
    if (it.kind === 'proposal') {
      return (
        <ProposalCard
          key={it.id}
          item={it}
          disabled={ws.busy}
          onYes={() => ws.yesRows(it)}
          onNo={() => ws.noRows(it)}
          onPer={() => ws.perRows(it)}
          onRowYes={(i) => ws.yesRow(it, i)}
          onRowNo={(i) => ws.noRow(it, i)}
        />
      )
    }
    if (it.kind === 'draft' && draft && ws.first) {
      return (
        <div key={it.id} className="sky-draft-block">
          <DraftCard
            conceptNames={draftCardConcepts}
            relationSentences={draftCardRelations}
            per={it.per}
            total={ws.first}
            left={ws.thin}
            onYesAll={ws.draftYesAll}
            onPer={() => ws.draftPer(it.id)}
            onRowYes={(row) => (row.type === 'concept' ? ws.acceptConcept(row.key) : ws.acceptRelation(row.id))}
            onRowNo={(row) => (row.type === 'concept' ? void ws.removeConceptRow(row.key) : void ws.removeRelationRow(row.index))}
          />
        </div>
      )
    }
    if (it.kind === 'answers' && draft) return <AnswerLines key={it.id} ws={ws} draft={draft} lang={lang} />
    if (it.kind === 'suggest') {
      // 「この問いで始める」→ 問いを書く道へ（`#/home?write=1&q=`）。開いている下書きがあれば、ホームが確かめてから切り替える
      return (
        <SuggestCard
          key={it.id}
          item={it}
          onStart={(q) => {
            window.location.hash = startQuestionHash(q)
          }}
          onClose={() => ws.closeSuggest(it.id)}
        />
      )
    }
    return null
  })

  const chips = (
    <div className="sky-chips" key="chips" data-testid="first-chips">
      <button type="button" className="btn btn--primary btn--sm" onClick={onAddData} data-testid="chip-data">
        {t('sky:chips.data')}
      </button>
      <button
        type="button"
        className="btn btn--activity btn--sm"
        aria-pressed={ws.writing}
        onClick={() => (ws.writing ? ws.cancelWriting() : ws.requestWriting())}
        data-testid="chip-question"
      >
        {t('sky:chips.question')}
      </button>
    </div>
  )
  // 最初の一手のチップは、下書きのセッション中も出す（「？ 問いを書く」は、いまの下書きをとっておくか確かめてから切り替える）
  const history: ReactNode[] = [chips, ...bubbles]

  // ── 先頭の札 ──
  const sessionBar = draft ? (
    <div className="sky-session-bar" key="session">
      <span className="sky-session-name" title={draft.label}>
        {draft.label.trim() || defaultLabel(draft.questions)}
      </span>
      {closing ? (
        <span className="sky-session-closing" role="group" aria-label={t('themes:wizard.closeAsk')}>
          <button type="button" className="btn btn--sm" onClick={() => void ws.closeSession(true).then(() => setClosing(false))}>
            {t('themes:wizard.keepClose')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void ws.closeSession(false).then(() => setClosing(false))}>
            {t('themes:wizard.discardClose')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setClosing(false)}>
            {t('themes:wizard.keepGoing')}
          </button>
        </span>
      ) : (
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setClosing(true)} data-testid="session-close">
          {t('themes:wizard.close')}
        </button>
      )}
    </div>
  ) : null
  const switchCard = ws.switchAsk ? (
    <section className="sky-card sky-card--session" role="group" aria-label={t('sky:switch.aria')} key="switch" data-testid="switch-ask">
      <p className="sky-hint">{t(ws.switchAsk.kind === 'open' ? 'sky:switch.open' : 'sky:switch.write')}</p>
      <div className="sky-about-actions">
        <button type="button" className="btn btn--primary btn--sm" onClick={() => void ws.answerSwitch(true)} data-testid="switch-yes">
          {t('sky:switch.keep')}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => void ws.answerSwitch(false)} data-testid="switch-no">
          {t('sky:switch.stay')}
        </button>
      </div>
    </section>
  ) : null
  const writeForm = ws.writing && !ws.sessionOn && !isReady ? <WriteForm key="write" ws={ws} initial={prefill?.text ?? ''} placeholder={t('sky:chat.writePlaceholder')} /> : null
  // 先頭の札は 1 枚だけ（履歴が狭くならないように）。優先: 切り替えの確認 > 選んだ星・線・札 > 読めなかった／AI 未設定の案内 >
  // 書き込み欄。下書きの名前と「閉じる」の 1 行（sessionBar）はいつも出る。
  const readStop = ws.read.status === 'failed' || ws.read.status === 'unconfigured' ? <ReadStop key="read" ws={ws} /> : null
  const card = switchCard ?? pinned ?? readStop ?? writeForm
  const allPinned =
    sessionBar || card ? (
      <>
        {sessionBar}
        {card}
      </>
    ) : null

  // 例の 1 行は、相談できるとき（AI 設定ずみで、使えると分かっている）だけ
  const focus = idle && !ws.writing && ws.aiReady ? <p className="ws-hint">{t('sky:chat.example')}</p> : null
  const tools = draft ? (
    <SessionTools ws={ws} draft={draft} lang={lang} selectedKey={selectedKey} selectedEdge={selectedEdge} onSelectEdge={onSelectEdge} />
  ) : ws.actionErr ? (
    <p className="promote-err" role="alert">
      {ws.actionErr}
    </p>
  ) : null
  const bind = isReady && idle ? (
    <button
      type="button"
      className="btn btn--sm"
      disabled={unlinkedKinds === 0 || ws.busy}
      title={t('sky:chat.bindHint', { n: unlinkedKinds })}
      onClick={ws.bindByAi}
      data-testid="bind-by-ai"
    >
      {t('sky:chat.bind')}
    </button>
  ) : null

  // 星を選んでいるときの一言の送り先は人が選ぶ（既定は「この星について」。「問いを書く」で問いの道に切り替える）
  const askStar = idle && starSelected && ws.aiReady
  const placeholder = ws.writing && idle ? t('sky:chat.writePlaceholder') : draft ? t('sky:chat.consultPlaceholder') : askStar ? t('sky:chat.starPlaceholder') : undefined
  const starToggle = askStar ? (
    <div className="sky-ask-toggle" role="group" aria-label={t('sky:chat.askAria')} data-testid="ask-toggle">
      <button type="button" className="btn btn--sm" aria-pressed={!ws.writing} onClick={ws.cancelWriting} data-testid="ask-star">
        {t('sky:chat.askStar')}
      </button>
      <button type="button" className="btn btn--sm" aria-pressed={ws.writing} onClick={() => ws.beginWriting()} data-testid="ask-question">
        {t('sky:chat.askQuestion')}
      </button>
    </div>
  ) : null

  return (
    <div className={`ws-chat sky-conv${open ? '' : ' is-folded'}`} data-testid="home-chat">
      {open ? (
        <GraphChat
          pinned={allPinned}
          focus={focus}
          tools={tools}
          bar={draft ? <PlaceBar ws={ws} draft={draft} lang={lang} /> : starToggle}
          prefill={prefill}
          aiReady={ws.aiReady}
          thinking={ws.thinking}
          locked={ws.applying}
          onSend={(text) => void ws.send(text)}
          onOpenSettings={() => openSettings('ai')}
          onFold={onFold}
          headAction={bind}
          placeholder={placeholder}
          hideEmptyHistory={false}
          focusSignal={focusTick}
          value={input}
          onValueChange={setInput}
        >
          {history}
        </GraphChat>
      ) : (
        <button type="button" className="btn ws-chat-open" onClick={onOpen} data-testid="chat-open">
          {t('themes:chat.open')}
          {waiting > 0 && <span className="thm-ws-badge">{t('themes:chat.openBadge', { n: waiting })}</span>}
        </button>
      )}
    </div>
  )
}

