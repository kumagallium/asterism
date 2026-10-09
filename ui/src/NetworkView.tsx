// 「全体グラフ」— 値でつなぐ網（契約メモ contract_network_view.md §3）。
// 描画は sigma（WebGL）。データと配置は networkModel.ts の純関数、取得は networkApi.ts。
// 配置は決定論（初期位置のハッシュ＋固定回数の ForceAtlas2）— 同じデータは同じ絵。
import Graph from 'graphology'
import Sigma from 'sigma'
import type { CameraState } from 'sigma/types'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BigViewOverlay, ExpandButton } from './BigView'
import { bigGraphHeight, useBigViewHeight } from './bigViewSize'
import { resolveSet } from './cards/cardsApi'
import type { SetResolveResult } from './cards/cardsApi'
import { getNetwork } from './networkApi'
import type { NetworkNodeKind, NetworkResponse } from './networkApi'
import {
  REST_ROLE,
  KIND_COLORS,
  ROLE_VAR,
  KIND_COLOR_COUNT,
  edgeAppearance,
  focusCamera,
  focusedKindRoles,
  barDatasetName,
  datasetLabelMap,
  kindQualifiers,
  legendKindRows,
  visibleLegendRows,
  roleCss,
  loadLaidOutNetwork,
  neighborhoodOf,
  nodeAppearance,
  searchNodes,
  emptyKind,
} from './networkModel'
import type { NetworkNodeAttrs } from './networkModel'
import './network.css'

export interface NetworkViewProps {
  /** 「このページを開く」（1 件のページ）。 */
  onOpenSubject: (iri: string) => void
  /** 「一覧で開く」— `resolveSet` の結果を受けて、絞り込みのページへ遷移する。 */
  onOpenSet: (result: SetResolveResult) => void
}


