import { useEffect, useState } from 'react'
import {
  type AlignmentReport,
  type DatasetStage,
  datasetStage,
  getAlignment,
  getLiveDatasets,
  getMappings,
  getOntologies,
  type LiveDataset,
  type MappingEntry,
  type OntologyEntry,
  promoteDataset,
  STAGE_INFO,
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
export function GalleryView({ focusClass }: { focusClass?: string | null }) {
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
        // When arriving via an Ask citation, select the ontology that defines
        // the focused class; otherwise the first one.
        const focused = focusClass
          ? onto.find((o) => o.classes.includes(focusClass))
          : undefined
        setSelectedId(focused?.id ?? onto[0]?.id ?? null)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [focusClass])

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

      {focusClass && (
        <div className="vocab-focus-banner">
          Ask の引用に対応する語彙クラス：<strong>{focusClass}</strong>
          <span className="vocab-focus-sub">この回答はこのクラスで型付けされたデータに基づきます。</span>
        </div>
      )}

      {error && <pre className="error">{error}</pre>}
      {!ontologies && !error && (
        <p className="trace-loading">
          <span className="spinner" />
          ギャラリーを読み込み中…
        </p>
      )}

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
                highlightClass={focusClass ?? undefined}
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
                ワークベンチで作成したマッピング
                <span className="gallery-count">{live.length}</span>
              </h3>
              <div className="mapping-list">
                {live.map((l) => (
                  <div key={l.mapping.id}>
                    <MappingCard entry={l.mapping} stage={datasetStage(l.meta)} />
                    <PromoteControl meta={l.meta} />
                  </div>
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
  highlightClass,
}: {
  entry: OntologyEntry
  selected: boolean
  onSelect: () => void
  draft?: boolean
  highlightClass?: string
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
          <span
            key={c}
            className={`onto-class-chip${c === highlightClass ? ' onto-class-chip--focus' : ''}`}
          >
            {c}
          </span>
        ))}
      </div>
    </button>
  )
}

function MappingCard({ entry, stage }: { entry: MappingEntry; stage?: DatasetStage }) {
  const ARTIFACT_JA: Record<string, string> = {
    ingester: 'ingester',
    mie: 'MIE',
    shex: 'ShEx',
    mapping: 'RML',
  }
  const info = stage ? STAGE_INFO[stage] : null
  return (
    <article className={`mapping-card${stage ? ' mapping-card--draft' : ''}`}>
      <div className="mapping-card-head">
        <div>
          <h3 className="mapping-card-name">
            {entry.name}
            {info && <span className={`stage-tag stage-tag--${info.tone}`}>{info.badge}</span>}
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

function shortIri(iri: string): string {
  const m = iri.split(/[#/]/).filter(Boolean)
  return m.length ? m[m.length - 1] : iri
}

/**
 * The S4 human gate: review the draft's vocabulary alignment (Reuse vs New)
 * against the canonical graph, then promote (MOVE draft → canonical) so Ask can
 * cite it. Only shown for ingested-but-not-yet-promoted drafts.
 */
function PromoteControl({ meta }: { meta: LiveDataset['meta'] }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [alignment, setAlignment] = useState<AlignmentReport | null>(null)
  const [promoted, setPromoted] = useState<number | null>(
    meta.promoted ? (meta.triples_promoted ?? 0) : null,
  )

  if (promoted !== null) {
    return (
      <p className="promote-ok">
        ✓ 共有データに昇格済み（{promoted} 件）。Ask が引用できます（正式グラフ＝canonical）。
      </p>
    )
  }
  if (!meta.ingested) return null // nothing to promote until ingested

  async function preview() {
    setErr('')
    try {
      setAlignment(await getAlignment(meta.id))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }
  async function promote() {
    setBusy(true)
    setErr('')
    try {
      setPromoted((await promoteDataset(meta.id)).triples_promoted)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="promote-control">
      <p className="promote-note">
        「共有データに昇格」すると、下書きグラフのこのデータが <strong>Ask が引用する正式グラフ
        （canonical）</strong> に移ります。昇格前に、使っている語彙が既存の再利用か新規かを確認できます。
      </p>
      {alignment ? (
        <div className="alignment-summary">
          <span>
            述語: 既存の再利用 {alignment.predicates.reuse.length} / 新規{' '}
            {alignment.predicates.new.length} ／ クラス: 既存の再利用{' '}
            {alignment.classes.reuse.length} / 新規 {alignment.classes.new.length}
          </span>
          {alignment.predicates.new.length > 0 && (
            <p className="alignment-new">
              新規の述語（既存語彙に無い）: {alignment.predicates.new.map(shortIri).join('、')}
            </p>
          )}
        </div>
      ) : (
        <button type="button" onClick={preview}>
          語彙の差分を確認（昇格前チェック）
        </button>
      )}
      <button type="button" className="promote-btn" onClick={promote} disabled={busy}>
        {busy ? '昇格中…' : '共有データに昇格（Ask で使えるように）'}
      </button>
      {err && <p className="promote-err">昇格に失敗しました: {err}</p>}
    </div>
  )
}
