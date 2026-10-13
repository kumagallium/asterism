import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type Alignment, type CrosswalkPerspective, getAlignments, getCrosswalks, unalign } from './crosswalkApi'
import { DatasetGroundingPanel } from './DatasetGroundingPanel'
import { type CatalogDataset, getCatalogDatasets } from './galleryApi'
import { AlignmentRow, PerspectiveAlignment } from './PerspectiveAlignment'
import { listTerms } from './vocabApi'
import { LINE_SCOPES, type LineScope, apiScope, cqTitleMap, orderLines } from './vocabQuestion'
import './vocabTerms.css'

const RELATIONS = new Set([
  'equivalentClass',
  'subClassOf',
  'equivalentProperty',
  'subPropertyOf',
  'hasQuantityKind',
])

/** 線の絞り込みの切り替え（すべて＋ 4 つ）。押された方に `aria-pressed`。 */
export function ScopeTabs({
  scope,
  onChange,
}: {
  scope: LineScope
  onChange: (next: LineScope) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="vocab-scope-tabs" role="group" aria-label={t('vocab:sections.lines')}>
      {LINE_SCOPES.map((s) => (
        <button
          key={s}
          type="button"
          className="vocab-scope-tab"
          aria-pressed={scope === s}
          onClick={() => onChange(s)}
        >
          {t(`vocab:lines.scope.${s}`)}
        </button>
      ))}
    </div>
  )
}

/** 絞り込んだ線の一覧（ことばへ／データセット同士／すべて）。切れている線は先頭・取り消せる。 */
function LineList({
  scope,
  reloadKey,
  onChanged,
  cqTitles,
}: {
  scope: LineScope
  reloadKey: number
  onChanged: () => void
  cqTitles: Record<string, string>
}) {
  const { t } = useTranslation()
  const [lines, setLines] = useState<Alignment[] | null>(null)
  // 読み込みの誤りと、外す操作の誤りは別に持つ（読み直しで前者だけが消える）。
  const [loadErr, setLoadErr] = useState('')
  const [err, setErr] = useState('')
  const [removing, setRemoving] = useState('')

  useEffect(() => {
    let off = false
    // 成功なら誤りを消し、失敗なら古い一覧は残さず空にする（effect の中で同期に state は触らない）。
    getAlignments(apiScope(scope))
      .then((d) => {
        if (off) return
        setLoadErr('')
        setLines(d.alignments)
      })
      .catch((e) => {
        if (off) return
        setLines(null)
        setLoadErr(e instanceof Error ? e.message : String(e))
      })
    return () => {
      off = true
    }
  }, [scope, reloadKey])

  const relationLabel = (rel: string): string =>
    RELATIONS.has(rel) ? t(`crosswalk:relation.${rel}`) : rel

  async function onRemove(a: Alignment) {
    setRemoving(a.alignment_iri)
    setErr('')
    try {
      await unalign(a.source, a.target, a.relation)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRemoving('')
    }
  }

  return (
    <div className="xw-align">
      {loadErr && <p className="promote-err">{loadErr}</p>}
      {err && <p className="promote-err">{err}</p>}
      {lines === null && !loadErr && (
        <p className="loading-row">
          <span className="spinner" />
          {t('vocab:lines.loading')}
        </p>
      )}
      {lines && lines.length === 0 && <p className="xw-align-none">{t('vocab:lines.empty')}</p>}
      {lines && lines.length > 0 && (
        <div className="xw-align-list">
          {orderLines(lines).map((a) => (
            <AlignmentRow
              key={a.alignment_iri}
              a={a}
              relationLabel={relationLabel}
              removing={removing === a.alignment_iri}
              onRemove={(x) => void onRemove(x)}
              cqTitles={cqTitles}
            />
          ))}
        </div>
      )}
    </div>
  )
}

/** つながり同士の対応づけ（旧 CrosswalkView の PerspectiveAlignment）。つながりの一覧を読んで渡す。 */
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
        {t('vocab:lines.perspectiveLoading')}
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

