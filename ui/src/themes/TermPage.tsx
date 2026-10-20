import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type Alignment, getAlignments } from '../crosswalkApi'
import { AttachKind, type AttachChoice } from '../decide/AttachKind'
import { isSystemKindIri } from '../decide/decide'
import { decisionSentence } from '../decide/decisionsModel'
import { isKnowledgeRelation } from '../lineChoice'
import { type SchemaSummary, getSchema } from '../demoApi'
import { type CatalogDataset, getCatalogDatasets } from '../galleryApi'
import { ArrowIcon } from '../icons'
import { type SharedTerm, VocabApiError, listTerms, removeTerm } from '../vocabApi'
import { localName } from '../vocab'
import { type ThemeSummary, listThemes, themeLabel } from './themesApi'
import './themes.css'

// 用語のページ（#/questions/term/<slug>）。用語 1 つの名前・英語名・説明・別名・ID・使う問い・
// 入っている種類（「この用語に種類を入れる」）・この用語の決めたこと・履歴。用語はテーマの持ち物では
// ないので、どのテーマからも同じページに来る。外せるのは、関係が 1 つも残っていないときだけ。

/** 「この用語に種類（項目）を入れる」の候補: 全データセットの種類・項目（公開済みの語彙から）。 */
function attachChoices(
  schema: SchemaSummary | null,
  datasets: CatalogDataset[],
): { classes: AttachChoice[]; properties: AttachChoice[] } | undefined {
  if (!schema) return undefined
  const owners = new Map<string, CatalogDataset>()
  for (const d of datasets) for (const iri of [...d.classIris, ...d.predicates]) if (!owners.has(iri)) owners.set(iri, d)
  // 系統用の型（つながりの内部・来歴・用語そのもの）は候補にしない。RDF の配管も同じ。
  const plumbing = ['http://www.w3.org/1999/02/22-rdf-syntax-ns#', 'http://www.w3.org/2000/01/rdf-schema#', 'http://www.w3.org/2002/07/owl#']
  const pick = (terms: SchemaSummary['classes'], termKind: 'class' | 'property'): AttachChoice[] =>
    terms
      .filter((term) => !isSystemKindIri(term.iri) && !plumbing.some((ns) => term.iri.startsWith(ns)))
      .map((term) => ({
        id: term.iri,
        iri: term.iri,
        label: term.label || localName(term.iri),
        role: 'dataset' as const,
        termKind,
        datasetName: owners.get(term.iri)?.name,
      }))
  return { classes: pick(schema.classes, 'class'), properties: pick(schema.predicates, 'property') }
}

