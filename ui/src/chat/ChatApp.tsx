// 殻（PR 11a）: 左＝話の一覧／中央＝チャット／右＝図の 3 領域。上部バー・ナビ・ページは無い。
// hash が唯一の真実源（`#/` 新しい話・`#/t/<id>` 保存した話・`#/settings` 設定）。`navigate()` が pushState/replaceState で
// hash を書き、hashchange（戻る・進む・手入力）でも state に反映する。
// 話の状態（手番・題・テーマ・未確認の下書き）はサーバが持つ。ここは取得・状態・DOM だけで、変換は chatState.ts の純関数。
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { GearIcon, MenuIcon } from '../icons'
import { subscribeJob, type JobHandle } from '../api'
import { useLlmSettings } from '../settings/context'
import { ThemesApiError } from '../themes/themesApi'
import {
  confirmTurn,
  createSession,
  deleteSession,
  getPoint,
  getSession,
  isAiUnset,
  isKeyMissing,
  listSessions,
  postAttach,
  postTurn,
  revertTurn,
  type BindingDecision,
  type GraphOp,
  type Point,
  RevertFailedError,
  type Undone,
  type SetAnswerBody,
  type Session,
  type SessionSummary,
  type SkyDraft,
  type TurnInput,
} from './chatApi'
import { chatHash, legacyHashRewrite, parseChatHash, type ChatRoute } from './chatRoute'
import { pointQueryOf, removeOf, sameSel, thinPointOf, type Highlight, type PointSel } from './chatState'
import { applyConfirm, applyRevert, applyTurnResponse, handFormShown, nameTable, nextHandKey, preferReloaded, runningJobs } from './chatState'
import { AddPanel } from './AddPanel'
import { ChatPane } from './ChatPane'
import { HandForm } from './HandForm'
import { ContentsPanel } from './ContentsPanel'
import { isAnswerable, type SkyQuestion } from '../sky/skyApi'
import { QuestionsPanel } from './QuestionsPanel'
import { normalizeQuestion } from './questionsState'
import { contentsKeyOf } from './contentsKey'
import { useContents } from './useContents'
import { MapView as MapHost } from './map/MapView'
import { useSky } from './useSky'
import { draftNodeId, kindId, termId } from '../sky/skyLayout'
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
  const { t: tsky } = useTranslation('sky')
  const { t: tq } = useTranslation('questions')
  const lang: 'ja' | 'en' = i18n.language.startsWith('en') ? 'en' : 'ja'
  const { openSettings, settingsOpen, getActiveCredentials, isReady } = useLlmSettings()
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
  const [chartTick, setChartTick] = useState(0)
  const [busy, setBusy] = useState<Busy>(null)
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [pendingReading, setPendingReading] = useState(false)
  // 読んでいる手番（表を添えた）の進み: 手番の id → 1 行
  const [progress, setProgress] = useState<Record<string, string>>({})
  const watching = useRef(new Map<string, JobHandle>())
  // 購読を終えた手番（id → ジョブの id）。読み直しても running のままなら再購読しない
  const finished = useRef(new Map<string, string>())
  const [interrupted, setInterrupted] = useState<Record<string, boolean>>({})
  const [error, setError] = useState<string | null>(null)
  const [composerKey, setComposerKey] = useState(0)
  const [tab, setTab] = useState<'chat' | 'chart' | 'questions'>('chat')
  // 右の領域の一番上のタブ（1,440）。375 では上のタブの行（tab）が兼ねる
  const [rtab, setRtab] = useState<'graph' | 'questions'>('graph')

  // ── 点を押した 1 枚（図の点・線 → GET /api/chat/point → チャットの先頭） ──
  const [sel, setSel] = useState<PointSel | null>(null)
  const [point, setPoint] = useState<Point | null>(null)
  const [pointTick, setPointTick] = useState(0)
  const [highlight, setHighlight] = useState<Highlight | null>(null)
  /** 確認済みの手番を戻した結果（手番の id → 結果）。 */
  const [undone, setUndone] = useState<Record<string, Undone>>({})
  /** 地図で押した点の id（局所の図の焦点）。 */
  const [pickedId, setPickedId] = useState<string | null>(null)
  const onSelect = useCallback((next: PointSel | null) => {
    setHighlight(null)
    setSel((cur) => (sameSel(cur, next) ? cur : next))
    if (!next) {
      setPoint(null)
      setPickedId(null)
    } else if (layout.tabs) setTab('chat')
  }, [layout.tabs])
  const onMapSelect = useCallback(
    (next: PointSel | null, nodeId: string | null) => {
      onSelect(next)
      // onSelect は next=null で pickedId を空にする。d|（データセット）は 1 枚を出さず中身だけ変える
      setPickedId(nodeId)
    },
    [onSelect],
  )
  useEffect(() => {
    if (!sel) return
    // 薄い星（未公開）は api を呼ばず、小さな 1 枚を作る
    if (sel.kind === 'kind' && sel.thin) {
      setPoint(thinPointOf(sel))
      return
    }
    let off = false
    getPoint(pointQueryOf(sel), lang)
      .then((p) => !off && setPoint(p))
      // 取れなければ 1 枚は出さない（消えた点・壊れた返り）
      .catch(() => !off && setPoint(null))
    return () => {
      off = true
    }
  }, [sel, pointTick, lang])

  const afterChange = useCallback(() => {
    setChartTick((n) => n + 1)
    reloadSessions()
  }, [reloadSessions])

  /** 手番・✓・戻すのあとは話をサーバから読み直して置き換える（他の手番の状態や下書きが変わるため）。読み直せなければ返りのまま。 */
  const reread = useCallback(async (id: string, applied: (cur: Session) => Session) => {
    const fresh = await getSession(id).catch(() => null)
    setLoaded((cur) => (cur && cur.id === id ? { id, session: preferReloaded(applied(cur.session), fresh) } : cur))
    return fresh
  }, [])

  /** 読んでいる手番のジョブを購読する。進みの 1 行を更新し、終わったら（失敗も）話を読み直す。 */
  const watch = useCallback(
    (sessionId: string, turnId: string, jobId: string) => {
      if (watching.current.has(turnId) || finished.current.get(turnId) === jobId) return
      setInterrupted((cur) => {
        if (!cur[turnId]) return cur
        const rest = { ...cur }
        delete rest[turnId]
        return rest
      })
      const finish = () => {
        watching.current.delete(turnId)
        finished.current.set(turnId, jobId)
        setProgress((cur) => {
          const rest = { ...cur }
          delete rest[turnId]
          return rest
        })
        void reread(sessionId, (cur) => cur).then((fresh) => {
          // 読み直してもまだ読んでいる印なら、ジョブは止まっている（サーバが次の読み直しで落とす）
          if (fresh?.turns.some((x) => x.id === turnId && x.state === 'running')) setInterrupted((cur) => ({ ...cur, [turnId]: true }))
          afterChange()
        })
      }
      watching.current.set(
        turnId,
        subscribeJob(jobId, {
          onRunning: (d) => {
            if (typeof d.message === 'string' && d.message) setProgress((cur) => ({ ...cur, [turnId]: d.message as string }))
          },
          onDone: finish,
          onError: finish,
          onCancelled: finish,
        }),
      )
    },
    [reread, afterChange],
  )
  // 話を開き直したとき、読んでいる手番があればジョブを購読し直す。閉じるとき（画面を離れる）は全部閉じる
  useEffect(() => {
    if (!session) return
    for (const j of runningJobs(session.turns)) watch(session.id, j.turnId, j.jobId)
  }, [session, watch])
  useEffect(() => {
    const open = watching.current
    return () => {
      for (const h of open.values()) h.close()
      open.clear()
    }
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
          await reread(id, (cur) => applyTurnResponse(cur, res))
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
    [busy, view.kind, session, lang, getActiveCredentials, navigate, reread, afterChange, reloadSessions],
  )

  const onSend = useCallback((text: string) => runTurn({ text }), [runTurn])
  const onSendOps = useCallback((ops: GraphOp[]) => runTurn({ ops }), [runTurn])
  const onSendAnswer = useCallback((set_answer: SetAnswerBody) => runTurn({ set_answer }), [runTurn])

  // ── AI 未設定: 右の領域に「データを添える」（かんたん）を出す ──
  const addOpen = view.kind !== 'settings' && !!view.add
  const openAdd = useCallback(() => {
    navigate(view.kind === 'session' ? { kind: 'session', id: view.id, add: true } : { kind: 'new', add: true })
    setTab('chart')
  }, [view, navigate])
  const closeAdd = useCallback(() => {
    navigate(view.kind === 'session' ? { kind: 'session', id: view.id } : { kind: 'new' }, { replace: true })
  }, [view, navigate])
  // 閉じたら図を取り直す（かんたんの完了で `#/home?new=` → `#/` に転送された場合も含む）
  const prevAdd = useRef(addOpen)
  useEffect(() => {
    if (prevAdd.current && !addOpen) afterChange()
    prevAdd.current = addOpen
  }, [addOpen, afterChange])

  /** 表を添える。選んだらすぐ送り、読んでいる手番（ジョブ）を購読する。AI 未設定なら右にかんたんを出す。 */
  const onAttach = useCallback(
    async (files: File[]): Promise<boolean> => {
      if (busy) return false
      if (view.kind === 'session' && !session) return false // 話を読み込み中
      if (!isReady) {
        openAdd()
        return false
      }
      setBusy('send')
      setError(null)
      setPendingUser(files.map((f) => f.name).join('\n'))
      setPendingReading(true)
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
        let res: Awaited<ReturnType<typeof postAttach>>
        try {
          res = await postAttach(id, files, lang, getActiveCredentials())
        } catch (e) {
          // 例外のときだけ空の話を消す（201 で job_id が無いのは成功）
          if (created) {
            await deleteSession(id).catch(() => undefined)
            setLoaded(null)
            reloadSessions()
          }
          if (isAiUnset(e)) {
            // 作った話は消したので、新しい話のまま右に出す
            if (created) navigate({ kind: 'new', add: true }, { replace: true })
            else openAdd()
            setTab('chart')
            return false
          }
          if (created) navigate({ kind: 'new' }, { replace: true })
          throw e
        }
        await reread(id, (cur) => applyTurnResponse(cur, { turn: res.turn, session: res.session, draft: null }))
        if (res.job_id) watch(id, res.turn.id, res.job_id)
        afterChange()
        return true
      } catch (e) {
        setError(messageOf(e))
        return false
      } finally {
        setPendingUser(null)
        setPendingReading(false)
        setBusy(null)
      }
    },
    [busy, view.kind, session, isReady, lang, getActiveCredentials, navigate, openAdd, reread, watch, afterChange, reloadSessions],
  )

  /** 返事の中の小さな選択を決める。続きがジョブで走る（読んでいます）。 */
  const onChoose = useCallback(
    async (turnId: string, noteIndex: number, value: string) => {
      if (!session || busy) return
      const id = session.id
      setBusy('send')
      setError(null)
      try {
        const res = await postTurn(id, { choice: { turn_id: turnId, note_index: noteIndex, value }, lang }, getActiveCredentials())
        await reread(id, (cur) => applyTurnResponse(cur, res))
        if (res.job_id) watch(id, res.turn.id, res.job_id)
        afterChange()
      } catch (e) {
        setError(messageOf(e))
      } finally {
        setBusy(null)
      }
    },
    [session, busy, lang, getActiveCredentials, reread, watch, afterChange],
  )

  const onConfirm = useCallback(
    async (turnId: string, decisions?: BindingDecision[]) => {
      if (!session || busy) return
      const id = session.id
      setBusy('confirm')
      setError(null)
      try {
        const res = await confirmTurn(id, turnId, lang, decisions, getActiveCredentials())
        await reread(id, (cur) => applyConfirm(cur, res))
        if (res.session.draft_id === null) setDraft(null)
        afterChange()
      } catch (e) {
        setError(messageOf(e))
      } finally {
        setBusy(null)
      }
    },
    [session, busy, lang, getActiveCredentials, reread, afterChange],
  )

  const onRevert = useCallback(
    async (turnId: string) => {
      if (!session || busy) return
      const id = session.id
      setBusy('revert')
      setError(null)
      try {
        const res = await revertTurn(id, turnId, lang)
        if (res.undone) setUndone((cur) => ({ ...cur, [turnId]: res.undone as Undone }))
        await reread(id, (cur) => applyRevert(cur, res))
        setDraft(res.draft)
        afterChange()
      } catch (e) {
        // 戻せなかったものがあれば、その手番に赤い 1 行（「戻す」は残る）
        if (e instanceof RevertFailedError) setUndone((cur) => ({ ...cur, [turnId]: e.undone }))
        else setError(messageOf(e))
      } finally {
        setBusy(null)
      }
    },
    [session, busy, lang, reread, afterChange],
  )

  /** 1 枚の「戻す」: 手番の確認を取り消し、図・話・1 枚を取り直す。 */
  const onPointRevert = useCallback(
    async (p: Point) => {
      if (!p.revert || busy) return
      const { session_id: sid, turn_id: tid } = p.revert
      setBusy('revert')
      setError(null)
      try {
        await revertTurn(sid, tid, lang)
        if (session && session.id === sid) await reread(sid, (cur) => cur)
        afterChange()
        setPointTick((n) => n + 1)
      } catch (e) {
        setError(messageOf(e))
      } finally {
        setBusy(null)
      }
    },
    [busy, lang, session, reread, afterChange],
  )
  /** 1 枚の「戻す」（確認済みの用語・線）: 外す提案の手番を足す。✓ で確定する。 */
  const onPointRemove = useCallback(
    async () => {
      if (!sel) return
      const remove = removeOf(sel)
      if (!remove) return
      await runTurn({ remove })
    },
    [sel, runTurn],
  )
  const titleOf = useCallback((id: string) => sessions?.find((x) => x.id === id)?.title || undefined, [sessions])

  const newChat = () => {
    setSel(null)
    setPoint(null)
    setPickedId(null)
    setHighlight(null)
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

  // ── 図の読み取り（地図・局所の図・上の 1 行・手で足すフォームが同じものを見る） ──
  const { data: skyData, retry: retrySky, tick: skyTick } = useSky(chartTick, lang)
  const chartDrafts = useMemo<SkyDraft[]>(() => (skyData.status === 'ok' ? skyData.proposed : []), [skyData])
  const skyTerms = useMemo(() => (skyData.status === 'ok' ? skyData.sky.terms.map((x) => ({ slug: x.slug, label: x.label })) : []), [skyData])

  // この話の未確認の図（手で足すフォームの材料）
  const ofSession = (d: SkyDraft | null | undefined): d is SkyDraft =>
    !!d && !!session && (d.draft_id === session.draft_id || d.session_id === session.id)
  const sessionDraft = session?.draft_id ? (chartDrafts.find(ofSession) ?? (ofSession(turnDraft) ? turnDraft : null)) : null
  const handKeys = [
    ...(sessionDraft?.concepts.map((c) => c.key) ?? []),
    ...(session?.turns.flatMap((x) => x.reply.ops.map((o) => (typeof o.key === 'string' ? o.key : ''))) ?? []),
  ]
  const names = useMemo(() => nameTable(skyTerms, sessionDraft), [skyTerms, sessionDraft])
  const showHand = !!session && handFormShown(session)
  const noKey = isKeyMissing(listError)

  // ── 右の領域: 地図（基本）＋局所のパネル（補足） ──
  const okSky = skyData.status === 'ok' ? skyData : null
  const focus = useMemo(() => {
    const slugs = new Set(session?.turns.filter((x) => x.state === 'confirmed').flatMap((x) => x.commit?.minted ?? []) ?? [])
    const termIris = new Set((okSky?.sky.terms ?? []).filter((x) => slugs.has(x.slug)).map((x) => x.iri))
    return { termIris, draftIds: new Set(sessionDraft ? [sessionDraft.draft_id] : []), selectedId: pickedId }
  }, [session, okSky, sessionDraft, pickedId])
  // この話の点だけ（確認した用語と、この話の提案の札）。1 ホップや押した点の周りは入れない
  const focusIds = useMemo(() => {
    const ids = new Set<string>()
    for (const iri of focus.termIris) ids.add(termId(iri))
    for (const d of okSky?.proposed ?? []) {
      if (!focus.draftIds.has(d.draft_id)) continue
      for (const c of d.concepts) if (!c.use.startsWith('existing:')) ids.add(draftNodeId(d.draft_id, c.key))
    }
    return ids
  }, [focus, okSky])
  // 押した点の中身（ABox）: pickedId → key → fetch。sky が読み直されたら読み直す
  const attachedId = useMemo(() => [...(session?.turns ?? [])].reverse().find((x) => x.attachment)?.attachment?.dataset_id ?? null, [session])
  const contentsTarget = useMemo(() => contentsKeyOf(pickedId, okSky, session?.id ?? null, attachedId), [pickedId, okSky, session?.id, attachedId])
  const contentsKey = contentsTarget && contentsTarget !== 'unpublished' ? contentsTarget : null
  const contents = useContents(contentsKey, lang, skyTick)
  const pickedName = useMemo(() => {
    if (!pickedId) return ''
    const n = pickedId.split('|')
    if (n[0] === 't') return okSky?.sky.terms.find((x) => x.iri === n.slice(1).join('|'))?.label ?? ''
    if (n[0] === 'd') return [...(okSky?.sky.datasets ?? []), ...(okSky?.proposedDatasets ?? [])].find((d) => d.id === n[1])?.label ?? ''
    if (n[0] === 'k') {
      const iri = n.slice(2).join('|')
      for (const d of [...(okSky?.sky.datasets ?? []), ...(okSky?.proposedDatasets ?? [])]) if (d.id === n[1]) return d.kinds.find((k) => k.iri === iri)?.label ?? ''
    }
    if (n[0] === 'h') return okSky?.sky.hubs.find((h) => h.id === n[1])?.label ?? ''
    if (n[0] === 'draft') return okSky?.proposed.find((d) => d.draft_id === n[1])?.concepts.find((c) => c.key === n.slice(2).join('|'))?.label ?? ''
    return ''
  }, [pickedId, okSky])
  const removeIds = useMemo(() => new Set((okSky?.proposed ?? []).flatMap((d) => d.removals?.terms ?? []).map(termId)), [okSky])
  const highlightIds = useMemo<ReadonlySet<string> | null>(() => {
    if (!highlight || !okSky) return null
    const ids = new Set<string>()
    const iris = 'edge' in highlight ? [highlight.edge.from, highlight.edge.to] : highlight.iris
    const want = new Set(iris)
    for (const x of okSky.sky.terms) if (want.has(x.iri)) ids.add(termId(x.iri))
    if (!('edge' in highlight)) {
      for (const d of [...okSky.sky.datasets, ...okSky.proposedDatasets]) {
        if (highlight.datasetId && d.id !== highlight.datasetId) continue
        for (const k of d.kinds) if (want.has(k.iri)) ids.add(kindId(d.id, k.iri))
      }
    }
    return ids.size > 0 ? ids : null
  }, [highlight, okSky])
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const [mapH, setMapH] = useState(420)
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const apply = () => {
      if (layout.tabs) return setMapH(Math.round(window.innerHeight * 0.55))
      const h = el.clientHeight
      if (h > 0) setMapH(Math.max(260, Math.round(h * 0.6)))
    }
    apply()
    window.addEventListener('resize', apply)
    if (typeof ResizeObserver === 'undefined') return () => window.removeEventListener('resize', apply)
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => {
      window.removeEventListener('resize', apply)
      ro.disconnect()
    }
  }, [layout.tabs, addOpen, tab])

  // ── 問いのタブ（PR 11l） ──
  const rightView: 'graph' | 'questions' = layout.tabs ? (tab === 'questions' ? 'questions' : 'graph') : rtab
  const qAnswerable = okSky ? okSky.sky.questions.filter(isAnswerable).length : 0
  const qTotal = okSky?.sky.questions.length ?? 0
  const proposedQuestions = useMemo(
    () => {
      if (!sessionDraft) return []
      const own = sessionDraft.questions ?? okSky?.proposed.find((d) => d.draft_id === sessionDraft.draft_id)?.questions ?? []
      const done = new Set((okSky?.sky.questions ?? []).map((q) => normalizeQuestion(q.title)))
      return own.filter((q) => !done.has(normalizeQuestion(q)))
    },
    [okSky, sessionDraft],
  )
  /** 問いを押す: 知識グラフに切り替え、要る用語とそれに結ばれたデータの種類を光らせる。 */
  const onPickQuestion = useCallback(
    (q: SkyQuestion) => {
      const members = (okSky?.sky.terms ?? []).filter((x) => q.needs.includes(x.iri)).flatMap((x) => x.members.map((m) => m.iri))
      setHighlight({ iris: [...new Set([...q.needs, ...members])] })
      if (layout.tabs) setTab('chart')
      else setRtab('graph')
    },
    [okSky, layout.tabs],
  )
  /** 「答えて」: チャットへ送り、必ず Ask。 */
  const onAskQuestion = useCallback(
    async (text: string) => {
      const ok = await runTurn({ text, intent: 'ask' })
      // 375 は答えも失敗もチャットに出るので、送れたらチャットへ
      if (ok && layout.tabs) setTab('chat')
      return ok
    },
    [runTurn, layout.tabs],
  )

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
            <MenuIcon size={20} />
          </button>
          <div className="chat-tabs" role="tablist" aria-label={t('tabs.label')}>
            <button type="button" role="tab" data-testid="tab-chat" aria-selected={tab === 'chat'} className={`chat-tab${tab === 'chat' ? ' active' : ''}`} onClick={() => setTab('chat')}>
              {t('tabs.chat')}
            </button>
            <button type="button" role="tab" data-testid="tab-chart" aria-selected={tab === 'chart'} className={`chat-tab${tab === 'chart' ? ' active' : ''}`} onClick={() => setTab('chart')}>
              {tq('tab.graph')}
            </button>
            <button type="button" role="tab" data-testid="tab-questions" aria-selected={tab === 'questions'} className={`chat-tab${tab === 'questions' ? ' active' : ''}`} onClick={() => setTab('questions')}>
              {tq('tab.questions', { n: qAnswerable, m: qTotal })}
            </button>
          </div>
          <button type="button" className="chat-icon-btn" onClick={() => openSettings()} aria-label={ts('open')} title={ts('open')}>
            <GearIcon size={18} />
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
          sending={busy === 'send' && pendingUser !== null}
          pendingUser={pendingUser}
          pendingReading={pendingReading}
          progress={progress}
          interrupted={interrupted}
          onAttach={onAttach}
          onChoose={(turnId, i, v) => void onChoose(turnId, i, v)}
          error={error}
          noKey={noKey}
          names={names}
          onSend={onSend}
          onConfirm={(id, d) => void onConfirm(id, d)}
          onRevert={(id) => void onRevert(id)}
          onOpenSettings={() => openSettings()}
          point={sel ? point : null}
          titleOf={titleOf}
          onContents={(name) => void onSend(t('point.showText', { name }))}
          onPointRevert={(p) => void onPointRevert(p)}
          onPointRemove={() => void onPointRemove()}
          undone={undone}
          onHighlight={setHighlight}
        />
      </section>
      <section className="chat-right" hidden={layout.tabs && tab === 'chat'}>
        {!layout.tabs && !addOpen && (
          <div className="chat-rtabs" role="tablist" aria-label={tq('tab.label')}>
            <button type="button" role="tab" data-testid="tab-knowledge" aria-selected={rightView === 'graph'} className={`chat-tab${rightView === 'graph' ? ' active' : ''}`} onClick={() => setRtab('graph')}>
              {tq('tab.graph')}
            </button>
            <button type="button" role="tab" data-testid="tab-questions" aria-selected={rightView === 'questions'} className={`chat-tab${rightView === 'questions' ? ' active' : ''}`} onClick={() => setRtab('questions')}>
              {tq('tab.questions', { n: qAnswerable, m: qTotal })}
            </button>
          </div>
        )}
        {addOpen && <AddPanel onClose={closeAdd} />}
        {skyData.status === 'error' && (
          <div className="sky-placeholder chart-placeholder" role="alert">
            <span>{t('chart.failed')}</span>
            <button type="button" className="btn btn--sm" onClick={retrySky}>
              {t('chart.retry')}
            </button>
          </div>
        )}
        {skyData.status === 'loading' && (
          <div className="sky-placeholder chart-placeholder" role="status">
            {tsky('state.loading')}
          </div>
        )}
        {okSky && !addOpen && rightView === 'questions' && (
          <div className="chat-right-scroll">
            <QuestionsPanel questions={okSky.sky.questions} sky={okSky.sky} proposed={proposedQuestions} onPick={onPickQuestion} onAsk={onAskQuestion} busy={busy !== null} />
          </div>
        )}
        <div className="chat-right-scroll" ref={scrollRef} hidden={addOpen || rightView !== 'graph'}>
          {okSky && (
            <>
              <MapHost
                sky={okSky.sky}
                drafts={okSky.proposed}
                proposedDatasets={okSky.proposedDatasets}
                lang={lang}
                height={mapH}
                focusIds={focusIds}
                selectedId={pickedId}
                highlightIds={highlightIds}
                removeIds={removeIds}
                onSelect={onMapSelect}
                emptyLabel={t('map.empty')}
                ariaLabel={t('map.aria')}
                fitLabel={t('map.fit')}
              />
              <p className="chat-map-legend">{t('map.legend')}</p>
              <ContentsPanel target={contentsTarget} state={contents.state} onRetry={contents.retry} name={pickedName} onHighlight={setHighlight} />
            </>
          )}
        </div>
        {showHand && rightView === 'graph' && <HandForm key={session?.id} draft={sessionDraft} nextKey={nextHandKey(handKeys)} busy={busy !== null} onSubmit={onSendOps} onAnswer={onSendAnswer} />}
      </section>
    </div>
  )
}
