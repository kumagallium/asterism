import { useEffect, useMemo, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { getSchema, type SchemaSummary, type SchemaTerm } from './demoApi'
import { type CatalogDataset, getCatalogDatasets } from './galleryApi'
import { ArrowIcon, LayersIcon, LinkIcon } from './icons'
import { deriveReuses, localName } from './vocab'

const STATUS_KEY: Record<CatalogDataset['statusKind'], string> = {
  pub: 'vocab:status.pub',
  draft: 'vocab:status.draft',
  design: 'vocab:status.design',
}

/** Reused external vocabularies actually present in the live schema terms. */
function schemaReuses(schema: SchemaSummary): { prefix: string; what: string }[] {
  return deriveReuses([...schema.classes, ...schema.predicates].map((t) => t.iri))
}

/**
 * Navigate without threading a callback down from App: the hash IS the router's
 * single source of truth (App re-reads it on `hashchange`), so assigning it is a
 * complete navigation.
 */
function goTo(hash: string): void {
  if (window.location.hash !== hash) window.location.hash = hash
}

/**
 * "hasSeebeckCoefficient" → "Has Seebeck Coefficient". Used only when a term
 * carries no human label: a readable phrase beats a raw identifier, and the
 * "(no name set)" mark next to it says the gap is fixable.
 */
function humanizeLocalName(name: string): string {
  const spaced = name
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim()
  return spaced.replace(/(^|\s)([a-z])/g, (_m, sep: string, c: string) => sep + c.toUpperCase())
}

/**
 * Shared vocabulary board (design_handoff_asterism_ux #6). The vocabulary stays
 * first-class — it is just SHARED across datasets.
 *
 * #20: this view is now driven ENTIRELY by live data. The classes/predicates are
 * introspected from whatever is actually loaded (the canonical FROM-merge across
 * all datasets), labels come from each dataset's projected TBox (step5), and the
 * reused external vocabularies are derived from the live term namespaces. There
 * is no hardcoded starrydata fixture here.
 */
export function SharedVocabView({ onBack }: { onBack?: () => void }) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<CatalogDataset[]>([])
  const [schema, setSchema] = useState<SchemaSummary | null>(null)
  const [schemaTried, setSchemaTried] = useState(false)
  const [loaded, setLoaded] = useState(false)
  // Bumped by the retry button; the fetch effect re-runs on every change.
  const [reloadKey, setReloadKey] = useState(0)

  // 「もう一度読み込む」: 表示を読み込み中に戻してから取得を再実行する
  // （state のリセットは effect の外で行う — effect 内の同期 setState は禁止）。
  function reload() {
    setSchemaTried(false)
    setLoaded(false)
    setReloadKey((k) => k + 1)
  }

  useEffect(() => {
    let cancelled = false
    getCatalogDatasets()
      .then((ds) => !cancelled && setDatasets(ds))
      .catch(() => {})
      .finally(() => !cancelled && setLoaded(true))
    getSchema()
      .then((s) => !cancelled && setSchema(s))
      .catch(() => !cancelled && setSchema(null))
      .finally(() => !cancelled && setSchemaTried(true))
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  // Consumers = real (materialized) datasets; the fixture-free list only has
  // entries that carry a live registry record.
  const consumers = datasets.filter((d) => d.live)
  const reuses = schema ? schemaReuses(schema) : []
  const noTerms = !!schema && schema.classes.length === 0 && schema.predicates.length === 0

  // IRI → the dataset that declares it, so an unnamed term can point at the
  // place where its name is actually set. Exact IRI match only (no guessing).
  const owners = useMemo(() => {
    const m = new Map<string, CatalogDataset>()
    for (const d of datasets) {
      for (const iri of [...d.classIris, ...d.predicates]) if (!m.has(iri)) m.set(iri, d)
    }
    return m
  }, [datasets])

  return (
    <div className="vocab">
      {onBack && (
        <button type="button" className="link-btn vocab-back" onClick={onBack}>
          <ArrowIcon size={14} className="vocab-back-arrow" /> {t('vocab:back')}
        </button>
      )}

      <div className="vocab-banner">
        <span className="vocab-banner-icon">
          <LinkIcon size={19} />
        </span>
        <div>
          <div className="vocab-banner-title">
            <Trans i18nKey="vocab:banner.title">
              データをまたいで使われている<span className="vocab-banner-hl">ことば</span>
            </Trans>
          </div>
          <div className="vocab-banner-sub">
            <Trans i18nKey="vocab:banner.sub">
              取り込んだデータから自動で集めた<strong>データの種類と項目</strong>の一覧です。
              名前が揃っているほど、複数のデータをまたいだ質問や比較ができます。
            </Trans>
          </div>
          {/* この画面は見るだけ — 名前を直す場所へ送り出す（K11: 行き止まりにしない）。 */}
          <div className="vocab-banner-sub">
            {t('vocab:banner.fixNote')}{' '}
            <button type="button" className="link-btn" onClick={() => goTo('#/datasets')}>
              {t('vocab:banner.fixLink')}
            </button>
          </div>
        </div>
      </div>

      {!schemaTried && (
        <p className="loading-row">
          <span className="spinner" />
          {t('vocab:loading')}
        </p>
      )}

      {schemaTried && !schema && (
        <>
          <p className="error">{t('vocab:schemaError')}</p>
          <p>
            <button type="button" className="btn btn--sm" onClick={reload}>
              {t('vocab:reload')}
            </button>
          </p>
        </>
      )}

      {schema && (
        <div className="vocab-grid">
          {/* live shared vocabulary (classes + predicates, schema-agnostic) */}
          <div className="card vocab-classes">
            <div className="vocab-card-head">
              <h3 className="card-h">{t('vocab:classesCard.title')}</h3>
              {!noTerms && (
                <span className="vocab-card-meta">
                  {t('vocab:classesCard.meta', {
                    classes: schema.classes.length,
                    predicates: schema.predicates.length,
                  })}
                </span>
              )}
            </div>
            {noTerms ? (
              // 公開済みデータが 0 件の初回: 空欄を並べず、次の一手だけを出す。
              <>
                <p className="ds-empty-note">{t('vocab:classesCard.emptyAll')}</p>
                <p>
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => goTo('#/workbench')}
                  >
                    {t('vocab:addData')}
                  </button>
                </p>
              </>
            ) : (
              <>
                <p className="vocab-live-note">
                  <Trans i18nKey="vocab:classesCard.note1">
                    右端の数字＝件数。<strong>データの種類は、その種類のものが何件あるか</strong>、
                    <strong>項目は、何回使われているか</strong>。
                  </Trans>
                </p>
                <p className="vocab-live-note">
                  <Trans i18nKey="vocab:classesCard.note2">
                    ※ 数えるのは <strong>公開済み（引用できる）データのみ</strong>。
                    公開前の下書きは含みません ── 公開すると集計に入ります。
                  </Trans>
                </p>
                <div className="ds-subhead">{t('vocab:classesCard.classesSubhead')}</div>
                <LiveTermList title="" terms={schema.classes} owners={owners} />
                <div className="ds-subhead">{t('vocab:classesCard.predicatesSubhead')}</div>
                <LiveTermList title="" terms={schema.predicates} limit={15} owners={owners} />
                {reuses.length > 0 && (
                  <>
                    <div className="ds-subhead">{t('vocab:classesCard.reusesSubhead')}</div>
                    <div className="ds-reuse-list">
                      {reuses.map((r) => (
                        <span key={r.prefix} className="reuse-chip" title={t(r.what)}>
                          <code>{r.prefix}</code>
                          <span className="reuse-chip-what">{t(r.what)}</span>
                        </span>
                      ))}
                    </div>
                  </>
                )}
              </>
            )}
          </div>

          {/* datasets that bind to this vocabulary (real, materialized) */}
          <div className="card vocab-users">
            <div className="vocab-card-head">
              <h3 className="card-h">{t('vocab:usersCard.title')}</h3>
              <span className="vocab-card-meta">{consumers.length}</span>
            </div>
            <div className="vocab-user-list">
              {loaded && consumers.length === 0 && (
                <div>
                  <p className="ds-empty-note">{t('vocab:usersCard.empty')}</p>
                  <p>
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => goTo('#/workbench')}
                    >
                      {t('vocab:addData')}
                    </button>
                  </p>
                </div>
              )}
              {consumers.map((u) => (
                <div key={u.id} className="vocab-user">
                  <div className="vocab-user-head">
                    <span className="vocab-user-icon">
                      <LayersIcon size={14} />
                    </span>
                    <button
                      type="button"
                      className="link-btn vocab-user-name"
                      onClick={() => goTo(`#/datasets/${encodeURIComponent(u.id)}`)}
                    >
                      {u.name}
                    </button>
                    <span className={`status-pill status-pill--${u.statusKind}`}>
                      {t(STATUS_KEY[u.statusKind])}
                    </span>
                    <span className="vocab-user-src">
                      {t('vocab:usersCard.classCount', { n: u.classes.length })}
                    </span>
                  </div>
                  {/* 下書きの語は左の集計に入らない（note2）— 壊れて見えないよう理由を書く。 */}
                  {u.statusKind === 'draft' && (
                    <p className="ds-empty-note">{t('vocab:usersCard.draftNote')}</p>
                  )}
                </div>
              ))}

              <div className="vocab-caution">
                <span className="vocab-caution-icon">
                  <LinkIcon size={16} />
                </span>
                <div>{t('vocab:usersCard.caution.body')}</div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/** A ranked list of live classes/predicates: human label + count. */
function LiveTermList({
  title,
  terms,
  limit = 50,
  owners,
}: {
  title: string
  terms: SchemaTerm[]
  limit?: number
  owners?: Map<string, CatalogDataset>
}) {
  const { t } = useTranslation()
  const [showAll, setShowAll] = useState(false)
  const shown = showAll ? terms : terms.slice(0, limit)
  return (
    <div className="vocab-live-col">
      {title && <div className="ds-subhead">{title}</div>}
      {terms.length === 0 && <p className="ds-empty-note">{t('vocab:termList.none')}</p>}
      <div className="vocab-live-list">
        {shown.map((term) => {
          const owner = term.label ? undefined : owners?.get(term.iri)
          return (
            <div key={term.iri} className="vocab-live-term" title={term.iri}>
              <span className="vocab-live-label">
                {term.label || humanizeLocalName(localName(term.iri))}
              </span>
              {!term.label && (
                <>
                  <span className="hint">{t('vocab:termList.unnamed')}</span>
                  {owner && (
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => goTo(`#/datasets/${encodeURIComponent(owner.id)}/design`)}
                    >
                      {t('vocab:termList.owner')}
                    </button>
                  )}
                </>
              )}
              <span className="vocab-live-count">{term.count.toLocaleString()}</span>
            </div>
          )
        })}
      </div>
      {terms.length > limit && (
        <p className="ds-empty-note">
          {!showAll && <>{t('vocab:termList.more', { n: terms.length - limit })} </>}
          <button type="button" className="link-btn" onClick={() => setShowAll((v) => !v)}>
            {showAll ? t('vocab:termList.showLess') : t('vocab:termList.showAll')}
          </button>
        </p>
      )}
    </div>
  )
}
