// ホーム＝作業場（UI 再設計 PR 2・PR 5 で削り、PR 8b で「左に会話・右に星図」に）。
//   ・上: 「答えられる問い N / M」を大きく 1 行
//   ・左: 会話（HomeConversation・常設 360px・たためる。375px は下からのシート）。最初の一手のチップ 2 つ
//     （＋ データを置く／？ 問いを書く）・星を選ぶと先頭に「この星について」の札・下書きの「N 件を置く」
//   ・右: 星図（canvas 2D・決定論の配置）。「…」（静かな表示・画像で保存・数で見る・中身まで見る）は図の右上。
//     下書き（提案）は上の帯に薄い札と薄い線で重なり、「図に入れる」で濃くなる。直下に凡例 1 行、その下に
//     「答えられる問い N / M・テーマを見る →」の帯。
// 「＋ データを置く」（`#/home?add=1`）のあいだは、右の領域が星図の代わりに「かんたん」の札（addView）になり、左の会話は
// AddConversation（段で送り先が決まる）に変わる。星図の状態（canvas・選択・会話の下書き）は mount したまま隠すだけ。
// 配置は skyLayout.ts（純関数）、描画は skyDraw.ts、会話の状態は useHomeWorkspace.ts。ここは取得・状態・DOM だけ。
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { isTauri } from '../desktop/updater'
import { getAlignments, type Alignment } from '../crosswalkApi'
import { MoreMenu } from '../MoreMenu'
import { SV_NS } from '../lineChoice'
import { relId } from '../workspace/workspaceState'
import { sheetNow, useSheet } from '../workspace/useNarrow'
import { ChatSlotContext } from '../kantan/hostedStage'
import { useLlmSettings } from '../settings/context'
import { AddConversation } from './AddConversation'
import { HomeConversation } from './HomeConversation'
import { ConceptCard, EdgeCard, OtherDraftCard } from './SessionCards'
import { SkyPinned } from './SkyCards'
import { SkyTables } from './SkyTables'
import { fetchSuggestions, type Suggestion } from './suggestApi'
import { irisOfKeys, overlayEdges, withoutKeys, type OverlayEdge } from './suggestModel'
import { SAMPLE_SKY } from './sample'
import { fetchDraftSky, getSky, type DraftSky, type SkyData } from './skyApi'
import { NEW_DRAFT_ID, draftSkyOf, edgeIndexOf, relOfSkyEdge, retargetKeyOf, skyEdgeId } from './skyDraft'
import {
  bornAlive,
  drawDraftOverlay,
  drawSky,
  drawSuggestions,
  formatCount,
  hitDraftEdge,
  hitHandle,
  hitTest,
  readSkyPalette,
  visiblePointLabels,
  type DraftOverlay,
  type SkyLabels,
  type SkyPalette,
} from './skyDraw'
import {
  draftEdgeId,
  layoutSky,
  neighborhood,
  neighborhoodOf,
  nodeIdsForIris,
  type SkyEdge,
  type SkyLayout,
} from './skyLayout'
import type { WriteRequest } from './skyRoute'
import { buildSkyTables } from './skyTable'
import { useHomeWorkspace } from './useHomeWorkspace'
import './sky.css'

export interface SkyViewProps {
  /** `#/home?deep=1` — 同じ場所に「中身まで見る」（値の網）を出す。 */
  deep: boolean
  onDeep: (on: boolean) => void
  /** `#/home?new=…` — 公開直後に灯す種類・用語の IRI。 */
  newIris: string[]
  /** 灯したら（1 度だけ）hash から new を外す。 */
  onNewConsumed: () => void
  /** `#/home?write=1…` — 左の会話を「問いを書く」で開く（題・続きの下書き・問いを足すテーマ）。 */
  write: WriteRequest | null
  /** 会話に渡したら（1 度だけ）hash から write を外す。 */
  onWriteConsumed: () => void
  onAddData: () => void
  /** `#/home?add=1` — 右の領域をかんたん（データを置く）にする。 */
  adding: boolean
  /** かんたんの札（WorkbenchTier）。`adding` のときだけ渡る。 */
  addView: ReactNode
  /** 右の領域の「閉じる」（星図に戻る。かんたんの途中は残る）。 */
  onCloseAdd: () => void
  onOpenKind: (iri: string) => void
  onOpenDataset: (id: string) => void
  onOpenTerm: (slug: string) => void
  onOpenQuestions: () => void
  /** 「中身まで見る」の中身（NetworkView）。 */
  deepView: ReactNode
}

const QUIET_KEY = 'asterism.sky.quiet'
const DEFAULT_WIDTH = 960