export function TermPage({ slug, onBack }: { slug: string; onBack: () => void }) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language.startsWith('en') ? 'en' : 'ja'
  const [term, setTerm] = useState<SharedTerm | null | undefined>(undefined)
  const [loadErr, setLoadErr] = useState('')
  const [lines, setLines] = useState<Alignment[]>([])
  const [datasets, setDatasets] = useState<CatalogDataset[]>([])
  const [schema, setSchema] = useState<SchemaSummary | null>(null)
  const [themes, setThemes] = useState<ThemeSummary[]>([])
  const [version, setVersion] = useState(0)
  const [attaching, setAttaching] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [actErr, setActErr] = useState('')

  useEffect(() => {
    let off = false
    listTerms()
      .then((ts) => {
        if (off) return
        setLoadErr('')
        setTerm(ts.find((x) => x.slug === slug) ?? null)
      })
      .catch((e) => {
        if (off) return
        setLoadErr(e instanceof Error ? e.message : String(e))
        setTerm(null)
      })
    getAlignments()
      .then((r) => !off && setLines(r.alignments))
      .catch(() => !off && setLines([]))
    getCatalogDatasets()
      .then((d) => !off && setDatasets(d))
      .catch(() => {})
    getSchema()
      .then((s) => !off && setSchema(s))
      .catch(() => !off && setSchema(null))
    listThemes(lang)
      .then((l) => !off && setThemes(l.themes))
      .catch(() => !off && setThemes([]))
    return () => {
      off = true
    }
  }, [slug, version, lang])

  const names = useMemo(() => {
    const m: Record<string, string> = {}
    for (const d of datasets) {
      m[d.id] = d.name
      m[d.id.replace(/^live-/, '')] = d.name
    }
    return m
  }, [datasets])
  const attach = useMemo(() => attachChoices(schema, datasets), [schema, datasets])

  async function remove() {
    setRemoving(true)
    setActErr('')
    try {
      await removeTerm(slug)
      onBack()
    } catch (e) {
      if (e instanceof VocabApiError && e.status === 409) setActErr(t('vocab:term.removeBlocked'))
      else setActErr(e instanceof Error ? e.message : String(e))
      setConfirming(false)
    } finally {
      setRemoving(false)
    }
  }

  const back = (
    <button type="button" className="link-btn vocab-back" onClick={onBack}>
      <ArrowIcon size={14} className="vocab-back-arrow" /> {t('themes:term.back')}
    </button>
  )

  if (term === undefined) {
    return (
      <div className="vocab thm-view">
        {back}
        <p className="loading-row">
          <span className="spinner" />
          {t('themes:loading')}
        </p>
      </div>
    )
  }
  if (term === null) {
    return (
      <div className="vocab thm-view">
        {back}
        {loadErr ? <pre className="error">{loadErr}</pre> : <p className="ds-empty-note">{t('themes:term.notFound', { slug })}</p>}
      </div>
    )
  }

  const mine = lines.filter((a) => a.source === term.iri || a.target === term.iri)
  // 問いから育てた知識の線（一部・下げる・上げる・変える）。「入っている種類」の下に文で並べる。
  const knowledgeLines = mine.filter((a) => isKnowledgeRelation(a.relation))
  const reasonLine = (a: Alignment) =>
    a.reason ? (
      <div className="thm-reason" title={t('decisions:reasonLabel')}>
        {a.reason}
        {a.drafted_by === 'ai' && <span className="thm-ai">{t('themes:term.knowledgeAi')}</span>}
      </div>
    ) : null
  const themesOf = (tool: string) => themes.filter((th) => th.questions.some((q) => q.tool_name === tool))

  return (
    <div className="vocab thm-view" data-testid="term-page">
      {back}
      <div className="card thm-term-head">
        <h2 className="thm-title">
          {lang === 'en' && term.label_en ? term.label_en : term.label}
          <span className="vocab-term-kind">{t(`vocab:term.kind_badge.${term.kind}`)}</span>
          <span className={`pill ${term.wired ? 'ok' : 'wait'}`}>
            {term.wired ? t('themes:term.wired', { n: term.answering_datasets }) : t('themes:term.notWired')}
          </span>
        </h2>
        <dl className="thm-kv">
          {term.label_en && lang !== 'en' && (
            <>
              <dt>{t('themes:term.english')}</dt>
              <dd>{term.label_en}</dd>
            </>
          )}
          {lang === 'en' && (
            <>
              <dt>{t('themes:term.japanese')}</dt>
              <dd>{term.label}</dd>
            </>
          )}
          {term.comment && (
            <>
              <dt>{t('themes:term.comment')}</dt>
              <dd>{term.comment}</dd>
            </>
          )}
          {term.aliases && term.aliases.length > 0 && (
            <>
              <dt>{t('themes:term.aliases')}</dt>
              <dd>
                {term.aliases.map((a) => (
                  <span key={a} className="thm-term-chip">
                    {a}
                  </span>
                ))}
              </dd>
            </>
          )}
        </dl>
      </div>

      <section className="vocab-sec" aria-labelledby="term-questions-h">
        <h3 className="vocab-sec-h" id="term-questions-h">
          {t('themes:term.questions')}
        </h3>
        {term.cqs.length === 0 ? (
          <p className="ds-empty-note">{t('vocab:term.no_cq')}</p>
        ) : (
          <ul className="vocab-term-cqs">
            {term.cqs.map((cq) => (
              <li key={cq.tool_name}>
                {cq.title} — {t('vocab:term.answering', { n: cq.answering_datasets })}
                {themesOf(cq.tool_name).map((th) => (
                  <a key={th.id} className="thm-term-chip" href={`#/questions/${encodeURIComponent(th.id)}`}>
                    {themeLabel(th, t)}
                  </a>
                ))}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="vocab-sec" aria-labelledby="term-members-h">
        <h3 className="vocab-sec-h" id="term-members-h">
          {t(term.kind === 'class' ? 'themes:term.membersClass' : 'themes:term.membersProperty')}
        </h3>
        {term.narrower.length === 0 ? (
          <p className="ds-empty-note">{t('themes:term.noMembers')}</p>
        ) : (
          <ul className="vocab-term-cqs">
            {term.narrower.map((n) => (
              <li key={n.iri} title={n.iri}>
                {n.label}
                {n.dataset_id && <span className="vocab-field-hint"> （{names[n.dataset_id] ?? n.dataset_id}）</span>}
              </li>
            ))}
          </ul>
        )}
        {knowledgeLines.length > 0 && (
          <div className="thm-term-knowledge" data-testid="term-knowledge">
            <h4 className="thm-sub-h">{t('themes:term.knowledge')}</h4>
            <ul className="vocab-term-cqs">
              {knowledgeLines.map((a) => (
                <li key={a.alignment_iri}>
                  {decisionSentence(a, names, t)}
                  {a.at && <span className="vocab-field-hint"> {a.at.slice(0, 10)}</span>}
                  {reasonLine(a)}
                </li>
              ))}
            </ul>
          </div>
        )}
        {attach &&
          (attaching ? (
            <AttachKind
              term={term}
              choices={term.kind === 'class' ? attach.classes : attach.properties}
              onDecided={() => setVersion((v) => v + 1)}
              onClose={() => setAttaching(false)}
            />
          ) : (
            <p>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setAttaching(true)}>
                {t(term.kind === 'class' ? 'decide:attach.openClass' : 'decide:attach.openProperty')}
              </button>
            </p>
          ))}
      </section>

      <section className="vocab-sec" aria-labelledby="term-decisions-h">
        <h3 className="vocab-sec-h" id="term-decisions-h">
          {t('themes:term.decisions')}
        </h3>
        {mine.length === 0 ? (
          <p className="ds-empty-note">{t('themes:term.noDecisions')}</p>
        ) : (
          <ul className="vocab-term-cqs" data-testid="term-decisions">
            {mine.map((a) => (
              <li key={a.alignment_iri}>
                {decisionSentence(a, names, t)}
                {a.at && <span className="vocab-field-hint"> {a.at.slice(0, 10)}</span>}
                {reasonLine(a)}
              </li>
            ))}
          </ul>
        )}
        <p className="vocab-sec-sub">
          <a className="link-btn" href="#/decisions">
            {t('themes:term.toDecisions')}
          </a>
        </p>
      </section>

      {/* ID・IRI・履歴・外す操作は「詳しく」に畳む（ふだんは見せない）。 */}
      <details className="thm-term-more">
        <summary>{t('themes:term.more')}</summary>
        <dl className="thm-kv">
          <dt>{t('themes:term.id')}</dt>
          <dd>
            <code>{term.slug}</code>
            <div className="vocab-field-hint" title={term.iri}>
              {term.iri}
            </div>
          </dd>
          <dt>{t('themes:term.history')}</dt>
          <dd data-testid="term-history">
            <ul className="vocab-term-cqs">
              {term.created_at && <li>{t('themes:term.createdAt', { date: term.created_at.slice(0, 10) })}</li>}
              {term.drafted_by === 'ai' && (
                <li>
                  {t('themes:term.draftedBy', { date: (term.confirmed_at ?? term.created_at).slice(0, 10) })}
                </li>
              )}
              {term.drafted_by !== 'ai' && <li>{t('themes:term.byHand')}</li>}
            </ul>
          </dd>
        </dl>
        <div className="vocab-term-actions">
          {confirming ? (
            <>
              <button type="button" className="btn btn--ghost btn--sm" disabled={removing} onClick={() => void remove()}>
                {removing ? t('vocab:term.removing') : t('vocab:term.remove_confirm')}
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setConfirming(false)}>
                {t('vocab:term.cancel')}
              </button>
            </>
          ) : (
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => setConfirming(true)}>
              {t('themes:term.remove')}
            </button>
          )}
        </div>
      </details>
      {actErr && <p className="promote-err">{actErr}</p>}
    </div>
  )
}