/** 標準の語へ（旧 DatasetGrounding）。データセットを選ぶと、その種類・項目を標準の語へ合わせられる。 */
function StandardScope({
  initialDataset,
  reloadKey,
  onChanged,
}: {
  initialDataset: string | null
  reloadKey: number
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<CatalogDataset[] | null>(null)
  const [picked, setPicked] = useState(initialDataset ?? '')
  useEffect(() => {
    let off = false
    getCatalogDatasets()
      .then((ds) => !off && setDatasets(ds))
      .catch(() => !off && setDatasets([]))
    return () => {
      off = true
    }
  }, [])
  if (!datasets)
    return (
      <p className="loading-row">
        <span className="spinner" />
        {t('vocab:lines.loading')}
      </p>
    )
  const choices = datasets.filter((d) => d.classIris.length + d.predicates.length > 0)
  if (choices.length === 0) return <p className="ds-empty-note">{t('vocab:lines.datasetNone')}</p>
  const current = choices.find((d) => d.id === picked) ?? choices[0]
  return (
    <div className="vocab-lines-standard">
      <p className="vocab-sec-sub">{t('vocab:lines.standardLead')}</p>
      <label className="vocab-field" style={{ maxWidth: '24rem' }}>
        <span>{t('vocab:lines.datasetPick')}</span>
        <select value={current.id} onChange={(e) => setPicked(e.target.value)}>
          {choices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </label>
      <DatasetGroundingPanel
        key={current.id}
        dataset={current}
        reloadKey={reloadKey}
        onChanged={onChanged}
      />
    </div>
  )
}

/**
 * 線（ADR upper-structure-shared-terms.md §2.5.1 (5)）。ことば・データセット・標準の語のあいだの
 * 線を、種類（scope）で絞って見る。つながり同士の対応づけと標準への合わせ（旧 PerspectiveAlignment・
 * 旧 DatasetGrounding）はここへ移した。
 */
export function VocabLinesSection({
  initialScope = null,
  initialDataset = null,
  reloadKey = 0,
  onChanged,
}: {
  initialScope?: LineScope | null
  initialDataset?: string | null
  /** 親の版。語・問い・線がどの節で変わっても、変わったら一覧を読み直す。 */
  reloadKey?: number
  /** 線を引いた／取り消した（親が版を進める）。 */
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const [scope, setScope] = useState<LineScope>(initialScope ?? 'all')
  const ref = useRef<HTMLElement | null>(null)
  // 線の問い（tool_name）を題で見せるための引き表。引けなければ tool_name のまま出る。
  const [cqTitles, setCqTitles] = useState<Record<string, string>>({})

  useEffect(() => {
    let off = false
    listTerms()
      .then((ts) => !off && setCqTitles(cqTitleMap(ts)))
      .catch(() => !off && setCqTitles({}))
    return () => {
      off = true
    }
  }, [reloadKey])

  // 他の画面から「標準の語へ」などで来たときだけ、この節まで運ぶ。
  useEffect(() => {
    if (initialScope) ref.current?.scrollIntoView({ block: 'start' })
  }, [initialScope])

  return (
    <section className="vocab-sec" id="vocab-lines" ref={ref} aria-labelledby="vocab-lines-h">
      <h2 className="vocab-sec-h" id="vocab-lines-h">
        {t('vocab:sections.lines')}
      </h2>
      <p className="vocab-sec-sub">{t('vocab:lines.lead')}</p>
      <ScopeTabs scope={scope} onChange={setScope} />
      {scope === 'perspective' ? (
        <PerspectiveScope reloadKey={reloadKey} onChanged={onChanged} cqTitles={cqTitles} />
      ) : scope === 'standard' ? (
        <StandardScope initialDataset={initialDataset} reloadKey={reloadKey} onChanged={onChanged} />
      ) : (
        <LineList
          key={scope}
          scope={scope}
          reloadKey={reloadKey}
          onChanged={onChanged}
          cqTitles={cqTitles}
        />
      )}
    </section>
  )
}
