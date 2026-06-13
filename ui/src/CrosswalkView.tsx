import { useEffect, useState } from 'react'
import {
  align,
  type Alignment,
  type AlignmentsResult,
  buildPerspective,
  type CrosswalkPerspective,
  getAlignments,
  getCrosswalks,
  unalign,
} from './crosswalkApi'
import { ArrowIcon, LinkIcon } from './icons'
import { ToolsPanel } from './ToolsPanel'
import { localName } from './vocab'

/**
 * Catalog → クロスウォーク管理面 (multi-perspective ADR, 管理=カタログ). The upper ontology
 * is PLURAL: a list of independent crosswalk PERSPECTIVES (lenses). Each is its own
 * graph + config; pick one to see its participants, stats, cross-dataset tools, and a
 * manual rebuild. Creation (incl. naming a new perspective) lives in データを追加 →
 * 横断でつなぐ (CrosswalkBuilder).
 */
export function CrosswalkView({ onBack }: { onBack?: () => void }) {
  const [perspectives, setPerspectives] = useState<CrosswalkPerspective[] | null>(null)
  const [err, setErr] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildErr, setRebuildErr] = useState('')
  const [note, setNote] = useState('')

  function load() {
    getCrosswalks()
      .then(setPerspectives)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    let off = false
    getCrosswalks()
      .then((ps) => !off && setPerspectives(ps))
      .catch((e) => !off && setErr(e instanceof Error ? e.message : String(e)))
    return () => {
      off = true
    }
  }, [])

  const list = perspectives ?? []
  const selected = list.find((p) => p.perspective_id === selectedId) ?? list[0] ?? null

  function pname(p: CrosswalkPerspective): string {
    return p.dataset?.name || p.perspective_id
  }

  async function onRebuild() {
    if (!selected) return
    setRebuilding(true)
    setRebuildErr('')
    setNote('')
    try {
      const r = await buildPerspective(selected.perspective_id) // no config → rebuild persisted
      setNote(`再構築しました（${r.shared_total} 件の共有・${r.participants_used.length} データセット）。`)
      load()
    } catch (e) {
      setRebuildErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRebuilding(false)
    }
  }

  const concepts = selected?.config?.concepts ?? []
  const participants = concepts.flatMap((c) => c.participants)
  const shared = selected?.dataset?.crosswalk_shared_compositions

  return (
    <div className="crosswalk-view">
      {onBack && (
        <button type="button" className="vocab-back" onClick={onBack}>
          <ArrowIcon size={14} className="vocab-back-arrow" /> カタログに戻る
        </button>
      )}

      <div className="vocab-banner">
        <span className="vocab-banner-icon">
          <LinkIcon size={22} />
        </span>
        <div>
          <h2 className="vocab-banner-title">クロスウォーク（横断をつなぐ橋）</h2>
          <p className="vocab-banner-sub">
            データセットの繋ぎ方は<strong>複数の視点（perspective）</strong>がありえます（組成・結晶構造…）。
            各視点は<strong>別々の独立した橋</strong>として持て、横断クエリは自動で効きます。
          </p>
        </div>
      </div>

      {err && <pre className="error">{err}</pre>}
      {!perspectives && !err && (
        <p className="loading-row">
          <span className="spinner" />
          読み込み中…
        </p>
      )}

      {perspectives && list.length === 0 && (
        <div className="state-block">
          <p className="state-title">まだクロスウォークがありません</p>
          <p className="state-sub">
            「データを追加 → 既存データを横断でつなぐ」で、2つ以上のデータセットから橋を作れます。
          </p>
        </div>
      )}

      {list.length > 0 && (
        <>
          <div className="ds-subhead">
            視点（perspective）
            <span className="xw-hint-inline">
              {list.length} 件 · それぞれ独立した「つなぎ方」
            </span>
          </div>
          <div className="xw-persp-tabs">
            {list.map((p) => (
              <button
                key={p.perspective_id}
                type="button"
                className={`xw-persp-tab${p.perspective_id === selected?.perspective_id ? ' active' : ''}`}
                onClick={() => setSelectedId(p.perspective_id)}
              >
                <span className="xw-persp-name">{pname(p)}</span>
                <span className="xw-persp-meta">
                  {p.dataset?.crosswalk_shared_compositions ?? '—'} 共有 ·{' '}
                  {p.config?.concepts.flatMap((c) => c.participants).length ?? 0} DS
                </span>
              </button>
            ))}
          </div>

          {selected && (
            <>
              <div className="xw-summary">
                <div className="xw-summary-stat">
                  <span className="xw-summary-num">{shared ?? '—'}</span>
                  <span className="xw-summary-label">共有された値</span>
                </div>
                <div className="xw-summary-stat">
                  <span className="xw-summary-num">{participants.length}</span>
                  <span className="xw-summary-label">参加データセット</span>
                </div>
                <div className="xw-summary-stat">
                  <span className="xw-summary-num">{concepts.length}</span>
                  <span className="xw-summary-label">共有概念</span>
                </div>
              </div>

              {concepts.map((c) => (
                <div className="xw-concept" key={c.name}>
                  <div className="ds-subhead">
                    概念「{c.name}」
                    <span className="xw-hint-inline">正規化: {c.normalizer ?? 'identity'}</span>
                  </div>
                  <div className="xw-participants">
                    {c.participants.map((p) => (
                      <span key={p.dataset_id} className="xw-part-chip" title={p.predicate}>
                        <span className="xw-part-name">{p.label}</span>
                        <code className="xw-part-pred">{localName(p.predicate)}</code>
                      </span>
                    ))}
                  </div>
                </div>
              ))}

              <div className="xw-rebuild-row">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={rebuilding}
                  onClick={onRebuild}
                >
                  {rebuilding ? '再構築中…' : 'この視点を再構築（最新のデータで）'}
                </button>
                {selected.dataset?.crosswalk_built_at && (
                  <span className="xw-built-at">
                    最終構築: {selected.dataset.crosswalk_built_at.slice(0, 19).replace('T', ' ')}
                  </span>
                )}
              </div>
              {note && <p className="lifecycle-ok">{note}</p>}
              {rebuildErr && <p className="promote-err">再構築に失敗しました: {rebuildErr}</p>}

              <div className="ds-subhead xw-tools-head">
                横断ツール
                <span className="xw-hint-inline">
                  この値は何データセットが報告？ など（決定論・引用可・キー不要）
                </span>
              </div>
              {/* The hub-resident cross-dataset tools — keyed by perspective so they
                  reload when you switch lens. */}
              <ToolsPanel
                key={selected.perspective_id}
                datasetId={selected.dataset?.id ?? 'crosswalk-bridge'}
              />
            </>
          )}
        </>
      )}

      {perspectives && <PerspectiveAlignment perspectives={list} />}
    </div>
  )
}

