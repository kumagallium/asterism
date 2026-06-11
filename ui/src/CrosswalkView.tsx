import { useEffect, useState } from 'react'
import { buildCrosswalk, type CrosswalkInfo, getCrosswalk } from './crosswalkApi'
import { ArrowIcon, LinkIcon } from './icons'
import { ToolsPanel } from './ToolsPanel'
import { localName } from './vocab'

/**
 * Catalog → クロスウォーク管理面 (ADR crosswalk-hub.md ④, 管理=カタログ). Shows the live
 * hub: which datasets/concepts participate, how many compositions are shared, the
 * cross-dataset tools (incl. "this composition is reported by N datasets"), and a
 * manual rebuild. Creation lives in データを追加 → 横断でつなぐ (CrosswalkBuilder).
 */
export function CrosswalkView({ onBack }: { onBack?: () => void }) {
  const [info, setInfo] = useState<CrosswalkInfo | null>(null)
  const [err, setErr] = useState('')
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildErr, setRebuildErr] = useState('')
  const [note, setNote] = useState('')

  function load() {
    getCrosswalk()
      .then(setInfo)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    let off = false
    getCrosswalk()
      .then((d) => !off && setInfo(d))
      .catch((e) => !off && setErr(e instanceof Error ? e.message : String(e)))
    return () => {
      off = true
    }
  }, [])

  async function onRebuild() {
    setRebuilding(true)
    setRebuildErr('')
    setNote('')
    try {
      const r = await buildCrosswalk() // no config → rebuild from the persisted one
      setNote(`再構築しました（${r.shared_total} 件の共有組成・${r.participants_used.length} データセット）。`)
      load()
    } catch (e) {
      setRebuildErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRebuilding(false)
    }
  }

  const concepts = info?.config?.concepts ?? []
  const participants = info?.config?.concepts.flatMap((c) => c.participants) ?? []
  const shared = info?.dataset?.crosswalk_shared_compositions

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
            複数のデータセットが共通で報告する値（組成など）を1つにまとめ、
            <strong>横断して検索・比較</strong>できるようにする薄い橋です。データセットが増えるほど橋は育ちます。
          </p>
        </div>
      </div>

      {err && <pre className="error">{err}</pre>}
      {!info && !err && (
        <p className="loading-row">
          <span className="spinner" />
          読み込み中…
        </p>
      )}

      {info && !info.exists && (
        <div className="state-block">
          <p className="state-title">まだクロスウォークがありません</p>
          <p className="state-sub">
            「データを追加 → 既存データを横断でつなぐ」で、2つ以上のデータセットから橋を作れます。
          </p>
        </div>
      )}

      {info?.exists && (
        <>
          <div className="xw-summary">
            <div className="xw-summary-stat">
              <span className="xw-summary-num">{shared ?? '—'}</span>
              <span className="xw-summary-label">共有された組成</span>
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
                概念「{c.name}」<span className="xw-hint-inline">正規化: {c.normalizer ?? 'identity'}</span>
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
              {rebuilding ? '再構築中…' : '橋を再構築（最新のデータで）'}
            </button>
            {info.dataset?.crosswalk_built_at && (
              <span className="xw-built-at">
                最終構築: {info.dataset.crosswalk_built_at.slice(0, 19).replace('T', ' ')}
              </span>
            )}
          </div>
          {note && <p className="lifecycle-ok">{note}</p>}
          {rebuildErr && <p className="promote-err">再構築に失敗しました: {rebuildErr}</p>}

          <div className="ds-subhead xw-tools-head">
            横断ツール
            <span className="xw-hint-inline">
              この組成は何データセットが報告？ など（決定論・引用可・キー不要）
            </span>
          </div>
          {/* The hub-resident cross-dataset tools (datasets_for_composition,
              zt_by_crystal_structure, …) — run them right here. */}
          <ToolsPanel datasetId="crosswalk-bridge" />
        </>
      )}
    </div>
  )
}