function readQuiet(): boolean {
  try {
    return localStorage.getItem(QUIET_KEY) === '1'
  } catch {
    return false
  }
}
function writeQuiet(on: boolean): void {
  try {
    localStorage.setItem(QUIET_KEY, on ? '1' : '0')
  } catch {
    /* 保存できなくても今回の表示には効く */
  }
}
function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/** 前に見たときの「答えられる問い」の数（公開した直後に「5 → 6」を 1 度だけ言うため）。 */
const ANSWERABLE_KEY = 'asterism.sky.answerable'
function readAnswerable(): number | null {
  try {
    const raw = localStorage.getItem(ANSWERABLE_KEY)
    const n = raw === null ? NaN : Number(raw)
    return Number.isFinite(n) ? n : null
  } catch {
    return null
  }
}
function writeAnswerable(n: number): void {
  try {
    localStorage.setItem(ANSWERABLE_KEY, String(n))
  } catch {
    /* 保存できなくても今回の表示には効く */
  }
}
/** 育ったことを会話の先頭に出しておく時間。 */
const GROWN_MS = 6000

type Load = { status: 'loading' } | { status: 'error' } | { status: 'ok'; sky: SkyData }

export function SkyView(props: SkyViewProps) {
  const { t, i18n } = useTranslation('sky')
  const lang: 'ja' | 'en' = i18n.language.startsWith('en') ? 'en' : 'ja'
  const [load, setLoad] = useState<Load>({ status: 'loading' })
  // まだ確かめていない下書き（認証つきの別口）。星図が読めなくても、取れた分だけで描く。
  const [drafts, setDrafts] = useState<DraftSky[]>([])
  // 公開のあとに機械が数えた「結べそうな線」（認証つき・鍵が無ければ空）。星図が読めなくても取れた分だけ使う。
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])

  const fetchSky = useCallback(() => {
    let cancelled = false
    getSky()
      .then((sky) => !cancelled && setLoad({ status: 'ok', sky }))
      .catch(() => !cancelled && setLoad({ status: 'error' }))
    void fetchDraftSky(lang).then((d) => !cancelled && setDrafts(d))
    void fetchSuggestions().then((s) => !cancelled && setSuggestions(s))
    return () => {
      cancelled = true
    }
  }, [lang])
  useEffect(() => fetchSky(), [fetchSky])
  // 選択を保ったまま取り直す（読み込み中の表示には戻さない）。
  const refresh = useCallback(() => {
    getSky()
      .then((sky) => setLoad({ status: 'ok', sky }))
      .catch(() => undefined)
    void fetchDraftSky(lang).then(setDrafts)
    void fetchSuggestions().then(setSuggestions)
  }, [lang])
  const retry = () => {
    setLoad({ status: 'loading' })
    fetchSky()
  }
  // データを置き終えて（または閉じて）星図に戻ったら、取り直す。公開で星が増えているので、「生まれた輪」（new）は
  // 新しい星図が届くまで渡さない（古い配置には灯す星が無い）。
  const adding = props.adding
  const [prevAdding, setPrevAdding] = useState(adding)
  const [stale, setStale] = useState(false)
  if (adding !== prevAdding) {
    setPrevAdding(adding)
    if (!adding) setStale(true)
  }
  useEffect(() => {
    if (!stale) return
    let off = false
    getSky()
      .then((sky) => !off && setLoad({ status: 'ok', sky }))
      .catch(() => undefined)
      .finally(() => !off && setStale(false))
    void fetchDraftSky(lang).then((d) => !off && setDrafts(d))
    void fetchSuggestions().then((s) => !off && setSuggestions(s))
    return () => {
      off = true
    }
  }, [stale, lang])

  if (props.deep && !adding) {
    return (
      <div className="sky-page">
        <div className="sky-deep-head">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => props.onDeep(false)}>
            ← {t('deepBack')}
          </button>
          <span>{t('deepLead')}</span>
        </div>
        {props.deepView}
      </div>
    )
  }

  if (load.status === 'loading') {
    return (
      <div className="sky-page">
        <div className="sky-placeholder" role="status">
          {t('state.loading')}
        </div>
      </div>
    )
  }
  // 障害を「まだデータがありません」という誤った空状態にしない。
  if (load.status === 'error') {
    // 星図が読めなくても、データを置く道は止めない（会話なし・かんたんの札だけ。③の会話は従来どおり表の右）
    if (adding) {
      return (
        <div className="sky-page">
          <AddArea onClose={props.onCloseAdd} hosted={false}>
            {props.addView}
          </AddArea>
        </div>
      )
    }
    return (
      <div className="sky-page">
        <div className="sky-placeholder" role="alert">
          <span>{t('state.failed')}</span>
          <button type="button" className="btn btn--sm" onClick={retry}>
            {t('state.retry')}
          </button>
        </div>
      </div>
    )
  }
  return (
    <SkyBody
      sky={load.sky}
      drafts={drafts}
      suggestions={suggestions}
      setSuggestions={setSuggestions}
      lang={lang}
      onRefresh={refresh}
      {...props}
      newIris={stale ? [] : props.newIris}
    />
  )
}

