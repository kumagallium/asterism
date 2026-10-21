import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { openProposalCount } from '../consult/workspaceChatState'
import { registerWorkspaceChat } from '../consult/workspaceChatActive'
import { useLlmSettings } from '../settings/context'
import { conceptName, defaultLabel } from '../themes/themeDraft'
import { GraphChat } from '../workspace/GraphChat'
import { DraftCard, ProposalCard } from '../workspace/ProposalCard'
import { relId } from '../workspace/workspaceState'
import { relationSentenceOf } from '../workspace/workspaceText'
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
        onClick={() => (ws.writing ? ws.cancelWriting() : ws.beginWriting())}
        data-testid="chip-question"
      >
        {t('sky:chips.question')}
      </button>
    </div>
  )
  const history: ReactNode[] = [...(idle ? [chips] : []), ...bubbles]

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
  const writeForm = ws.writing && !ws.sessionOn && !isReady ? <WriteForm key="write" ws={ws} initial={prefill?.text ?? ''} placeholder={t('sky:chat.writePlaceholder')} /> : null
  const allPinned =
    sessionBar || writeForm || ws.read.status !== 'idle' || pinned ? (
      <>
        {sessionBar}
        {writeForm}
        {ws.read.status === 'failed' || ws.read.status === 'unconfigured' ? <ReadStop ws={ws} /> : null}
        {pinned}
      </>
    ) : null

  const focus = idle && !ws.writing ? <p className="ws-hint">{t('sky:chat.example')}</p> : null
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

  const placeholder = ws.writing && idle ? t('sky:chat.writePlaceholder') : draft ? t('sky:chat.consultPlaceholder') : undefined

  return (
    <div className={`ws-chat sky-conv${open ? '' : ' is-folded'}`} data-testid="home-chat">
      {open ? (
        <GraphChat
          pinned={allPinned}
          focus={focus}
          tools={tools}
          bar={draft ? <PlaceBar ws={ws} draft={draft} lang={lang} /> : null}
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

