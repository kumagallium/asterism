import { useEffect, useState } from 'react'
import { getSchema, type SchemaSummary, type SchemaTerm } from './demoApi'
import { type CatalogDataset, getCatalogDatasets } from './galleryApi'
import { ArrowIcon, LayersIcon, LinkIcon } from './icons'

const STATUS_LABEL: Record<CatalogDataset['statusKind'], string> = {
  pub: '公開済み',
  draft: '下書き',
  design: '設計中',
}

/** Local name of an IRI (after the last # / /), for a compact code display. */
function localName(iri: string): string {
  const i = Math.max(iri.lastIndexOf('#'), iri.lastIndexOf('/'))
  return i >= 0 ? iri.slice(i + 1) : iri
}

/** Namespace of an IRI (everything up to and including the last # or /). */
function namespaceOf(iri: string): string {
  const i = Math.max(iri.lastIndexOf('#'), iri.lastIndexOf('/'))
  return i >= 0 ? iri.slice(0, i + 1) : iri
}

// Well-known EXTERNAL vocabularies. When the live data uses a term under one of
// these namespaces, that vocabulary is being "reused" rather than re-minted.
// Structural namespaces (rdf/rdfs/owl/xsd) are not interesting to surface.
const KNOWN_VOCABS: { ns: string; prefix: string; what: string }[] = [
  { ns: 'https://schema.org/', prefix: 'schema:', what: 'schema.org（人物・出版物などのメタデータ）' },
  { ns: 'http://www.w3.org/ns/prov#', prefix: 'prov:', what: 'PROV-O（来歴 Entity / Activity / Agent）' },
  { ns: 'http://purl.org/dc/terms/', prefix: 'dcterms:', what: 'Dublin Core terms（identifier / created 等）' },
  { ns: 'http://purl.org/ontology/bibo/', prefix: 'bibo:', what: 'BIBO（volume / issue / pages）' },
  { ns: 'http://qudt.org/schema/qudt/', prefix: 'qudt:', what: 'QUDT（物性量・単位の共有語彙）' },
  { ns: 'http://www.w3.org/2004/02/skos/core#', prefix: 'skos:', what: 'SKOS（概念体系）' },
]

/** Reused external vocabularies actually present in the live terms. */
function deriveReuses(schema: SchemaSummary): { prefix: string; what: string }[] {
  const present = new Set<string>()
  for (const t of [...schema.classes, ...schema.predicates]) present.add(namespaceOf(t.iri))
  return KNOWN_VOCABS.filter((v) => present.has(v.ns)).map(({ prefix, what }) => ({ prefix, what }))
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
  const reuses = schema ? deriveReuses(schema) : []

  return (
    <div className="vocab">
      {onBack && (
        <button type="button" className="link-btn vocab-back" onClick={onBack}>
          <ArrowIcon size={14} className="vocab-back-arrow" /> カタログに戻る
        </button>
      )}

      <div className="vocab-banner">
        <span className="vocab-banner-icon">
          <LinkIcon size={19} />
        </span>
        <div>
          <div className="vocab-banner-title">
            「設計図（語彙）」は無くなりません — <span className="vocab-banner-hl">共有</span>されるだけ
          </div>
          <div className="vocab-banner-sub">
            これは<strong>実データから自動で内省した語彙</strong>です（全データセットを横断・
            ラベルは各データセットの設計図から投影された TBox 由来）。揃えるほど横断検索・比較が効きます。
          </div>
        </div>
      </div>

      {!schemaTried && (
        <p className="loading-row">
          <span className="spinner" />
          読み込み中…
        </p>
      )}

      {schemaTried && !schema && (
        <p className="ds-empty-note">
          実データの語彙を取得できませんでした（クエリ層が応答していません）。
        </p>
      )}

      {schema && (
        <div className="vocab-grid">
          {/* live shared vocabulary (classes + predicates, schema-agnostic) */}
          <div className="card vocab-classes">
            <div className="vocab-card-head">
              <h3 className="card-h">共有クラス（実データ）</h3>
              <span className="vocab-card-meta">
                {schema.classes.length} クラス · {schema.predicates.length} 述語
              </span>
            </div>
            <p className="vocab-live-note">
              右端の数字＝実データ中の件数。<strong>クラスはインスタンス数</strong>（その型のものが何件あるか）、
              <strong>述語は使用回数</strong>（そのプロパティを使うトリプル数）。
            </p>
            <div className="ds-subhead">クラス（インスタンス数）</div>
            <LiveTermList title="" terms={schema.classes} />
            <div className="ds-subhead">主な述語（使用回数）</div>
            <LiveTermList title="" terms={schema.predicates} limit={15} />
            {reuses.length > 0 && (
              <>
                <div className="ds-subhead">再利用している語彙（実データの名前空間から検出）</div>
                <div className="ds-reuse-list">
                  {reuses.map((r) => (
                    <span key={r.prefix} className="reuse-chip" title={r.what}>
                      <code>{r.prefix}</code>
                      <span className="reuse-chip-what">{r.what}</span>
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>

          {/* datasets that bind to this vocabulary (real, materialized) */}
          <div className="card vocab-users">
            <div className="vocab-card-head">
              <h3 className="card-h">この語彙を使うデータセット</h3>
              <span className="vocab-card-meta">{consumers.length}</span>
            </div>
            <div className="vocab-user-list">
              {loaded && consumers.length === 0 && (
                <p className="ds-empty-note">
                  まだ取り込み済みのデータセットがありません。ワークベンチで設計を保存して取り込むと、
                  ここに横断対象として並びます。
                </p>
              )}
              {consumers.map((u) => (
                <div key={u.id} className="vocab-user">
                  <div className="vocab-user-head">
                    <span className="vocab-user-icon">
                      <LayersIcon size={14} />
                    </span>
                    <span className="vocab-user-name">{u.name}</span>
                    <span className={`status-pill status-pill--${u.statusKind}`}>
                      {STATUS_LABEL[u.statusKind]}
                    </span>
                    <span className="vocab-user-src">{u.classes.length} クラス</span>
                  </div>
                </div>
              ))}

              <div className="vocab-caution">
                <span className="vocab-caution-icon">
                  <LinkIcon size={16} />
                </span>
                <div>
                  <strong>なぜ「要注意」？</strong>{' '}
                  共有クラスを書き換えると、それを使うデータセットすべての検索・回答に波及します。
                  変更は<strong>影響範囲のプレビュー</strong>を見てから確定します。
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
  return (
    <div className="vocab-live-col">
      {title && <div className="ds-subhead">{title}</div>}
      {terms.length === 0 && <p className="ds-empty-note">（なし）</p>}
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
