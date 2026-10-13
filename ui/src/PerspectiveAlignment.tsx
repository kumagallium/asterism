import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  align,
  type AlignEndKind,
  type Alignment,
  type AlignmentsResult,
  type CrosswalkPerspective,
  getAlignments,
  unalign,
} from './crosswalkApi'
import { perspectiveDisplayName } from './crosswalkLabels'
import { ArrowIcon } from './icons'
import { localName } from './vocab'
import { cqLabel } from './vocabQuestion'

// 「つながりどうしを対応づける」— 旧 CrosswalkView の PerspectiveAlignment を「ことば」画面の
// 「線」の節へ移したもの（ADR upper-structure-shared-terms.md §2.5.1 (5)・§3.1 (b)）。
// 中身は移しただけで、変えたのは次の 2 点: 一覧を api の `scope=perspective` で絞る（旧「ここに
// 出ていない対応づけ」の折り畳みは、絞り込みの切り替えが担うので無い）／1 行に「切れている」
// 札と問い（CQ）を出す。

// --- 視点をつなぐ (multi-perspective ADR §Phase 2) -------------------------------
// Assert a human-vetted, citable, reversible SCHEMA relationship between two
// perspectives' terms (a concept class or its link predicate). Closed relation set;
// stored in a promoted alignment graph the FROM-merge unions. Oxigraph runs no OWL
// reasoner, so this is a fact a tool can FOLLOW — it never rewrites queries.

const RELATION_KEYS = new Set([
  'equivalentClass',
  'subClassOf',
  'equivalentProperty',
  'subPropertyOf',
  'hasQuantityKind',
])
const CLASS_RELATIONS = new Set(['equivalentClass', 'subClassOf'])

interface PerspTerm {
  iri: string
  kind: 'class' | 'property'
  conceptName: string
  name: string
}

function usePerspName(): (p: CrosswalkPerspective) => string {
  const { t } = useTranslation()
  return (p) => perspectiveDisplayName(p) ?? t('crosswalk:view.unnamed')
}

/** A perspective's alignable terms: each concept contributes its class + its link
 * predicate. */
function perspectiveTerms(p: CrosswalkPerspective | undefined): PerspTerm[] {
  const out: PerspTerm[] = []
  for (const c of p?.config?.concepts ?? []) {
    if (c.class_iri)
      out.push({ iri: c.class_iri, kind: 'class', conceptName: c.name, name: localName(c.class_iri) })
    if (c.link_predicate)
      out.push({
        iri: c.link_predicate,
        kind: 'property',
        conceptName: c.name,
        name: localName(c.link_predicate),
      })
  }
  return out
}

