import { useEffect, useState } from 'react'
import {
  buildCrosswalk,
  type BuildResult,
  type CrosswalkConfig,
  type PredicateCandidate,
  proposeCrosswalkMapping,
} from './crosswalkApi'
import { type CatalogDataset, getCatalogDatasets } from './galleryApi'
import { LinkIcon } from './icons'
import { localName } from './vocab'

const API_KEY_STORAGE = 'asterism.apiKey'

/** Slug a dataset name into a stable crosswalk label (falls back to its id). */
function labelFor(d: CatalogDataset): string {
  const slug = d.name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return slug || d.live?.meta.id || d.id
}

/**
 * Crosswalk authoring (ADR crosswalk-hub.md ④, 作成=「データを追加」). A crosswalk is an
 * ontology built FROM existing datasets: pick >=2 promoted datasets, declare each
 * one's concept-bearing predicate (the human-vetted mapping claim — a dropdown,
 * AI-assisted), and build. The hub then joins them on the shared normalized value.
 */
export function CrosswalkBuilder() {
  const [datasets, setDatasets] = useState<CatalogDataset[] | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  // dataset_id -> chosen predicate IRI (the concept-bearing predicate).
  const [predicate, setPredicate] = useState<Record<string, string>>({})
  // dataset_id -> AI-sampled candidates (iri + sample value), populated by propose.
  const [candidates, setCandidates] = useState<Record<string, PredicateCandidate[]>>({})
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem(API_KEY_STORAGE) ?? '')
  // The join key. 'composition' = conservative (fold subscripts + strip); '
  // element_canonical' also reorders elements so Bi2Te3 == Te3Bi2 (ADR ①).
  const [normalizer, setNormalizer] = useState('composition')
  const [proposing, setProposing] = useState(false)
  const [proposeErr, setProposeErr] = useState('')
  const [proposeNote, setProposeNote] = useState('')
  const [building, setBuilding] = useState(false)
  const [buildErr, setBuildErr] = useState('')
  const [result, setResult] = useState<BuildResult | null>(null)

  useEffect(() => {
    let off = false
    getCatalogDatasets()
      .then((all) => {
        if (off) return
        // Only PROMOTED, non-crosswalk datasets can participate (they have live data
        // to join; the hub itself is a bridge, not a participant).
        setDatasets(all.filter((d) => d.statusKind === 'pub' && !d.isCrosswalk))
      })
      .catch((e) => !off && setLoadErr(e instanceof Error ? e.message : String(e)))
    return () => {
      off = true
    }
  }, [])

  function datasetId(d: CatalogDataset): string {
    return d.live?.meta.id ?? d.id
  }

  function toggle(d: CatalogDataset) {
    const id = datasetId(d)
    setResult(null)
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  function onApiKeyChange(v: string) {
    setApiKey(v)
    sessionStorage.setItem(API_KEY_STORAGE, v)
  }

  const chosen = (datasets ?? []).filter((d) => selected.has(datasetId(d)))
  const readyCount = chosen.filter((d) => predicate[datasetId(d)]).length
  const canBuild = !building && readyCount >= 2

  // Predicate options for a dataset: AI-sampled candidates (with example values) if
  // proposed, else the predicates the dataset actually uses (from its alignment).
  function optionsFor(d: CatalogDataset): PredicateCandidate[] {
    const id = datasetId(d)
    if (candidates[id]?.length) return candidates[id]
    return d.predicates.map((iri) => ({ iri, sample: '' }))
  }

  async function onPropose() {
    setProposing(true)
    setProposeErr('')
    setProposeNote('')
    try {
      const ids = chosen.map(datasetId)
      const r = await proposeCrosswalkMapping(ids, 'composition', apiKey)
      const cand: Record<string, PredicateCandidate[]> = {}
      for (const c of r.candidates) cand[c.dataset_id] = c.predicates
      setCandidates((prev) => ({ ...prev, ...cand }))
      const picks: Record<string, string> = {}
      for (const p of r.participants) picks[p.dataset_id] = p.predicate
      setPredicate((prev) => ({ ...prev, ...picks }))
      const n = r.participants.length
      setProposeNote(
        n
          ? `AI が ${n} 件の述語を提案しました（確認・修正してください）。`
          : 'AI は組成を担う述語を見つけられませんでした。手動で選んでください。',
      )
    } catch (e) {
      setProposeErr(e instanceof Error ? e.message : String(e))
    } finally {
      setProposing(false)
    }
  }

  async function onBuild() {
    setBuilding(true)
    setBuildErr('')
    setResult(null)
    try {
      const config: CrosswalkConfig = {
        min_datasets: 2,
        concepts: [
          {
            name: 'composition',
            normalizer,
            participants: chosen
              .filter((d) => predicate[datasetId(d)])
              .map((d) => ({
                dataset_id: datasetId(d),
                label: labelFor(d),
                predicate: predicate[datasetId(d)],
              })),
          },
        ],
      }
      setResult(await buildCrosswalk(config))
    } catch (e) {
      setBuildErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBuilding(false)
    }
  }

  return (
    <div className="xw-builder">
      <p className="subtitle">
        既存の<strong>データセットを2つ以上選び</strong>、それぞれの「<strong>組成を表す列</strong>」を指定すると、
        共通の組成で<strong>横断してつながる橋（クロスウォーク）</strong>を作れます。
        新しい CSV からではなく、<strong>すでにあるデータから作る</strong>オントロジーです。
      </p>

      {loadErr && <pre className="error">{loadErr}</pre>}
      {!datasets && !loadErr && (
        <p className="loading-row">
          <span className="spinner" />
          データセットを読み込み中…
        </p>
      )}

      {datasets && datasets.length < 2 && (
        <div className="state-block">
          <p className="state-title">横断できる共有データが2つ未満です</p>
          <p className="state-sub">
            「共有データに昇格」したデータセットが2つ以上あると、ここで横断クロスウォークを作れます。
          </p>
        </div>
      )}

      {datasets && datasets.length >= 2 && (
        <>
          <div className="ds-subhead">1. つなぐデータセットを選ぶ（2つ以上）</div>
          <div className="xw-ds-grid">
            {datasets.map((d) => {
              const id = datasetId(d)
              const on = selected.has(id)
              return (
                <button
                  key={id}
                  type="button"
                  className={`xw-ds-card${on ? ' active' : ''}`}
                  onClick={() => toggle(d)}
                >
                  <span className="xw-ds-check">{on ? '✓' : ''}</span>
                  <span className="xw-ds-body">
                    <span className="xw-ds-name">{d.name}</span>
                    <span className="xw-ds-sub">
                      {d.predicates.length} 述語{' · '}
                      {(d.counts.find((c) => c.label === '事実')?.value ?? '?') + ' 事実'}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>

          {chosen.length > 0 && (
            <>
              <div className="ds-subhead">
                2. 各データセットで「組成を担う述語」を指定する
                <span className="xw-hint-inline">（人間が確認するマッピング）</span>
              </div>

              <div className="xw-ai-row">
                <input
                  type="password"
                  className="xw-key-input"
                  placeholder="Anthropic API キー（AI 提案に使用・保存は端末内のみ）"
                  value={apiKey}
                  onChange={(e) => onApiKeyChange(e.target.value)}
                />
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={proposing || !apiKey || chosen.length < 1}
                  onClick={onPropose}
                >
                  {proposing ? 'AI が提案中…' : 'AI に提案させる'}
                </button>
              </div>
              {proposeNote && <p className="xw-note">{proposeNote}</p>}
              {proposeErr && <p className="promote-err">AI 提案に失敗しました: {proposeErr}</p>}

              <div className="xw-map-list">
                {chosen.map((d) => {
                  const id = datasetId(d)
                  const opts = optionsFor(d)
                  const sel = predicate[id] ?? ''
                  const sample = opts.find((o) => o.iri === sel)?.sample
                  return (
                    <div className="xw-map-row" key={id}>
                      <span className="xw-map-ds">{d.name}</span>
                      <select
                        className="xw-map-select"
                        value={sel}
                        onChange={(e) =>
                          setPredicate((prev) => ({ ...prev, [id]: e.target.value }))
                        }
                      >
                        <option value="">— 述語を選択 —</option>
                        {opts.map((o) => (
                          <option key={o.iri} value={o.iri}>
                            {localName(o.iri)}
                            {o.sample ? ` （例: ${o.sample}）` : ''}
                          </option>
                        ))}
                      </select>
                      {sel && sample && <span className="xw-map-sample">例: {sample}</span>}
                    </div>
                  )
                })}
              </div>

              <div className="ds-subhead">
                3. 同じ組成とみなす基準（正規化）
                <span className="xw-hint-inline">表記揺れをどこまで同一視するか</span>
              </div>
              <div className="xw-norm-row">
                <select
                  className="xw-map-select xw-norm-select"
                  value={normalizer}
                  onChange={(e) => setNormalizer(e.target.value)}
                >
                  <option value="composition">組成（標準・添字/空白を吸収）</option>
                  <option value="element_canonical">
                    組成・元素順も正規化（Bi2Te3 = Te3Bi2）
                  </option>
                </select>
                <span className="xw-norm-hint">
                  {normalizer === 'element_canonical'
                    ? '元素の並び順が違っても同じ組成として結合します（化学式＝多重集合）。'
                    : '添字・空白の違いだけを吸収します（元素順は区別）。'}
                </span>
              </div>

              <button
                type="button"
                className="promote-btn xw-build-btn"
                disabled={!canBuild}
                onClick={onBuild}
              >
                {building
                  ? '構築中…'
                  : `クロスウォークを構築（${readyCount} データセットを横断）`}
              </button>
              {!canBuild && !building && (
                <p className="hint">
                  述語を指定したデータセットが2つ以上になると構築できます（現在 {readyCount}）。
                </p>
              )}
              {buildErr && <p className="promote-err">構築に失敗しました: {buildErr}</p>}
            </>
          )}

          {result && (
            <div className="xw-result card">
              <div className="xw-result-head">
                <span className="xw-result-icon">
                  <LinkIcon size={18} />
                </span>
                クロスウォークを構築しました
              </div>
              <p className="xw-result-stat">
                <strong>{result.shared_total}</strong> 件の組成が
                <strong> {result.participants_used.length} </strong>
                データセットで共有されています。
              </p>
              <div className="xw-links">
                {Object.entries(result.links.composition ?? {}).map(([label, n]) => (
                  <span key={label} className="xw-link-chip">
                    {label} <span className="mono-strong">{n}</span> リンク
                  </span>
                ))}
              </div>
              {result.participants_skipped.length > 0 && (
                <p className="xw-skip">
                  除外: {result.participants_skipped.map((s) => s.label || s.dataset_id).join('、')}
                  （未昇格などで横断対象外）
                </p>
              )}
              <p className="xw-result-next">
                「カタログ → クロスウォーク」で参加データセットやツール（この組成は何データセットが報告？）を確認できます。
              </p>
            </div>
          )}
        </>
      )}
    </div>
  )
}
