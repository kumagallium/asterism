import { useEffect, useState } from 'react'
import { getOntologies, type OntologyEntry } from './galleryApi'
import { Mermaid } from './Mermaid'

/**
 * M4 gallery: an overview of the assets that have been built. The two layers
 * are presented as visually distinct so their edit-risk differs at a glance
 * (design doc §6.6/D8):
 *
 *   - Ontologies (shared TBox): high blast radius — edit with care.
 *   - Mappings (dataset bindings, purpose-tagged): local & disposable. [M4b]
 *
 * M4a ships the Ontologies gallery; the Mappings gallery (with prominent
 * purpose tags) lands in M4b. The framing below names both layers now so the
 * shared-vs-disposable mental model is established up front.
 */
export function GalleryView() {
  const [ontologies, setOntologies] = useState<OntologyEntry[] | null>(null)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getOntologies()
      .then((list) => {
        if (cancelled) return
        setOntologies(list)
        setSelectedId(list[0]?.id ?? null)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [])

  const selected = ontologies?.find((o) => o.id === selectedId) ?? null

  return (
    <>
      <p className="subtitle">
        作成した<strong>語彙（オントロジー）</strong>と<strong>マッピング</strong>を俯瞰します。
        2 つは性質が異なります — 語彙は<strong>共有資産</strong>で変更が下流全体に波及（要注意）、
        マッピングは<strong>dataset ごとの束縛</strong>で局所・使い捨て。
      </p>

      {/* Layer legend: makes the edit-risk contrast explicit before the cards. */}
      <div className="layer-legend">
        <span className="layer-chip layer-chip--ontology">
          <span className="risk-dot risk-dot--high" /> Ontologies（共有 TBox・変更注意）
        </span>
        <span className="layer-chip layer-chip--mapping">
          <span className="risk-dot risk-dot--low" /> Mappings（局所・使い捨て）
          <span className="soon-tag">M4b で追加</span>
        </span>
      </div>

      {error && <pre className="error">{error}</pre>}
      {!ontologies && !error && <p className="trace-loading">ギャラリーを読み込み中…</p>}

      {ontologies && (
        <section className="gallery-section">
          <h2 className="gallery-h">
            Ontologies <span className="gallery-count">{ontologies.length}</span>
            <span className="risk-badge risk-badge--high">共有資産・変更注意</span>
          </h2>

          <div className="onto-card-grid">
            {ontologies.map((o) => (
              <button
                key={o.id}
                type="button"
                className={`onto-card${o.id === selectedId ? ' active' : ''}`}
                onClick={() => setSelectedId(o.id)}
              >
                <div className="onto-card-head">
                  <span className="onto-card-name">{o.name}</span>
                  <code className="onto-card-prefix">{o.prefix}</code>
                </div>
                <div className="onto-card-meta">
                  <span className="onto-card-stat">{o.classes.length} クラス</span>
                  <span className="onto-card-stat">{o.reuses.length} 語彙を再利用</span>
                </div>
                <div className="onto-card-classes">
                  {o.classes.map((c) => (
                    <span key={c} className="onto-class-chip">
                      {c}
                    </span>
                  ))}
                </div>
              </button>
            ))}
          </div>

          {selected && <OntologyDetail entry={selected} />}
        </section>
      )}
    </>
  )
}

function OntologyDetail({ entry }: { entry: OntologyEntry }) {
  return (
    <section className="onto-detail">
      <div className="onto-detail-head">
        <div>
          <h3 className="onto-detail-name">{entry.name}</h3>
          <code className="onto-detail-iri" title={entry.baseIri}>
            {entry.baseIri}
          </code>
        </div>
        <span className="risk-badge risk-badge--high">
          <span className="risk-dot risk-dot--high" />
          共有 TBox — 壊すと下流全体に波及
        </span>
      </div>

      <p className="onto-detail-desc">{entry.description}</p>

      <div className="onto-reuse">
        <span className="onto-reuse-label">再利用している語彙</span>
        <div className="onto-reuse-list">
          {entry.reuses.map((r) => (
            <span key={r.prefix} className="onto-reuse-chip" title={r.what}>
              <code>{r.prefix}</code>
              <span className="onto-reuse-what">{r.what}</span>
            </span>
          ))}
        </div>
      </div>

      <div className="onto-diagram">
        <Mermaid chart={entry.mermaid} />
      </div>
    </section>
  )
}
