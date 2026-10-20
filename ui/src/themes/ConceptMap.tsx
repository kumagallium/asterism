import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { layoutConceptMap } from './conceptMapLayout'
import { type ConceptRow, type RelKind, type RelationRow, type StandardInfo, conceptName } from './themeDraft'

// 知識の図（②の左 2/3）。星図と同じ紙の見た目。札＝概念（種類は --entity、項目は --activity の薄塗り、
// 標準用語に一致したものは地平線の色の枠）、線＝関係。一種・一部は矢じり付きで段をまたぎ、
// 下げる／上げる／変えるは同じ段の横の線に「↓」「↑」「↕」の札、同じものは両向き。
// 配置は conceptMapLayout.ts（純関数）。点を押すと右の文が光り、押した点の下に
// 「ここを広げる」「外す」が出る。問いにつながらない概念には印を付け、確定の数から外す。

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

export interface ConceptMapProps {
  concepts: ConceptRow[]
  relations: RelationRow[]
  /** 標準の語（curie → 表示名）。関係の端にあれば、地平線の色の小さな札として図に出す。 */
  standards: Record<string, StandardInfo>
  lang: string
  selectedKey: string | null
  /** 右の文に重ねたポインタ。その関係の線を光らせる。 */
  litRelation: number | null
  /** 問いにつながっていない概念。 */
  unlinked: ReadonlySet<string>
  /** 広げている最中の概念。 */
  expandingKey: string | null
  /** 「ここを広げる」が使えるか（AI が設定されているか）。 */
  canExpand: boolean
  onSelect: (key: string | null) => void
  onExpand: (key: string) => void
  onRemove: (key: string) => void
}

function isStandardMatched(c: ConceptRow): boolean {
  return c.use.startsWith('standard:') || c.match?.kind === 'standard' || c.match?.kind === 'quantity'
}

