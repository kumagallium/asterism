// 右の領域＝図（PR 11a）。PR 11f から「1 枚の知識の図」（概念の札・札の直下の星・最下段の名前のない星。`graph/`）。
// 表（SkyTables）・パレット・輪・選択の濃淡は sky/ の純関数と部品を import で流用する。
// `GET /api/sky`（鍵つき）の `proposed` を未確認の薄い札として重ねる。`refreshKey` が変わるたびに取り直し、
// 増えた用語・種類の点に「生まれた輪」を 1 度出す。線のドラッグ・札は載せない（11b 以降）。
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { isTauri } from '../desktop/updater'
import { drawRemovals, removalOverlay } from '../chart/chartOverlay'
import { MoreMenu } from '../MoreMenu'
import { SAMPLE_SKY } from '../sky/sample'
import { EMPTY_SKY, type SkyData, type SkyDataset } from '../sky/skyApi'
import { bornAlive, formatCount, readSkyPalette, visiblePointLabels, type SkyPalette } from '../sky/skyDraw'
import { kindId, neighborhood, neighborhoodOf, nodeIdsForIris, type SkyLayout, type SkyNode } from '../sky/skyLayout'
import { SkyTables } from '../sky/SkyTables'
import { buildSkyTables } from '../sky/skyTable'
import '../sky/sky.css'
import { getSkyWithProposed, type SkyDraft } from './chatApi'
import { selectionOfEdge, selectionOfNode, type Highlight, type PointSel } from './chatState'
import { drawGraph, type GraphLabels } from './graph/graphDraw'
import { layoutGraph } from './graph/graphLayout'
import { hitEdge, hitNode, hitStdLine } from './graph/hit'

// sigma（WebGL）は「中身まで見る」を開くときだけ読む。
const NetworkView = lazy(() => import('../NetworkView').then((m) => ({ default: m.NetworkView })))

const QUIET_KEY = 'asterism.sky.quiet'
const DEFAULT_WIDTH = 640

