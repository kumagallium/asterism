// ホーム＝星図（UI 再設計 PR 2）。参考実装 `docs/design/sky-chart-mock/`。
//   ・上: 事実の帯（答えられる問い N/M が先頭）
//   ・左: 星図（canvas 2D・決定論の配置）。右: 「この星について」「知りたいこと」「最近の育ち」
//   ・下: 図と同じ内容の表（静かな表示）・「画像で保存」・「中身まで見る」（値の網）
// 配置は skyLayout.ts（純関数）、描画は skyDraw.ts。ここは取得・状態・DOM だけ。
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { isTauri } from '../desktop/updater'
import { SkyRail } from './SkyRail'
import { SkyTables } from './SkyTables'
import { SAMPLE_SKY } from './sample'
import { getSky, type SkyData } from './skyApi'
import {
  bornAlive,
  drawSky,
  formatCount,
  hitTest,
  readSkyPalette,
  visiblePointLabels,
  type SkyLabels,
  type SkyPalette,
} from './skyDraw'
import { layoutSky, neighborhood, neighborhoodOf, nodeIdsForIris, type SkyLayout } from './skyLayout'
import { buildSkyTables } from './skyTable'
import './sky.css'

export interface SkyViewProps {
  /** `#/home?deep=1` — 同じ場所に「中身まで見る」（値の網）を出す。 */
  deep: boolean
  onDeep: (on: boolean) => void
  /** `#/home?new=…` — 公開直後に灯す種類・用語の IRI。 */
  newIris: string[]
  /** 灯したら（1 度だけ）hash から new を外す。 */
  onNewConsumed: () => void
  onAddData: () => void
  onWriteQuestion: () => void
  onOpenKind: (iri: string) => void
  onOpenDataset: (id: string) => void
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

type Load = { status: 'loading' } | { status: 'error' } | { status: 'ok'; sky: SkyData }

export function SkyView(props: SkyViewProps) {
  const { t, i18n } = useTranslation('sky')
  const lang: 'ja' | 'en' = i18n.language.startsWith('en') ? 'en' : 'ja'
  const [load, setLoad] = useState<Load>({ status: 'loading' })

  const fetchSky = useCallback(() => {
    let cancelled = false
    getSky()
      .then((sky) => !cancelled && setLoad({ status: 'ok', sky }))
      .catch(() => !cancelled && setLoad({ status: 'error' }))
    return () => {
      cancelled = true
    }
  }, [])
  useEffect(() => fetchSky(), [fetchSky])
  // 選択を保ったまま取り直す（読み込み中の表示には戻さない）。
  const refresh = useCallback(() => {
    getSky()
      .then((sky) => setLoad({ status: 'ok', sky }))
      .catch(() => undefined)
  }, [])
  const retry = () => {
    setLoad({ status: 'loading' })
    fetchSky()
  }

  if (props.deep) {
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
  return <SkyBody sky={load.sky} lang={lang} onRefresh={refresh} {...props} />
}

function SkyBody(props: SkyViewProps & { sky: SkyData; lang: 'ja' | 'en'; onRefresh: () => void }) {
  const { sky, lang } = props
  const { t } = useTranslation('sky')
  const nothing = sky.datasets.length === 0 && sky.questions.length === 0
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

  const layout = useMemo<SkyLayout>(() => layoutSky(drawn, { width, lang }), [drawn, width, lang])
  const tables = useMemo(() => buildSkyTables(sky, lang), [sky, lang])

  // ── 静かな表示 ──
  const [quiet, setQuiet] = useState(readQuiet)
  const [osQuiet] = useState(prefersReducedMotion)
  const reduced = quiet || osQuiet

  // ── 選択 ──
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedQuestion, setSelectedQuestion] = useState<string | null>(null)
  const selectNode = useCallback((id: string | null) => {
    setSelectedId(id)
    setSelectedQuestion(null)
  }, [])
  const selectQuestion = useCallback((tool: string | null) => {
    setSelectedQuestion(tool)
    setSelectedId(null)
  }, [])
  // 配置が作り直されても、その点が残っていれば選択を保つ。無くなったら外す。
  const activeId = selectedId && layout.nodes.has(selectedId) ? selectedId : null

  // ── 描画 ──
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const paletteRef = useRef<SkyPalette | null>(null)
  const hoverRef = useRef<string | null>(null)
  const bornRef = useRef<Map<string, number>>(new Map())
  const rafRef = useRef(0)
  const labels = useMemo<SkyLabels>(
    () => ({
      names: t('bands.names'),
      mid: t('bands.data'),
      midCircles: t('bands.dataCircles'),
      std: t('bands.std'),
      empty: t('bands.noStar'),
      property: t('bands.property'),
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

  interface PaintState {
    layout: SkyLayout
    labels: SkyLabels
    visible: ReadonlySet<string>
    selectedId: string | null
    questionNear: ReadonlySet<string> | null
    reduced: boolean
    nothing: boolean
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
    const near = s.nothing ? null : focusId ? neighborhood(s.layout, focusId) : s.questionNear
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
      },
      s.visible,
    )
    if (!s.reduced && bornAlive(bornRef.current, now)) rafRef.current = requestAnimationFrame(paintFrame)
  }, [])
  const schedule = useCallback(() => {
    if (!rafRef.current) rafRef.current = requestAnimationFrame(paint)
  }, [paint])

  useEffect(() => {
    stateRef.current = { layout, labels, visible, selectedId: activeId, questionNear, reduced, nothing }
    schedule()
  }, [layout, labels, visible, activeId, questionNear, reduced, nothing, schedule])
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
  useEffect(() => {
    const g = growRef.current
    if (!g || g.from === sky) return
    growRef.current = null
    if (reduced) return
    const now = performance.now()
    for (const id of nodeIdsForIris(layout, g.iris)) bornRef.current.set(id, now)
    schedule()
  }, [sky, layout, reduced, schedule])

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
    if (!reduced) {
      const now = performance.now()
      for (const id of nodeIdsForIris(layout, newIris)) bornRef.current.set(id, now)
      schedule()
    }
    onNewConsumed()
  }, [newIris, onNewConsumed, layout, reduced, nothing, schedule])

  // ── ポインタ ──
  const localPoint = (ev: React.MouseEvent<HTMLCanvasElement>): { x: number; y: number } => {
    const r = ev.currentTarget.getBoundingClientRect()
    return { x: ((ev.clientX - r.left) * layout.width) / (r.width || layout.width), y: ev.clientY - r.top }
  }
  const onMove = (ev: React.MouseEvent<HTMLCanvasElement>) => {
    if (nothing) return
    const p = localPoint(ev)
    const id = hitTest(layout, p.x, p.y)?.id ?? null
    if (id !== hoverRef.current) {
      hoverRef.current = id
      ev.currentTarget.classList.toggle('sky-canvas--hit', !!id)
      schedule()
    }
  }
  const onLeave = () => {
    if (hoverRef.current) {
      hoverRef.current = null
      schedule()
    }
  }
  const onClick = (ev: React.MouseEvent<HTMLCanvasElement>) => {
    if (nothing) return
    const p = localPoint(ev)
    selectNode(hitTest(layout, p.x, p.y)?.id ?? null)
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

  const f = sky.facts
  const fmt = (n: number): string => n.toLocaleString('en-US')
  const ariaLabel = nothing
    ? t('chart.ariaSample')
    : t('chart.aria', { datasets: sky.datasets.length, kinds: f.kinds, terms: f.terms })

  return (
    <div className="sky-page">
      <div className="sky-facts">
        <span className="sky-facts-lead">
          <b>
            {f.questions_answerable}/{f.questions_total}
          </b>
          {t('facts.answerable')}
        </span>
        <span>
          <b>{fmt(f.kinds)}</b>
          {t('facts.kinds')}
        </span>
        <span>
          <b>{fmt(f.terms)}</b>
          {t('facts.terms')}
        </span>
        <span>
          <b>{fmt(sky.datasets.length)}</b>
          {t('facts.datasets')}
        </span>
        <span>
          <b>{fmt(f.triples)}</b>
          {t('facts.triples')}
        </span>
      </div>

      {sky.datasets.length > 0 && sky.questions.length === 0 && kindsWithoutTerm > 0 && (
        <p className="sky-note">{t('empty.dataOnly', { n: kindsWithoutTerm })}</p>
      )}
      {sky.datasets.length === 0 && sky.questions.length > 0 && <p className="sky-note">{t('empty.questionsOnly')}</p>}
      {sky.truncated && <p className="sky-note">{t('state.truncated')}</p>}

      <div className="sky-grid">
        <div className="sky-chartwrap" ref={wrapRef}>
          <canvas
            ref={canvasRef}
            className="sky-canvas"
            style={{ height: layout.height }}
            role="img"
            aria-label={ariaLabel}
            onMouseMove={onMove}
            onMouseLeave={onLeave}
            onClick={onClick}
          />
          {nothing && (
            <>
              <span className="sky-sample-tag">{t('empty.sample')}</span>
              <div className="sky-empty-card">
                <h2 className="sky-empty-title">{t('empty.none')}</h2>
                <p className="sky-empty-sub">{t('empty.noneSub')}</p>
                <div className="sky-empty-actions">
                  <button type="button" className="btn btn--primary btn--sm" onClick={props.onAddData}>
                    {t('common:entries.data')}
                  </button>
                  <button type="button" className="btn btn--activity btn--sm" onClick={props.onWriteQuestion}>
                    {t('common:entries.question')}
                  </button>
                </div>
              </div>
            </>
          )}
          <div className="sky-legend">
            <span>
              <i style={{ background: 'var(--entity)' }} />
              {t('legend.kind')}
            </span>
            <span>
              <i style={{ background: 'var(--activity)' }} />
              {t('legend.term')}
            </span>
            <span>
              <i style={{ background: 'var(--accent)' }} />
              {t('legend.std')}
            </span>
            <span>{layout.mode === 'circles' ? t('legend.circles') : t('legend.lines')}</span>
            <span className="sky-legend-end">
              <label className="sky-quiet">
                <input
                  type="checkbox"
                  checked={reduced}
                  disabled={osQuiet}
                  onChange={(e) => {
                    setQuiet(e.target.checked)
                    writeQuiet(e.target.checked)
                  }}
                />
                {t('quiet')}
              </label>
              <button type="button" className="btn btn--ghost btn--sm" onClick={savePng} disabled={tauri || nothing}>
                {t('png.button')}
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => props.onDeep(true)}>
                {t('deep')}
              </button>
            </span>
          </div>
          {tauri && <p className="sky-png-note">{t('png.desktop')}</p>}
          {pngError && (
            <p className="sky-png-note" role="alert">
              {t('png.failed')}
            </p>
          )}
        </div>
        <SkyRail
          sky={sky}
          layout={layout}
          lang={lang}
          selectedId={activeId}
          selectedQuestion={selectedQuestion}
          onSelectNode={selectNode}
          onSelectQuestion={selectQuestion}
          onOpenKind={props.onOpenKind}
          onOpenDataset={props.onOpenDataset}
          onOpenQuestions={props.onOpenQuestions}
          onDecided={grow}
        />
      </div>

      <SkyTables tables={tables} />
    </div>
  )
}