/** CSS 変数を実際の色に解決する（sigma は CSS 変数を読めない）。 */
function cssColor(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

interface Palette {
  roles: Record<string, string>
  edge: string
  edgeFaded: string
  nodeFaded: string
  label: string
}

function readPalette(): Palette {
  const roles: Record<string, string> = {}
  KIND_COLORS.forEach((c, i) => {
    roles[`kind-${i}`] = c
  })
  for (const [role, v] of Object.entries(ROLE_VAR)) roles[role] = cssColor(v, '#888888')
  return {
    roles,
    edge: cssColor('--border-strong', '#c7d4c4'),
    edgeFaded: cssColor('--border', '#dde6da'),
    nodeFaded: cssColor('--border-strong', '#c7d4c4'),
    label: cssColor('--fg', '#16241a'),
  }
}

interface CanvasProps {
  graph: Graph
  height: number
  selectedId: string | null
  focusId: string | null
  focusTick: number
  onSelect: (id: string | null) => void
  ariaLabel: string
  /** 大きく見る等の作り直しをまたいでカメラを引き継ぐ置き場（同じ graph のときだけ復元）。 */
  cameraRef: { current: { graph: Graph; state: CameraState } | null }
  /** 色の役割（種類の鍵 → 役割）と、凡例で押した種類。変わったら sigma.refresh() だけ。 */
  roles: ReadonlyMap<string, string>
  focused: ReadonlySet<string>
}

/** sigma を作って壊すだけの素の使い方（React 19 では公式の @react-sigma は使わない）。 */
function NetworkCanvas({ graph, height, selectedId, focusId, focusTick, onSelect, ariaLabel, cameraRef, roles, focused }: CanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const selRef = useRef<string | null>(selectedId)
  const nearRef = useRef<Set<string>>(neighborhoodOf(graph, selectedId))
  // 載せている点（hover）。あいだは選びより優先して強調する。
  const hovRef = useRef<string | null>(null)
  const rolesRef = useRef(roles)
  const focusedRef = useRef(focused)
  const consumedTickRef = useRef(focusTick)
  const onSelectRef = useRef(onSelect)
  useEffect(() => {
    onSelectRef.current = onSelect
  })

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const pal = readPalette()
    const sigma = new Sigma(graph, host, {
      renderEdgeLabels: false,
      zIndex: true,
      // 名前は重ならないものだけ出す（格子の升ごとに大きい点から）。値の点は大きめなので優先される。
      labelRenderedSizeThreshold: 5,
      labelDensity: 0.6,
      labelGridCellSize: 180,
      labelColor: { color: pal.label },
      defaultEdgeColor: pal.edge,
      minCameraRatio: 0.02,
      maxCameraRatio: 8,
      nodeReducer: (id, data) => {
        const a = data as unknown as NetworkNodeAttrs & Record<string, unknown>
        const sel = hovRef.current ?? selRef.current
        const ap = nodeAppearance({
          nodeKind: a.nodeKind,
          kindKey: a.kindKey,
          roles: rolesRef.current,
          focused: focusedRef.current,
          sel,
          isSel: id === sel,
          isNear: nearRef.current.has(id),
        })
        const out: Record<string, unknown> = {
          ...data,
          color: ap.faded ? pal.nodeFaded : (pal.roles[ap.role] ?? pal.roles[REST_ROLE]),
          zIndex: ap.zIndex,
        }
        if (ap.forceLabel) out.forceLabel = true
        if (ap.highlighted) out.highlighted = true
        if (ap.hideLabel) {
          out.label = null
          out.forceLabel = false
        }
        return out
      },
      edgeReducer: (edge, data) => {
        const [s, t] = graph.extremities(edge)
        const sa = graph.getNodeAttributes(s) as unknown as NetworkNodeAttrs
        const ta = graph.getNodeAttributes(t) as unknown as NetworkNodeAttrs
        const ap = edgeAppearance({
          sel: hovRef.current ?? selRef.current,
          source: s,
          target: t,
          sourceNode: sa,
          targetNode: ta,
          focused: focusedRef.current,
        })
        if (ap.on) return { ...data, color: pal.label, zIndex: ap.zIndex }
        if (ap.faded) return { ...data, color: pal.edgeFaded, zIndex: ap.zIndex }
        return data
      },
    })
    const prev = cameraRef.current
    if (prev && prev.graph === graph) sigma.getCamera().setState(prev.state)
    const highlight = (id: string | null) => {
      hovRef.current = id
      nearRef.current = neighborhoodOf(graph, id ?? selRef.current)
      sigma.refresh()
    }
    sigma.on('enterNode', ({ node }) => highlight(node))
    sigma.on('leaveNode', () => highlight(null))
    sigma.on('clickNode', ({ node }) => onSelectRef.current(node))
    sigma.on('clickStage', () => onSelectRef.current(null))
    sigmaRef.current = sigma
    return () => {
      cameraRef.current = { graph, state: sigma.getCamera().getState() }
      sigma.kill()
      sigmaRef.current = null
    }
  }, [graph, cameraRef])

  useEffect(() => {
    selRef.current = selectedId
    nearRef.current = neighborhoodOf(graph, hovRef.current ?? selectedId)
    sigmaRef.current?.refresh()
  }, [graph, selectedId])

  useEffect(() => {
    rolesRef.current = roles
    focusedRef.current = focused
    sigmaRef.current?.refresh()
  }, [roles, focused])

  useEffect(() => {
    const sigma = sigmaRef.current
    // 探した直後の 1 回だけ寄る（作り直しや来歴の切り替えでは動かさない）
    if (focusTick === consumedTickRef.current) return
    consumedTickRef.current = focusTick
    if (!sigma || !focusId || !graph.hasNode(focusId)) return
    const pts = [...neighborhoodOf(graph, focusId)]
      .map((id) => sigma.getNodeDisplayData(id))
      .filter((d): d is NonNullable<typeof d> => d != null)
    if (pts.length > 0) sigma.getCamera().animate(focusCamera(pts), { duration: 400 })
  }, [graph, focusId, focusTick])

  return (
    <div
      ref={hostRef}
      className="network-canvas"
      style={{ height }}
      role="img"
      aria-label={ariaLabel}
    />
  )
}

