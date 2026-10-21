import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLlmSettings } from '../settings/context'
import { GraphChat } from '../workspace/GraphChat'
import { ProposalCard } from '../workspace/ProposalCard'
import { ThemesApiError } from '../themes/themesApi'
import type { GraphOp } from '../themes/consultGraphApi'
import {
  type ChatItem,
  type ProposalItem,
  dropReason,
  dropsByOriginal,
  ghostOpsOf,
  historyOf,
  newProposalItem,
  nextItemId,
  openRows,
  withDependencies,
} from '../workspace/workspaceState'
import {
  type ApplyOnScreenResult,
  type ChatScreen,
  type WorkspaceContext,
  applyOnScreen,
  dismissOnScreen,
  proposeOnScreen,
} from './graphChatApi'
import { openProposalCount, settleRows } from './workspaceChatState'
import { registerWorkspaceChat } from './workspaceChatActive'
import '../themes/themes.css'
import './workspaceChat.css'

// 作業場（左に図・右に会話）の会話を、ホーム（星図）とデータ（かんたん ③）に置く部品（design.md §16・
// 契約 pr7c）。7b の部品（GraphChat・ProposalCard・提案の状態）をそのまま使い、送る・当てる・却下する
// 先だけを画面（screen）で切り替える。ここは持たない: 図・星図（画面が持つ）・提案の検査（api が持つ）。
//
// 人の一言 → 提案カード（何も書かない）→ 「そう」で api の apply（home は alignment graph に線・
// dataset は upper.json）→ onApplied で画面が取り直す。「違う」は却下の保存。AI の散文は保存しない。
// AI が使えない（鍵なし・503）ときは入力欄の代わりに 1 行と「設定を開く」。

export interface WorkspaceChatProps {
  screen: ChatScreen
  /** `home` は {focus_kind?}・`dataset` は {dataset_id}。変わっても会話は続く（選んだ星が変わるだけ）。 */
  context: WorkspaceContext
  lang: string
  /** 履歴の先頭（決定論のカードなど。AI を呼ばない）。 */
  intro?: ReactNode
  /** 選んでいるものの欄（入力欄の上）。 */
  focus?: ReactNode
  /** 外から定型の一言を送る合図。`id` が変わるたびに 1 回だけ送る（「AI に結ばせる」）。 */
  request?: { id: number; text: string } | null
  /** 「そう」で当たったあと（画面が取り直す）。`ops` は当たった操作（落ちたものを除く）。 */
  onApplied?: (done: { ops: GraphOp[]; result: ApplyOnScreenResult }) => void
  /** 外のボタンが会話を開く合図（値が変わるたびに開く）。広い画面だけ最初から開き、375px は閉じて始める。 */
  openSignal?: number
  /** 見出し（相談）の右の小さな操作。 */
  headAction?: ReactNode
  /** 入力欄の placeholder（例を入れる）。 */
  placeholder?: string
  className?: string
  testId?: string
}

type AiState = 'ok' | 'unconfigured'

/** api の 503（AI が未設定）の detail。鍵（write token）が無い 503 と見分ける。 */
const AI_UNSET = 'AI が設定されていません'

function isWide(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(min-width: 721px)').matches
    : true
}