export function PerspectiveAlignment({
  perspectives,
  reloadKey = 0,
  onChanged,
  cqTitles,
}: {
  perspectives: CrosswalkPerspective[]
  /** 親の版。変わったら対応づけの一覧を読み直す（ほかの節で線が変わったとき）。 */
  reloadKey?: number
  /** 線を引いた／取り消した（親が版を進め、ほかの節も読み直す）。 */
  onChanged: () => void
  /** 線の問い（tool_name）→ 題。 */
  cqTitles?: Record<string, string>
}) {
  const { t } = useTranslation()
  const perspName = usePerspName()
  const relationLabel = (rel: string): string =>
    RELATION_KEYS.has(rel) ? t(`crosswalk:relation.${rel}`) : rel
  const [data, setData] = useState<AlignmentsResult | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [srcPid, setSrcPid] = useState('')
  const [srcIri, setSrcIri] = useState('')
  const [relation, setRelation] = useState('')
  const [tgtPid, setTgtPid] = useState('')
  const [tgtIri, setTgtIri] = useState('')
  const [busy, setBusy] = useState(false)
  const [actErr, setActErr] = useState('')
  const [note, setNote] = useState('')
  const [removing, setRemoving] = useState('')

  useEffect(() => {
    let off = false
    // 成功なら誤りを消し、失敗なら古い一覧は残さず空にする（effect の中で同期に state は触らない）。
    getAlignments('perspective')
      .then((d) => {
        if (off) return
        setLoadErr('')
        setData(d)
      })
      .catch((e) => {
        if (off) return
        setData(null)
        setLoadErr(e instanceof Error ? e.message : String(e))
      })
    return () => {
      off = true
    }
  }, [reloadKey])

  // Effective (fallback-resolved) selections, so the controlled selects stay valid as
  // the user narrows source kind / perspectives.
  const srcPersp = perspectives.find((p) => p.perspective_id === srcPid) ?? perspectives[0]
  const tgtPersp =
    perspectives.find((p) => p.perspective_id === tgtPid) ?? perspectives[1] ?? perspectives[0]
  const srcTerms = perspectiveTerms(srcPersp)
  const srcTerm = srcTerms.find((t) => t.iri === srcIri) ?? srcTerms[0]
  const kind = srcTerm?.kind ?? 'class'
  const relOptions = (data?.relations ?? []).filter((r) =>
    kind === 'class' ? CLASS_RELATIONS.has(r) : !CLASS_RELATIONS.has(r),
  )
  const rel = relOptions.includes(relation) ? relation : relOptions[0]
  // Target term must be the same kind as the source (a class aligns to a class).
  const tgtTerms = perspectiveTerms(tgtPersp).filter((t) => t.kind === kind)
  const tgtTerm = tgtTerms.find((t) => t.iri === tgtIri) ?? tgtTerms[0]

  const canAssert = Boolean(srcTerm && tgtTerm && rel && srcTerm.iri !== tgtTerm.iri)
  // Two perspectives built on the SAME concept key share one hub term (xw:Composition),
  // so there is nothing to align — say that instead of "pick two different concepts".
  const sameTerm = Boolean(srcTerm && tgtTerm && srcTerm.iri === tgtTerm.iri)

  async function onAssert() {
    if (!canAssert || !srcTerm || !tgtTerm || !srcPersp || !tgtPersp) return
    setBusy(true)
    setActErr('')
    setNote('')
    try {
      await align(srcTerm.iri, tgtTerm.iri, rel, perspName(srcPersp), perspName(tgtPersp))
      setNote(
        t('crosswalk:align.assertNote', {
          source: srcTerm.name,
          relation: relationLabel(rel),
          target: tgtTerm.name,
        }),
      )
      onChanged()
    } catch (e) {
      setActErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function onRemove(a: Alignment) {
    setRemoving(a.alignment_iri)
    setActErr('')
    setNote('')
    try {
      await unalign(a.source, a.target, a.relation)
      onChanged()
    } catch (e) {
      setActErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRemoving('')
    }
  }

  // 端の種類（perspective）で絞った一覧は api が返す（scope=perspective）。標準への合わせは
  // 「線」の「標準の語へ」、ことばへの線は「ことばへ」の絞り込みで見る。
  const alignments = data?.alignments ?? []

  // Nothing to author and nothing asserted: an empty form reads as "pick your datasets
  // here" and is where 初見 gets stuck. Say nothing rather than show empty selects.
  if (perspectives.length === 0 && alignments.length === 0) return null

  return (
    <div className="xw-align">
      <div className="ds-subhead xw-tools-head">
        {t('crosswalk:align.head')}
        <span className="xw-hint-inline">{t('crosswalk:align.hint')}</span>
      </div>

      {loadErr && <pre className="error">{loadErr}</pre>}

      {/* Aligning needs two crosswalks to align BETWEEN — until then the form would be
          a row of empty selects, which reads as "choose your datasets here". */}
      {perspectives.length < 2 ? (
        <p className="xw-align-gate">
          {t('crosswalk:align.needTwo', { count: perspectives.length })}
        </p>
      ) : (
        <>
      {/* Authoring form: pick two perspectives' terms + a closed-set relation. */}
      <div className="xw-align-form">
        <div className="xw-align-side">
          <span className="xw-align-side-label">{t('crosswalk:align.sourceLabel')}</span>
          <select
            className="xw-map-select"
            aria-label={t('crosswalk:align.a11y.srcPerspective')}
            value={srcPersp?.perspective_id ?? ''}
            onChange={(e) => {
              setSrcPid(e.target.value)
              setSrcIri('')
            }}
            disabled={perspectives.length === 0}
          >
            {perspectives.map((p) => (
              <option key={p.perspective_id} value={p.perspective_id}>
                {perspName(p)}
              </option>
            ))}
          </select>
          <select
            className="xw-map-select"
            aria-label={t('crosswalk:align.a11y.srcTerm')}
            value={srcTerm?.iri ?? ''}
            onChange={(e) => setSrcIri(e.target.value)}
            disabled={srcTerms.length === 0}
          >
            {srcTerms.map((term) => (
              <option key={term.iri} value={term.iri}>
                {t('crosswalk:align.termOption', {
                  kind: term.kind === 'class' ? t('crosswalk:term.class') : t('crosswalk:term.property'),
                  name: term.name,
                })}
              </option>
            ))}
          </select>
        </div>

        <div className="xw-align-rel">
          <select
            className="xw-map-select"
            aria-label={t('crosswalk:align.a11y.relation')}
            value={rel ?? ''}
            onChange={(e) => setRelation(e.target.value)}
            disabled={relOptions.length === 0}
          >
            {relOptions.map((r) => (
              <option key={r} value={r}>
                {relationLabel(r)}
              </option>
            ))}
          </select>
          <ArrowIcon size={16} className="xw-align-arrow" />
        </div>

        <div className="xw-align-side">
          <span className="xw-align-side-label">{t('crosswalk:align.targetLabel')}</span>
          <select
            className="xw-map-select"
            aria-label={t('crosswalk:align.a11y.tgtPerspective')}
            value={tgtPersp?.perspective_id ?? ''}
            onChange={(e) => {
              setTgtPid(e.target.value)
              setTgtIri('')
            }}
            disabled={perspectives.length === 0}
          >
            {perspectives.map((p) => (
              <option key={p.perspective_id} value={p.perspective_id}>
                {perspName(p)}
              </option>
            ))}
          </select>
          <select
            className="xw-map-select"
            aria-label={t('crosswalk:align.a11y.tgtTerm')}
            value={tgtTerm?.iri ?? ''}
            onChange={(e) => setTgtIri(e.target.value)}
            disabled={tgtTerms.length === 0}
          >
            {tgtTerms.map((term) => (
              <option key={term.iri} value={term.iri}>
                {t('crosswalk:align.termOption', {
                  kind: term.kind === 'class' ? t('crosswalk:term.class') : t('crosswalk:term.property'),
                  name: term.name,
                })}
              </option>
            ))}
          </select>
        </div>

        <button
          type="button"
          className="btn btn--accent btn--sm xw-align-btn"
          disabled={!canAssert || busy}
          onClick={onAssert}
        >
          {busy ? t('crosswalk:align.asserting') : t('crosswalk:align.assert')}
        </button>
      </div>

      {!canAssert && (
        <p className="xw-align-empty-hint">
          {srcTerms.length === 0
            ? t('crosswalk:align.noSrcTerms')
            : tgtTerms.length === 0
              ? t('crosswalk:align.noTgtTerms')
              : sameTerm
                ? t('crosswalk:align.sameTerm')
                : t('crosswalk:align.pickDistinct')}
        </p>
      )}
        </>
      )}
      {note && <p className="lifecycle-ok">{note}</p>}
      {actErr && <p className="promote-err">{t('crosswalk:align.actErr', { detail: actErr })}</p>}

      {/* The asserted alignments (each withdrawable). */}
      {alignments.length > 0 ? (
        <div className="xw-align-list">
          {alignments.map((a) => (
            <AlignmentRow
              key={a.alignment_iri}
              a={a}
              relationLabel={relationLabel}
              removing={removing === a.alignment_iri}
              onRemove={onRemove}
              cqTitles={cqTitles}
            />
          ))}
        </div>
      ) : (
        data && <p className="xw-align-none">{t('crosswalk:align.none')}</p>
      )}

    </div>
  )
}

/** One asserted alignment: the claim, where it came from, and its withdrawal. */
export function AlignmentRow({
  a,
  relationLabel,
  removing,
  onRemove,
  cqTitles,
}: {
  a: Alignment
  relationLabel: (rel: string) => string
  removing: boolean
  onRemove: (a: Alignment) => void
  /** 線の問い（tool_name）→ 題。引けなければ tool_name のまま出す。 */
  cqTitles?: Record<string, string>
}) {
  const { t } = useTranslation()
  return (
    <div className="xw-align-row">
      <div className="xw-align-claim">
        <EndKindBadge kind={a.source_kind} datasetId={a.source_dataset} />
        <code className="xw-align-term" title={a.source}>
          {localName(a.source)}
        </code>
        <span className="xw-align-relchip" title={a.relation}>
          {relationLabel(a.relation)}
        </span>
        <EndKindBadge kind={a.target_kind} datasetId={a.target_dataset} />
        <code className="xw-align-term" title={a.target}>
          {localName(a.target)}
        </code>
      </div>
      <div className="xw-align-meta">
        {/* 結ばれていた先が今は無い線（ことば／データセットが消えた）。取り消しはできる。 */}
        {a.broken && (
          <span className="vocab-line-broken" title={t('vocab:lines.brokenHint')}>
            {t('vocab:lines.broken')}
          </span>
        )}
        {a.cq && (
          <span className="vocab-line-cq">{t('vocab:lines.cq', { cq: cqLabel(cqTitles, a.cq) })}</span>
        )}
        {(a.from_perspective || a.to_perspective) && (
          <span className="xw-align-persp">
            {t('crosswalk:align.perspArrow', {
              from: a.from_perspective || '—',
              to: a.to_perspective || '—',
            })}
          </span>
        )}
        {a.at && <span className="xw-built-at">{a.at.slice(0, 19).replace('T', ' ')}</span>}
      </div>
      <button
        type="button"
        className="btn btn--ghost btn--sm xw-align-remove"
        disabled={removing}
        onClick={() => onRemove(a)}
      >
        {removing ? t('crosswalk:align.removing') : t('crosswalk:align.remove')}
      </button>
    </div>
  )
}

/** 線の端の種類の札（データセットなら id つき）。素性不明（unknown）と未設定は札なし。 */
export function EndKindBadge({
  kind,
  datasetId,
}: {
  kind?: AlignEndKind
  datasetId?: string | null
}) {
  const { t } = useTranslation()
  if (!kind || kind === 'unknown') return null
  const label = t(`vocab:lines.kind_badge.${kind}`)
  return (
    <span className="vocab-line-kind">{kind === 'dataset' && datasetId ? `${label} · ${datasetId}` : label}</span>
  )
}
