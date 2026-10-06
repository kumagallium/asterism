// 1 件から「つながり」を 1 段ずつ広げる図（契約メモ contract_graph_explore.md §3）。
// 図は GraphView（React Flow）。箱を押す＝選ぶ → 下の帯に操作を出す（ホバーでは
// 出さない）。隣のデータは `GET /api/subjects/neighbors`。開くまで API を呼ばない
// （親が開いたときだけこのコンポーネントを置く）。
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getNeighbors, resolveSet } from './cardsApi'
import type { SetResolveResult, SetSpec } from './cardsApi'
import { GraphView } from './GraphView'
import {
  MAX_BOXES,
  buildNeighborGraph,
  collapseNode,
  columnLayout,
  initialState,
  isOverLimit,
  openBundle,
  openNode,
} from './neighborGraph'
import type { ExplorerState } from './neighborGraph'
import './explorer.css'

export interface NeighborExplorerProps {
  iri: string
  /** 「このページを開く」。 */
  onOpenSubject: (iri: string) => void
  /** 「一覧で開く」— `resolveSet` の結果を受けて、絞り込みのページへ遷移する。 */
  onOpenSet: (result: SetResolveResult) => void
}

type Load = 'loading' | 'error' | 'empty' | 'ok'
type Notice = 'limit' | 'limit_initial' | 'error' | 'no_neighbors' | null

export function NeighborExplorer({ iri, onOpenSubject, onOpenSet }: NeighborExplorerProps) {
  const { t } = useTranslation('cards')
  const [load, setLoad] = useState<Load>('loading')
  const [state, setState] = useState<ExplorerState | null>(null)
  const [selectedId, setSelectedId] = useState<string>(iri)
  const [notice, setNotice] = useState<Notice>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    getNeighbors(iri)
      .then((r) => {
        if (cancelled) return
        if (!r.found || !r.center) {
          setLoad('empty')
          return
        }
        const s0 = initialState(r)
        setState(s0)
        // 最初の 1 段で上限を超えるときも黙らない（枠の中で言う）。
        // 最初の 1 段で上限を超えたときは、たためる箱が無い — 「たたむ」を勧めない文言にする
        setNotice(isOverLimit(s0) ? 'limit_initial' : null)
        setSelectedId(r.iri)
        setLoad('ok')
      })
      .catch(() => {
        if (!cancelled) setLoad('error')
      })
    return () => {
      cancelled = true
    }
  }, [iri])

  const view = useMemo(
    () =>
      state
        ? buildNeighborGraph(state, {
            bundleName: (name, count) => t('explore.bundle_name', { name, count }),
            restName: (name, count) => t('explore.bundle_rest', { name, count }),
          })
        : null,
    [state, t],
  )
  const layout = useMemo(() => (view ? columnLayout(view.columns) : undefined), [view])

  const selected = view ? (view.meta.get(selectedId) ?? view.meta.get(iri)) : undefined

  const onOpenNode = useCallback(
    async (target: string) => {
      if (!state) return
      setBusy(true)
      setNotice(null)
      try {
        const r = state.data[target] ?? (await getNeighbors(target))
        const res = openNode(state, target, r)
        setState(res.state)
        if (res.blocked) setNotice('limit')
        else if (r.groups.length === 0) setNotice('no_neighbors')
      } catch {
        setNotice('error')
      } finally {
        setBusy(false)
      }
    },
    [state],
  )

  const onPeek = useCallback(
    (bundleNodeId: string) => {
      if (!state) return
      const res = openBundle(state, bundleNodeId)
      setState(res.state)
      setNotice(res.blocked ? 'limit' : null)
    },
    [state],
  )

  const onOpenList = useCallback(
    async (spec: SetSpec | null) => {
      if (!spec) return
      setBusy(true)
      setNotice(null)
      try {
        onOpenSet(await resolveSet(spec))
      } catch {
        setNotice('error')
      } finally {
        setBusy(false)
      }
    },
    [onOpenSet],
  )

  if (load === 'loading') return <div className="explorer explorer-note">{t('explore.loading')}</div>
  if (load === 'error') return <div className="explorer explorer-note">{t('explore.error')}</div>
  if (load === 'empty' || !state || !view || !layout) {
    return <div className="explorer explorer-note">{t('explore.empty')}</div>
  }

  const centerLabel = view.meta.get(state.centerIri)?.label ?? ''
  const noticeText =
    notice === 'limit'
      ? t('explore.limit', { max: MAX_BOXES })
      : notice === 'limit_initial'
        ? t('explore.limit_initial', { max: MAX_BOXES })
        : notice === 'error'
          ? t('explore.action_error')
          : notice === 'no_neighbors'
            ? t('explore.no_neighbors')
            : null

  return (
    <section className="explorer" aria-label={t('explore.title')}>
      <GraphView
        graph={view.graph}
        ariaLabel={t('explore.aria', { label: centerLabel })}
        onNodeClick={(id) => {
          setSelectedId(id)
          setNotice(null)
        }}
        maxHeight={440}
        layout={layout}
        selectedId={selected?.id}
      />
      <div className="explorer-bar">
        {selected && (
          <>
            <div className="explorer-bar-who">
              <span className="explorer-bar-name">{selected.label}</span>
              {selected.classLabel && <span className="explorer-bar-kind">{selected.classLabel}</span>}
              {selected.isHub && <span className="explorer-bar-kind">{t('explore.hub_tag')}</span>}
            </div>
            <div className="explorer-bar-actions">
              {selected.kind !== 'bundle' && selected.iri && (
                <>
                  {selected.isOpen ? (
                    selected.kind !== 'center' && (
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => {
                          setState(collapseNode(state, selected.iri as string))
                          setNotice(null)
                        }}
                      >
                        {t('explore.collapse')}
                      </button>
                    )
                  ) : (
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      disabled={busy}
                      onClick={() => void onOpenNode(selected.iri as string)}
                    >
                      {t('explore.open_neighbors')}
                    </button>
                  )}
                  {selected.kind !== 'center' && (
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => onOpenSubject(selected.iri as string)}
                    >
                      {t('explore.open_page')}
                    </button>
                  )}
                </>
              )}
              {selected.kind === 'bundle' && selected.bundle && !selected.opened && selected.bundle.group.set_spec && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={busy}
                  onClick={() => void onOpenList(selected.bundle?.group.set_spec ?? null)}
                >
                  {t('explore.open_list')}
                </button>
              )}
              {selected.kind === 'bundle' && selected.bundle && !selected.opened && !selected.bundle.group.set_spec && (
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => onPeek(selected.id)}>
                  {t('explore.peek')}
                </button>
              )}
            </div>
            {selected.kind === 'bundle' && selected.bundle && (
              <p className="explorer-bar-hint">
                {selected.opened
                  ? t('explore.rest_note', { n: selected.bundle.remaining })
                  : selected.bundle.group.set_spec
                    ? t('explore.set_hint', { count: selected.bundle.group.count })
                    : t('explore.peek_hint', { count: selected.bundle.group.count })}
              </p>
            )}
          </>
        )}
      </div>
      {noticeText && <p className="explorer-note explorer-note--warn">{noticeText}</p>}
      {view.truncated && <p className="explorer-note">{t('explore.truncated')}</p>}
      <p className="explorer-note">{t('explore.legend')}</p>
    </section>
  )
}
