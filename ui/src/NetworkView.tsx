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
  kindDisplayName,
  roleCss,
  alwaysLabeled,
  loadLaidOutNetwork,
  neighborhoodOf,
  searchNodes,
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
}

/** sigma を作って壊すだけの素の使い方（React 19 では公式の @react-sigma は使わない）。 */
function NetworkCanvas({ graph, height, selectedId, focusId, focusTick, onSelect, ariaLabel, cameraRef }: CanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const selRef = useRef<string | null>(selectedId)
  const nearRef = useRef<Set<string>>(neighborhoodOf(graph, selectedId))
  // 載せている点（hover）。あいだは選びより優先して強調する。
  const hovRef = useRef<string | null>(null)
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
      labelRenderedSizeThreshold: 9,
      labelColor: { color: pal.label },
      defaultEdgeColor: pal.edge,
      minCameraRatio: 0.02,
      maxCameraRatio: 8,
      nodeReducer: (id, data) => {
        const a = data as unknown as NetworkNodeAttrs & Record<string, unknown>
        const color = pal.roles[a.role] ?? pal.roles[REST_ROLE]
        const sel = hovRef.current ?? selRef.current
        const out: Record<string, unknown> = { ...data, color }
        if (alwaysLabeled(a.nodeKind)) out.forceLabel = true
        if (sel) {
          if (id === sel) {
            out.forceLabel = true
            out.highlighted = true
            out.zIndex = 2
          } else if (nearRef.current.has(id)) {
            out.zIndex = 1
          } else {
            out.color = pal.nodeFaded
            out.label = null
            out.forceLabel = false
            out.zIndex = 0
          }
        }
        return out
      },
      edgeReducer: (edge, data) => {
        const sel = hovRef.current ?? selRef.current
        if (!sel) return data
        const [s, t] = graph.extremities(edge)
        const on = s === sel || t === sel
        return on
          ? { ...data, color: pal.label, zIndex: 1 }
          : { ...data, color: pal.edgeFaded, zIndex: 0 }
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
    const sigma = sigmaRef.current
    // 探した直後の 1 回だけ寄る（作り直しや来歴の切り替えでは動かさない）
    if (focusTick === consumedTickRef.current) return
    consumedTickRef.current = focusTick
    if (!sigma || !focusId || !graph.hasNode(focusId)) return
    const d = sigma.getNodeDisplayData(focusId)
    if (d) sigma.getCamera().animate({ x: d.x, y: d.y, ratio: 0.15 }, { duration: 400 })
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

function Legend({ resp }: { resp: NetworkResponse }) {
  const { t } = useTranslation('network')
  const shown = resp.kinds.slice(0, 8)
  const hasRest = resp.kinds.length > 8
  const dot = (role: string) => ({ background: roleCss(role) })
  return (
    <section aria-label={t('legend_title')}>
      <ul className="network-legend">
        {shown.map((k, i) => (
          <li key={k.class_iri}>
            <span className="network-swatch" style={dot(`kind-${i}`)} />
            {kindDisplayName(k.class_label, k.class_iri) ?? t('legend_unnamed')}
          </li>
        ))}
        {hasRest && (
          <li>
            <span className="network-swatch" style={dot(REST_ROLE)} />
            {t('legend_rest')}
          </li>
        )}
        <li>
          <span className="network-swatch" style={dot('value')} />
          {t('legend_value')}
        </li>
        <li>
          <span className="network-swatch network-swatch--bundle" style={dot('kind-0')} />
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
        <p className="network-note">{response.stats.entities === 0 ? t('empty') : t('no_points')}</p>
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
          <Legend resp={response} />
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