// --- 視点をつなぐ (multi-perspective ADR §Phase 2) -------------------------------
// Assert a human-vetted, citable, reversible SCHEMA relationship between two
// perspectives' terms (a concept class or its link predicate). Closed relation set;
// stored in a promoted alignment graph the FROM-merge unions. Oxigraph runs no OWL
// reasoner, so this is a fact a tool can FOLLOW — it never rewrites queries.

const RELATION_LABEL: Record<string, string> = {
  equivalentClass: '同値クラス（≡）',
  subClassOf: '下位クラス（⊑）',
  equivalentProperty: '同値述語（≡）',
  subPropertyOf: '下位述語（⊑）',
}
const CLASS_RELATIONS = new Set(['equivalentClass', 'subClassOf'])

interface PerspTerm {
  iri: string
  kind: 'class' | 'property'
  conceptName: string
  name: string
}

function perspName(p: CrosswalkPerspective): string {
  return p.dataset?.name || p.perspective_id
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

function relationLabel(rel: string): string {
  return RELATION_LABEL[rel] ?? rel
}

function PerspectiveAlignment({ perspectives }: { perspectives: CrosswalkPerspective[] }) {
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

  function load() {
    getAlignments()
      .then(setData)
      .catch((e) => setLoadErr(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    let off = false
    getAlignments()
      .then((d) => !off && setData(d))
      .catch((e) => !off && setLoadErr(e instanceof Error ? e.message : String(e)))
    return () => {
      off = true
    }
  }, [])

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

  async function onAssert() {
    if (!canAssert || !srcTerm || !tgtTerm || !srcPersp || !tgtPersp) return
    setBusy(true)
    setActErr('')
    setNote('')
    try {
      await align(srcTerm.iri, tgtTerm.iri, rel, perspName(srcPersp), perspName(tgtPersp))
      setNote(`つなぎました: ${srcTerm.name} ${relationLabel(rel)} ${tgtTerm.name}`)
      load()
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
      load()
    } catch (e) {
      setActErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRemoving('')
    }
  }

  const alignments = data?.alignments ?? []

  return (
    <div className="xw-align">
      <div className="ds-subhead xw-tools-head">
        視点をつなぐ
        <span className="xw-hint-inline">
          別々に作った視点の概念どうしに関係を主張（引用できる事実・推論はしない・撤回可）
        </span>
      </div>

      {loadErr && <pre className="error">{loadErr}</pre>}

      {/* Authoring form: pick two perspectives' terms + a closed-set relation. */}
      <div className="xw-align-form">
        <div className="xw-align-side">
          <span className="xw-align-side-label">起点（source）</span>
          <select
            className="xw-map-select"
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
            value={srcTerm?.iri ?? ''}
            onChange={(e) => setSrcIri(e.target.value)}
            disabled={srcTerms.length === 0}
          >
            {srcTerms.map((t) => (
              <option key={t.iri} value={t.iri}>
                {t.kind === 'class' ? 'クラス' : '述語'} · {t.name}
              </option>
            ))}
          </select>
        </div>

        <div className="xw-align-rel">
          <select
            className="xw-map-select"
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
          <span className="xw-align-side-label">対象（target）</span>
          <select
            className="xw-map-select"
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
            value={tgtTerm?.iri ?? ''}
            onChange={(e) => setTgtIri(e.target.value)}
            disabled={tgtTerms.length === 0}
          >
            {tgtTerms.map((t) => (
              <option key={t.iri} value={t.iri}>
                {t.kind === 'class' ? 'クラス' : '述語'} · {t.name}
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
          {busy ? 'つないでいます…' : 'つなぐ'}
        </button>
      </div>

      {!canAssert && perspectives.length > 0 && (
        <p className="xw-align-empty-hint">
          {srcTerms.length === 0
            ? 'この視点には繋げる概念がありません。'
            : tgtTerms.length === 0
              ? '同じ種類（クラス／述語）の対象がありません。別の対象視点を選んでください。'
              : '起点と対象に別々の概念を選んでください（2つ以上の視点があると繋げます）。'}
        </p>
      )}
      {note && <p className="lifecycle-ok">{note}</p>}
      {actErr && <p className="promote-err">操作に失敗しました: {actErr}</p>}

      {/* The asserted alignments (each withdrawable). */}
      {alignments.length > 0 ? (
        <div className="xw-align-list">
          {alignments.map((a) => (
            <div className="xw-align-row" key={a.alignment_iri}>
              <div className="xw-align-claim">
                <code className="xw-align-term" title={a.source}>
                  {localName(a.source)}
                </code>
                <span className="xw-align-relchip">{relationLabel(a.relation)}</span>
                <code className="xw-align-term" title={a.target}>
                  {localName(a.target)}
                </code>
              </div>
              <div className="xw-align-meta">
                {(a.from_perspective || a.to_perspective) && (
                  <span className="xw-align-persp">
                    {a.from_perspective || '—'} → {a.to_perspective || '—'}
                  </span>
                )}
                {a.at && <span className="xw-built-at">{a.at.slice(0, 19).replace('T', ' ')}</span>}
              </div>
              <button
                type="button"
                className="btn btn--ghost btn--sm xw-align-remove"
                disabled={removing === a.alignment_iri}
                onClick={() => onRemove(a)}
              >
                {removing === a.alignment_iri ? '撤回中…' : '撤回'}
              </button>
            </div>
          ))}
        </div>
      ) : (
        data && <p className="xw-align-none">まだ視点をつなぐ整合はありません。</p>
      )}
    </div>
  )
}
