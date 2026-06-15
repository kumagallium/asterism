import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
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

// The CLOSED recipe primitives (mirror asterism.crosswalk.RECIPE_PRIMITIVES). A recipe
// = an ordered list of these; the build applies the vetted functions (the preview
// endpoint is the source of truth for behavior). Plain-language labels are resolved at
// render via t('crosswalk:builder.recipePrimitive.<id>').
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
// + materials pack; mirrors asterism.crosswalk.NORMALIZERS). Maps the select value to
// its i18n hint key (RECIPE_OPTION → 'recipe'); resolved at render via
// t('crosswalk:builder.normHint.<key>').
const NORMALIZER_HINT_KEYS: Record<string, string> = {
  identity: 'identity',
  casefold: 'casefold',
  whitespace: 'whitespace',
  nfkc: 'nfkc',
  loose_text: 'loose_text',
  composition: 'composition',
  element_canonical: 'element_canonical',
  [RECIPE_OPTION]: 'recipe',
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
  const { t } = useTranslation()
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
  const canBuild = !building && readyCount >= 2 && conceptValid && (!recipeMode || recipe.length > 0)

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
          ? t('crosswalk:builder.proposeNote', { count: n })
          : t('crosswalk:builder.proposeNone', { key: conceptKey }),
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
            name: conceptKey,
            class_iri: classIri,
            link_predicate: linkPred,
            // A custom recipe is sent declaratively (the runtime applies the closed
            // primitives); else the named normalizer.
            normalizer: recipeMode ? 'recipe' : normalizer,
            ...(recipeMode ? { normalizer_recipe: recipe } : {}),
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
        <Trans
          i18nKey="crosswalk:builder.subtitle"
          components={[<strong />, <strong />, <strong />, <strong />]}
        />
      </p>

      {loadErr && <pre className="error">{loadErr}</pre>}
      {!datasets && !loadErr && (
        <p className="loading-row">
          <span className="spinner" />
          {t('crosswalk:builder.loading')}
        </p>
      )}

      {datasets && datasets.length < 2 && (
        <div className="state-block">
          <p className="state-title">{t('crosswalk:builder.tooFew.title')}</p>
          <p className="state-sub">{t('crosswalk:builder.tooFew.sub')}</p>
        </div>
      )}

      {datasets && datasets.length >= 2 && (
        <>
          <div className="ds-subhead">{t('crosswalk:builder.step1')}</div>
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
                      {t('crosswalk:builder.dsSub', {
                        predicates: d.predicates.length,
                        facts:
                          d.counts.find((c) => c.key === 'fact')?.value ??
                          t('crosswalk:builder.factsUnknown'),
                      })}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>

          {chosen.length > 0 && (
            <>
              <div className="ds-subhead">
                {t('crosswalk:builder.step2')}
                <span className="xw-hint-inline">{t('crosswalk:builder.step2Hint')}</span>
              </div>
              <div className="xw-norm-row">
                <input
                  type="text"
                  className="xw-key-input xw-norm-select"
                  placeholder={t('crosswalk:builder.conceptPlaceholder')}
                  value={concept}
                  onChange={(e) => onConceptChange(e.target.value)}
                />
                <span className="xw-norm-hint">
                  {conceptValid ? (
                    <Trans
                      i18nKey="crosswalk:builder.conceptVocab"
                      values={{ key: conceptKey, className: pascalCase(conceptKey) }}
                      components={[<code />, <code />]}
                    />
                  ) : (
                    t('crosswalk:builder.conceptInvalid')
                  )}
                </span>
              </div>

              <div className="ds-subhead">
                {t('crosswalk:builder.step3', { key: conceptKey })}
                <span className="xw-hint-inline">{t('crosswalk:builder.step3Hint')}</span>
              </div>

              <div className="xw-ai-row">
                <input
                  type="password"
                  className="xw-key-input"
                  placeholder={t('crosswalk:builder.apiKeyPlaceholder')}
                  value={apiKey}
                  onChange={(e) => onApiKeyChange(e.target.value)}
                />
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={proposing || !apiKey || chosen.length < 1}
                  onClick={onPropose}
                >
                  {proposing ? t('crosswalk:builder.proposing') : t('crosswalk:builder.propose')}
                </button>
              </div>
              {proposeNote && <p className="xw-note">{proposeNote}</p>}
              {proposeErr && (
                <p className="promote-err">
                  {t('crosswalk:builder.proposeErr', { detail: proposeErr })}
                </p>
              )}

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
                        <option value="">{t('crosswalk:builder.selectPredicate')}</option>
                        {opts.map((o) => (
                          <option key={o.iri} value={o.iri}>
                            {localName(o.iri)}
                            {o.sample ? t('crosswalk:builder.predicateSample', { sample: o.sample }) : ''}
                          </option>
                        ))}
                      </select>
                      {sel && sample && (
                        <span className="xw-map-sample">
                          {t('crosswalk:builder.sampleLabel', { sample })}
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>

              <div className="ds-subhead">
                {t('crosswalk:builder.step4')}
                <span className="xw-hint-inline">{t('crosswalk:builder.step4Hint')}</span>
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
                  <optgroup label={t('crosswalk:builder.normGroup.generic')}>
                    <option value="identity">{t('crosswalk:builder.norm.identity')}</option>
                    <option value="casefold">{t('crosswalk:builder.norm.casefold')}</option>
                    <option value="whitespace">{t('crosswalk:builder.norm.whitespace')}</option>
                    <option value="nfkc">{t('crosswalk:builder.norm.nfkc')}</option>
                    <option value="loose_text">{t('crosswalk:builder.norm.loose_text')}</option>
                  </optgroup>
                  <optgroup label={t('crosswalk:builder.normGroup.materials')}>
                    <option value="composition">{t('crosswalk:builder.norm.composition')}</option>
                    <option value="element_canonical">
                      {t('crosswalk:builder.norm.element_canonical')}
                    </option>
                  </optgroup>
                  <optgroup label={t('crosswalk:builder.normGroup.custom')}>
                    <option value={RECIPE_OPTION}>{t('crosswalk:builder.norm.recipe')}</option>
                  </optgroup>
                </select>
                <span className="xw-norm-hint">
                  {NORMALIZER_HINT_KEYS[normalizer]
                    ? t(`crosswalk:builder.normHint.${NORMALIZER_HINT_KEYS[normalizer]}`)
                    : ''}
                </span>
              </div>

              {recipeMode && (
                <div className="xw-recipe">
                  {/* The ordered recipe — closed primitives applied top→bottom. */}
                  <div className="xw-recipe-steps">
                    {recipe.length === 0 && (
                      <p className="xw-norm-hint">{t('crosswalk:builder.recipeEmpty')}</p>
                    )}
                    {recipe.map((op, i) => (
                      <div className="xw-recipe-step" key={`${op}-${i}`}>
                        <span className="xw-recipe-num">{i + 1}</span>
                        <span className="xw-recipe-op">
                          {RECIPE_PRIMITIVE_IDS.includes(op)
                            ? t(`crosswalk:builder.recipePrimitive.${op}`)
                            : op}
                        </span>
                        <span className="xw-recipe-actions">
                          <button
                            type="button"
                            className="xw-recipe-btn"
                            disabled={i === 0}
                            title={t('crosswalk:builder.recipeUp')}
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
                            title={t('crosswalk:builder.recipeDown')}
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
                            title={t('crosswalk:builder.recipeDelete')}
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
                      <option value="">{t('crosswalk:builder.recipeAdd')}</option>
                      {RECIPE_PRIMITIVE_IDS.map((id) => (
                        <option key={id} value={id}>
                          {t(`crosswalk:builder.recipePrimitive.${id}`)}
                        </option>
                      ))}
                    </select>
                  </div>
                  {/* Live preview: what join key this recipe produces for a sample. */}
                  <div className="xw-recipe-preview">
                    <span className="xw-recipe-prev-label">{t('crosswalk:builder.recipePreviewLabel')}</span>
                    <input
                      type="text"
                      className="xw-key-input xw-recipe-sample"
                      value={recipeSample}
                      onChange={(e) => setRecipeSample(e.target.value)}
                      placeholder={t('crosswalk:builder.recipeSamplePlaceholder')}
                    />
                    <span className="xw-recipe-arrow">→</span>
                    <code className="xw-recipe-out">{recipePreview ?? '—'}</code>
                  </div>
                </div>
              )}

              <div className="ds-subhead">
                {t('crosswalk:builder.step5')}
                <span className="xw-hint-inline">{t('crosswalk:builder.step5Hint')}</span>
              </div>
              <div className="xw-norm-row">
                <input
                  type="text"
                  className="xw-key-input xw-norm-select"
                  placeholder={t('crosswalk:builder.perspectivePlaceholder')}
                  value={perspectiveName}
                  onChange={(e) => setPerspectiveName(e.target.value)}
                />
                <span className="xw-norm-hint">
                  {perspectiveName.trim()
                    ? t('crosswalk:builder.perspectiveNamed')
                    : t('crosswalk:builder.perspectiveDefault')}
                </span>
              </div>

              <button
                type="button"
                className="promote-btn xw-build-btn"
                disabled={!canBuild}
                onClick={onBuild}
              >
                {building
                  ? t('crosswalk:builder.building')
                  : t('crosswalk:builder.build', { count: readyCount })}
              </button>
              {!canBuild && !building && (
                <p className="hint">
                  {!conceptValid
                    ? t('crosswalk:builder.buildHintInvalid')
                    : t('crosswalk:builder.buildHintFewer', { count: readyCount })}
                </p>
              )}
              {buildErr && (
                <p className="promote-err">{t('crosswalk:builder.buildErr', { detail: buildErr })}</p>
              )}
            </>
          )}

          {result && (
            <div className="xw-result card">
              <div className="xw-result-head">
                <span className="xw-result-icon">
                  <LinkIcon size={18} />
                </span>
                {t('crosswalk:builder.result.head')}
              </div>
              <p className="xw-result-stat">
                <Trans
                  i18nKey="crosswalk:builder.result.stat"
                  values={{
                    shared: result.shared_total,
                    key: conceptKey,
                    count: result.participants_used.length,
                  }}
                  components={[<strong />, <strong />]}
                />
              </p>
              <div className="xw-links">
                {Object.entries(result.links[conceptKey] ?? {}).map(([label, n]) => (
                  <span key={label} className="xw-link-chip">
                    <Trans
                      i18nKey="crosswalk:builder.result.linkChip"
                      values={{ label, n }}
                      components={[<span className="mono-strong" />]}
                    />
                  </span>
                ))}
              </div>
              {result.participants_skipped.length > 0 && (
                <p className="xw-skip">
                  {t('crosswalk:builder.result.skipped', {
                    labels: result.participants_skipped
                      .map((s) => s.label || s.dataset_id)
                      .join('、'),
                  })}
                </p>
              )}
              <p className="xw-result-next">{t('crosswalk:builder.result.next')}</p>
            </div>
          )}
        </>
      )}
    </div>
  )
}
