// 殻（PR 11a）: 左＝話の一覧／中央＝チャット／右＝図の 3 領域。上部バー・ナビ・ページは無い。
// hash が唯一の真実源（`#/` 新しい話・`#/t/<id>` 保存した話・`#/settings` 設定）。`navigate()` が pushState/replaceState で
// hash を書き、hashchange（戻る・進む・手入力）でも state に反映する。
// 話の状態（手番・題・テーマ・未確認の下書き）はサーバが持つ。ここは取得・状態・DOM だけで、変換は chatState.ts の純関数。
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLlmSettings } from '../settings/context'
import { ThemesApiError } from '../themes/themesApi'
import {
  confirmTurn,
  createSession,
  deleteSession,
  getSession,
  isKeyMissing,
  listSessions,
  postTurn,
  revertTurn,
  type GraphOp,
  type SetAnswerBody,
  type Session,
  type SessionSummary,
  type SkyDraft,
  type TurnInput,
} from './chatApi'
import { chatHash, legacyHashRewrite, parseChatHash, type ChatRoute } from './chatRoute'
import { applyConfirm, applyRevert, applyTurnResponse, handFormShown, nextHandKey } from './chatState'
import { ChartPane } from './ChartPane'
import { ChatPane } from './ChatPane'
import { HandForm } from './HandForm'
import { SessionList } from './SessionList'
import { useChatLayout } from './useChatLayout'
import './chat.css'

type Busy = 'send' | 'confirm' | 'revert' | null

function applyLegacyHashRewrite(): void {
  const next = legacyHashRewrite(window.location.hash)
  if (next) window.history.replaceState(null, '', next)
}

const messageOf = (e: unknown): string => (e instanceof Error ? e.message : String(e))