const FOLDED_KINDS = 12

interface LegendProps {
  resp: NetworkResponse
  roles: ReadonlyMap<string, string>
  focused: ReadonlySet<string>
  onToggle: (key: string) => void
  onClear: () => void
  expanded: boolean
  onExpandedChange: (v: boolean) => void
}

function Legend({ resp, roles, focused, onToggle, onClear, expanded, onExpandedChange }: LegendProps) {
  const { t } = useTranslation('network')
  const setExpanded = onExpandedChange
  const rows = legendKindRows(resp.kinds, roles, focused)
  const shown = visibleLegendRows(rows, expanded, FOLDED_KINDS)
  const qualifiers = kindQualifiers(rows, resp.kinds, datasetLabelMap(resp.datasets))
  const full = focused.size >= KIND_COLOR_COUNT
  const dot = (role: string) => ({ background: roleCss(role) })
  return (
    <section aria-label={t('legend_title')} className="network-legend-wrap">
      <p className="network-note">{t('legend_kinds_hint', { max: KIND_COLOR_COUNT })}</p>
      <ul className="network-legend network-legend--kinds">
        {shown.map((r) => {
          const disabled = full && !r.pressed
          const q = qualifiers.get(r.key) ?? null
          const shownName = r.name ?? t('legend_unnamed')
          const count = r.count.toLocaleString()
          return (
            <li key={r.key}>
              <button
                type="button"
                className="network-kind"
                aria-pressed={r.pressed}
                disabled={disabled}
                title={
                  disabled
                    ? t('legend_kind_max', { max: KIND_COLOR_COUNT })
                    : q
                      ? `${t('legend_kind_qualified', { name: shownName, qualifier: q, count })} — ${t('legend_kind_title')}`
                      : t('legend_kind_title')
                }
                onClick={() => onToggle(r.key)}
              >
                <span className="network-swatch" style={dot(r.role)} />
                {q
                  ? t('legend_kind_qualified', { name: shownName, qualifier: q, count })
                  : `${shownName} ${count}`}
              </button>
            </li>
          )
        })}
      </ul>
      <div className="network-legend-actions">
        {rows.length > FOLDED_KINDS && (
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? t('legend_collapse') : t('legend_show_all', { count: rows.length })}
          </button>
        )}
        {focused.size > 0 && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClear}>
            {t('legend_clear')}
          </button>
        )}
      </div>
      <p className="network-note">{t('legend_shapes')}</p>
      <ul className="network-legend">
        <li>
          <span className="network-swatch" style={dot('value')} />
          {t('legend_value')}
        </li>
        <li>
          <span className="network-swatch network-swatch--bundle" style={dot(REST_ROLE)} />
          {t('legend_bundle')}
        </li>
        <li>
          <span className="network-swatch" style={dot('hub')} />
          {t('legend_hub')}
        </li>
      </ul>
      <p className="network-note">{t('legend_size')}</p>
    </section>
  )
}

