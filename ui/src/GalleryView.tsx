import { useEffect, useState } from 'react'
import {
  getLiveDatasets,
  getMappings,
  getOntologies,
  type LiveDataset,
  type MappingEntry,
  type OntologyEntry,
} from './galleryApi'
import { Mermaid } from './Mermaid'

/**
 * M4 gallery: an overview of the assets that have been built. The two layers
 * are presented as visually distinct so their edit-risk differs at a glance
 * (design doc §6.6/D8):
 *
 *   - Ontologies (shared TBox): high blast radius — edit with care.
 *   - Mappings (dataset bindings, purpose-tagged): local & disposable.
 *
 * The Mappings gallery leads with each binding's PURPOSE tags — the
 * "purpose-scoped mapping" idea is the showcase for reviewers (handoff §1).
 */
export function GalleryView() {
  const [ontologies, setOntologies] = useState<OntologyEntry[] | null>(null)
  const [mappings, setMappings] = useState<MappingEntry[] | null>(null)
  const [live, setLive] = useState<LiveDataset[]>([])
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // getLiveDatasets is best-effort (resolves [] if the workbench API is
    // absent), so it never blocks the fixtures from rendering.
    Promise.all([getOntologies(), getMappings(), getLiveDatasets()])
      .then(([onto, maps, liveDatasets]) => {
        if (cancelled) return
        setOntologies(onto)
        setMappings(maps)
        setLive(liveDatasets)
        setSelectedId(onto[0]?.id ?? null)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Selection spans both the seeded fixtures and the live (materialized) drafts.
  const selected =
    ontologies?.find((o) => o.id === selectedId) ??
    live.find((l) => l.ontology.id === selectedId)?.ontology ??
    null

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
              <OntologyCard
                key={o.id}
                entry={o}
                selected={o.id === selectedId}
                onSelect={() => setSelectedId(o.id)}
              />
            ))}
          </div>

          {/* Live drafts: datasets materialized in the workbench (api V1a),
              now surfacing in the catalog — the authoring→catalog loop. */}
          {live.length > 0 && (
            <div className="live-subsection">
              <h3 className="gallery-subh">
                ワークベンチで作成した設計（ドラフト）
                <span className="gallery-count">{live.length}</span>
              </h3>
              <div className="onto-card-grid">
                {live.map((l) => (
                  <OntologyCard
                    key={l.ontology.id}
                    entry={l.ontology}
                    selected={l.ontology.id === selectedId}
                    onSelect={() => setSelectedId(l.ontology.id)}
                    draft
                  />
                ))}
              </div>
            </div>
          )}

          {selected && <OntologyDetail entry={selected} />}
        </section>
      )}

      {mappings && (
        <section className="gallery-section gallery-section--mappings">
          <h2 className="gallery-h">
            Mappings <span className="gallery-count">{mappings.length}</span>
            <span className="risk-badge risk-badge--low">
              <span className="risk-dot risk-dot--low" />
              局所・使い捨て — 安全に編集可
            </span>
          </h2>
          <p className="gallery-note">
            各マッピングは「どの<strong>目的（問い）</strong>に応えるための束縛か」を
            <strong>目的タグ</strong>で示します。語彙（共有資産）と違い、目的ごとに増やして構いません。
          </p>

          <div className="mapping-list">
            {mappings.map((m) => (
              <MappingCard key={m.id} entry={m} />
            ))}
          </div>

          {live.length > 0 && (
            <div className="live-subsection">
              <h3 className="gallery-subh">
                ワークベンチで作成したマッピング（ドラフト・未取り込み）
                <span className="gallery-count">{live.length}</span>
              </h3>
              <div className="mapping-list">
                {live.map((l) => (
                  <MappingCard key={l.mapping.id} entry={l.mapping} draft />
                ))}
              </div>
            </div>
          )}
        </section>
      )}
    </>
  )
}

