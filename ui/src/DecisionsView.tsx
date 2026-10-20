import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type Alignment, type CrosswalkPerspective, getAlignments, getCrosswalks, unalign } from './crosswalkApi'
import { type DecidePreview, effectText } from './decide/decide'
import {
  DECISION_FILTERS,
  type DecisionFilter,
  type DecisionsTab,
  decisionSentence,
  filterCounts,
  matchesFilter,
  newestFirst,
  questionCount,
} from './decide/decisionsModel'
import { previewDecision } from './decide/decideApi'
import './decide/decide.css'
import { type CatalogDataset, getCatalogDatasets } from './galleryApi'
import { JobsView } from './JobsView'
import { type AlignRelation, relationKey } from './lineChoice'
import { PerspectiveAlignment } from './PerspectiveAlignment'
import { type SharedTerm, listTerms } from './vocabApi'
import { cqTitleMap } from './vocabQuestion'

// 「決めたこと」（UI 再設計 PR 3）。人が決めた関係を 1 行 1 文で並べ、いつでも取り消せる。
// 取り消す前に、答えられる問いの数がどう変わるかを見せる（試算は store に書かない）。
// タブは「決めたこと」「アクティビティ」（取り込みの履歴 = 従来のアクティビティ画面）。
// 「つながり同士」の絞り込みでは、つながり（crosswalk）どうしの対応づけを出す。

/** つながり同士の対応づけ。つながりの一覧を読んで PerspectiveAlignment に渡す。 */
function PerspectiveScope({
  reloadKey,
  onChanged,
  cqTitles,
}: {
  reloadKey: number
  onChanged: () => void
  cqTitles: Record<string, string>
}) {
  const { t } = useTranslation()
  const [perspectives, setPerspectives] = useState<CrosswalkPerspective[] | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    let off = false
    getCrosswalks()
      .then((p) => !off && setPerspectives(p))
      .catch((e) => !off && setErr(e instanceof Error ? e.message : String(e)))
    return () => {
      off = true
    }
  }, [])
  if (err) return <pre className="error">{err}</pre>
  if (!perspectives)
    return (
      <p className="loading-row">
        <span className="spinner" />
        {t('decisions:loadingPerspectives')}
      </p>
    )
  return (
    <PerspectiveAlignment
      perspectives={perspectives}
      reloadKey={reloadKey}
      onChanged={onChanged}
      cqTitles={cqTitles}
    />
  )
}

type UndoState =
  | { iri: string; status: 'loading' }
  | { iri: string; status: 'ready'; preview: DecidePreview | null }

