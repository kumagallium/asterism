import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { layoutConceptMap } from './conceptMapLayout'
import { type RelKind, type StandardInfo, conceptName } from './themeDraft'
import type { CanvasModel } from './workspaceState'

// 知識の図（②の左）。星図と同じ紙の見た目。札＝概念（種類は --entity、項目は --activity の薄塗り、
// 標準用語に一致したものは地平線の色の枠）、線＝関係。一種・一部は矢じり付きで段をまたぎ、
// 下げる／上げる／変えるは同じ段の横の線に「↓」「↑」「↕」の札、同じものは両向き。
// 配置は conceptMapLayout.ts（純関数）。段の中は幅に合わせて**折り返す**。
//
// 提案は薄く重ねる（足す札＝薄い塗り＋「提案」の印・付け替える線＝薄い線と「→」・外す札＝打ち消し）。
// 札を押す → 右の会話にその概念の文。線を押す → その文を右に出して A・関係・B を直せる。
// 線の端（選んだ線の矢じりのそばの丸）を持って別の札に落とすと、付け替え（retarget）。

/**
 * 図を縮める下限。これより小さくなるなら縮めず、図の中で横へスクロールさせる（ページははみ出さない）。
 * 0.85 より小さいと 375px 幅で札の文字（0.78rem）が読めなくなる。
 */
const MIN_SCALE = 0.85

const PILL: Partial<Record<RelKind, string>> = {
  lowers: '↓',
  raises: '↑',
  affects: '↕',
  eq: '＝',
}

export interface GraphCanvasProps {
  model: CanvasModel
  /** 標準の語（curie → 表示名）。関係の端にあれば、地平線の色の小さな札として図に出す。 */
  standards: Record<string, StandardInfo>
  lang: string
  selectedKey: string | null
  /** 選んだ線（下書きの関係の添字）。 */
  selectedEdge: number | null
  /** 右の文に重ねたポインタ。その関係（下書きの添字）の線を光らせる。 */
  litRelation: number | null
  /** 問いにつながっていない概念。 */
  unlinked: ReadonlySet<string>
  onSelect: (key: string | null) => void
  onSelectEdge: (draftIndex: number | null) => void
  /** 線の端を別の札に落とした（下書きの関係の添字・新しい相手の key か curie）。 */
  onRetarget: (draftIndex: number, newTo: string) => void
}

function isStandardMatched(c: { use: string; match: { kind: string } | null }): boolean {
  return c.use.startsWith('standard:') || c.match?.kind === 'standard' || c.match?.kind === 'quantity'
}

interface Drag {
  index: number
  x: number
  y: number
  over: string | null
}