export function ChatApp() {
  const { t, i18n } = useTranslation('chat')
  const { t: ts } = useTranslation('settings')
  const lang: 'ja' | 'en' = i18n.language.startsWith('en') ? 'en' : 'ja'
  const { openSettings, settingsOpen, getActiveCredentials } = useLlmSettings()
  const layout = useChatLayout()

  // ── ルート ──
  const [route, setRoute] = useState<ChatRoute>(() => {
    applyLegacyHashRewrite()
    return parseChatHash(window.location.hash)
  })
  // 設定は上に重なるだけ。中身（話）は設定を開く前のまま
  const [view, setView] = useState<ChatRoute>(() => (route.kind === 'settings' ? { kind: 'new' } : route))
  const show = useCallback((r: ChatRoute) => {
    setRoute(r)
    if (r.kind !== 'settings') setView(r)
  }, [])
  const navigate = useCallback(
    (r: ChatRoute, opts?: { replace?: boolean }) => {
      const h = chatHash(r)
      if (window.location.hash !== h) {
        if (opts?.replace) window.history.replaceState(null, '', h)
        else window.history.pushState(null, '', h)
      }
      show(r)
    },
    [show],
  )
  useEffect(() => {
    const onHash = () => {
      applyLegacyHashRewrite()
      show(parseChatHash(window.location.hash))
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [show])

  // #/settings: モーダルを開き、閉じたら元の話へ戻す
  const armed = useRef(false)
  useEffect(() => {
    if (route.kind === 'settings') openSettings()
    else armed.current = false
    // openSettings は毎回作り直されるので、ルートの変化だけで回す
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.kind])
  useEffect(() => {
    if (route.kind !== 'settings') return
    if (settingsOpen) armed.current = true
    else if (armed.current) navigate(view, { replace: true })
  }, [route.kind, settingsOpen, view, navigate])

  // ── 話の一覧 ──
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [listError, setListError] = useState<Error | null>(null)
  const reloadSessions = useCallback(() => {
    listSessions()
      .then((s) => {
        setSessions(s)
        setListError(null)
      })
      .catch((e: unknown) => setListError(e instanceof Error ? e : new Error(String(e))))
  }, [])
  useEffect(() => {
    reloadSessions()
  }, [reloadSessions])

  // ── 開いている話 ──
  // 話は「どの id のものか」と一緒に持つ（別の話へ移った瞬間に古い中身が出ない・effect で state を捨てない）
  const [loaded, setLoaded] = useState<{ id: string; session: Session } | null>(null)
  const [failed, setFailed] = useState<{ id: string; message: string } | null>(null)
  const viewId = view.kind === 'session' ? view.id : null
  const session = viewId && loaded?.id === viewId ? loaded.session : null
  const loadError = viewId && failed?.id === viewId ? failed.message : null
  const loadedId = loaded?.id ?? null
  useEffect(() => {
    if (!viewId || loadedId === viewId) return
    let off = false
    getSession(viewId)
      .then((s) => {
        if (off) return
        setLoaded({ id: viewId, session: s })
        setFailed(null)
      })
      .catch((e: unknown) => {
        if (off) return
        // 無い話（消した・別の PC）は新しい話に戻す
        if (e instanceof ThemesApiError && e.status === 404) navigate({ kind: 'new' }, { replace: true })
        else setFailed({ id: viewId, message: messageOf(e) })
      })
    return () => {
      off = true
    }
  }, [viewId, loadedId, navigate])

  // 未確認の図: 手番の返りのもの（すぐ使える）と、図を取り直して届いたもの（再読み込みのあとでも拾える）
  const [turnDraft, setDraft] = useState<SkyDraft | null>(null)
  const [chartDrafts, setChartDrafts] = useState<SkyDraft[]>([])
  const [chartTick, setChartTick] = useState(0)
  // 生まれた輪は ✓ の直後だけ（手番の直後は薄い札が増えるだけ）
  const [bornTick, setBornTick] = useState(0)
  const [busy, setBusy] = useState<Busy>(null)
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [composerKey, setComposerKey] = useState(0)
  const [tab, setTab] = useState<'chat' | 'chart'>('chat')

  const afterChange = useCallback(() => {
    setChartTick((n) => n + 1)
    reloadSessions()
  }, [reloadSessions])

  const update = useCallback((id: string, fn: (s: Session) => Session) => {
    setLoaded((cur) => (cur && cur.id === id ? { id, session: fn(cur.session) } : cur))
  }, [])

  /** 手番を送る。最初の送信では話を作り、hash を `#/t/<id>` に置き換えてから手番を送る。 */
  const runTurn = useCallback(
    async (input: TurnInput): Promise<boolean> => {
      if (busy) return false
      if (view.kind === 'session' && !session) return false // 話を読み込み中
      setBusy('send')
      setError(null)
      setPendingUser(input.text ?? null)
      try {
        let s = session
        let created = false
        if (!s) {
          s = await createSession('')
          created = true
          setLoaded({ id: s.id, session: s })
          navigate({ kind: 'session', id: s.id }, { replace: true })
        }
        const id = s.id
        try {
          const res = await postTurn(id, { ...input, lang }, getActiveCredentials())
          update(id, (cur) => applyTurnResponse(cur, res))
          setDraft(res.draft)
          afterChange()
          return true
        } catch (e) {
          // 最初の送信で手番が通らなかったら、空の話は残さず新しい話へ戻す（入力欄の文は Composer が残す）
          if (created) {
            await deleteSession(id).catch(() => undefined)
            navigate({ kind: 'new' }, { replace: true })
            setLoaded(null)
            reloadSessions()
          }
          throw e
        }
      } catch (e) {
        setError(messageOf(e))
        return false
      } finally {
        setPendingUser(null)
        setBusy(null)
      }
    },
    [busy, view.kind, session, lang, getActiveCredentials, navigate, update, afterChange, reloadSessions],
  )

  const onSend = useCallback((text: string) => runTurn({ text }), [runTurn])
  const onSendOps = useCallback((ops: GraphOp[]) => runTurn({ ops }), [runTurn])
  const onSendAnswer = useCallback((set_answer: SetAnswerBody) => runTurn({ set_answer }), [runTurn])

  const onConfirm = useCallback(
    async (turnId: string) => {
      if (!session || busy) return
      const id = session.id
      setBusy('confirm')
      setError(null)
      try {
        const res = await confirmTurn(id, turnId, lang)
        update(id, (cur) => applyConfirm(cur, res))
        if (res.session.draft_id === null) setDraft(null)
        setBornTick((n) => n + 1)
        afterChange()
      } catch (e) {
        setError(messageOf(e))
      } finally {
        setBusy(null)
      }
    },
    [session, busy, lang, update, afterChange],
  )

  const onRevert = useCallback(
    async (turnId: string) => {
      if (!session || busy) return
      const id = session.id
      setBusy('revert')
      setError(null)
      try {
        const res = await revertTurn(id, turnId, lang)
        update(id, (cur) => applyRevert(cur, res))
        setDraft(res.draft)
        afterChange()
      } catch (e) {
        setError(messageOf(e))
      } finally {
        setBusy(null)
      }
    },
    [session, busy, lang, update, afterChange],
  )

  const newChat = () => {
    setComposerKey((n) => n + 1)
    setError(null)
    setDraft(null)
    navigate({ kind: 'new' })
    layout.closeSheet()
    setTab('chat')
  }
  const openSession = (id: string) => {
    setError(null)
    navigate({ kind: 'session', id })
    layout.closeSheet()
    setTab('chat')
  }

  // この話の未確認の図（手で足すフォームの材料）
  const ofSession = (d: SkyDraft | null | undefined): d is SkyDraft =>
    !!d && !!session && (d.draft_id === session.draft_id || d.session_id === session.id)
  const sessionDraft = session?.draft_id ? (chartDrafts.find(ofSession) ?? (ofSession(turnDraft) ? turnDraft : null)) : null
  const handKeys = [
    ...(sessionDraft?.concepts.map((c) => c.key) ?? []),
    ...(session?.turns.flatMap((x) => x.reply.ops.map((o) => (typeof o.key === 'string' ? o.key : ''))) ?? []),
  ]
  const showHand = !!session && handFormShown(session)
  const noKey = isKeyMissing(listError)

  const list = (
    <SessionList
      sessions={sessions}
      error={listError}
      activeId={viewId}
      folded={layout.folded}
      showToggle={!layout.tabs}
      onToggle={layout.toggleList}
      onNew={newChat}
      onOpen={openSession}
      onOpenSettings={() => openSettings()}
      onChanged={reloadSessions}
      onDeleted={(id) => {
        if (id === viewId) navigate({ kind: 'new' }, { replace: true })
      }}
    />
  )

  return (
    <div className={`chat-app${layout.tabs ? ' is-tabs' : ''}${layout.folded ? ' is-folded' : ''}`}>
      {layout.tabs && (
        <div className="chat-tabbar">
          <button type="button" className="chat-icon-btn" data-testid="list-toggle" onClick={layout.toggleList} aria-label={t('list.toggle')} title={t('list.toggle')} aria-expanded={layout.sheetOpen}>
            ≡
          </button>
          <div className="chat-tabs" role="tablist" aria-label={t('tabs.label')}>
            <button type="button" role="tab" data-testid="tab-chat" aria-selected={tab === 'chat'} className={`chat-tab${tab === 'chat' ? ' active' : ''}`} onClick={() => setTab('chat')}>
              {t('tabs.chat')}
            </button>
            <button type="button" role="tab" data-testid="tab-chart" aria-selected={tab === 'chart'} className={`chat-tab${tab === 'chart' ? ' active' : ''}`} onClick={() => setTab('chart')}>
              {t('tabs.chart')}
            </button>
          </div>
          <button type="button" className="chat-icon-btn" onClick={() => openSettings()} aria-label={ts('open')} title={ts('open')}>
            ⚙
          </button>
        </div>
      )}
      {layout.sheetOpen && <button type="button" className="chat-scrim" aria-label={t('list.close')} onClick={layout.closeSheet} />}
      <aside className={`chat-list${layout.sheetOpen ? ' is-open' : ''}`} inert={layout.tabs && !layout.sheetOpen}>{list}</aside>
      <section className="chat-center" hidden={layout.tabs && tab !== 'chat'}>
        <ChatPane
          key={composerKey}
          session={session}
          loading={!!viewId && !session && !loadError}
          loadError={loadError}
          busy={busy !== null}
          sending={busy === 'send'}
          pendingUser={pendingUser}
          error={error}
          noKey={noKey}
          onSend={onSend}
          onConfirm={(id) => void onConfirm(id)}
          onRevert={(id) => void onRevert(id)}
          onOpenSettings={() => openSettings()}
        />
      </section>
      <section className="chat-right" hidden={layout.tabs && tab !== 'chart'}>
        <div className="chat-right-scroll">
          <ChartPane lang={lang} refreshKey={chartTick} bornKey={bornTick} onProposed={setChartDrafts} />
        </div>
        {showHand && <HandForm key={session?.id} draft={sessionDraft} nextKey={nextHandKey(handKeys)} busy={busy !== null} onSubmit={onSendOps} onAnswer={onSendAnswer} />}
      </section>
    </div>
  )
}