function DecisionRow({
  a,
  names,
  terms,
  undo,
  removing,
  onAskUndo,
  onCancelUndo,
  onUndo,
}: {
  a: Alignment
  names: Record<string, string>
  terms: SharedTerm[]
  undo: UndoState | null
  removing: boolean
  onAskUndo: (a: Alignment) => void
  onCancelUndo: () => void
  onUndo: (a: Alignment) => void
}) {
  const { t } = useTranslation()
  const q = questionCount(a, terms)
  const open = undo?.iri === a.alignment_iri
  const tag = (kind?: string) =>
    kind && kind !== 'unknown' ? (
      <span className="decisions-tag">{t(`vocab:lines.kind_badge.${kind}`)}</span>
    ) : null
  return (
    <div
      className={`decisions-row${a.broken ? ' decisions-row--broken' : ''}`}
      data-testid="decision-row"
    >
      <div className="decisions-sentence" title={`${a.source} → ${a.target}`}>
        {decisionSentence(a, names, t)}
        {tag(a.source_kind)}
        {tag(a.target_kind)}
      </div>
      {a.broken && (
        <span className="decisions-broken" title={t('decisions:brokenHint')}>
          {t('decisions:broken')}
        </span>
      )}
      {q > 0 && <span className="decisions-pill-q">{t('decisions:questions', { n: q })}</span>}
      <span className="decisions-date">{a.at ? a.at.slice(0, 10) : ''}</span>
      {!open && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          disabled={removing}
          onClick={() => onAskUndo(a)}
        >
          {t('decisions:undo')}
        </button>
      )}
      {open && (
        <div className="decisions-undo" role="group" aria-label={t('decisions:undoAria')}>
          <div data-testid="undo-effect">
            {undo.status === 'loading'
              ? t('decide:effect.loading')
              : undo.preview?.valid
                ? t('decisions:undoLead', { effect: effectText(undo.preview, t) })
                : t('decisions:undoNoCount')}
          </div>
          <div className="decisions-undo-actions">
            <button
              type="button"
              className="btn btn--accent btn--sm"
              disabled={removing || undo.status === 'loading'}
              onClick={() => onUndo(a)}
            >
              {removing ? t('decisions:undoing') : t('decisions:undoConfirm')}
            </button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={onCancelUndo}>
              {t('decisions:cancel')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function DecisionsPane({
  filter,
  onFilterChange,
  reloadKey,
  onChanged,
  cqTitles,
}: {
  filter: DecisionFilter
  onFilterChange: (filter: DecisionFilter) => void
  reloadKey: number
  onChanged: () => void
  cqTitles: Record<string, string>
}) {
  const { t } = useTranslation()
  const [list, setList] = useState<Alignment[] | null>(null)
  const [terms, setTerms] = useState<SharedTerm[]>([])
  const [datasets, setDatasets] = useState<CatalogDataset[]>([])
  const [loadErr, setLoadErr] = useState('')
  const [err, setErr] = useState('')
  const [undo, setUndo] = useState<UndoState | null>(null)
  const [removing, setRemoving] = useState('')
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let off = false
    getAlignments()
      .then((d) => {
        if (off) return
        setLoadErr('')
        setList(d.alignments)
      })
      .catch((e) => {
        if (off) return
        setList(null)
        setLoadErr(e instanceof Error ? e.message : String(e))
      })
    // 補助: 取れなくても一覧は出す（問いの数と、データセット名が欠けるだけ）。
    listTerms()
      .then((ts) => !off && setTerms(ts))
      .catch(() => !off && setTerms([]))
    getCatalogDatasets()
      .then((ds) => !off && setDatasets(ds))
      .catch(() => !off && setDatasets([]))
    return () => {
      off = true
    }
  }, [reloadKey, tick])

  const names = useMemo(() => {
    const m: Record<string, string> = {}
    for (const d of datasets) {
      m[d.id] = d.name
      m[d.id.replace(/^live-/, '')] = d.name
    }
    return m
  }, [datasets])
  const counts = useMemo(() => filterCounts(list ?? []), [list])
  const shown = useMemo(
    () => newestFirst((list ?? []).filter((a) => matchesFilter(a, filter))),
    [list, filter],
  )

  const askUndo = useCallback((a: Alignment) => {
    setErr('')
    setUndo({ iri: a.alignment_iri, status: 'loading' })
    const rel = relationKey(a.relation) as AlignRelation
    // 取り消した場合の帰結（逆向きの試算）。試算できなくても取り消しはできる。
    previewDecision(a.source, a.target, rel, { remove: true })
      .then((preview) =>
        setUndo((cur) => (cur?.iri === a.alignment_iri ? { iri: a.alignment_iri, status: 'ready', preview } : cur)),
      )
      .catch(() =>
        setUndo((cur) =>
          cur?.iri === a.alignment_iri ? { iri: a.alignment_iri, status: 'ready', preview: null } : cur,
        ),
      )
  }, [])

  async function doUndo(a: Alignment) {
    setRemoving(a.alignment_iri)
    setErr('')
    try {
      await unalign(a.source, a.target, a.relation)
      setUndo(null)
      setTick((n) => n + 1)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRemoving('')
    }
  }

  return (
    <>
      <div className="decisions-pills" role="group" aria-label={t('decisions:filterAria')}>
        {DECISION_FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            className="decisions-pill"
            aria-pressed={filter === f}
            onClick={() => {
              onFilterChange(f)
              setUndo(null)
            }}
          >
            {t(`decisions:filter.${f}`)}
            {f !== 'all' && f !== 'perspective' && (f !== 'broken' || counts.broken > 0)
              ? ` ${counts[f]}`
              : ''}
          </button>
        ))}
      </div>
      {filter === 'perspective' && (
        <PerspectiveScope reloadKey={reloadKey} onChanged={onChanged} cqTitles={cqTitles} />
      )}
      {filter !== 'perspective' && loadErr && <p className="promote-err">{loadErr}</p>}
      {filter !== 'perspective' && err && <p className="promote-err">{err}</p>}
      {filter !== 'perspective' && list === null && !loadErr && (
        <p className="loading-row">
          <span className="spinner" />
          {t('decisions:loading')}
        </p>
      )}
      {filter !== 'perspective' && list && list.length === 0 && (
        <p className="ds-empty-note">{t('decisions:empty')}</p>
      )}
      {filter !== 'perspective' && list && list.length > 0 && shown.length === 0 && (
        <p className="ds-empty-note">{t('decisions:emptyFilter')}</p>
      )}
      {filter !== 'perspective' && shown.length > 0 && (
        <div className="decisions-list">
          {shown.map((a) => (
            <DecisionRow
              key={a.alignment_iri}
              a={a}
              names={names}
              terms={terms}
              undo={undo}
              removing={removing === a.alignment_iri}
              onAskUndo={askUndo}
              onCancelUndo={() => setUndo(null)}
              onUndo={(x) => void doUndo(x)}
            />
          ))}
        </div>
      )}
    </>
  )
}

/**
 * 「決めたこと」画面。`tab` と `filter` は hash（`#/decisions?tab=activity` ・`?filter=perspective`）が
 * 唯一の真実源で、変えるときは親（App）が hash を書く。
 */
export function DecisionsView({
  tab,
  filter,
  onTabChange,
  onFilterChange,
}: {
  tab: DecisionsTab
  /** `perspective`（つながり同士）のときだけ、一覧ではなく対応づけの画面を出す。 */
  filter: DecisionFilter
  onTabChange: (tab: DecisionsTab) => void
  onFilterChange: (filter: DecisionFilter) => void
}) {
  const { t } = useTranslation()
  const [version, setVersion] = useState(0)
  const onChanged = () => setVersion((v) => v + 1)
  const [cqTitles, setCqTitles] = useState<Record<string, string>>({})
  useEffect(() => {
    let off = false
    listTerms()
      .then((ts) => !off && setCqTitles(cqTitleMap(ts)))
      .catch(() => !off && setCqTitles({}))
    return () => {
      off = true
    }
  }, [version])

  return (
    <div className="decisions">
      <div className="decisions-tabs" role="group" aria-label={t('decisions:tabsAria')}>
        {(['decisions', 'activity'] as const).map((k) => (
          <button
            key={k}
            type="button"
            className="decisions-tab"
            aria-pressed={tab === k}
            onClick={() => onTabChange(k)}
          >
            {t(`decisions:tab.${k}`)}
          </button>
        ))}
      </div>
      {tab === 'activity' ? (
        <JobsView />
      ) : (
        <>
          <p className="decisions-lead">{t('decisions:lead')}</p>
          <DecisionsPane
            filter={filter}
            onFilterChange={onFilterChange}
            reloadKey={version}
            onChanged={onChanged}
            cqTitles={cqTitles}
          />
        </>
      )}
    </div>
  )
}
