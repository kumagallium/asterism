import { useEffect, useState } from 'react'
import {
  buildCrosswalk,
  buildPerspective,
  type BuildResult,
  type CrosswalkConfig,
  type PredicateCandidate,
  previewNormalizer,
  proposeCrosswalkMapping,
} from './crosswalkApi'
import { type CatalogDataset, getCatalogDatasets } from './galleryApi'
import { LinkIcon } from './icons'
import { localName } from './vocab'

// The CLOSED recipe primitives (mirror asterism.crosswalk.RECIPE_PRIMITIVES) + a
// plain-language label. A recipe = an ordered list of these; the build applies the
// vetted functions (the preview endpoint is the source of truth for behavior).
const RECIPE_PRIMITIVE_LABEL: Record<string, string> = {
  nfkc: '全角・半角／互換文字をそろえる（NFKC）',
  casefold: '大文字・小文字をなくす',
  strip: '前後の空白を削る',
  collapse_ws: '連続する空白を1つにする',
  remove_ws: '空白をすべて消す',
  fold_subscripts: '下付き数字をふつうの数字に（₂→2）',
}
const RECIPE_PRIMITIVE_IDS = ['nfkc', 'casefold', 'strip', 'collapse_ws', 'remove_ws', 'fold_subscripts']
// Sentinel select value: author a custom recipe instead of a named normalizer.
const RECIPE_OPTION = '__recipe__'

const API_KEY_STORAGE = 'asterism.apiKey'

// The crosswalk hub vocabulary namespace (matches the runtime's `XW`). A concept's
// class + link predicate are minted under here so each concept gets its own term
// (xw:Composition / xw:hasComposition, xw:CrystalSystem / xw:hasCrystalSystem, …) —
// the hub is generic, not composition-only (crosswalk-multi-perspective.md).
const XW_NS = 'https://kumagallium.github.io/asterism/crosswalk/ontology#'

/** PascalCase an ascii concept key for an IRI localname ("crystal_system" →
 * "CrystalSystem"). Returns '' when the key has no ascii alnum (e.g. pure Japanese),
 * so the caller can require an ascii key and keep the minted IRI clean + citable. */
function pascalCase(key: string): string {
  return key
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join('')
}

/** The hub class IRI minted for a concept key (xw:<PascalCase>). */
function classIriForConcept(key: string): string {
  const p = pascalCase(key)
  return p ? `${XW_NS}${p}` : ''
}

/** The hub link-predicate IRI minted for a concept key (xw:has<PascalCase>). */
function linkPredicateForConcept(key: string): string {
  const p = pascalCase(key)
  return p ? `${XW_NS}has${p}` : ''
}

// One-line explanation per normalizer (the closed, vetted join-key set — generic core
// + materials pack; mirrors asterism.crosswalk.NORMALIZERS).
const NORMALIZER_HINTS: Record<string, string> = {
  identity: '値が完全に一致するものだけを結合します（どの概念でも使える既定）。',
  casefold: '大文字・小文字の違いだけを無視します（例: FeO = feo）。組成には不可（Co ≠ CO）。',
  whitespace: '前後・連続する空白の違いだけを無視します。',
  nfkc: '全角・半角や互換文字（ﾊﾝｶｸ等）を揃えてから一致を見ます。',
  loose_text: '大小・空白・全角半角をまとめて無視してゆるく一致（並び替えはしません）。',
  composition: '添字・空白の違いだけを吸収します（元素順は区別）。',
  element_canonical: '元素の並び順が違っても同じ組成として結合します（化学式＝多重集合）。',
  [RECIPE_OPTION]: '手順（プリミティブ）を自分で並べて、独自の「同じ値とみなす基準」を作ります。',
}

/** A perspective id (slug) from a human name. Falls back to a generated id when the
 * name has no ascii (e.g. a Japanese name) so the id stays IRI-safe. */