export function NetworkView({ onOpenSubject, onOpenSet }: NetworkViewProps) {
  const { t } = useTranslation('network')
  const [includeProv, setIncludeProv] = useState(false)
  // 取得の結果。prov は「どの切り替えで取ったか」— 違うあいだは古い絵を出さず読み込み中にする。
  const [net, setNet] = useState<{ graph: Graph; response: NetworkResponse; prov: boolean } | null>(null)
  const [failedProv, setFailedProv] = useState<boolean | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  // 凡例で押した種類（押した順）。空なら色は既定（上位 8 種類）。
  const [focusKinds, setFocusKinds] = useState<string[]>([])
  // 凡例の「すべて表示」。「大きく見る」で木が変わって再マウントされても残す
  const [legendExpanded, setLegendExpanded] = useState(false)
  const [query, setQuery] = useState('')
  const [searchNote, setSearchNote] = useState<string | null>(null)
  const [focus, setFocus] = useState<{ id: string | null; tick: number }>({ id: null, tick: 0 })
  const cameraRef = useRef<{ graph: Graph; state: CameraState } | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(false)
  // 大きく見る。状態はここが持ち、描く場所だけ切り替える（2 つ目の図を作らない）。
  const [big, setBig] = useState(false)
  const bigH = useBigViewHeight(big)
  const expandRef = useRef<HTMLButtonElement>(null)
  const wasBigRef = useRef(false)
  useEffect(() => {
    if (wasBigRef.current && !big) expandRef.current?.focus()
    wasBigRef.current = big
  }, [big])

  // 束の名前は言語ごとに組み立てる（サーバは種類の名前だけを返す）。t が変わっても取り直さないよう ref で持つ。
  const tRef = useRef(t)
  useEffect(() => {
    tRef.current = t
  })
  useEffect(() => {
    let cancelled = false
    loadLaidOutNetwork(getNetwork, includeProv, (name, count) =>
      tRef.current('bundle_name', { name, count: count.toLocaleString() }),
    )
      .then((r) => {
        if (cancelled) return
        setNet({ ...r, prov: includeProv })
        const keep = new Set(r.response.kinds.map((k) => k.class_iri))
        setFocusKinds((cur) => {
          const next = cur.filter((k) => keep.has(k))
          return next.length === cur.length ? cur : next
        })
        setFailedProv(null)
        setSelectedId(null)
        setSearchNote(null)
      })
      .catch(() => {
        if (!cancelled) setFailedProv(includeProv)
      })
    return () => {
      cancelled = true
    }
  }, [includeProv])

  const graph = net?.graph ?? null
  const focused = useMemo(() => new Set(focusKinds), [focusKinds])
  const roles = useMemo(
    () => focusedKindRoles(net?.response.kinds ?? [], focused),
    [net, focused],
  )
  const datasetLabels = useMemo(() => datasetLabelMap(net?.response.datasets), [net])
  const selected = useMemo(() => {
    if (!graph || !selectedId || !graph.hasNode(selectedId)) return null
    return { id: selectedId, ...(graph.getNodeAttributes(selectedId) as unknown as NetworkNodeAttrs) }
  }, [graph, selectedId])

  const onSearch = useCallback(() => {
    if (!graph) return
    const hits = searchNodes(graph, query)
    if (hits.length === 0) {
      setSearchNote(query.trim() ? t('search_none') : null)
      return
    }
    setSearchNote(hits.length > 1 ? t('search_many', { count: hits.length }) : null)
    setSelectedId(hits[0])
    setFocus((f) => ({ id: hits[0], tick: f.tick + 1 }))
  }, [graph, query, t])

  const onOpenList = useCallback(async () => {
    if (!selected?.setSpec) return
    setBusy(true)
    setActionError(false)
    try {
      onOpenSet(await resolveSet(selected.setSpec))
    } catch {
      setActionError(true)
    } finally {
      setBusy(false)
    }
  }, [selected, onOpenSet])

  const failed = failedProv === includeProv
  if (!net || !graph) {
    return <div className="network network-note">{failed ? t('error') : t('loading')}</div>
  }

  const { response } = net
  // 切り替え直後（古い絵）や失敗のあいだも、ヘッダ（切り替えと探す）は残す。
  const pending = net.prov !== includeProv
  const isEmpty = response.nodes.length === 0
  const tagOf = (k: NetworkNodeKind) => t(`tag.${k}`)

  const body = (
    <>
      <div className="network-head">
        <span className="network-summary">
          {t('summary', {
            entities: response.stats.entities.toLocaleString(),
            nodes: response.stats.nodes.toLocaleString(),
            values: response.stats.values.toLocaleString(),
            bundles: response.stats.bundles.toLocaleString(),
            edges: response.stats.edges.toLocaleString(),
          })}
        </span>
        <label className="network-toggle" title={t('prov_hint')}>
          <input
            type="checkbox"
            checked={includeProv}
            onChange={(e) => {
              setFailedProv(null)
              setIncludeProv(e.target.checked)
            }}
          />
          {t('prov_toggle')}
        </label>
        <form
          className="network-search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault()
            onSearch()
          }}
        >
          <input
            type="search"
            value={query}
            aria-label={t('search_label')}
            placeholder={t('search_placeholder')}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="submit" className="btn btn--ghost btn--sm">
            {t('search_button')}
          </button>
        </form>
      </div>
      {searchNote && <p className="network-note">{searchNote}</p>}
      {pending ? (
        <p className="network-note" role={failed ? 'alert' : 'status'}>
          {failed ? t('error') : t('loading')}
        </p>
      ) : isEmpty ? (
        <p className="network-note">{emptyKind(response.stats) === 'unpublished' ? t('empty') : t('no_points')}</p>
      ) : (
        <>
          <div className="network-canvas-wrap">
            {!big && <ExpandButton ref={expandRef} onClick={() => setBig(true)} />}
            <NetworkCanvas
              graph={graph}
              height={big ? bigGraphHeight(bigH, 280) : 520}
              selectedId={selectedId}
              focusId={focus.id}
              focusTick={focus.tick}
              onSelect={(id) => {
                setSelectedId(id)
                setActionError(false)
              }}
              ariaLabel={t('aria')}
              cameraRef={cameraRef}
              roles={roles}
              focused={focused}
            />
          </div>
          <div className="network-bar">
            {selected ? (
              <>
                <span className="network-bar-name">{selected.label}</span>
                <span className="network-bar-meta">
                  {[
                    tagOf(selected.nodeKind),
                    selected.classLabel,
                    selected.nodeKind === 'entity' || selected.nodeKind === 'bundle'
                      ? barDatasetName(selected.datasetId, datasetLabels)
                      : null,
                    selected.nodeKind === 'bundle'
                      ? t('bar_bundle', { count: selected.count })
                      : t('bar_degree', { count: selected.degree }),
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
                <div className="network-bar-actions">
                  {(selected.nodeKind === 'entity' || selected.nodeKind === 'hub') && (
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => onOpenSubject(selected.id)}
                    >
                      {t('open_page')}
                    </button>
                  )}
                  {(selected.nodeKind === 'value' || selected.nodeKind === 'bundle') && selected.setSpec && (
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      disabled={busy}
                      onClick={() => void onOpenList()}
                    >
                      {t('open_list')}
                    </button>
                  )}
                </div>
              </>
            ) : (
              <span className="network-bar-meta">{t('bar_hint')}</span>
            )}
          </div>
          {actionError && <p className="network-note network-note--warn">{t('action_error')}</p>}
          {response.truncated && <p className="network-note network-note--warn">{t('truncated')}</p>}
          <Legend
            resp={response}
            roles={roles}
            focused={focused}
            onToggle={(key) =>
              setFocusKinds((cur) =>
                cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key],
              )
            }
            onClear={() => setFocusKinds([])}
            expanded={legendExpanded}
            onExpandedChange={setLegendExpanded}
          />
        </>
      )}
    </>
  )

  return (
    <section className={big ? 'network network--big' : 'network'} aria-label={t('title')}>
      {big ? (
        <>
          <p className="network-note">{t('big_now')}</p>
          <BigViewOverlay open onClose={() => setBig(false)} title={t('title')}>
            <div className="network network--big">{body}</div>
          </BigViewOverlay>
        </>
      ) : (
        body
      )}
    </section>
  )
}