export function WorkspaceChat(p: WorkspaceChatProps) {
  const { t } = useTranslation()
  const { isReady, getActiveCredentials, openSettings } = useLlmSettings()
  const { screen, context, lang } = p
  const [items, setItems] = useState<ChatItem[]>([])
  const [thinking, setThinking] = useState(false)
  const [applying, setApplying] = useState(false)
  const [ai, setAi] = useState<AiState>('ok')
  const [actionErr, setActionErr] = useState('')
  const [open, setOpen] = useState(isWide())
  // 開いたままのときも、外の「相談」ボタンで入力欄へフォーカスする
  const [focusTick, setFocusTick] = useState(0)
  const aiReady = isReady && ai === 'ok'
  const busy = thinking || applying

  // 会話が出ているあいだは、右下の「AI に相談」を隠す（会話が 2 つにならない）
  useEffect(() => registerWorkspaceChat(), [])

  // 非同期の続きから最新の文脈を読む（選んだ星が変わっても、返ってきた提案は取り違えない）
  const contextRef = useRef(context)
  useEffect(() => {
    contextRef.current = context
  }, [context])
  const itemsRef = useRef(items)
  useEffect(() => {
    itemsRef.current = items
  }, [items])

  const errText = (e: unknown): string =>
    e instanceof ThemesApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e)
  const patch = (id: string, fn: (it: ProposalItem) => ProposalItem) =>
    setItems((l) => l.map((it) => (it.kind === 'proposal' && it.id === id ? fn(it) : it)))

  const send = useCallback(
    async (text: string) => {
      if (thinking || applying) return
      setActionErr('')
      const history = [...historyOf(itemsRef.current), { role: 'user' as const, content: text }]
      setItems((l) => [...l, { kind: 'user', id: nextItemId(), text }])
      setThinking(true)
      try {
        const prop = await proposeOnScreen(screen, contextRef.current, history, lang, getActiveCredentials())
        setItems((l) => [
          ...l,
          ...(prop.reply ? [{ kind: 'ai' as const, id: nextItemId(), text: prop.reply }] : []),
          ...(prop.ops.length > 0
            ? [newProposalItem({ proposalId: prop.proposal_id, ops: prop.ops, sentences: prop.sentences, by: 'ai' })]
            : [{ kind: 'note' as const, id: nextItemId(), text: t('consult:workspace.noProposal') }]),
        ])
      } catch (e) {
        if (e instanceof ThemesApiError && e.status === 503 && e.detail.includes(AI_UNSET)) setAi('unconfigured')
        else setItems((l) => [...l, { kind: 'error', id: nextItemId(), text: t('themes:chat.failed', { detail: errText(e) }) }])
      } finally {
        setThinking(false)
      }
    },
    [screen, lang, thinking, applying, getActiveCredentials, t],
  )

  // 外からの定型の一言（1 つの id につき 1 回だけ）
  const sentRequest = useRef(0)
  useEffect(() => {
    const r = p.request
    if (!r || r.id === sentRequest.current) return
    sentRequest.current = r.id
    if (!aiReady) return
    setOpen(true)
    void send(r.text)
    // send は毎回作り直されるので、合図の id だけで回す
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.request?.id])

  // 外の「AI と相談」ボタンが押された
  const sawOpen = useRef(p.openSignal ?? 0)
  useEffect(() => {
    if ((p.openSignal ?? 0) !== sawOpen.current) {
      sawOpen.current = p.openSignal ?? 0
      setOpen(true)
      setFocusTick((n) => n + 1)
    }
  }, [p.openSignal])

  async function proposalYes(item: ProposalItem, rows: number[]) {
    if (rows.length === 0 || busy) return
    setActionErr('')
    patch(item.id, (it) => ({ ...it, busy: true }))
    setApplying(true)
    try {
      const ops = rows.map((i) => item.ops[i])
      const result = await applyOnScreen({ screen, context: contextRef.current, ops, proposalId: item.proposalId })
      const drops = dropsByOriginal(result.dropped, rows)
      patch(item.id, (it) =>
        settleRows(it, rows, new Map([...drops].map(([i, d]) => [i, dropReason(d.reason)])), 'yes'),
      )
      const gone = result.dropped.map((d) => d.index)
      const applied = ops.filter((_, i) => !gone.includes(i))
      if (result.applied > 0 || result.duplicate) p.onApplied?.({ ops: applied, result })
    } catch (e) {
      patch(item.id, (it) => ({ ...it, busy: false }))
      if (e instanceof ThemesApiError && e.status === 503 && e.detail.includes(AI_UNSET)) setAi('unconfigured')
      setActionErr(errText(e))
    } finally {
      setApplying(false)
    }
  }

  async function proposalNo(item: ProposalItem, rows: number[]) {
    if (rows.length === 0 || busy) return
    setActionErr('')
    patch(item.id, (it) => ({ ...it, busy: true }))
    try {
      await dismissOnScreen(screen, contextRef.current, rows.map((i) => item.ops[i]))
      patch(item.id, (it) => settleRows(it, rows, new Map(), 'no'))
    } catch (e) {
      patch(item.id, (it) => ({ ...it, busy: false }))
      setActionErr(errText(e))
    }
  }

  const bubbles = items.map((it) => {
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
          disabled={busy}
          bind={screen === 'dataset'}
          onYes={() => void proposalYes(it, openRows(it))}
          onNo={() => void proposalNo(it, openRows(it))}
          onPer={() => patch(it.id, (x) => ({ ...x, per: true }))}
          onRowYes={(i) => void proposalYes(it, withDependencies(it.ops, i, it.decided))}
          onRowNo={(i) => void proposalNo(it, [i])}
        />
      )
    }
    return null
  })
  const history: ReactNode[] = [...(p.intro ? [<div key="intro">{p.intro}</div>] : []), ...bubbles]

  const waiting = useMemo(() => openProposalCount(items), [items])
  const ghost = useMemo(() => ghostOpsOf(items), [items])

  return (
    <div className={`ws-chat ${p.className ?? ''}`} data-testid={p.testId ?? 'workspace-chat'} data-ghost={ghost.length}>
      {open ? (
        <GraphChat
          focus={p.focus ?? null}
          tools={
            actionErr ? (
              <p className="promote-err" role="alert">
                {actionErr}
              </p>
            ) : null
          }
          aiReady={aiReady}
          thinking={thinking}
          locked={applying}
          onSend={(text) => void send(text)}
          onOpenSettings={() => openSettings('ai')}
          onFold={() => setOpen(false)}
          headAction={p.headAction}
          placeholder={p.placeholder}
          hideEmptyHistory
          focusSignal={focusTick}
        >
          {history}
        </GraphChat>
      ) : (
        <button type="button" className="btn ws-chat-open" onClick={() => setOpen(true)} data-testid="chat-open">
          {t('themes:chat.open')}
          {waiting > 0 && <span className="thm-ws-badge">{t('themes:chat.openBadge', { n: waiting })}</span>}
        </button>
      )}
      {!open && actionErr && (
        <p className="promote-err" role="alert">
          {actionErr}
        </p>
      )}
    </div>
  )
}