function perspectiveIdFromName(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return slug || `p-${Date.now().toString(36)}`
}

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
  // The concept these datasets share (the lens's join target). An ascii key minted
  // into the hub vocabulary (xw:<PascalCase>); 'composition' is just the default, not
  // a lock — '結晶系' would be 'crystal_system' → xw:CrystalSystem, '著者' → 'author', …
  const [concept, setConcept] = useState('composition')
  // The join key normalizer. 'identity' = exact match (the GENERIC default for any
  // concept); 'composition'/'element_canonical' are materials-chemistry options.
  const [normalizer, setNormalizer] = useState('composition')
  // Whether the user picked the normalizer by hand. Until then it tracks the concept
  // (composition → 'composition', anything else → generic 'identity') so a non-material
  // concept doesn't silently keep a chemistry join key.
  const [normalizerTouched, setNormalizerTouched] = useState(false)
  // A human name for THIS perspective (a distinct lens, multi-perspective ADR). Empty
  // = the default "composition" perspective (back-compat); named = a new lens.
  const [perspectiveName, setPerspectiveName] = useState('')
  const [proposing, setProposing] = useState(false)
  const [proposeErr, setProposeErr] = useState('')
  const [proposeNote, setProposeNote] = useState('')
  const [building, setBuilding] = useState(false)
  const [buildErr, setBuildErr] = useState('')
  const [result, setResult] = useState<BuildResult | null>(null)
  // Custom normalizer recipe (active when normalizer === RECIPE_OPTION): an ordered
  // list of closed primitive ids + a sample whose join key is previewed live.
  const [recipe, setRecipe] = useState<string[]>(['nfkc', 'casefold', 'collapse_ws'])
  const [recipeSample, setRecipeSample] = useState('Iron  Oxide')
  const [recipePreview, setRecipePreview] = useState<string | null>(null)
  // Compound key (crosswalk-compound-keys.md): EXTRA match conditions ANDed with the
  // primary concept. Empty = a single-value join (legacy, byte-identical). Each extra
  // part has its own name + normalizer + per-dataset predicate; >= 1 makes the build a
  // compound (tuple) key.
  const [extraParts, setExtraParts] = useState<{ id: string; name: string; normalizer: string }[]>(
    [],
  )
  // extra-part id -> dataset_id -> predicate IRI
  const [extraPred, setExtraPred] = useState<Record<string, Record<string, string>>>({})

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
  const conceptKey = concept.trim() || 'composition'
  const classIri = classIriForConcept(conceptKey)
  const linkPred = linkPredicateForConcept(conceptKey)
  // A concept needs an ascii key so the minted hub IRI stays clean + citable.
  const conceptValid = classIri !== ''
  const recipeMode = normalizer === RECIPE_OPTION
  const readyDatasets = chosen.filter((d) => predicate[datasetId(d)])
  const compound = extraParts.length > 0
  // Every extra condition needs a distinct non-empty name (≠ the concept) and a
  // predicate for every participating dataset.
  const extraNames = extraParts.map((ep) => ep.name.trim())
  const extraComplete =
    extraNames.every((n) => n && n !== conceptKey) &&
    new Set(extraNames).size === extraNames.length &&
    extraParts.every((ep) => readyDatasets.every((d) => extraPred[ep.id]?.[datasetId(d)]))
  const canBuild =
    !building &&
    readyCount >= 2 &&
    conceptValid &&
    (!recipeMode || recipe.length > 0) &&
    (!compound || extraComplete)

  // Live-preview the recipe's join key on the sample (the preview endpoint is the
  // source of truth for behavior, so the UI never re-implements the primitives).
  useEffect(() => {
    let off = false
    const p =
      recipeMode && recipe.length > 0
        ? previewNormalizer(recipe, [recipeSample]).then((r) => r[0]?.output ?? '')
        : Promise.resolve(null)
    p.then((out) => !off && setRecipePreview(out)).catch(() => !off && setRecipePreview(null))
    return () => {
      off = true
    }
  }, [recipeMode, recipe, recipeSample])

  // Until the user picks a normalizer by hand, default it from the concept: chemistry
  // join key for composition, generic exact-match otherwise.
  function onConceptChange(v: string) {
    setConcept(v)
    if (!normalizerTouched) {
      setNormalizer(/^composition$/i.test(v.trim()) ? 'composition' : 'identity')
    }
  }

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
      const r = await proposeCrosswalkMapping(ids, conceptKey, apiKey)
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
          : `AI は「${conceptKey}」を担う述語を見つけられませんでした。手動で選んでください。`,
      )
    } catch (e) {
      setProposeErr(e instanceof Error ? e.message : String(e))
    } finally {
      setProposing(false)
    }
  }

  function addPart() {
    setExtraParts((p) => [
      ...p,
      { id: `p${Date.now().toString(36)}${p.length}`, name: '', normalizer: 'identity' },
    ])
  }
  function removePart(id: string) {
    setExtraParts((p) => p.filter((x) => x.id !== id))
  }
  function patchPart(id: string, patch: Partial<{ name: string; normalizer: string }>) {
    setExtraParts((p) => p.map((x) => (x.id === id ? { ...x, ...patch } : x)))
  }
  function setPartPred(id: string, dsid: string, pred: string) {
    setExtraPred((prev) => ({ ...prev, [id]: { ...(prev[id] ?? {}), [dsid]: pred } }))
  }

  async function onBuild() {
    setBuilding(true)
    setBuildErr('')
    setResult(null)
    try {
      // The primary part = the concept's own normalizer (named or recipe). Extra parts
      // (compound) carry their own normalizer + per-dataset predicate.
      const primaryNorm = recipeMode ? 'recipe' : normalizer
      const concept0 = compound
        ? {
            name: conceptKey,
            class_iri: classIri,
            link_predicate: linkPred,
            key_parts: [
              {
                name: conceptKey,
                normalizer: primaryNorm,
                ...(recipeMode ? { normalizer_recipe: recipe } : {}),
              },
              ...extraParts.map((ep) => ({ name: ep.name.trim(), normalizer: ep.normalizer })),
            ],
            participants: readyDatasets.map((d) => ({
              dataset_id: datasetId(d),
              label: labelFor(d),
              predicates: {
                [conceptKey]: predicate[datasetId(d)],
                ...Object.fromEntries(
                  extraParts.map((ep) => [ep.name.trim(), extraPred[ep.id]?.[datasetId(d)] ?? '']),
                ),
              },
            })),
          }
        : {
            // Single value (legacy, byte-identical): a custom recipe is sent
            // declaratively; else the named normalizer.
            name: conceptKey,
            class_iri: classIri,
            link_predicate: linkPred,
            normalizer: primaryNorm,
            ...(recipeMode ? { normalizer_recipe: recipe } : {}),
            participants: readyDatasets.map((d) => ({
              dataset_id: datasetId(d),
              label: labelFor(d),
              predicate: predicate[datasetId(d)],
            })),
          }
      const config: CrosswalkConfig = { min_datasets: 2, concepts: [concept0] }
      // A named perspective = a distinct lens (its own graph); empty name = the
      // default composition perspective (back-compat).
      const trimmed = perspectiveName.trim()
      setResult(
        trimmed
          ? await buildPerspective(perspectiveIdFromName(trimmed), config, trimmed)
          : await buildCrosswalk(config),
      )
    } catch (e) {
      setBuildErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBuilding(false)
    }
  }

  return (
    <div className="xw-builder">
      <p className="subtitle">
        既存の<strong>データセットを2つ以上選び</strong>、<strong>つなぐ概念</strong>（組成・結晶系・著者…）と
        それを表す列を指定すると、共通の値で<strong>横断してつながる橋（クロスウォーク）</strong>を作れます。
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
                2. つなぐ「概念」を決める
                <span className="xw-hint-inline">例: composition / crystal_system / author（英数字のキー）</span>
              </div>
              <div className="xw-norm-row">
                <input
                  type="text"
                  className="xw-key-input xw-norm-select"
                  placeholder="composition"
                  value={concept}
                  onChange={(e) => onConceptChange(e.target.value)}
                />
                <span className="xw-norm-hint">
                  {conceptValid ? (
                    <>
                      「{conceptKey}」から自動で作る語彙 → クラス{' '}
                      <code>xw:{pascalCase(conceptKey)}</code> ・ つなぐ述語{' '}
                      <code>xw:has{pascalCase(conceptKey)}</code>
                    </>
                  ) : (
                    '英数字のキーを入力してください（例: crystal_system）。日本語の呼び名は「この視点の名前」へ。'
                  )}
                </span>
              </div>

              <div className="ds-subhead">
                3. 各データセットで「{conceptKey} を表す述語」を指定する
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
                4. 同じ値とみなす基準（正規化）
                <span className="xw-hint-inline">表記揺れをどこまで同一視するか</span>
              </div>
              <div className="xw-norm-row">
                <select
                  className="xw-map-select xw-norm-select"
                  value={normalizer}
                  onChange={(e) => {
                    setNormalizerTouched(true)
                    setNormalizer(e.target.value)
                  }}
                >
                  <optgroup label="汎用（どの概念でも）">
                    <option value="identity">そのまま一致（表記が同じものだけ）</option>
                    <option value="casefold">大文字・小文字を無視</option>
                    <option value="whitespace">空白の違いを無視（前後・連続空白を畳む）</option>
                    <option value="nfkc">全角・半角／互換文字を揃える（NFKC）</option>
                    <option value="loose_text">ゆるく一致（大小・空白・全角半角をまとめて無視）</option>
                  </optgroup>
                  <optgroup label="材料向け">
                    <option value="composition">組成式として揃える（添字/空白を吸収）</option>
                    <option value="element_canonical">組成式＋元素順も揃える（Bi2Te3 = Te3Bi2）</option>
                  </optgroup>
                  <optgroup label="自分で作る">
                    <option value={RECIPE_OPTION}>カスタム（手順を組む）…</option>
                  </optgroup>
                </select>
                <span className="xw-norm-hint">{NORMALIZER_HINTS[normalizer] ?? ''}</span>
              </div>

              {recipeMode && (
                <div className="xw-recipe">
                  {/* The ordered recipe — closed primitives applied top→bottom. */}
                  <div className="xw-recipe-steps">
                    {recipe.length === 0 && (
                      <p className="xw-norm-hint">下の「手順を追加」から組み立ててください。</p>
                    )}
                    {recipe.map((op, i) => (
                      <div className="xw-recipe-step" key={`${op}-${i}`}>
                        <span className="xw-recipe-num">{i + 1}</span>
                        <span className="xw-recipe-op">{RECIPE_PRIMITIVE_LABEL[op] ?? op}</span>
                        <span className="xw-recipe-actions">
                          <button
                            type="button"
                            className="xw-recipe-btn"
                            disabled={i === 0}
                            title="上へ"
                            onClick={() =>
                              setRecipe((r) => {
                                const n = [...r]
                                ;[n[i - 1], n[i]] = [n[i], n[i - 1]]
                                return n
                              })
                            }
                          >
                            ↑
                          </button>
                          <button
                            type="button"
                            className="xw-recipe-btn"
                            disabled={i === recipe.length - 1}
                            title="下へ"
                            onClick={() =>
                              setRecipe((r) => {
                                const n = [...r]
                                ;[n[i + 1], n[i]] = [n[i], n[i + 1]]
                                return n
                              })
                            }
                          >
                            ↓
                          </button>
                          <button
                            type="button"
                            className="xw-recipe-btn xw-recipe-del"
                            title="削除"
                            onClick={() => setRecipe((r) => r.filter((_, j) => j !== i))}
                          >
                            ×
                          </button>
                        </span>
                      </div>
                    ))}
                  </div>
                  <div className="xw-norm-row">
                    <select
                      className="xw-map-select xw-norm-select"
                      value=""
                      onChange={(e) => {
                        if (e.target.value) setRecipe((r) => [...r, e.target.value])
                      }}
                    >
                      <option value="">＋ 手順を追加…</option>
                      {RECIPE_PRIMITIVE_IDS.map((id) => (
                        <option key={id} value={id}>
                          {RECIPE_PRIMITIVE_LABEL[id]}
                        </option>
                      ))}
                    </select>
                  </div>
                  {/* Live preview: what join key this recipe produces for a sample. */}
                  <div className="xw-recipe-preview">
                    <span className="xw-recipe-prev-label">プレビュー</span>
                    <input
                      type="text"
                      className="xw-key-input xw-recipe-sample"
                      value={recipeSample}
                      onChange={(e) => setRecipeSample(e.target.value)}
                      placeholder="サンプルの値"
                    />
                    <span className="xw-recipe-arrow">→</span>
                    <code className="xw-recipe-out">{recipePreview ?? '—'}</code>
                  </div>
                </div>
              )}

              <div className="ds-subhead">
                追加の一致条件（すべて一致 = AND）
                <span className="xw-hint-inline">
                  例: 組成 <strong>かつ</strong> 結晶系。空なら1つの値だけで一致します
                </span>
              </div>
              <div className="xw-conds">
                {extraParts.map((ep) => (
                  <div className="xw-cond-card" key={ep.id}>
                    <div className="xw-cond-head">
                      <input
                        type="text"
                        className="xw-key-input xw-cond-name"
                        placeholder="条件名（英数字・例: crystal_system）"
                        value={ep.name}
                        onChange={(e) => patchPart(ep.id, { name: e.target.value })}
                      />
                      <select
                        className="xw-map-select xw-cond-norm"
                        value={ep.normalizer}
                        onChange={(e) => patchPart(ep.id, { normalizer: e.target.value })}
                      >
                        <optgroup label="汎用（どの概念でも）">
                          <option value="identity">そのまま一致</option>
                          <option value="casefold">大文字・小文字を無視</option>
                          <option value="whitespace">空白の違いを無視</option>
                          <option value="nfkc">全角・半角をそろえる（NFKC）</option>
                          <option value="loose_text">ゆるく一致</option>
                        </optgroup>
                        <optgroup label="材料向け">
                          <option value="composition">組成式として揃える</option>
                          <option value="element_canonical">組成式＋元素順</option>
                        </optgroup>
                      </select>
                      <button
                        type="button"
                        className="xw-recipe-btn xw-recipe-del"
                        title="この条件を削除"
                        onClick={() => removePart(ep.id)}
                      >
                        ×
                      </button>
                    </div>
                    <div className="xw-map-list">
                      {readyDatasets.map((d) => {
                        const id = datasetId(d)
                        const opts = optionsFor(d)
                        return (
                          <div className="xw-map-row" key={id}>
                            <span className="xw-map-ds">{d.name}</span>
                            <select
                              className="xw-map-select"
                              value={extraPred[ep.id]?.[id] ?? ''}
                              onChange={(e) => setPartPred(ep.id, id, e.target.value)}
                            >
                              <option value="">— 述語を選択 —</option>
                              {opts.map((o) => (
                                <option key={o.iri} value={o.iri}>
                                  {localName(o.iri)}
                                </option>
                              ))}
                            </select>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ))}
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={addPart}
                  disabled={chosen.length < 1}
                >
                  ＋ 一致条件を追加
                </button>
                {compound && !extraComplete && (
                  <p className="xw-norm-hint">
                    各条件に名前（重複なし・概念名と別）と、各データセットの述語を指定してください。
                  </p>
                )}
              </div>

              <div className="ds-subhead">
                5. この視点の名前
                <span className="xw-hint-inline">
                  複数の「視点（つなぎ方）」を区別して持てます
                </span>
              </div>
              <div className="xw-norm-row">
                <input
                  type="text"
                  className="xw-key-input xw-norm-select"
                  placeholder="例: 組成で繋ぐ（空欄なら標準の「組成」視点に上書き）"
                  value={perspectiveName}
                  onChange={(e) => setPerspectiveName(e.target.value)}
                />
                <span className="xw-norm-hint">
                  {perspectiveName.trim()
                    ? '新しい独立した視点として作成します（別グラフ・カタログに並びます）。'
                    : '空欄のときは標準の「組成」視点を作成/更新します。'}
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
                  {!conceptValid
                    ? '概念を英数字のキーで入力してください（例: crystal_system）。'
                    : `述語を指定したデータセットが2つ以上になると構築できます（現在 ${readyCount}）。`}
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
                <strong>{result.shared_total}</strong> 件の「{conceptKey}」が
                <strong> {result.participants_used.length} </strong>
                データセットで共有されています。
              </p>
              <div className="xw-links">
                {Object.entries(result.links[conceptKey] ?? {}).map(([label, n]) => (
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
                「カタログ → クロスウォーク」で参加データセットやツール（この値は何データセットが報告？）を確認できます。
              </p>
            </div>
          )}
        </>
      )}
    </div>
  )
}