export function GraphCanvas(p: GraphCanvasProps) {
  const { t } = useTranslation()
  const { concepts, relations } = p.model
  const rowByKey = useMemo(() => new Map(concepts.map((c) => [c.row.key, c])), [concepts])
  // 関係の端が概念でなく標準の語（curie）なら、その語を小さな札として足す（文の「…は Thermal Conductivity（標準）」と図の線を対応させる）
  const stdWords = useMemo(() => {
    const keys = new Set(concepts.map((c) => c.row.key))
    const out = new Map<string, string>()
    for (const r of relations) {
      for (const end of [r.row.from, r.row.to]) {
        if (keys.has(end) || out.has(end) || !end.includes(':')) continue
        const info = p.standards[end]
        out.set(end, info?.label || end.slice(end.indexOf(':') + 1))
      }
    }
    return out
  }, [concepts, relations, p.standards])
  const mapIn = useMemo(
    () => ({
      nodes: [
        ...concepts.map((c) => ({ key: c.row.key, label: conceptName(c.row, p.lang), mark: c.row.kind === 'property' })),
        ...[...stdWords].map(([key, label]) => ({ key, label, std: true })),
      ],
      rels: relations.map((r, index) => ({
        index,
        from: r.row.from,
        to: r.row.to,
        rel: r.row.rel,
        problem: r.row.problem !== null,
      })),
    }),
    [concepts, relations, p.lang, stdWords],
  )

  // 図は置き場の幅で折り返す。縮める下限（0.85）でも収まらないときだけ、図の中で横にスクロールさせる。
  const isEmpty = concepts.length === 0
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [avail, setAvail] = useState(0)
  useLayoutEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const apply = () => setAvail(Math.round(el.getBoundingClientRect().width))
    apply()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [isEmpty])
  const wrapped = useMemo(
    () => layoutConceptMap(mapIn.nodes, mapIn.rels, avail > 0 ? { maxWidth: avail } : {}),
    [mapIn, avail],
  )
  const scrolls = avail > 0 && wrapped.width * MIN_SCALE > avail
  // 横にスクロールするときは段を左端にそろえる（中央寄せだと、細い画面で 2 段目が右の見えない所に出る）
  const layout = useMemo(
    () => (scrolls ? layoutConceptMap(mapIn.nodes, mapIn.rels, { maxWidth: avail, align: 'start' }) : wrapped),
    [scrolls, wrapped, mapIn, avail],
  )
  // 「全体を収める」: 縮小の下限を無視して、全体を 1 画面に（もう 1 度押すと戻る）
  const [fitAll, setFitAll] = useState(false)
  const availH = typeof window === 'undefined' ? 600 : Math.max(240, window.innerHeight - 220)
  let scale = avail > 0 && layout.width > avail ? Math.max(MIN_SCALE, avail / layout.width) : 1
  if (fitAll && avail > 0) scale = Math.min(1, avail / layout.width, availH / Math.max(1, layout.height))
  const scrollsNow = scrolls && !fitAll

  // ---- 線の端のドラッグ ----
  const canvasRef = useRef<HTMLDivElement | null>(null)
  const [drag, setDrag] = useState<Drag | null>(null)
  const toCanvas = useCallback(
    (clientX: number, clientY: number) => {
      const r = canvasRef.current?.getBoundingClientRect()
      return r ? { x: (clientX - r.left) / scale, y: (clientY - r.top) / scale } : { x: 0, y: 0 }
    },
    [scale],
  )
  const nodeAt = useCallback(
    (x: number, y: number): string | null => {
      for (const n of layout.nodes) if (x >= n.x && x <= n.x + n.w && y >= n.y && y <= n.y + n.h) return n.key
      return null
    },
    [layout],
  )
  const edgeOf = (index: number) => relations[index]
  // ドラッグ中は window で動きを受ける（指やマウスが図の外に出ても追う・捕まえ損ねない）
  const dragIndex = drag?.index ?? null
  const { onRetarget } = p
  useEffect(() => {
    if (dragIndex === null) return
    const at = (ev: PointerEvent) => toCanvas(ev.clientX, ev.clientY)
    const move = (ev: PointerEvent) => {
      const pt = at(ev)
      setDrag({ index: dragIndex, ...pt, over: nodeAt(pt.x, pt.y) })
    }
    const up = (ev: PointerEvent) => {
      const pt = at(ev)
      const target = nodeAt(pt.x, pt.y)
      const rel = relations[dragIndex]
      setDrag(null)
      if (target && rel && rel.draftIndex !== null && target !== rel.row.to && target !== rel.row.from) {
        onRetarget(rel.draftIndex, target)
      }
    }
    const cancel = () => setDrag(null)
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', cancel)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', cancel)
    }
  }, [dragIndex, toCanvas, nodeAt, relations, onRetarget])

  if (isEmpty) {
    return (
      <div className="thm-map thm-map--empty" data-testid="concept-map">
        <p className="ds-empty-note">{t('themes:map.empty')}</p>
      </div>
    )
  }

  const touches = (e: { from: string; to: string }) =>
    !!p.selectedKey && (e.from === p.selectedKey || e.to === p.selectedKey)
  const isSelectedEdge = (index: number) => {
    const di = relations[index]?.draftIndex ?? null
    return di !== null && p.selectedEdge === di
  }
  const isLit = (index: number, e: { from: string; to: string }) => {
    const di = relations[index]?.draftIndex ?? null
    return (di !== null && p.litRelation === di) || touches(e) || isSelectedEdge(index)
  }
  const dimming = !!p.selectedKey || p.litRelation !== null || p.selectedEdge !== null
  const dragFrom = drag ? layout.byKey.get(edgeOf(drag.index)?.row.from ?? '') : undefined
  const hasGhost = concepts.some((c) => c.ghost) || relations.some((r) => r.ghost)

  return (
    <div className="thm-map" data-testid="concept-map">
      <div className="thm-map-tools">
        <button
          type="button"
          className={`btn btn--ghost btn--sm${fitAll ? ' is-on' : ''}`}
          aria-pressed={fitAll}
          data-testid="fit-all"
          onClick={() => setFitAll((v) => !v)}
        >
          {t('themes:map.fitAll')}
        </button>
      </div>
      <div className="thm-map-scroll" ref={wrapRef}>
        <div className="thm-map-fit" style={{ width: layout.width * scale, height: layout.height * scale }}>
          <div
            className="thm-map-canvas"
            ref={canvasRef}
            style={{
              width: layout.width,
              height: layout.height,
              transform: scale < 1 ? `scale(${scale})` : undefined,
            }}
            onClick={() => {
              p.onSelect(null)
              p.onSelectEdge(null)
            }}
          >
            <svg width={layout.width} height={layout.height} className="thm-map-lines" aria-hidden="true">
              <defs>
                {(['hier', 'side', 'bad'] as const).map((k) => (
                  <marker
                    key={k}
                    id={`thm-arrow-${k}`}
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="7"
                    markerHeight="7"
                    orient="auto-start-reverse"
                  >
                    <path d="M0,1 L10,5 L0,9 z" className={`thm-arrow thm-arrow--${k}`} />
                  </marker>
                ))}
              </defs>
              {layout.edges.map((e) => {
                const k = e.problem ? 'bad' : e.kind
                const lit = isLit(e.index, e)
                const ghost = edgeOf(e.index)?.ghost
                const di = edgeOf(e.index)?.draftIndex ?? null
                return (
                  <g key={e.index}>
                    <path
                      d={e.path}
                      className={`thm-line thm-line--${k}${ghost ? ` thm-line--ghost-${ghost}` : ''}${
                        lit ? ' is-lit' : dimming ? ' is-dim' : ''
                      }${isSelectedEdge(e.index) ? ' is-on' : ''}`}
                      markerEnd={`url(#thm-arrow-${k})`}
                      markerStart={e.both ? `url(#thm-arrow-${k})` : undefined}
                      data-rel={e.rel}
                      data-draft-index={di ?? undefined}
                    />
                    {di !== null && (
                      <path
                        d={e.path}
                        className="thm-line-hit"
                        data-testid="line-hit"
                        onClick={(ev) => {
                          ev.stopPropagation()
                          p.onSelect(null)
                          p.onSelectEdge(p.selectedEdge === di ? null : di)
                        }}
                      />
                    )}
                  </g>
                )
              })}
              {drag && dragFrom && (
                <line
                  className="thm-drag-line"
                  x1={dragFrom.x + dragFrom.w / 2}
                  y1={dragFrom.y + dragFrom.h / 2}
                  x2={drag.x}
                  y2={drag.y}
                />
              )}
            </svg>
            {layout.edges.map((e) => {
              const rel = edgeOf(e.index)
              const ghost = rel?.ghost
              const text =
                ghost === 'retarget'
                  ? '→'
                  : ghost === 'remove'
                    ? '×'
                    : e.problem
                      ? '×'
                      : e.kind === 'hier' || e.rel === 'quantity'
                        ? t(`themes:map.pill.${e.rel}`)
                        : PILL[e.rel]
              if (!text) return null
              const lit = isLit(e.index, e)
              const di = rel?.draftIndex ?? null
              return (
                <span
                  key={e.index}
                  className={`thm-pill thm-pill--${e.problem ? 'bad' : e.kind}${di !== null ? ' thm-pill--click' : ''}${
                    ghost ? ` thm-pill--ghost-${ghost}` : ''
                  }${
                    lit ? ' is-lit' : dimming ? ' is-dim' : ''
                  }`}
                  style={{ left: e.mid.x, top: e.mid.y }}
                  title={
                    ghost === 'retarget' ? t('themes:map.pillRetarget') : t(`themes:map.pillTitle.${e.rel}`)
                  }
                  onClick={
                    di !== null
                      ? (ev) => {
                          ev.stopPropagation()
                          p.onSelect(null)
                          p.onSelectEdge(p.selectedEdge === di ? null : di)
                        }
                      : undefined
                  }
                >
                  {text}
                </span>
              )
            })}
            {layout.nodes.map((n) => {
              const cc = rowByKey.get(n.key)
              if (!cc && n.std) {
                return (
                  <div
                    key={n.key}
                    className={`thm-node thm-node--word${drag?.over === n.key ? ' is-drop' : ''}`}
                    style={{ left: n.x, top: n.y + 5, width: n.w, height: n.h - 10 }}
                    data-key={n.key}
                    data-testid="std-word"
                    title={t('themes:rel.stdName', { name: stdWords.get(n.key) ?? n.key })}
                    onClick={(ev) => ev.stopPropagation()}
                  >
                    <span className="thm-node-name">{stdWords.get(n.key)}</span>
                  </div>
                )
              }
              if (!cc) return null
              const c = cc.row
              const lonely = p.unlinked.has(n.key)
              const std = isStandardMatched(c)
              const on = p.selectedKey === n.key
              return (
                <button
                  key={n.key}
                  type="button"
                  className={`thm-node thm-node--${c.kind}${std ? ' thm-node--std' : ''}${on ? ' is-on' : ''}${
                    lonely ? ' is-lonely' : ''
                  }${cc.ghost ? ` thm-node--ghost-${cc.ghost}` : ''}${drag?.over === n.key ? ' is-drop' : ''}`}
                  style={{ left: n.x, top: n.y, width: n.w, height: n.h }}
                  aria-pressed={on}
                  data-key={n.key}
                  data-ghost={cc.ghost}
                  title={std && c.match ? t('themes:map.std', { ref: c.match.ref }) : c.reason || undefined}
                  onClick={(ev) => {
                    ev.stopPropagation()
                    p.onSelectEdge(null)
                    p.onSelect(on ? null : n.key)
                  }}
                >
                  <span className="thm-node-name">{conceptName(c, p.lang)}</span>
                  {c.kind === 'property' && <span className="thm-node-kind">{t('themes:kind.property')}</span>}
                  {cc.ghost && (
                    <span className="thm-node-tag">
                      {cc.ghost === 'remove' ? t('themes:map.tagRemove') : t('themes:map.tagProposal')}
                    </span>
                  )}
                  {lonely && !cc.ghost && (
                    <span className="thm-node-warn" role="note">
                      {t('themes:map.lonely')}
                    </span>
                  )}
                </button>
              )
            })}
            {/* 選んだ線（と、選んだ札の線）の端: 持って別の札に落とすと付け替え */}
            {layout.edges.map((e) => {
              const rel = edgeOf(e.index)
              const di = rel?.draftIndex ?? null
              if (di === null || rel?.ghost === 'remove' || rel?.ghost === 'moved') return null
              if (!(isSelectedEdge(e.index) || touches(e))) return null
              const dragging = drag !== null && drag.index === e.index
              return (
                <span
                  key={`h${e.index}`}
                  className={`thm-handle${dragging ? ' is-drag' : ''}`}
                  style={{ left: dragging ? drag.x : e.end.x, top: dragging ? drag.y : e.end.y }}
                  role="button"
                  tabIndex={0}
                  aria-label={t('themes:map.handle')}
                  title={t('themes:map.handle')}
                  data-testid="edge-handle"
                  onClick={(ev) => ev.stopPropagation()}
                  onPointerDown={(ev) => {
                    ev.stopPropagation()
                    ev.preventDefault()
                    const pt = toCanvas(ev.clientX, ev.clientY)
                    p.onSelect(null)
                    p.onSelectEdge(di)
                    setDrag({ index: e.index, ...pt, over: null })
                  }}
                />
              )
            })}
          </div>
        </div>
      </div>
      {scrollsNow && <p className="thm-map-hint">{t('themes:map.scrollHint')}</p>}
      <div className="thm-map-legend" aria-hidden="true">
        <span>
          <i className="thm-sw thm-sw--class" />
          {t('themes:kind.class')}
        </span>
        <span>
          <i className="thm-sw thm-sw--property" />
          {t('themes:kind.property')}
        </span>
        <span>
          <i className="thm-sw thm-sw--std" />
          {t('themes:map.legendStd')}
        </span>
        {hasGhost && (
          <span>
            <i className="thm-sw thm-sw--ghost" />
            {t('themes:map.legendGhost')}
          </span>
        )}
      </div>
    </div>
  )
}
