import { useEffect, useState } from 'react'
import { getSchema, type SchemaSummary, type SchemaTerm } from './demoApi'
import { type CatalogDataset, getCatalogDatasets, getOntologies, type OntologyEntry } from './galleryApi'
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

/**
 * Shared vocabulary board (design_handoff_asterism_ux #6). The answer to "if
 * datasets are primary, do ontology/mapping disappear?" — No: the vocabulary
 * stays first-class, it is just SHARED across datasets.
 *
 * Real data: the shared vocabulary IS the committed canonical ontology
 * (getOntologies); the datasets that bind to it are the real catalog datasets.
 * No fabricated classes/usage.
 */
export function SharedVocabView({ onBack }: { onBack?: () => void }) {
  const [onto, setOnto] = useState<OntologyEntry | null>(null)
  const [datasets, setDatasets] = useState<CatalogDataset[]>([])
  const [schema, setSchema] = useState<SchemaSummary | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([getOntologies(), getCatalogDatasets()])
      .then(([os, ds]) => {
        if (cancelled) return
        setOnto(os[0] ?? null)
        setDatasets(ds)
        setLoaded(true)
      })
      .catch(() => !cancelled && setLoaded(true))
    // Live vocabulary actually in the store (best-effort; null = agent absent).
    getSchema()
      .then((s) => !cancelled && setSchema(s))
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // Datasets other than the canonical itself are the consumers of the vocabulary.
  const consumers = datasets.filter((d) => d.id !== onto?.id)

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
            データセットを主役にしても、語彙と取り込みルールは各データセットの中に残ります。
            ここはそのうち<strong>みんなで共通して使う部分</strong>。揃えるほど横断検索・比較が効きます。
          </div>
        </div>
      </div>

      {!loaded && (
        <p className="loading-row">
          <span className="spinner" />
          読み込み中…
        </p>
      )}

      {loaded && !onto && (
        <p className="ds-empty-note">共有オントロジーがまだありません。</p>
      )}

      {/* #20: live vocabulary actually in the canonical store (FROM-merge),
          with rdfs:label from each dataset's projected TBox (step5). This is the
          schema-agnostic, generic view — it reflects whatever is loaded, not a
          hardcoded starrydata fixture. Shown only when the agent answered. */}
      {schema && (schema.classes.length > 0 || schema.predicates.length > 0) && (
        <div className="card vocab-live">
          <div className="vocab-card-head">
            <h3 className="card-h">実データの語彙（横断・自動内省）</h3>
            <span className="vocab-card-meta">
              {schema.classes.length} クラス · {schema.predicates.length} 述語
            </span>
          </div>
          <p className="vocab-live-note">
            公開済みの全データセットを横断して、いま実際に入っている語彙を数えています
            （ラベルは各データセットの設計図から投影された TBox 由来）。
          </p>
          <div className="vocab-live-cols">
            <LiveTermList title="クラス" terms={schema.classes} />
            <LiveTermList title="述語" terms={schema.predicates} />
          </div>
        </div>
      )}

      {onto && (
        <div className="vocab-grid">
          {/* shared classes (canonical ontology) */}
          <div className="card vocab-classes">
            <div className="vocab-card-head">
              <h3 className="card-h">共有クラス</h3>
              <span className="vocab-card-meta">
                {onto.classes.length} · {onto.prefix}
              </span>
            </div>
            <div className="vocab-class-list">
              {onto.classes.map((c) => (
                <div key={c} className="vocab-class">
                  <div className="vocab-class-body">
                    <div className="vocab-class-title">
                      <code className="vocab-class-en">{c}</code>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            {onto.reuses.length > 0 && (
              <>
                <div className="ds-subhead">再利用している語彙</div>
                <div className="ds-reuse-list">
                  {onto.reuses.map((r) => (
                    <span key={r.prefix} className="reuse-chip" title={r.what}>
                      <code>{r.prefix}</code>
                      <span className="reuse-chip-what">{r.what}</span>
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>

          {/* datasets that bind to this vocabulary */}
          <div className="card vocab-users">
            <div className="vocab-card-head">
              <h3 className="card-h">このオントロジーを使うデータセット</h3>
              <span className="vocab-card-meta">{consumers.length}</span>
            </div>
            <div className="vocab-user-list">
              {consumers.length === 0 && (
                <p className="ds-empty-note">
                  まだ他のデータセットはこの語彙に紐づいていません。ワークベンチで設計を保存すると、
                  共有語彙との差分（再利用 / 新規）を「取り込みルール」タブで確認できます。
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
function LiveTermList({ title, terms }: { title: string; terms: SchemaTerm[] }) {
  return (
    <div className="vocab-live-col">
      <div className="ds-subhead">{title}</div>
      {terms.length === 0 && <p className="ds-empty-note">（なし）</p>}
      <div className="vocab-live-list">
        {terms.slice(0, 20).map((t) => (
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