function OntologyCard({
  entry,
  selected,
  onSelect,
  draft,
}: {
  entry: OntologyEntry
  selected: boolean
  onSelect: () => void
  draft?: boolean
}) {
  return (
    <button
      type="button"
      className={`onto-card${selected ? ' active' : ''}${draft ? ' onto-card--draft' : ''}`}
      onClick={onSelect}
    >
      <div className="onto-card-head">
        <span className="onto-card-name">{entry.name}</span>
        {draft ? (
          <span className="draft-tag">ドラフト</span>
        ) : (
          <code className="onto-card-prefix">{entry.prefix}</code>
        )}
      </div>
      <div className="onto-card-meta">
        <span className="onto-card-stat">{entry.classes.length} クラス</span>
        {!draft && <span className="onto-card-stat">{entry.reuses.length} 語彙を再利用</span>}
      </div>
      <div className="onto-card-classes">
        {entry.classes.map((c) => (
          <span key={c} className="onto-class-chip">
            {c}
          </span>
        ))}
      </div>
    </button>
  )
}

function MappingCard({ entry, draft }: { entry: MappingEntry; draft?: boolean }) {
  const ARTIFACT_JA: Record<string, string> = { ingester: 'ingester', mie: 'MIE', shex: 'ShEx' }
  return (
    <article className={`mapping-card${draft ? ' mapping-card--draft' : ''}`}>
      <div className="mapping-card-head">
        <div>
          <h3 className="mapping-card-name">
            {entry.name}
            {draft && <span className="draft-tag">ドラフト・未取り込み</span>}
          </h3>
          <span className="mapping-card-dataset">{entry.dataset}</span>
        </div>
        <span className="mapping-target" title={`${entry.targetOntologyName} へ束縛`}>
          → {entry.targetOntologyName}
        </span>
      </div>

      {/* Purpose tags lead — the showcase signal. Drafts have none yet. */}
      {entry.purposes.length > 0 ? (
        <div className="purpose-block">
          <span className="purpose-label">目的（この束縛が応える問い）</span>
          <div className="purpose-tags">
            {entry.purposes.map((p) => (
              <span key={p.tag} className="purpose-tag" title={p.detail}>
                <span className="purpose-tag-name">{p.tag}</span>
                <span className="purpose-tag-detail">{p.detail}</span>
              </span>
            ))}
          </div>
        </div>
      ) : (
        <p className="mapping-desc">{entry.description}</p>
      )}

      <div className="mapping-artifacts">
        <span className="mapping-artifacts-label">構成物</span>
        {entry.artifacts.map((a) => (
          <div key={a.name} className="mapping-artifact">
            <span className={`artifact-kind artifact-kind--${a.kind}`}>{ARTIFACT_JA[a.kind]}</span>
            <code className="artifact-name">{a.name}</code>
            <span className="artifact-summary">{a.summary}</span>
          </div>
        ))}
      </div>
    </article>
  )
}

function OntologyDetail({ entry }: { entry: OntologyEntry }) {
  // A draft (materialized but not promoted) TBox is local/disposable; the
  // seeded shared vocabulary is high-risk. Reflect that in the badge.
  const isDraft = entry.editRisk === 'low'
  return (
    <section className="onto-detail">
      <div className="onto-detail-head">
        <div>
          <h3 className="onto-detail-name">{entry.name}</h3>
          <code className="onto-detail-iri" title={entry.baseIri}>
            {entry.baseIri}
          </code>
        </div>
        {isDraft ? (
          <span className="risk-badge risk-badge--low">
            <span className="risk-dot risk-dot--low" />
            ドラフト設計 — 共有語彙への昇格前
          </span>
        ) : (
          <span className="risk-badge risk-badge--high">
            <span className="risk-dot risk-dot--high" />
            共有 TBox — 壊すと下流全体に波及
          </span>
        )}
      </div>

      <p className="onto-detail-desc">{entry.description}</p>

      {entry.reuses.length > 0 && (
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
      )}

      {entry.mermaid ? (
        <div className="onto-diagram">
          <Mermaid chart={entry.mermaid} />
        </div>
      ) : (
        <p className="trace-loading">クラス図はありません。</p>
      )}
    </section>
  )
}