function SkyBody(
  props: SkyViewProps & {
    sky: SkyData
    drafts: DraftSky[]
    suggestions: Suggestion[]
    setSuggestions: (f: (s: Suggestion[]) => Suggestion[]) => void
    lang: 'ja' | 'en'
    onRefresh: () => void
  },
) {
  const { sky, lang, suggestions, adding } = props
  const { t } = useTranslation('sky')
  const { isReady } = useLlmSettings()
  // データを置いているあいだ、③の会話の置き場（左の欄の div）。DatasetChat がここへ portal する。
  const [chatSlot, setChatSlot] = useState<HTMLElement | null>(null)

  // ── 選択（星図の点・提案の線・問い） ──
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null)
  const [selectedQuestion, setSelectedQuestion] = useState<string | null>(null)
  const selectNode = useCallback((id: string | null) => {
    setSelectedId(id)
    setSelectedEdge(null)
    setSelectedQuestion(null)
  }, [])
  const selectEdge = useCallback((id: string | null) => {
    setSelectedEdge(id)
    setSelectedId(null)
    setSelectedQuestion(null)
  }, [])
  const selectQuestion = useCallback((tool: string | null) => {
    setSelectedQuestion(tool)
    setSelectedId(null)
    setSelectedEdge(null)
  }, [])

  // ── 決めたあとの輪: 取り直した星図が届いたら、その用語と星に 1 度だけ出す ──
  const growRef = useRef<{ iris: string[]; from: SkyData } | null>(null)
  const { onRefresh } = props
  const grow = useCallback(
    (iris: string[]) => {
      growRef.current = { iris, from: sky }
      onRefresh()
    },
    [sky, onRefresh],
  )

  // ── 会話（左）と下書きの道。星図の道の相談は、選んでいる星（種類）を文脈に持つ ──
  const focusKind = selectedId?.startsWith('k|') ? selectedId.split('|').slice(2).join('|') : null
  const ws = useHomeWorkspace({
    lang,
    focusKind,
    onHomeApplied: grow,
    onPlaced: (result) => grow(result.minted.map((slug) => `${SV_NS}${slug}`)),
    onDraftsChanged: onRefresh,
  })

  // 星図の上に描く下書き: 開いている下書き（本物と提案）＋ 開いていない下書き（全部提案）
  const sessionId = ws.draft ? ws.draft.id || NEW_DRAFT_ID : ''
  const drafts = useMemo<DraftSky[]>(() => {
    const others = props.drafts.filter((d) => d.draft_id !== sessionId)
    return ws.draft && ws.model ? [...others, draftSkyOf(ws.draft, ws.model, lang)] : others
  }, [props.drafts, ws.draft, ws.model, lang, sessionId])

  // 下書きだけでも図を描く（見本は出さない）
  const nothing = sky.datasets.length === 0 && sky.questions.length === 0 && drafts.length === 0
  const drawn = nothing ? SAMPLE_SKY : sky

  // ── 幅（図の列の幅に追従） ──
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(DEFAULT_WIDTH)
  useLayoutEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const apply = () => {
      const w = Math.round(el.getBoundingClientRect().width)
      if (w > 0) setWidth(w)
    }
    apply()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const layout = useMemo<SkyLayout>(() => layoutSky(drawn, { width, lang, drafts: nothing ? [] : drafts }), [drawn, width, lang, drafts, nothing])
  const tables = useMemo(() => buildSkyTables(sky, lang), [sky, lang])

  // ── 静かな表示 ──
  const [quiet, setQuiet] = useState(readQuiet)
  const [osQuiet] = useState(prefersReducedMotion)
  const reduced = quiet || osQuiet

  // 配置が作り直されても、その点が残っていれば選択を保つ。無くなったら外す。
  const activeId = selectedId && layout.nodes.has(selectedId) ? selectedId : null
  const activeNode = activeId ? layout.nodes.get(activeId) : undefined
  // 選んだ線は、下書きにまだあるときだけ（直したあと・外したあとは外れる）
  const edgeIndex = edgeIndexOf(ws.draft, selectedEdge, sessionId)
  const activeEdge = edgeIndex >= 0 ? selectedEdge : null
  // 会話の札・文の一覧は下書きの中の ID（from|rel|to）で話す。星図の線の ID は頭に draftId が付く
  const activeRel = relOfSkyEdge(activeEdge, sessionId)
  const selectRel = useCallback((rel: string | null) => selectEdge(rel === null ? null : skyEdgeId(sessionId, rel)), [selectEdge, sessionId])

  // ── 描画 ──
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const paletteRef = useRef<SkyPalette | null>(null)
  const hoverRef = useRef<string | null>(null)
  const bornRef = useRef<Map<string, number>>(new Map())
  const rafRef = useRef(0)
  const dragRef = useRef<{ edge: SkyEdge; x: number; y: number; overId: string | null } | null>(null)
  const labels = useMemo<SkyLabels>(
    () => ({
      names: t('bands.names'),
      mid: t('bands.data'),
      midCircles: t('bands.dataCircles'),
      std: t('bands.std'),
      empty: t('bands.noStar'),
      property: t('bands.property'),
      draft: t('bands.draft'),
      draftRemove: t('bands.draftRemove'),
      partOf: t('line.partOf'),
      relSub: t('line.sub'),
      relQuantity: t('line.quantity'),
      count: (n) => t('unit.count', { n: formatCount(n, lang) }),
      kinds: (n) => t('unit.kinds', { n }),
    }),
    [t, lang],
  )
  const visible = useMemo(() => visiblePointLabels(layout), [layout])

  // 問いを選んだとき: 要る用語の札（とその近傍）だけを濃く残す。
  const questionNear = useMemo(() => {
    if (!selectedQuestion) return null
    const q = sky.questions.find((x) => x.tool_name === selectedQuestion)
    if (!q) return null
    const ids = q.needs.map((iri) => `t|${iri}`).filter((id) => layout.nodes.has(id))
    return neighborhoodOf(layout, ids)
  }, [selectedQuestion, sky.questions, layout])

  // 端の丸を出す提案の線: 選んだ線と、選んだ提案の札につながる線（付け替えは線の端を持って別の札に落とす）
  const handleEdges = useMemo<SkyEdge[]>(() => {
    const out: SkyEdge[] = []
    for (const e of layout.edges) {
      const d = e.kind === 'draft' ? e.draft : undefined
      if (!d || d.index === null || (d.state !== 'thin' && d.state !== 'solid')) continue
      // 開いている下書きの線だけ（開いていない下書きの線は付け替えられない）
      if (d.draftId !== sessionId) continue
      const touches = activeNode?.type === 'draft' && (e.from === activeNode.id || e.to === activeNode.id)
      if ((activeEdge && draftEdgeId(d) === activeEdge) || touches) out.push(e)
    }
    return out
  }, [layout, activeEdge, activeNode, sessionId])

  // 選んだ線の両端だけを濃く残す
  const edgeNear = useMemo(() => {
    if (!activeEdge) return null
    const e = layout.edges.find((x) => x.kind === 'draft' && x.draft && draftEdgeId(x.draft) === activeEdge)
    return e ? new Set([e.from, e.to]) : null
  }, [layout, activeEdge])

  interface PaintState {
    layout: SkyLayout
    labels: SkyLabels
    visible: ReadonlySet<string>
    selectedId: string | null
    selectedEdge: string | null
    questionNear: ReadonlySet<string> | null
    edgeNear: ReadonlySet<string> | null
    handles: readonly SkyEdge[]
    reduced: boolean
    nothing: boolean
    /** 薄い線で重ねる「結べそうな線」（星 → 用語の札）。 */
    overlay: readonly OverlayEdge[]
  }
  const stateRef = useRef<PaintState | null>(null)

  const paint = useCallback(function paintFrame() {
    rafRef.current = 0
    const canvas = canvasRef.current
    const s = stateRef.current
    if (!canvas || !s) return
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const w = s.layout.width
    const h = s.layout.height
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
    }
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    paletteRef.current ??= readSkyPalette()
    const now = performance.now()
    const focusId = s.nothing ? null : (hoverRef.current ?? s.selectedId)
    const near = s.nothing ? null : focusId ? neighborhood(s.layout, focusId) : (s.edgeNear ?? s.questionNear)
    drawSky(
      ctx,
      s.layout,
      paletteRef.current,
      s.labels,
      {
        focusId,
        near,
        born: bornRef.current,
        now,
        reducedMotion: s.reduced,
        alpha: s.nothing ? 0.38 : 1,
        selectedEdge: s.selectedEdge,
      },
      s.visible,
    )
    if (!s.nothing) drawSuggestions(ctx, s.layout, paletteRef.current, s.overlay, focusId)
    if (!s.nothing) {
      const ov: DraftOverlay = { handles: s.handles, drag: dragRef.current }
      drawDraftOverlay(ctx, s.layout, paletteRef.current, ov)
    }
    if (!s.reduced && bornAlive(bornRef.current, now)) rafRef.current = requestAnimationFrame(paintFrame)
  }, [])
  const schedule = useCallback(() => {
    if (!rafRef.current) rafRef.current = requestAnimationFrame(paint)
  }, [paint])

  const overlay = useMemo(() => overlayEdges(layout, suggestions), [layout, suggestions])
  useEffect(() => {
    stateRef.current = {
      layout,
      labels,
      visible,
      selectedId: activeId,
      selectedEdge: activeEdge,
      questionNear,
      edgeNear,
      handles: handleEdges,
      reduced,
      nothing,
      overlay,
    }
    schedule()
  }, [layout, labels, visible, activeId, activeEdge, questionNear, edgeNear, handleEdges, reduced, nothing, overlay, schedule])
  // フォントが届いたら描き直す（札の幅が変わる）。
  useEffect(() => {
    let alive = true
    void document.fonts?.ready.then(() => {
      if (alive) schedule()
    })
    return () => {
      alive = false
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [schedule])

  // ── 育ったことの 1 行（会話の先頭に 6 秒だけ）: 答えられる問い 5 → 6 ──
  const [grown, setGrown] = useState<{ from: number; to: number } | null>(null)
  const grownTimer = useRef(0)
  const showGrown = useCallback((from: number, to: number) => {
    window.clearTimeout(grownTimer.current)
    // effect の中から同期的に setState しない（描画のあとに回す）
    grownTimer.current = window.setTimeout(() => {
      setGrown({ from, to })
      grownTimer.current = window.setTimeout(() => setGrown(null), GROWN_MS)
    }, 0)
  }, [])
  useEffect(() => () => window.clearTimeout(grownTimer.current), [])
  // 前に見たときの数（この画面を開いた時点のもの）。公開直後の「5 → 6」に使う。
  const prevAnswerable = useRef<number | null | undefined>(undefined)
  if (prevAnswerable.current === undefined) prevAnswerable.current = readAnswerable()
  const answerableNow = sky.facts.questions_answerable
  useEffect(() => {
    if (!nothing) writeAnswerable(answerableNow)
  }, [answerableNow, nothing])

  useEffect(() => {
    const g = growRef.current
    if (!g || g.from === sky) return
    growRef.current = null
    if (sky.facts.questions_answerable > g.from.facts.questions_answerable) {
      showGrown(g.from.facts.questions_answerable, sky.facts.questions_answerable)
    }
    if (reduced) return
    const now = performance.now()
    for (const id of nodeIdsForIris(layout, g.iris)) bornRef.current.set(id, now)
    schedule()
  }, [sky, layout, reduced, schedule, showGrown])

  // ── 生まれた輪（`#/home?new=…`・1 度だけ） ──
  const consumedRef = useRef<string>('')
  const { newIris, onNewConsumed } = props
  useEffect(() => {
    if (newIris.length === 0) {
      consumedRef.current = ''
      return
    }
    if (nothing) {
      // 描く星が無くても、hash に new を残さない
      onNewConsumed()
      return
    }
    const key = newIris.join('\n')
    if (consumedRef.current === key) return
    consumedRef.current = key
    const prev = prevAnswerable.current
    if (typeof prev === 'number' && answerableNow > prev) showGrown(prev, answerableNow)
    if (!reduced) {
      const now = performance.now()
      for (const id of nodeIdsForIris(layout, newIris)) bornRef.current.set(id, now)
      schedule()
    }
    onNewConsumed()
  }, [newIris, onNewConsumed, layout, reduced, nothing, schedule, answerableNow, showGrown])

  // ── 会話の開閉・「問いを書く」の合図（`#/home?write=1…`） ──
  const sheet = useSheet()
  const [chatOpen, setChatOpen] = useState(() => !sheetNow())
  // AI が未設定でも、人が左の欄を開いたなら開いておく（データを置いているあいだだけの記憶）
  const [addAsked, setAddAsked] = useState(false)
  const [prevAdding, setPrevAdding] = useState(adding)
  if (adding !== prevAdding) {
    setPrevAdding(adding)
    if (!adding) setAddAsked(false)
  }
  const firstSheet = useRef(sheet)
  useEffect(() => {
    if (firstSheet.current === sheet) return
    firstSheet.current = sheet
    setChatOpen(!sheet)
  }, [sheet])
  const [prefill, setPrefill] = useState<{ id: number; text: string } | null>(null)
  const [focusTick, setFocusTick] = useState(0)
  const { write, onWriteConsumed } = props
  const writeKey = write ? JSON.stringify(write) : ''
  useEffect(() => {
    if (!write) return
    setChatOpen(true)
    if (write.resume) ws.openDraft(write.resume)
    else {
      ws.requestWriting(write.extend)
      if (write.q) setPrefill((p) => ({ id: (p?.id ?? 0) + 1, text: write.q }))
      setFocusTick((n) => n + 1)
    }
    onWriteConsumed()
    // 合図は 1 度だけ（ws の関数は毎回作り直されるので、合図の中身だけで回す）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [writeKey])

  // ── ポインタ ──
  const localPoint = (ev: React.PointerEvent<HTMLCanvasElement> | React.MouseEvent<HTMLCanvasElement>): { x: number; y: number } => {
    const r = ev.currentTarget.getBoundingClientRect()
    return { x: ((ev.clientX - r.left) * layout.width) / (r.width || layout.width), y: ev.clientY - r.top }
  }
  const justDragged = useRef(false)
  // 選べる・ドラッグできる提案の線は、開いている下書きのものだけ
  const openId = ws.draft ? sessionId : null
  /** 線の端を落とせる札（開いている下書きの概念になる札）の id。線の元の端・先の端は除く。 */
  const dropTargetAt = (edge: SkyEdge, x: number, y: number): string | null => {
    const n = hitTest(layout, x, y)
    if (!n || n.id === edge.from || n.id === edge.to) return null
    return retargetKeyOf(n, ws.draft, sky.terms, sessionId) ? n.id : null
  }
  const onPointerDown = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    if (nothing || ev.button !== 0) return
    const p = localPoint(ev)
    const e = hitHandle(layout, handleEdges, p.x, p.y)
    if (!e) return
    ev.preventDefault()
    ev.currentTarget.setPointerCapture?.(ev.pointerId)
    dragRef.current = { edge: e, x: p.x, y: p.y, overId: null }
    schedule()
  }
  const onMove = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    if (nothing) return
    const p = localPoint(ev)
    const drag = dragRef.current
    if (drag) {
      dragRef.current = { ...drag, x: p.x, y: p.y, overId: dropTargetAt(drag.edge, p.x, p.y) }
      schedule()
      return
    }
    const id = hitTest(layout, p.x, p.y)?.id ?? null
    const onLine = !id && (hitDraftEdge(layout, p.x, p.y, openId) !== null || hitHandle(layout, handleEdges, p.x, p.y) !== null)
    if (id !== hoverRef.current) {
      hoverRef.current = id
      schedule()
    }
    ev.currentTarget.classList.toggle('sky-canvas--hit', !!id || onLine)
  }
  const onLeave = () => {
    if (hoverRef.current) {
      hoverRef.current = null
      schedule()
    }
  }
  const finishDrag = (ev: React.PointerEvent<HTMLCanvasElement>, drop: boolean) => {
    const drag = dragRef.current
    if (!drag) return
    dragRef.current = null
    ev.currentTarget.releasePointerCapture?.(ev.pointerId)
    justDragged.current = true
    window.setTimeout(() => (justDragged.current = false), 0)
    schedule()
    const info = drag.edge.draft
    if (!drop || !info || info.index === null || info.draftId !== sessionId) return
    const target = dropTargetAt(drag.edge, drag.x, drag.y)
    const key = target ? retargetKeyOf(layout.nodes.get(target), ws.draft, sky.terms, sessionId) : null
    const rel = ws.draft?.relations[info.index]
    if (!key || !rel || key === rel.from || key === rel.to) return
    void ws.editRelation(info.index, { from: rel.from, rel: rel.rel, to: key }).then((ok) => {
      if (ok) setSelectedEdge(skyEdgeId(sessionId, relId({ from: rel.from, rel: rel.rel, to: key })))
    })
  }
  const onClick = (ev: React.MouseEvent<HTMLCanvasElement>) => {
    if (nothing || justDragged.current) return
    const p = localPoint(ev)
    const n = hitTest(layout, p.x, p.y)
    if (n) return selectNode(n.id)
    const e = hitDraftEdge(layout, p.x, p.y, openId)
    if (e?.draft) return selectEdge(draftEdgeId(e.draft))
    selectNode(null)
  }

  // ── 画像で保存 ──
  const tauri = isTauri()
  const [pngError, setPngError] = useState(false)
  const savePng = () => {
    setPngError(false)
    const off = document.createElement('canvas')
    const scale = 2
    off.width = Math.round(layout.width * scale)
    off.height = Math.round(layout.height * scale)
    const ctx = off.getContext('2d')
    if (!ctx) return setPngError(true)
    ctx.setTransform(scale, 0, 0, scale, 0, 0)
    paletteRef.current ??= readSkyPalette()
    // 選択・hover の濃淡は入れない（そのままの星図）。
    drawSky(ctx, layout, paletteRef.current, labels, { focusId: null, near: null, born: new Map(), now: 0, reducedMotion: true, alpha: nothing ? 0.38 : 1 }, visible)
    off.toBlob((blob) => {
      if (!blob) return setPngError(true)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'asterism-sky.png'
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 4000)
    }, 'image/png')
  }

  // 用語の無い種類の数（「データだけ」の 1 行）
  const kindsWithoutTerm = useMemo(() => {
    const withTerm = new Set<string>()
    for (const tm of sky.terms) for (const m of tm.members) withTerm.add(`${m.dataset_id}\u0001${m.iri}`)
    let n = 0
    for (const d of sky.datasets) for (const k of d.kinds) if (!withTerm.has(`${d.id}\u0001${k.iri}`)) n++
    return n
  }, [sky])

  // 星の横の「決めたこと N 件 →」の数え元（星図を取り直すたびに 1 回だけ。星ごとには取らない）。
  const [alignments, setAlignments] = useState<Alignment[]>([])
  useEffect(() => {
    let off = false
    getAlignments()
      .then((r) => !off && setAlignments(r.alignments))
      .catch(() => !off && setAlignments([]))
    return () => {
      off = true
    }
  }, [sky])

  const [tablesOpen, setTablesOpen] = useState(false)
  // 「数で見る」を押したら、開いた表の見出しへ移る（図は画面より高く、表は下に出るため）。
  const tablesRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = tablesRef.current
    if (!tablesOpen || !el) return
    el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' })
    el.focus({ preventScroll: true })
  }, [tablesOpen, reduced])
  const f = sky.facts
  const ariaLabel = nothing
    ? t('chart.ariaSample')
    : t('chart.aria', { datasets: sky.datasets.length, kinds: f.kinds, terms: f.terms })
  // 説明の 1 行は 1 つだけ（状態の注記が重ならないように）。
  const dataOnly = sky.datasets.length > 0 && sky.questions.length === 0 && kindsWithoutTerm > 0
  const note =
    dataOnly
      ? t('empty.dataOnly', { n: kindsWithoutTerm })
      : sky.datasets.length === 0 && sky.questions.length > 0
        ? t('empty.questionsOnly')
        : sky.truncated
          ? t('state.truncated')
          : null

  // ── 会話の先頭に差し込む札 ──
  const cardProps = {
    sky,
    layout,
    lang,
    grown,
    alignments,
    drafts: props.drafts.filter((d) => d.draft_id !== sessionId),
    selectedId: activeId,
    selectedQuestion,
    onSelectNode: selectNode,
    onSelectQuestion: selectQuestion,
    onOpenKind: props.onOpenKind,
    onOpenDataset: props.onOpenDataset,
    onOpenTerm: props.onOpenTerm,
    onDecided: grow,
    suggestions,
    onSuggestionsApplied: (keys: Parameters<typeof irisOfKeys>[0]) => {
      props.setSuggestions((l) => withoutKeys(l, keys))
      grow(irisOfKeys(keys))
    },
    onSuggestionsDismissed: (keys: Parameters<typeof irisOfKeys>[0]) => props.setSuggestions((l) => withoutKeys(l, keys)),
  }
  let focusCard: ReactNode = null
  if (ws.draft && edgeIndex >= 0) {
    focusCard = <EdgeCard key={activeEdge} ws={ws} draft={ws.draft} index={edgeIndex} lang={lang} onSelectEdge={selectRel} />
  } else if (activeNode?.type === 'draft') {
    if (ws.draft && activeNode.draftId === sessionId) {
      focusCard = <ConceptCard ws={ws} draft={ws.draft} conceptKey={activeNode.conceptKey} lang={lang} onSelectEdge={selectRel} />
    } else {
      const other = props.drafts.find((d) => d.draft_id === activeNode.draftId)
      if (other) {
        focusCard = (
          <OtherDraftCard
            draft={other}
            name={activeNode.label}
            onOpen={() => {
              setChatOpen(true)
              ws.openDraft(other.draft_id)
            }}
            onDiscard={() => {
              void ws.discardOther(other.draft_id)
              selectNode(null)
            }}
          />
        )
      }
    }
  }
  const pinned = (
    <SkyPinned {...cardProps} focusCard={focusCard} aiReady={ws.aiReady} />
  )
  const hasPinned =
    !!focusCard ||
    !!grown ||
    suggestions.length > 0 ||
    !!selectedQuestion ||
    (!!activeNode && activeNode.type !== 'draft') ||
    cardProps.drafts.length > 0
  // 会話をたたんでいる（またはシートが閉じている）あいだは、札を図の下の帯に出す
  const bandPinned = !chatOpen && hasPinned

  const selectedKey = activeNode?.type === 'draft' && activeNode.draftId === sessionId ? activeNode.conceptKey : null

  // データを置いているときの左の欄: AI が使えない（未設定）なら閉じて始める（チップと札だけで進める。開けば設定の案内が出る）
  const addOpen = chatOpen && (isReady || addAsked)

  return (
    <div className="sky-page">
      {!adding && (
        <div className="sky-facts">
          {f.questions_total > 0 ? (
            <span className="sky-facts-lead">
              <b>
                {f.questions_answerable} / {f.questions_total}
              </b>
              {t('facts.answerable')}
            </span>
          ) : dataOnly ? null : (
            // 説明の 2 行（「問いはまだありません」＋「用語の無い種類が…」）は 1 行に — 問いが 0 で種類の注記が出るときは後者だけ
            <span className="sky-facts-none">{t('facts.none')}</span>
          )}
        </div>
      )}

      {note && !adding && <p className="sky-note">{note}</p>}

      <div className={`sky-ws${(adding ? addOpen : chatOpen) ? '' : ' is-folded'}${sheet ? ' is-sheet' : ''}`}>
        {adding ? (
          <AddConversation
            open={addOpen}
            onOpen={() => {
              setAddAsked(true)
              setChatOpen(true)
            }}
            onFold={() => setChatOpen(false)}
            setSlot={setChatSlot}
          />
        ) : (
          <HomeConversation
            ws={ws}
            lang={lang}
            pinned={chatOpen && hasPinned ? pinned : null}
            open={chatOpen}
            onOpen={() => setChatOpen(true)}
            onFold={() => setChatOpen(false)}
            selectedKey={selectedKey}
            selectedEdge={activeRel}
            onSelectEdge={selectRel}
            unlinkedKinds={kindsWithoutTerm}
            starSelected={focusKind !== null}
            onAddData={props.onAddData}
            prefill={prefill}
            focusTick={focusTick}
          />
        )}
        <div className="sky-main">
          {adding && (
            <ChatSlotContext.Provider value={chatSlot}>
              <AddArea onClose={props.onCloseAdd} hosted={chatSlot !== null}>
                {props.addView}
              </AddArea>
            </ChatSlotContext.Provider>
          )}
          <div className="sky-sky" hidden={adding}>
            <div className="sky-chartwrap" ref={wrapRef}>
              <div className="sky-figure">
                <canvas
                  ref={canvasRef}
                  className={`sky-canvas${handleEdges.length > 0 ? ' sky-canvas--handles' : ''}`}
                  style={{ height: layout.height }}
                  role="img"
                  aria-label={ariaLabel}
                  onPointerDown={onPointerDown}
                  onPointerMove={onMove}
                  onPointerUp={(ev) => finishDrag(ev, true)}
                  onPointerCancel={(ev) => finishDrag(ev, false)}
                  onPointerLeave={onLeave}
                  onClick={onClick}
                />
                {nothing && (
                  <>
                    <span className="sky-sample-tag">{t('empty.sample')}</span>
                    <div className="sky-empty-card">
                      <h2 className="sky-empty-title">{t('empty.none')}</h2>
                    </div>
                  </>
                )}
                <MoreMenu
                  className="sky-more"
                  label={t('more.label')}
                  items={[
                    {
                      key: 'quiet',
                      label: t('quiet'),
                      checked: reduced,
                      disabled: osQuiet,
                      onSelect: () => {
                        setQuiet(!reduced)
                        writeQuiet(!reduced)
                      },
                    },
                    {
                      key: 'png',
                      label: t('png.button'),
                      disabled: tauri || nothing,
                      title: tauri ? t('png.desktop') : undefined,
                      onSelect: savePng,
                    },
                    { key: 'numbers', label: t('more.numbers'), onSelect: () => setTablesOpen(true) },
                    { key: 'deep', label: t('deep'), onSelect: () => props.onDeep(true) },
                  ]}
                />
              </div>
              <div className="sky-legend">
                <span>
                  <i style={{ background: 'var(--entity)' }} />
                  {layout.mode === 'circles' ? t('legend.dataset') : t('legend.kind')}
                </span>
                <span>
                  <i style={{ background: 'var(--activity)' }} />
                  {t('legend.term')}
                </span>
                <span>
                  <i style={{ background: 'var(--accent)' }} />
                  {t('legend.std')}
                </span>
              </div>
              {pngError && (
                <p className="sky-png-note" role="alert">
                  {t('png.failed')}
                </p>
              )}
            </div>
            {f.questions_total > 0 && (
              <p className="sky-band" data-testid="questions-band">
                <span>
                  {t('band.answerable', { ok: f.questions_answerable, total: f.questions_total })}
                </span>
                <button type="button" className="link-btn" onClick={props.onOpenQuestions}>
                  {t('band.themes')}
                </button>
              </p>
            )}
            {bandPinned && !adding && (
              <div className="sky-sheetband" data-testid="pinned-band">
                {pinned}
              </div>
            )}
          </div>
        </div>
      </div>

      {tablesOpen && (
        <div ref={tablesRef} tabIndex={-1} className="sky-tables-anchor">
          <SkyTables tables={tables} sky={sky} onClose={() => setTablesOpen(false)} />
        </div>
      )}
    </div>
  )
}

/** 右の領域（データを置く）。かんたんの札（children）を星図と同じ幅のページのまま描き、閉じるを添える。 */
function AddArea({ onClose, hosted, children }: { onClose: () => void; hosted: boolean; children: ReactNode }) {
  const { t } = useTranslation('sky')
  return (
    <section className={`sky-add-area${hosted ? ' is-hosted' : ''}`} aria-label={t('add.area')} data-testid="add-area">
      <div className="sky-add-head">
        <span className="sky-add-title">{t('add.area')}</span>
        <span className="sky-add-hint">{t('add.closeHint')}</span>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose} data-testid="add-close">
          {t('add.close')}
        </button>
      </div>
      {children}
    </section>
  )
}