const readQuiet = (): boolean => {
  try {
    return localStorage.getItem(QUIET_KEY) === '1'
  } catch {
    return false
  }
}
const writeQuiet = (on: boolean): void => {
  try {
    localStorage.setItem(QUIET_KEY, on ? '1' : '0')
  } catch {
    /* 保存できなくても今回の表示には効く */
  }
}
const prefersReducedMotion = (): boolean => {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

type Load = { status: 'loading' } | { status: 'error' } | { status: 'ok'; sky: SkyData; proposed: SkyDraft[]; proposedDatasets: SkyDataset[] }

/** 星図に出ている種類・用語の IRI（増えたものに輪を出すため）。 */
function irisOf(sky: SkyData): Set<string> {
  const out = new Set<string>()
  for (const t of sky.terms) out.add(t.iri)
  for (const d of sky.datasets) for (const k of d.kinds) out.add(k.iri)
  return out
}

export interface ChartPaneProps {
  lang: 'ja' | 'en'
  /** 変わるたびに図を取り直す（手番・✓・戻すのあと）。 */
  refreshKey: number
  /** 変わったときの取り直しだけ、増えた点に生まれた輪を出す（✓ の直後）。 */
  bornKey?: number
  /** 取り直した未確認の札を知らせる（手で足すフォームが、いまある概念を選べるように）。 */
  onProposed?: (drafts: SkyDraft[]) => void
  /** 図の語（slug → 名前を引くため）を知らせる。 */
  onTerms?: (terms: { slug: string; label: string }[]) => void
  /** 点か線を押した（空白を押すと null＝1 枚を閉じる）。 */
  onSelect?: (sel: PointSel | null) => void
  /** 1 枚の中の文・表・出典を押した: 図でそれを濃くする（null で戻す）。 */
  highlight?: Highlight | null
}

export function ChartPane({ lang, refreshKey, bornKey = 0, onProposed, onTerms, onSelect, highlight = null }: ChartPaneProps) {
  const { t } = useTranslation('sky')
  const { t: tc } = useTranslation('chat')
  const [load, setLoad] = useState<Load>({ status: 'loading' })
  const [retryTick, setRetryTick] = useState(0)
  const [deep, setDeep] = useState(false)
  const prevIris = useRef<Set<string> | null>(null)
  const lastBornKey = useRef(bornKey)
  const bornIris = useRef<string[]>([])
  const onProposedRef = useRef(onProposed)
  const onTermsRef = useRef(onTerms)
  useEffect(() => {
    onProposedRef.current = onProposed
    onTermsRef.current = onTerms
  })

  useEffect(() => {
    let off = false
    getSkyWithProposed(lang)
      .then(({ sky, proposed, proposedDatasets }) => {
        if (off) return
        const now = irisOf(sky)
        // 取り直したあとに増えた点（最初の読み込みは除く）に輪を出す
        const ring = bornKey !== lastBornKey.current
        lastBornKey.current = bornKey
        bornIris.current = ring && prevIris.current ? [...now].filter((i) => !prevIris.current!.has(i)) : []
        prevIris.current = now
        setLoad({ status: 'ok', sky, proposed, proposedDatasets })
        onProposedRef.current?.(proposed)
        onTermsRef.current?.(sky.terms.map((x) => ({ slug: x.slug, label: x.label })))
      })
      // 取り直しの失敗では、いま描いている図を消さない（最初の読み込みだけエラーにする）
      .catch(() => !off && setLoad((cur) => (cur.status === 'ok' ? cur : { status: 'error' })))
    return () => {
      off = true
    }
  }, [lang, refreshKey, bornKey, retryTick])

  const sky = load.status === 'ok' ? load.sky : EMPTY_SKY
  const proposed = useMemo<SkyDraft[]>(() => (load.status === 'ok' ? load.proposed : []), [load])
  const proposedDatasets = useMemo<SkyDataset[]>(() => (load.status === 'ok' ? load.proposedDatasets : []), [load])
  const nothing = sky.datasets.length === 0 && sky.questions.length === 0 && proposed.length === 0 && proposedDatasets.length === 0
  // 取り込み済みで未公開のデータセットは、薄い星座として足す（公開すると datasets に移り濃くなる）
  const withThin = useMemo<SkyData>(
    () => (proposedDatasets.length === 0 ? sky : { ...sky, datasets: [...sky.datasets, ...proposedDatasets] }),
    [sky, proposedDatasets],
  )
  const drawn = nothing ? SAMPLE_SKY : withThin

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
  }, [load.status, deep])

  const layout = useMemo<SkyLayout>(
    () => layoutGraph(drawn, { width, lang, drafts: nothing ? [] : proposed }),
    [drawn, width, lang, proposed, nothing],
  )
  const removals = useMemo(() => removalOverlay(layout, nothing ? [] : proposed), [layout, proposed, nothing])
  // 外す提案の札は drawGraph が描く（札の形が変わるので）。線の「×」だけ drawRemovals で重ねる
  const removeNodeIds = useMemo<ReadonlySet<string>>(() => new Set(removals.nodeIds), [removals])
  const removalLines = useMemo(() => ({ nodeIds: [], edges: removals.edges }), [removals])
  const tables = useMemo(() => buildSkyTables(sky, lang), [sky, lang])
  const visible = useMemo(() => visiblePointLabels(layout), [layout])

  const [quiet, setQuiet] = useState(readQuiet)
  const [osQuiet] = useState(prefersReducedMotion)
  const reduced = quiet || osQuiet

  // 1 枚の中で押したもの（文の線・表の種類・出典）を濃くする
  const hl = useMemo<{ focusId: string; near: ReadonlySet<string> } | null>(() => {
    if (!highlight) return null
    if ('edge' in highlight) {
      const a = nodeIdsForIris(layout, [highlight.edge.from])[0]
      const b = nodeIdsForIris(layout, [highlight.edge.to])[0]
      return a && b ? { focusId: a, near: new Set([a, b]) } : null
    }
    let ids = nodeIdsForIris(layout, highlight.iris)
    if (highlight.datasetId) {
      // 出典: その星座の種類を先に。引けなければ星座（データセットの丸と種類）を濃く
      const ds = highlight.datasetId
      const k = highlight.iris.map((i) => kindId(ds, i)).filter((id) => layout.nodes.has(id))
      if (k.length > 0) ids = k
      else {
        const all = [...layout.nodes.values()].filter((n) => (n.type === 'dataset' || n.type === 'kind') && n.datasetId === ds).map((n) => n.id)
        if (all.length > 0) return { focusId: all[0], near: new Set(all) }
      }
    }
    return ids.length > 0 ? { focusId: ids[0], near: neighborhoodOf(layout, ids) } : null
  }, [highlight, layout])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const activeId = selectedId && layout.nodes.has(selectedId) ? selectedId : null

  const labels = useMemo<GraphLabels>(
    () => ({
      noName: tc('chart.noName'),
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
    [t, tc, lang],
  )

  // ── 描画 ──
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const paletteRef = useRef<SkyPalette | null>(null)
  const hoverRef = useRef<string | null>(null)
  const bornRef = useRef<Map<string, number>>(new Map())
  const rafRef = useRef(0)
  const stateRef = useRef<{ layout: SkyLayout; labels: GraphLabels; visible: ReadonlySet<string>; removeNodeIds: ReadonlySet<string>; removalLines: ReturnType<typeof removalOverlay>; selectedId: string | null; hl: { focusId: string; near: ReadonlySet<string> } | null; reduced: boolean; nothing: boolean } | null>(null)

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
    const hover = hoverRef.current
    const focusId = s.nothing ? null : (hover ?? s.hl?.focusId ?? s.selectedId)
    const near = !focusId ? null : !hover && s.hl ? s.hl.near : neighborhood(s.layout, focusId)
    drawGraph(
      ctx,
      s.layout,
      paletteRef.current,
      s.labels,
      { focusId, near, born: bornRef.current, now, reducedMotion: s.reduced, alpha: s.nothing ? 0.38 : 1, removeNodeIds: s.removeNodeIds },
      s.visible,
    )
    drawRemovals(ctx, s.layout, paletteRef.current, s.removalLines, s.labels.draftRemove)
    if (!s.reduced && bornAlive(bornRef.current, now)) rafRef.current = requestAnimationFrame(paintFrame)
  }, [])
  const schedule = useCallback(() => {
    if (!rafRef.current) rafRef.current = requestAnimationFrame(paint)
  }, [paint])

  useEffect(() => {
    stateRef.current = { layout, labels, visible, removeNodeIds, removalLines, selectedId: activeId, hl, reduced, nothing }
    schedule()
  }, [layout, labels, visible, removeNodeIds, removalLines, activeId, hl, reduced, nothing, schedule])
  useEffect(() => {
    let alive = true
    void document.fonts?.ready.then(() => alive && schedule())
    return () => {
      alive = false
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [schedule])

  // 取り直しで増えた点に、生まれた輪を 1 度だけ
  useEffect(() => {
    const iris = bornIris.current
    if (iris.length === 0 || reduced || nothing) return
    bornIris.current = []
    const now = performance.now()
    for (const id of nodeIdsForIris(layout, iris)) bornRef.current.set(id, now)
    schedule()
  }, [layout, reduced, nothing, schedule])

  // ── ポインタ（hover で近傍を濃く・押して選ぶ） ──
  const localPoint = (ev: React.PointerEvent<HTMLCanvasElement> | React.MouseEvent<HTMLCanvasElement>) => {
    const r = ev.currentTarget.getBoundingClientRect()
    return { x: ((ev.clientX - r.left) * layout.width) / (r.width || layout.width), y: ev.clientY - r.top }
  }
  // hover の吹き出し（省いた名前の全文・標準の語・星のデータセットと件数）
  const datasetLabels = useMemo(() => new Map(drawn.datasets.map((d) => [d.id, d.label])), [drawn])
  const titleOf = (n: SkyNode): string => {
    if (n.type === 'term') return n.stdLabel ? `${n.label} ≡ ${n.stdLabel}` : n.label
    if (n.type === 'kind') return [n.label, n.count > 0 ? labels.count(n.count) : '', datasetLabels.get(n.datasetId) ?? ''].filter(Boolean).join(' · ')
    return n.label
  }
  const onMove = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    if (nothing) return
    const p = localPoint(ev)
    const n = hitNode(layout, p.x, p.y)
    const id = n?.id ?? null
    if (id !== hoverRef.current) {
      hoverRef.current = id
      schedule()
      ev.currentTarget.title = n ? titleOf(n) : ''
    }
    ev.currentTarget.classList.toggle('sky-canvas--hit', !!id)
  }
  const onLeave = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    ev.currentTarget.title = ''
    if (hoverRef.current) {
      hoverRef.current = null
      schedule()
    }
  }
  const onClick = (ev: React.MouseEvent<HTMLCanvasElement>) => {
    if (nothing) return
    const p = localPoint(ev)
    const n = hitNode(layout, p.x, p.y)
    if (n) {
      // 札の中の標準の語の 1 行を押したら、標準の語の 1 枚
      const std = hitStdLine(layout, p.x, p.y)
      if (std && std.stdId && std.stdLabel) {
        setSelectedId(std.id)
        onSelect?.({ kind: 'std', iri: std.stdId.slice(2), label: std.stdLabel.slice(std.stdLabel.indexOf(':') + 1) })
        return
      }
      const sel = selectionOfNode(n, layout)
      setSelectedId(sel ? n.id : null)
      onSelect?.(sel)
      return
    }
    const e = hitEdge(layout, p.x, p.y)
    const sel = e ? selectionOfEdge(layout, e) : null
    setSelectedId(null)
    onSelect?.(sel)
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
    drawGraph(ctx, layout, paletteRef.current, labels, { focusId: null, near: null, born: new Map(), now: 0, reducedMotion: true, alpha: nothing ? 0.38 : 1, removeNodeIds }, visible)
    drawRemovals(ctx, layout, paletteRef.current, removalLines, labels.draftRemove)
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

  const [tablesOpen, setTablesOpen] = useState(false)
  const tablesRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = tablesRef.current
    if (!tablesOpen || !el) return
    el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' })
    el.focus({ preventScroll: true })
  }, [tablesOpen, reduced])

  if (deep) {
    return (
      <div className="chart-pane chart-pane--deep">
        <div className="sky-deep-head">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setDeep(false)}>
            ← {tc('chart.back')}
          </button>
          <span>{t('deepLead')}</span>
        </div>
        <Suspense fallback={null}>
          <NetworkView onOpenSet={() => undefined} />
        </Suspense>
      </div>
    )
  }

  if (load.status === 'loading') {
    return (
      <div className="chart-pane">
        <div className="sky-placeholder chart-placeholder" role="status">
          {t('state.loading')}
        </div>
      </div>
    )
  }
  if (load.status === 'error') {
    return (
      <div className="chart-pane">
        <div className="sky-placeholder chart-placeholder" role="alert">
          <span>{tc('chart.failed')}</span>
          <button type="button" className="btn btn--sm" onClick={() => setRetryTick((n) => n + 1)}>
            {tc('chart.retry')}
          </button>
        </div>
      </div>
    )
  }

  const f = sky.facts
  const ariaLabel = nothing ? t('chart.ariaSample') : t('chart.aria', { datasets: sky.datasets.length, kinds: f.kinds, terms: f.terms })
  return (
    <div className="chart-pane">
      <div className="sky-facts">
        {f.questions_total > 0 ? (
          <span className="sky-facts-lead">
            <b>
              {f.questions_answerable} / {f.questions_total}
            </b>
            {t('facts.answerable')}
          </span>
        ) : (
          <span className="sky-facts-none">{t('facts.none')}</span>
        )}
      </div>
      <div className="sky-chartwrap" ref={wrapRef}>
        <div className="sky-figure">
          <canvas
            ref={canvasRef}
            className="sky-canvas"
            data-testid="chart-canvas"
            style={{ height: layout.height }}
            role="img"
            aria-label={ariaLabel}
            onPointerMove={onMove}
            onPointerLeave={onLeave}
            onClick={onClick}
          />
          {nothing && <span className="sky-sample-tag">{t('empty.sample')}</span>}
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
              { key: 'png', label: t('png.button'), disabled: tauri || nothing, title: tauri ? t('png.desktop') : undefined, onSelect: savePng },
              { key: 'numbers', label: t('more.numbers'), onSelect: () => setTablesOpen(true) },
              { key: 'deep', label: t('deep'), onSelect: () => setDeep(true) },
            ]}
          />
        </div>
        <div className="sky-legend chart-legend">
          <span>
            <i className="chart-legend-card" style={{ background: 'var(--activity)' }} />
            {tc('chart.legendCard')}
          </span>
          <span>
            <i style={{ background: 'var(--entity)' }} />
            {tc('chart.legendStar')}
          </span>
          <span>
            <i className="chart-legend-card chart-legend-thin" style={{ background: 'var(--activity)' }} />
            {tc('chart.legendThin')}
          </span>
        </div>
        {pngError && (
          <p className="sky-png-note" role="alert">
            {t('png.failed')}
          </p>
        )}
      </div>
      {tablesOpen && (
        <div ref={tablesRef} tabIndex={-1} className="sky-tables-anchor">
          <SkyTables tables={tables} sky={sky} onClose={() => setTablesOpen(false)} />
        </div>
      )}
    </div>
  )
}
