import { useEffect, useState } from 'react'
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

  useEffect(() => {
    let cancelled = false
    getCatalogDatasets()
      .then((ds) => !cancelled && setDatasets(ds))
      .catch(() => {})
      .finally(() => !cancelled && setLoaded(true))
    getSchema()
      .then((s) => !cancelled && setSchema(s))
      .catch(() => {})
      .finally(() => !cancelled && setSchemaTried(true))
    return () => {
      cancelled = true
    }
  }, [])

  // Consumers = real (materialized) datasets; the fixture-free list only has
  // entries that carry a live registry record.
  const consumers = datasets.filter((d) => d.live)
  const reuses = schema ? schemaReuses(schema) : []

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
              「設計図（ことば）」は無くなりません — <span className="vocab-banner-hl">共有</span>されるだけ
            </Trans>
          </div>
          <div className="vocab-banner-sub">
            <Trans i18nKey="vocab:banner.sub">
              これは<strong>実データから自動で読み取ったことば</strong>です（全データセットを横断・
              名前は各データセットの設計図から自動で反映）。揃えるほど横断検索・比較が効きます。
            </Trans>
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
        <p className="ds-empty-note">{t('vocab:schemaError')}</p>
      )}

      {schema && (
        <div className="vocab-grid">
          {/* live shared vocabulary (classes + predicates, schema-agnostic) */}
          <div className="card vocab-classes">
            <div className="vocab-card-head">
              <h3 className="card-h">{t('vocab:classesCard.title')}</h3>
              <span className="vocab-card-meta">
                {t('vocab:classesCard.meta', {
                  classes: schema.classes.length,
                  predicates: schema.predicates.length,
                })}
              </span>
            </div>
            <p className="vocab-live-note">
              <Trans i18nKey="vocab:classesCard.note1">
                右端の数字＝実データ中の件数。<strong>データの種類は、その型のものが何件あるか</strong>、
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
            <LiveTermList title="" terms={schema.classes} />
            <div className="ds-subhead">{t('vocab:classesCard.predicatesSubhead')}</div>
            <LiveTermList title="" terms={schema.predicates} limit={15} />
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
          </div>

          {/* datasets that bind to this vocabulary (real, materialized) */}
          <div className="card vocab-users">
            <div className="vocab-card-head">
              <h3 className="card-h">{t('vocab:usersCard.title')}</h3>
              <span className="vocab-card-meta">{consumers.length}</span>
            </div>
            <div className="vocab-user-list">
              {loaded && consumers.length === 0 && (
                <p className="ds-empty-note">{t('vocab:usersCard.empty')}</p>
              )}
              {consumers.map((u) => (
                <div key={u.id} className="vocab-user">
                  <div className="vocab-user-head">
                    <span className="vocab-user-icon">
                      <LayersIcon size={14} />
                    </span>
                    <span className="vocab-user-name">{u.name}</span>
                    <span className={`status-pill status-pill--${u.statusKind}`}>
                      {t(STATUS_KEY[u.statusKind])}
                    </span>
                    <span className="vocab-user-src">
                      {t('vocab:usersCard.classCount', { n: u.classes.length })}
                    </span>
                  </div>
                </div>
              ))}

              <div className="vocab-caution">
                <span className="vocab-caution-icon">
                  <LinkIcon size={16} />
                </span>
                <div>
                  <strong>{t('vocab:usersCard.caution.title')}</strong>{' '}
                  <Trans i18nKey="vocab:usersCard.caution.body">
                    共有されていることばを書き換えると、それを使うデータセットすべての検索・回答に波及します。
                    変更は<strong>影響範囲のプレビュー</strong>を見てから確定します。
                  </Trans>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/** A ranked list of live classes/predicates: human label + IRI localname + count. */
function LiveTermList({ title, terms, limit = 50 }: { title: string; terms: SchemaTerm[]; limit?: number }) {
  const { t } = useTranslation()
  return (
    <div className="vocab-live-col">
      {title && <div className="ds-subhead">{title}</div>}
      {terms.length === 0 && <p className="ds-empty-note">{t('vocab:termList.none')}</p>}
      <div className="vocab-live-list">
        {terms.slice(0, limit).map((t) => (
          <div key={t.iri} className="vocab-live-term" title={t.iri}>
            <span className="vocab-live-label">{t.label || localName(t.iri)}</span>
            {t.label && <code className="vocab-live-iri">{localName(t.iri)}</code>}
            <span className="vocab-live-count">{t.count.toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
