// ホーム＝作業場の会話の状態（design.md §17・契約 pr8b）。左の会話と右の星図が共有する。
//
//   ・会話は一時的、図が状態の正本。AI の散文は保存しない。保存されるのは、図に入れた差分（来歴つき）と、
//     人が決めたこと。AI は SPARQL・コード・IRI を書かない／データの値を見ない／確定は人。
//   ・2 つの道を 1 つの会話にしている:
//       - 下書き（テーマ）の道: 「？ 問いを書く」→ 問いを書く → AI の下書き（星図の上の帯に薄く）→ 「図に入れる」→
//         一言の相談 → 提案 → 「図に入れる」→ 「N 件を置く」。api は `POST /api/themes/drafts`・`/draft`・
//         `PUT drafts/{id}`・`/{id}/commit`・`POST /api/consult/graph`（screen=theme）。
//       - 星図の道: 星を選んで一言（「この種類は 試料 と同じもの？」）→ 提案 → 「図に入れる」で alignment graph に線
//         （screen=home）。下書きが開いているあいだは、一言は下書きの相談になる。
//   ・提案（薄い）と本物の区別は 7b の `Pending` をそのまま使う（サーバの `pending` にも置く）。確定に送るのは本物だけ。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { dismiss } from '../decide/decideApi'
import { type ChatScreen, applyOnScreen, dismissOnScreen, proposeOnScreen, suggestQuestions } from '../consult/graphChatApi'
import { bindUnlinkedMessage, settleRows } from '../consult/workspaceChatState'
import { useLlmSettings } from '../settings/context'
import { type GraphApplyResult, type GraphOp, applyGraph, dismissGraph, proposeGraph } from '../themes/consultGraphApi'
import {
  type AnswerRow,
  type ConceptRow,
  type RelKind,
  type TermKind,
  type ThemeDraft,
  applyDraftResponse,
  applyMatches,
  buildCommitBody,
  canStart,
  conceptDismissKey,
  confirmCounts,
  defaultLabel,
  dismissEndKey,
  hasConceptNamed,
  matchSignature,
  parseQuestionLines,
  placeBlocker,
  relationPredicate,
  removeConcept,
  removeRelation,
  rowProblems,
  sentConcepts,
  startManual,
  syncAnswers,
  unlinkedKeys,
} from '../themes/themeDraft'
import {
  type CommitResult,
  ThemesApiError,
  commitTheme,
  createDraft,
  deleteDraft,
  draftWithAi,
  getDraft,
  matchTerms,
  saveDraft,
} from '../themes/themesApi'
import { type SharedTerm, listTerms } from '../vocabApi'
import { conversationPath } from './conversationContext'
import { registerDraftFlush } from './draftFlush'
import { createDraftSaver } from './draftSaver'
import {
  type CanvasModel,
  type ChatItem,
  NO_PENDING,
  type Pending,
  type ProposalItem,
  type SuggestItem,
  buildCanvas,
  dropReason,
  dropsByOriginal,
  expandMessage,
  ghostOpsOf,
  historyOf,
  newProposalId,
  newProposalItem,
  nextConceptKey,
  nextItemId,
  openRows,
  pendingOf,
  pendingToWire,
  pendingTotal,
  pruneToDraft,
  realDraft,
  relationEditSteps,
  removeRelationOp,
  addConceptOp,
  addRelationOp,
  restoreItems,
  restorePending,
  savePending,
  settlePending,
  acceptConcepts,
  acceptRelations,
  withDependencies,
} from '../workspace/workspaceState'
import { opSentence } from '../workspace/workspaceText'

/** api の 503（AI が未設定）の detail。鍵（write token）が無い 503 と見分ける。 */
const AI_UNSET = 'AI が設定されていません'

/** 直しを保存するまでの待ち（連続して打つあいだは待つ）。 */
export const SAVE_DEBOUNCE_MS = 500

type AiState = 'ok' | 'unconfigured'
export type ReadState = { status: 'idle' | 'running' | 'unconfigured' | 'failed'; detail?: string }

/** 開いている下書き（テーマ）。`null`＝下書きの道は始まっていない。 */
export interface SessionDraft {
  draft: ThemeDraft
  pending: Pending
  /** 最初の AI の下書きの大きさ（会話の 1 枚目のカード）。開き直した下書きは残りの数（`resumed`）。 */
  first: { concepts: number; relations: number; resumed?: boolean } | null
}

export interface HomeWorkspaceOptions {
  lang: 'ja' | 'en'
  /** 星図で選んでいる種類の IRI（星図の道の相談の文脈）。 */
  focusKind: string | null
  /** 星図の道で「図に入れる」が当たった（線の端の IRI。星図を取り直して輪を出す）。 */
  onHomeApplied: (iris: string[]) => void
  /** テーマを置いた（置いた概念の IRI は `minted`。星図を取り直して輪を出す）。 */
  onPlaced: (result: CommitResult) => void
  /** 下書きを閉じた・捨てた（帯の下書きを取り直す）。 */
  onDraftsChanged: () => void
}