export function ConceptMap(p: ConceptMapProps) {
  const { t } = useTranslation()
  const rowByKey = useMemo(() => new Map(p.concepts.map((c) => [c.key, c])), [p.concepts])
  // 関係の端が概念でなく標準の語（curie）なら、その語を小さな札として足す（文の「…は Thermal Conductivity（標準）」と図の線を対応させる）
  const stdWords = useMemo(() => {
    const keys = new Set(p.concepts.map((c) => c.key))
    const out = new Map<string, string>()
    for (const r of p.relations) {
      for (const end of [r.from, r.to]) {
        if (keys.has(end) || out.has(end) || !end.includes(':')) continue
        const info = p.standards[end]
        out.set(end, info?.label || end.slice(end.indexOf(':') + 1))
      }
    }
    return out
  }, [p.concepts, p.relations, p.standards])
  const layout = useMemo(
    () =>
      layoutConceptMap(
        [
          ...p.concepts.map((c) => ({ key: c.key, label: conceptName(c, p.lang), mark: c.kind === 'property' })),
          ...[...stdWords].map(([key, label]) => ({ key, label, std: true })),
        ],
        p.relations.map((r, index) => ({
          index,
          from: r.from,
          to: r.to,
          rel: r.rel,
          problem: r.problem !== null,
        })),
      ),
    [p.concepts, p.relations, p.lang, stdWords],
  )

  // 図は置き場の幅に縮めて収める（縮める下限を下回るときだけ横にスクロール）
  const isEmpty = p.concepts.length === 0
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
  const scale = avail > 0 && layout.width > avail ? Math.max(MIN_SCALE, avail / layout.width) : 1

  if (isEmpty) {
    return (
      <div className="thm-map thm-map--empty" data-testid="concept-map">
        <p className="ds-empty-note">{t('themes:map.empty')}</p>
      </div>
    )
  }

  const sel = p.selectedKey ? layout.byKey.get(p.selectedKey) : undefined
  const touches = (e: { from: string; to: string }) =>
    !!p.selectedKey && (e.from === p.selectedKey || e.to === p.selectedKey)
  const dimming = !!p.selectedKey || p.litRelation !== null
  const actionsW = 232

  return (
    <div className="thm-map" data-testid="concept-map">
      <div className="thm-map-scroll" ref={wrapRef}>
        <div
          className="thm-map-fit"
          style={{ width: layout.width * scale, height: layout.height * scale + (sel ? 44 : 0) }}
        >
          <div
            className="thm-map-canvas"
            style={{
              width: layout.width,
              height: layout.height + (sel ? 40 : 0),
              transform: scale < 1 ? `scale(${scale})` : undefined,
            }}
            onClick={() => p.onSelect(null)}
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
                const lit = p.litRelation === e.index || touches(e)
                return (
                  <path
                    key={e.index}
                    d={e.path}
                    className={`thm-line thm-line--${k}${lit ? ' is-lit' : dimming ? ' is-dim' : ''}`}
                    markerEnd={`url(#thm-arrow-${k})`}
                    markerStart={e.both ? `url(#thm-arrow-${k})` : undefined}
                    data-rel={e.rel}
                  />
                )
              })}
            </svg>
            {layout.edges.map((e) => {
              const text = e.problem ? '×' : e.kind === 'hier' || e.rel === 'quantity' ? t(`themes:map.pill.${e.rel}`) : PILL[e.rel]
              if (!text) return null
              const lit = p.litRelation === e.index || touches(e)
              return (
                <span
                  key={e.index}
                  className={`thm-pill thm-pill--${e.problem ? 'bad' : e.kind}${lit ? ' is-lit' : dimming ? ' is-dim' : ''}`}
                  style={{ left: e.mid.x, top: e.mid.y }}
                  title={t(`themes:map.pillTitle.${e.rel}`)}
                >
                  {text}
                </span>
              )
            })}
            {layout.nodes.map((n) => {
              const c = rowByKey.get(n.key)
              if (!c && n.std) {
                return (
                  <div
                    key={n.key}
                    className="thm-node thm-node--word"
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
              if (!c) return null
              const lonely = p.unlinked.has(n.key)
              const std = isStandardMatched(c)
              const on = p.selectedKey === n.key
              return (
                <button
                  key={n.key}
                  type="button"
                  className={`thm-node thm-node--${c.kind}${std ? ' thm-node--std' : ''}${on ? ' is-on' : ''}${lonely ? ' is-lonely' : ''}`}
                  style={{ left: n.x, top: n.y, width: n.w, height: n.h }}
                  aria-pressed={on}
                  data-key={n.key}
                  title={std && c.match ? t('themes:map.std', { ref: c.match.ref }) : c.reason || undefined}
                  onClick={(ev) => {
                    ev.stopPropagation()
                    p.onSelect(on ? null : n.key)
                  }}
                >
                  <span className="thm-node-name">{conceptName(c, p.lang)}</span>
                  {c.kind === 'property' && <span className="thm-node-kind">{t('themes:kind.property')}</span>}
                  {lonely && (
                    <span className="thm-node-warn" role="note">
                      {t('themes:map.lonely')}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
          {sel && p.selectedKey && (
            <div
              className="thm-map-actions"
              style={{
                top: (sel.y + sel.h) * scale + (p.unlinked.has(p.selectedKey) ? 18 : 6),
                left: Math.max(0, Math.min(sel.x * scale, layout.width * scale - actionsW)),
              }}
              onClick={(ev) => ev.stopPropagation()}
            >
              <button
                type="button"
                className="btn btn--sm"
                disabled={!p.canExpand || p.expandingKey !== null}
                title={p.canExpand ? undefined : t('themes:map.expandOff')}
                onClick={() => p.onExpand(p.selectedKey!)}
              >
                {p.expandingKey === p.selectedKey ? t('themes:map.expanding') : t('themes:map.expand')}
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => p.onRemove(p.selectedKey!)}>
                {t('themes:map.remove')}
              </button>
            </div>
          )}
        </div>
      </div>
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
      </div>
    </div>
  )
}
