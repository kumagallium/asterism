import { useEffect, useState } from 'react'
import { buildPerspective, type CrosswalkPerspective, getCrosswalks } from './crosswalkApi'
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
    </div>
  )
}