/** 下書きを切り替える前の確認（いまの下書きをとっておくか）。 */
export type SwitchAsk = { kind: 'write'; extend: { id: string; label: string } | null } | { kind: 'open'; id: string }

function blankDraft(extend: string | undefined, label: string, questions: string[]): ThemeDraft {
  return {
    id: '',
    label,
    step: 2,
    ...(extend ? { extend } : {}),
    questions,
    concepts: [],
    relations: [],
    answers: syncAnswers([], questions),
    suggested_questions: [],
    standards: {},
    updated_at: '',
  }
}

const errText = (e: unknown): string =>
  e instanceof ThemesApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e)

export function useHomeWorkspace(o: HomeWorkspaceOptions) {
  const { t } = useTranslation()
  const tf = t as unknown as (k: string, opts?: Record<string, unknown>) => string
  const { lang } = o
  const { isReady, getActiveCredentials } = useLlmSettings()

  // ── 下書き（テーマ）の道 ──
  const [draft, setDraftState] = useState<ThemeDraft | null>(null)
  const draftRef = useRef<ThemeDraft | null>(null)
  const [pendingRaw, setPendingState] = useState<Pending>(NO_PENDING)
  const pendingRef = useRef(pendingRaw)
  const [first, setFirst] = useState<SessionDraft['first']>(null)
  const [read, setRead] = useState<ReadState>({ status: 'idle' })
  const [writing, setWriting] = useState(false)
  const [extend, setExtend] = useState<{ id: string; label: string } | null>(null)
  const [existing, setExisting] = useState<SharedTerm[]>([])
  const [saveErr, setSaveErr] = useState('')
  const [switchAsk, setSwitchAsk] = useState<SwitchAsk | null>(null)
  const [placing, setPlacing] = useState(false)
  const [placeErr, setPlaceErr] = useState('')
  // 提案（薄い）の状態を、いつ覚えさせてよいか: 開き直して読んだあと・AI の下書きを置いたあと・人が返事をしたあと
  const pendingKnown = useRef(false)
  const serverPending = useRef('')
  const matchedSigs = useRef<Record<string, string>>({})
  const startingRef = useRef(false)
  // 答え方・テーマ名・ID・確定済みの行の ✕ などの直しは、その場で（debounce）サーバの下書きに保存する。
  // 離れる前（hash が変わる前・閉じる前・別の下書きを開く前）には flush() で待つ。
  const [saver] = useState(() =>
    createDraftSaver<ThemeDraft>({
      current: () => draftRef.current,
      save: (d) => saveDraft(d),
      delay: SAVE_DEBOUNCE_MS,
      onOk: () => setSaveErr(''),
      onError: (e) => setSaveErr(errText(e)),
    }),
  )

  // ── 会話 ──
  const [items, setItems] = useState<ChatItem[]>([])
  const itemsRef = useRef(items)
  const [thinking, setThinking] = useState(false)
  const [applying, setApplying] = useState(false)
  const [ai, setAi] = useState<AiState>('ok')
  const [actionErr, setActionErr] = useState('')
  const unsaid = useRef<string[]>([])
  const focusKindRef = useRef(o.focusKind)

  useEffect(() => {
    draftRef.current = draft
  }, [draft])
  useEffect(() => {
    itemsRef.current = items
  }, [items])
  useEffect(() => {
    focusKindRef.current = o.focusKind
  }, [o.focusKind])

  // 下書きから消えた行は、提案の状態からも外す（読むときに合わせる）
  const pending = useMemo(() => (draft ? pruneToDraft(pendingRaw, draft) : pendingRaw), [pendingRaw, draft])
  useEffect(() => {
    pendingRef.current = pending
  }, [pending])
  const aiReady = isReady && ai === 'ok'
  const busy = thinking || applying

  const setPending = useCallback((f: (p: Pending) => Pending) => {
    pendingKnown.current = true
    setPendingState(f)
  }, [])
  /** 下書きを直した（その場で state・ref に反映し、500ms 後に保存する）。 */
  const edit = useCallback(
    (fn: (d: ThemeDraft) => ThemeDraft) => {
      const cur = draftRef.current
      if (!cur) return
      const next = fn(cur)
      draftRef.current = next
      setDraftState(next)
      saver.mark()
    },
    [saver],
  )
  // 画面を離れる前（hash が変わる前）に保存を済ませる合図に、flush を登録する。外れるときにも 1 度流す。
  useEffect(() => {
    const off = registerDraftFlush(() => void saver.flush())
    return () => {
      off()
      void saver.flush()
    }
  }, [saver])
  const say = useCallback((kind: 'note' | 'error' | 'ai', text: string) => {
    setItems((l) => [...l, { kind, id: nextItemId(), text }])
  }, [])

  // 既にある用語（ID の重なり・「データ N」の数）。下書きの道が始まったときに 1 度読む。
  const sessionOn = draft !== null
  useEffect(() => {
    if (!sessionOn) return
    let off = false
    listTerms()
      .then((ts) => !off && setExisting(ts))
      .catch(() => !off && setExisting([]))
    return () => {
      off = true
    }
  }, [sessionOn])
  const existingSlugs = useMemo(() => new Set(existing.map((e) => e.slug)), [existing])
  const answering = useMemo(() => Object.fromEntries(existing.map((e) => [e.slug, e.answering_datasets])), [existing])

  // 提案（薄い）の状態は、サーバの下書きと、この端末（控え）に置く（再開で続きから）
  const draftId = draft?.id ?? ''
  useEffect(() => {
    if (!draftId || !pendingKnown.current) return
    savePending(draftId, pending)
    const wire = pendingToWire(pending)
    const sig = JSON.stringify(wire)
    if (sig === serverPending.current) return
    serverPending.current = sig
    const d = draftRef.current
    if (!d) return
    saveDraft(d, { pending: wire }).catch(() => {
      serverPending.current = '' // 置けなかった — 次の変化でもう一度
    })
  }, [draftId, pending])

  // 手で足した行の名前・別名・種類が変わったら、400ms 待って機械の照合（完全一致・AI は使わない）に出す。
  const concepts = draft?.concepts
  useEffect(() => {
    if (!concepts) return
    const timer = window.setTimeout(() => {
      const sent: Record<string, string> = {}
      const batch: ConceptRow[] = []
      for (const r of concepts) {
        if (r.by !== 'human') continue
        const sig = matchSignature(r)
        if (matchedSigs.current[r.key] === sig) continue
        matchedSigs.current[r.key] = sig
        if (!r.label_ja.trim() && !r.label_en.trim() && r.aliases.length === 0) continue
        sent[r.key] = sig
        batch.push(r)
      }
      if (batch.length === 0) return
      // 仮置きの ID（t<n>）が、下書きに既にある ID と重ならないように、照合に渡す（2 回に分けても重ならない）
      const inBatch = new Set(batch.map((r) => r.key))
      const taken = concepts.filter((r) => !inBatch.has(r.key) && r.slug).map((r) => r.slug).slice(0, 400)
      matchTerms(
        batch.map((r) => ({ key: r.key, label_ja: r.label_ja, label_en: r.label_en, aliases: r.aliases, kind: r.kind })),
        taken,
      )
        .then((results) => edit((d) => ({ ...d, concepts: applyMatches(d.concepts, results, sent) })))
        .catch(() => {})
    }, 400)
    return () => window.clearTimeout(timer)
  }, [concepts, edit])

  // ── 導出 ──
  const real = useMemo(() => (draft ? realDraft(draft, pending) : null), [draft, pending])
  const ghostOps = useMemo(() => ghostOpsOf(items), [items])
  const model = useMemo<CanvasModel | null>(() => (draft ? buildCanvas(draft, pending, ghostOps) : null), [draft, pending, ghostOps])
  const unlinked = useMemo(() => (real ? unlinkedKeys(real) : new Set<string>()), [real])
  const counts = useMemo(() => (real ? confirmCounts(real) : { concepts: 0, relations: 0, total: 0 }), [real])
  const problems = useMemo(() => (real ? rowProblems(sentConcepts(real), existingSlugs) : {}), [real, existingSlugs])
  const blocker = useMemo(() => (real ? placeBlocker(real, existingSlugs) : null), [real, existingSlugs])
  const thin = pendingTotal(pending)

  // ── 保存 ──
  /** 下書きを保存する（まだ id が無ければ作る）。失敗しても進める — 置くときに id が要る。 */
  const persist = useCallback(async (next: ThemeDraft, extra?: { pending?: Pending; resetChat?: boolean }): Promise<ThemeDraft> => {
    setSaveErr('')
    await saver.idle()
    try {
      if (!next.id) {
        const created = await createDraft(next.label, next.questions, next.extend)
        next = { ...next, id: created.id, label: next.label.trim() ? next.label : created.label }
      }
      const wire = extra?.pending ? pendingToWire(extra.pending) : undefined
      const saved = await saveDraft(next, { pending: wire, resetChat: extra?.resetChat })
      if (wire) serverPending.current = JSON.stringify(wire)
      next = { ...next, updated_at: saved.updated_at }
      saver.clear() // 全部を送った
    } catch (e) {
      setSaveErr(errText(e))
    }
    draftRef.current = next
    setDraftState(next)
    return next
  }, [saver])

  // ── ① 問いを書く → AI の下書き ──
  /** 「？ 問いを書く」を押した（次の一言は問い）。`extend`＝既にあるテーマに問いを足す。 */
  const beginWriting = useCallback((ext?: { id: string; label: string } | null) => {
    setWriting(true)
    setExtend(ext ?? null)
  }, [])
  const cancelWriting = useCallback(() => {
    setWriting(false)
    setExtend(null)
  }, [])

  /** `label`＝人が付けたテーマ名（空なら AI が付ける）。 */
  async function runAi(base: ThemeDraft, label: string): Promise<void> {
    if (!isReady) {
      setRead({ status: 'unconfigured' })
      return
    }
    setRead({ status: 'running' })
    setThinking(true)
    try {
      const res = await draftWithAi(base.questions, label.trim(), lang, getActiveCredentials())
      // 読み直した下書きは新しい下書き: 古い会話（消えた行についての話）は戻さない
      const applied: ThemeDraft = { ...applyDraftResponse({ ...base, label: label.trim() }, res), chat: undefined }
      matchedSigs.current = Object.fromEntries(applied.concepts.map((r) => [r.key, matchSignature(r)]))
      setRead({ status: 'idle' })
      // 下書きを先に置いてから、全部「提案（薄い）」にする（先に薄くすると、まだ空の下書きに合わせて外される）。
      const thinAll = pendingOf(applied)
      await persist({ ...applied, step: 2 }, { pending: thinAll, resetChat: true })
      pendingKnown.current = true
      setPendingState(thinAll)
      setFirst({ concepts: applied.concepts.length, relations: applied.relations.length })
      setItems((l) => [
        ...l.filter((i) => i.kind !== 'draft' && i.kind !== 'answers'),
        { kind: 'draft', id: nextItemId(), per: false },
        { kind: 'answers', id: nextItemId() },
      ])
    } catch (e) {
      if (e instanceof ThemesApiError && e.status === 503) setRead({ status: 'unconfigured' })
      else setRead({ status: 'failed', detail: errText(e) })
    } finally {
      setThinking(false)
    }
  }

  /** 問いを書いた → 下書きを作る → AI が下書きを作る（AI 未設定なら手入力の下書き）。 */
  async function startFromText(text: string): Promise<void> {
    const questions = parseQuestionLines(text)
    if (!canStart(questions) || startingRef.current) return
    startingRef.current = true
    setActionErr('')
    setItems((l) => [...l, { kind: 'user', id: nextItemId(), text }])
    pendingKnown.current = false
    serverPending.current = ''
    setPendingState(NO_PENDING)
    setFirst(null)
    try {
      // テーマ名は AI が付ける（無ければ最初の問いの先頭 20 字）。「問いを足す」は既にあるテーマの名前のまま。
      const label = extend?.label ?? ''
      const saved = await persist(blankDraft(extend?.id, label, questions))
      setWriting(false)
      if (isReady) {
        await runAi(saved, label)
        // AI が下書きを作れなかったときも、答え方は手で選べる（会話に 1 枚足す）
        setItems((l) => (l.some((i) => i.kind === 'answers') ? l : [...l, { kind: 'answers', id: nextItemId() }]))
      } else {
        await persist(startManual({ ...saved, label: label || defaultLabel(questions) }))
        say('note', t('themes:read.unconfigured.short'))
        setItems((l) => [...l, { kind: 'answers', id: nextItemId() }])
      }
    } finally {
      startingRef.current = false
    }
  }

  /** 開き直す（`resume=<id>`・続きの下書き）。会話は保存された発言と差分から戻す。 */
  const resume = useCallback(
    async (id: string): Promise<void> => {
      try {
        const d = await getDraft(id)
        saver.clear()
        matchedSigs.current = Object.fromEntries(d.concepts.map((r) => [r.key, matchSignature(r)]))
        const left = restorePending(d)
        pendingKnown.current = true
        serverPending.current = d.pending ? JSON.stringify(pendingToWire(left)) : ''
        draftRef.current = d
        setDraftState(d)
        setPendingState(left)
        const resumed = pendingTotal(left) > 0 ? { concepts: left.concepts.length, relations: left.relations.length, resumed: true } : null
        setFirst(resumed)
        const restored = restoreItems(d.chat, (op, labels) => opSentence(d, op, lang, tf, labels))
        setItems([
          ...(resumed ? [{ kind: 'draft' as const, id: nextItemId(), per: false }] : []),
          ...(d.questions.length > 0 ? [{ kind: 'answers' as const, id: nextItemId() }] : []),
          ...restored,
        ])
        unsaid.current = []
        setWriting(false)
        setExtend(null)
        setRead({ status: 'idle' })
        setActionErr('')
        setPlaceErr('')
      } catch (e) {
        setActionErr(errText(e))
      }
    },
    [lang, tf, saver],
  )

  /** 下書きの道を終える。`keep=false` なら下書きを捨てる。 */
  async function closeSession(keep: boolean): Promise<void> {
    const d = draftRef.current
    // とっておくなら直しを保存してから。捨てるなら、待っている保存は要らない
    if (keep) await saver.flush()
    else saver.clear()
    if (d?.id && !keep) {
      try {
        await deleteDraft(d.id)
      } catch {
        /* 消せなくても閉じる（一覧に残る下書きから、もう一度捨てられる） */
      }
    }
    endSession()
    // 閉じた下書きは、星図の上の帯では開いていない下書きになる（捨てたら無くなる）— 古いまま残さない
    o.onDraftsChanged()
  }

  /** 別の下書きを開く。いま下書きが開いていれば、とっておくか確かめる（確認なしで置き換えない）。 */
  function openDraft(id: string): void {
    const cur = draftRef.current
    if (cur && cur.id === id) return
    if (cur) setSwitchAsk({ kind: 'open', id })
    else void resume(id)
  }
  /** 「？ 問いを書く」（下書きが開いていれば、とっておいて書くか確かめる）。 */
  function requestWriting(ext?: { id: string; label: string } | null): void {
    if (draftRef.current) setSwitchAsk({ kind: 'write', extend: ext ?? null })
    else beginWriting(ext)
  }
  /** 切り替えの確認の返事。「とっておく」なら閉じて（残して）から次へ。 */
  async function answerSwitch(yes: boolean): Promise<void> {
    const a = switchAsk
    setSwitchAsk(null)
    if (!yes || !a) return
    await closeSession(true)
    if (a.kind === 'open') await resume(a.id)
    else beginWriting(a.extend)
  }
  /** 開いていない下書きを捨てる（確認は呼ぶ側の札で取る）。 */
  async function discardOther(id: string): Promise<void> {
    setActionErr('')
    try {
      await deleteDraft(id)
    } catch (e) {
      setActionErr(errText(e))
    }
    o.onDraftsChanged()
  }

  function endSession(): void {
    saver.clear()
    setSwitchAsk(null)
    draftRef.current = null
    setDraftState(null)
    setPendingState(NO_PENDING)
    setFirst(null)
    setItems([])
    setRead({ status: 'idle' })
    setActionErr('')
    setPlaceErr('')
    setSaveErr('')
    setWriting(false)
    setExtend(null)
    unsaid.current = []
    pendingKnown.current = false
    serverPending.current = ''
  }

  // ── 会話: 一言を送る ──
  async function sendHome(text: string): Promise<void> {
    const ctx = focusKindRef.current ? { focus_kind: focusKindRef.current } : {}
    setActionErr('')
    const history = [...historyOf(itemsRef.current), { role: 'user' as const, content: text }]
    setItems((l) => [...l, { kind: 'user', id: nextItemId(), text }])
    setThinking(true)
    try {
      const prop = await proposeOnScreen('home', ctx, history, lang, getActiveCredentials())
      setItems((l) => [
        ...l,
        ...(prop.reply ? [{ kind: 'ai' as const, id: nextItemId(), text: prop.reply }] : []),
        ...(prop.ops.length > 0
          ? [{ ...newProposalItem({ proposalId: prop.proposal_id, ops: prop.ops, sentences: prop.sentences, by: 'ai' }), screen: 'home' as const }]
          : [{ kind: 'note' as const, id: nextItemId(), text: t('consult:workspace.noProposal') }]),
      ])
    } catch (e) {
      if (e instanceof ThemesApiError && e.status === 503 && e.detail.includes(AI_UNSET)) setAi('unconfigured')
      else say('error', t('themes:chat.failed', { detail: errText(e) }))
    } finally {
      setThinking(false)
    }
  }

  async function sendTheme(text: string): Promise<void> {
    setActionErr('')
    const history = [...historyOf(itemsRef.current), { role: 'user' as const, content: text }]
    setItems((l) => [...l, { kind: 'user', id: nextItemId(), text }])
    unsaid.current.push(text)
    setThinking(true)
    try {
      const saved = await persist(draftRef.current as ThemeDraft)
      if (!saved.id) throw new Error(t('themes:place.noDraft'))
      const prop = await proposeGraph('theme', { draft_id: saved.id }, history, lang, getActiveCredentials())
      setItems((l) => [
        ...l,
        ...(prop.reply ? [{ kind: 'ai' as const, id: nextItemId(), text: prop.reply }] : []),
        ...(prop.ops.length > 0
          ? [{ ...newProposalItem({ proposalId: prop.proposal_id, ops: prop.ops, sentences: prop.sentences, by: 'ai' }), screen: 'theme' as const }]
          : []),
      ])
    } catch (e) {
      if (e instanceof ThemesApiError && e.status === 503) setAi('unconfigured')
      else say('error', t('themes:chat.failed', { detail: errText(e) }))
    } finally {
      setThinking(false)
    }
  }

  /**
   * 「問いを提案してもらう」（PR 10a）。人が押したときだけ呼ぶ。会話にカードを 1 枚足し、返ってきたら
   * 中身を入れる。何も保存しない（カードは一時的・閉じれば消える）。下書きは触らない。
   */
  async function askQuestions(datasetId: string, label: string): Promise<void> {
    const id = nextItemId()
    const patch = (p: Partial<SuggestItem>) =>
      setItems((l) => l.map((it) => (it.kind === 'suggest' && it.id === id ? { ...it, ...p } : it)))
    setItems((l) => [...l, { kind: 'suggest', id, label, status: 'running', rows: [], detail: '' }])
    try {
      const rows = await suggestQuestions(datasetId, lang, getActiveCredentials())
      patch({ status: 'done', rows })
    } catch (e) {
      if (e instanceof ThemesApiError && e.status === 503 && e.detail.includes(AI_UNSET)) setAi('unconfigured')
      patch({ status: 'failed', detail: errText(e) })
    }
  }
  const closeSuggest = (id: string): void => setItems((l) => l.filter((it) => !(it.kind === 'suggest' && it.id === id)))

  /**
   * 人の一言。送り先は `conversationPath`（conversationContext.ts）で決まる — 下書きがあれば下書きの相談・
   * 星を選んでいれば星図の相談（「この種類は 試料 と同じもの？」。既定）・人が「問いを書く」を選んでいる、または
   * 星を選んでいなければ問い（下書きを作る）。星を選んだままの問いは、人が切替で選ぶ。
   */
  async function send(text: string): Promise<void> {
    if (busy || !text.trim()) return
    const path = conversationPath({
      adding: false,
      hosted: { recipe: null, bindReady: false },
      pick: null,
      draftOpen: !!draftRef.current,
      starSelected: !!focusKindRef.current,
      writing,
    })
    if (path === 'draft') return sendTheme(text)
    if (path === 'star') return sendHome(text)
    return startFromText(text)
  }

  /** 「AI に結ばせる」（星図の道の定型の一言）。 */
  function bindByAi(): void {
    if (busy || !aiReady || draftRef.current) return
    void sendHome(bindUnlinkedMessage(lang))
  }

  // ── 提案カードの返事 ──
  const patch = (id: string, fn: (it: ProposalItem) => ProposalItem) =>
    setItems((l) => l.map((it) => (it.kind === 'proposal' && it.id === id ? fn(it) : it)))

  /** 差分を当てる（AI の提案も人の直接編集も同じ口）。 */
  async function applyOps(ops: GraphOp[], proposalId: string, by: 'ai' | 'human', said: string[]): Promise<GraphApplyResult> {
    const saved = await persist(draftRef.current as ThemeDraft)
    if (!saved.id) throw new Error(t('themes:place.noDraft'))
    const res = await applyGraph({ context: { draft_id: saved.id }, ops, proposalId, draftedBy: by, messages: said })
    if (!res.draft) throw new ThemesApiError(t('themes:read.badShape'), 502, '')
    const next = res.draft
    draftRef.current = next
    setDraftState(next)
    const gone = new Set(res.dropped.map((d) => d.index))
    // もう当たっている再送（duplicate）は何も変えない — 薄い線は薄いまま
    const applied = res.duplicate ? [] : ops.filter((_, i) => !gone.has(i))
    if (applied.length > 0) setPending((q) => settlePending(q, applied, next))
    return res
  }

  async function proposalYes(item: ProposalItem, rows: number[]): Promise<void> {
    if (rows.length === 0 || busy) return
    setActionErr('')
    patch(item.id, (it) => ({ ...it, busy: true }))
    setApplying(true)
    try {
      if (item.screen === 'home') {
        const screen: ChatScreen = 'home'
        const ctx = focusKindRef.current ? { focus_kind: focusKindRef.current } : {}
        const sent = rows.map((i) => item.ops[i])
        const result = await applyOnScreen({ screen, context: ctx, ops: sent, proposalId: item.proposalId })
        const drops = dropsByOriginal(result.dropped, rows)
        patch(item.id, (it) => settleRows(it, rows, new Map([...drops].map(([i, d]) => [i, dropReason(d.reason)])), 'yes'))
        if (result.applied > 0 || result.duplicate) {
          o.onHomeApplied([...new Set(result.lines.flatMap((l) => [l.source, l.target]))])
        }
      } else {
        const said = [...unsaid.current]
        const res = await applyOps(rows.map((i) => item.ops[i]), item.proposalId, 'ai', said)
        unsaid.current = unsaid.current.slice(said.length)
        const drops = dropsByOriginal(res.dropped, rows)
        if (res.duplicate) for (const i of rows) if (!drops.has(i)) drops.set(i, { index: -1, op: '', reason: 'duplicate', ref: '' })
        patch(item.id, (it) => settleRows(it, rows, new Map([...drops].map(([i, d]) => [i, dropReason(d.reason)])), 'yes'))
      }
    } catch (e) {
      patch(item.id, (it) => ({ ...it, busy: false }))
      if (e instanceof ThemesApiError && e.status === 503 && (item.screen === 'home' ? e.detail.includes(AI_UNSET) : true)) setAi('unconfigured')
      setActionErr(errText(e))
    } finally {
      setApplying(false)
    }
  }

  async function proposalNo(item: ProposalItem, rows: number[]): Promise<void> {
    if (rows.length === 0 || busy) return
    setActionErr('')
    patch(item.id, (it) => ({ ...it, busy: true }))
    try {
      const sent = rows.map((i) => item.ops[i])
      if (item.screen === 'home') {
        const ctx = focusKindRef.current ? { focus_kind: focusKindRef.current } : {}
        await dismissOnScreen('home', ctx, sent)
      } else {
        await dismissGraph({ draft_id: draftRef.current?.id }, sent)
      }
      patch(item.id, (it) => settleRows(it, rows, new Map(), 'no'))
    } catch (e) {
      patch(item.id, (it) => ({ ...it, busy: false }))
      setActionErr(errText(e))
    }
  }

  const yesRows = (item: ProposalItem) => void proposalYes(item, openRows(item))
  const noRows = (item: ProposalItem) => void proposalNo(item, openRows(item))
  const perRows = (item: ProposalItem) => patch(item.id, (x) => ({ ...x, per: true }))
  const yesRow = (item: ProposalItem, i: number) => void proposalYes(item, withDependencies(item.ops, i, item.decided))
  const noRow = (item: ProposalItem, i: number) => void proposalNo(item, [i])

  // ── 最初の AI の下書きのカード ──
  const draftYesAll = () => setPending(() => ({ concepts: [], relations: [] }))
  const draftPer = (id: string) => setItems((l) => l.map((it) => (it.kind === 'draft' && it.id === id ? { ...it, per: true } : it)))

  // ── 人の直接編集（同じ形の差分で適用） ──
  /** `rollback`: あとの段が失敗したとき、すでに当たった段を戻す差分（足した線を外す — 二重の線を残さない）。 */
  async function applyHuman(steps: GraphOp[][], rollback?: GraphOp[]): Promise<boolean> {
    if (busy || steps.length === 0) return false
    setActionErr('')
    setApplying(true)
    const done: GraphOp[] = []
    let failed = false
    try {
      for (const ops of steps) {
        const res = await applyOps(ops, newProposalId(), 'human', [])
        if (res.dropped.length > 0) {
          setActionErr(t(`themes:chat.dropped.${dropReason(res.dropped[0].reason)}`))
          failed = true
          break
        }
        done.push(...ops)
      }
    } catch (e) {
      failed = true
      if (e instanceof ThemesApiError && e.status === 404) setActionErr(t('themes:chat.noDraft'))
      else setActionErr(errText(e))
    }
    if (failed && rollback && done.length > 0) {
      try {
        await applyOps(rollback, newProposalId(), 'human', [])
        done.length = 0
      } catch {
        /* 戻せなかったときは、足した線が残っていることを記録のカードで見せる */
      }
    }
    setApplying(false)
    if (done.length === 0) return false
    const after = draftRef.current as ThemeDraft
    setItems((l) => [
      ...l,
      {
        ...newProposalItem({
          proposalId: 'human',
          ops: done,
          sentences: done.map((op) => opSentence(after, op, lang, tf)),
          by: 'human',
          decided: 'yes',
        }),
        screen: 'theme' as const,
      },
    ])
    return !failed
  }

  /** 関係の A・関係・B を直す（B だけなら retarget）。ドラッグでも同じ。成功なら true。 */
  async function editRelation(index: number, next: { from: string; rel: RelKind; to: string }): Promise<boolean> {
    const old = draftRef.current?.relations[index]
    if (!old) return false
    const steps = relationEditSteps(old, next)
    // 足してから外す 2 段のとき、外すが失敗したら足した線を戻す
    return applyHuman(steps, steps.length > 1 ? [removeRelationOp(next)] : undefined)
  }

  async function addHand(h: { name: string; kind: TermKind; en: string; aliases: string }): Promise<boolean> {
    const name = h.name.trim()
    const d = draftRef.current
    if (!d || !name || hasConceptNamed(d.concepts, name) || busy) return false
    const aliases = h.aliases
      .split(/[,、]/)
      .map((s) => s.trim())
      .filter(Boolean)
    const key = nextConceptKey(d.concepts.map((c) => c.key))
    return applyHuman([[addConceptOp({ key, label_ja: name, label_en: h.en.trim(), aliases, kind: h.kind })]])
  }

  /** 手で関係を足す（A・関係・B。AI を使わない下書きの道）。成功なら true。 */
  async function addHandRelation(r: { from: string; rel: RelKind; to: string }): Promise<boolean> {
    const d = draftRef.current
    if (!d || busy || !r.from || !r.to || r.from === r.to) return false
    if (d.relations.some((x) => x.from === r.from && x.rel === r.rel && x.to === r.to)) return false
    return applyHuman([[addRelationOp(r)]])
  }

  // ── 外す（下書きだけ変える。AI の新しい概念・関係は次の下書きで出さない） ──
  async function dismissSafely(subject: string, term: string, relation?: string): Promise<void> {
    try {
      await dismiss(subject, term, relation)
    } catch (e) {
      // 却下を保存できなくても、下書きからは外す（次の下書きでまた出ることがあるだけ）
      setActionErr(errText(e))
    }
  }

  async function removeConceptRow(key: string): Promise<void> {
    const d = draftRef.current
    const row = d?.concepts.find((r) => r.key === key)
    if (!d || !row) return
    setActionErr('')
    edit((x) => removeConcept(x, key))
    if (row.by === 'ai' && row.use === 'new' && row.match === null) {
      await dismissSafely(conceptDismissKey(row.label_ja), 'concept')
    }
  }

  async function removeRelationRow(index: number): Promise<void> {
    const d = draftRef.current
    const rel = d?.relations[index]
    if (!d || !rel) return
    setActionErr('')
    edit((x) => removeRelation(x, index))
    const from = d.concepts.find((r) => r.key === rel.from)
    if (rel.by === 'ai' && from) {
      await dismissSafely(
        dismissEndKey(from, rel.from, d.standards),
        dismissEndKey(d.concepts.find((r) => r.key === rel.to), rel.to, d.standards),
        relationPredicate(rel.rel, from.kind),
      )
    }
  }

  /** 「ここを広げる」（その概念の周りを AI に提案させる）。 */
  function expand(name: string): void {
    if (!aiReady || busy) return
    void sendTheme(expandMessage(name, lang))
  }

  // ── 「全部図に入れる」の 1 件ずつ ──
  const acceptConcept = (key: string) => setPending((q) => acceptConcepts(q, [key]))
  const acceptRelation = (id: string) => setPending((q) => acceptRelations(q, [id], draftRef.current as ThemeDraft))

  // ── 答え方・テーマ名・ID の直し ──
  function updateAnswer(index: number, fn: (a: AnswerRow) => AnswerRow): void {
    edit((d) => ({ ...d, answers: d.answers.map((a, i) => (i === index ? fn(a) : a)) }))
  }
  const updateSlug = (key: string, slug: string) =>
    edit((d) => ({ ...d, concepts: d.concepts.map((c) => (c.key === key ? { ...c, slug } : c)) }))
  const setLabel = (label: string) => edit((d) => ({ ...d, label }))

  // ── 置く ──
  /** 「N 件を置く」。本物だけを送る。置けたら下書きの道を終える（星図が育つ）。 */
  async function place(): Promise<void> {
    if (placing || !draftRef.current) return
    setPlacing(true)
    setPlaceErr('')
    try {
      await saver.flush()
      const cur = draftRef.current
      const saved = cur.id ? cur : await persist(cur)
      if (!saved.id) {
        setPlaceErr(saveErr || t('themes:place.noDraft'))
        return
      }
      const body = buildCommitBody(realDraft(draftRef.current ?? saved, pendingRef.current), lang)
      const result = await commitTheme(saved.id, body)
      endSession()
      setItems([{ kind: 'note', id: nextItemId(), text: t('themes:place.done', { label: result.theme.label || body.label }) }])
      o.onPlaced(result)
    } catch (e) {
      const msg = errText(e)
      setPlaceErr(msg)
      if (e instanceof ThemesApiError && e.status === 422) setActionErr(msg)
    } finally {
      setPlacing(false)
    }
  }
  return {
    // 下書きの道
    draft,
    pending,
    first,
    read,
    writing,
    extend,
    sessionOn,
    model,
    real,
    unlinked,
    counts,
    problems,
    blocker,
    thin,
    answering,
    saveErr,
    placing,
    placeErr,
    beginWriting,
    cancelWriting,
    startFromText,
    resume,
    closeSession,
    flush: saver.flush,
    switchAsk,
    openDraft,
    requestWriting,
    answerSwitch,
    discardOther,
    retryRead: () => draftRef.current && void runAi(draftRef.current, extend?.label ?? ''),
    // 会話
    items,
    thinking,
    applying,
    busy,
    aiReady,
    actionErr,
    send,
    askQuestions,
    closeSuggest,
    bindByAi,
    yesRows,
    noRows,
    perRows,
    yesRow,
    noRow,
    draftYesAll,
    draftPer,
    acceptConcept,
    acceptRelation,
    // 直す
    editRelation,
    addHand,
    addHandRelation,
    removeConceptRow,
    removeRelationRow,
    expand,
    updateAnswer,
    updateSlug,
    setLabel,
    place,
  }
}

export type HomeWorkspace = ReturnType<typeof useHomeWorkspace>
