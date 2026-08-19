import type { DatasetNamespaceInfo, MappingSkeleton } from './api'

/** Deterministic dataset-namespace naming (kantan ADR K13) — the UI twin of
 * step0's `instance_iri.py` (`slugify_dataset_name` / `derive_prefix_pair` /
 * `normalize_dataset_namespace`). Keep the rules byte-compatible: the gate
 * renames locally for instant feedback, then the server re-annotates the
 * edited skeleton with the same functions.
 *
 * A CURIE prefix ("al3v:") is pure notation — it never appears in stored
 * data, so it is nobody's judgment call: it derives from the dataset slug,
 * the ONE naming decision that persists (inside the minted IRI). */

/** Prefix names a derived pair must never shadow: RDF builtins plus the
 * standard vocabularies the prompts invite reuse of (superset of vocab.ts
 * KNOWN_VOCABS) plus names the instruction examples use. Mirror of
 * `_RESERVED_PREFIX_NAMES` in step0/instance_iri.py. */
const RESERVED_PREFIX_NAMES = new Set([
  'xsd', 'rdf', 'rdfs', 'owl', 'sh', 'fn',
  'schema', 'prov', 'dcterms', 'dc', 'bibo', 'skos', 'foaf', 'dcat', 'sosa',
  'qudt', 'unit', 'emmo', 'cmso',
  'doco', 'deo', 'fabio', 'cito', 'po', 'sd', 'sdr', 'ast',
])

/** Standard vocabulary IRIs the machine may declare on the human's behalf when
 * the AI used one of them without declaring it (kantan ADR K4/K13: a missing
 * declaration is a machine-knowable fact, not a judgment). Mirror of ingest's
 * `STANDARD_PREFIXES`, the curated `vocab.ts` KNOWN_VOCABS namespaces and the
 * document layer's SPAR set (`ingest/asterism/documents.py`) — keep the four
 * in sync. Every name {@link RESERVED_PREFIX_NAMES} reserves and whose real
 * IRI this repo states belongs here: what is missing gets folded into a
 * dataset-local word instead, which quietly drops the standard term's meaning.
 * A prefix NOT in this table is never guessed at either way — an invented IRI
 * would silently change what the data means. */
export const STANDARD_VOCAB_IRIS: Record<string, string> = {
  rdf: 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
  rdfs: 'http://www.w3.org/2000/01/rdf-schema#',
  owl: 'http://www.w3.org/2002/07/owl#',
  xsd: 'http://www.w3.org/2001/XMLSchema#',
  sh: 'http://www.w3.org/ns/shacl#',
  schema: 'https://schema.org/',
  dcterms: 'http://purl.org/dc/terms/',
  dc: 'http://purl.org/dc/elements/1.1/',
  prov: 'http://www.w3.org/ns/prov#',
  bibo: 'http://purl.org/ontology/bibo/',
  skos: 'http://www.w3.org/2004/02/skos/core#',
  foaf: 'http://xmlns.com/foaf/0.1/',
  dcat: 'http://www.w3.org/ns/dcat#',
  sosa: 'http://www.w3.org/ns/sosa/',
  qudt: 'http://qudt.org/schema/qudt/',
  unit: 'http://qudt.org/vocab/unit/',
  emmo: 'https://w3id.org/emmo#',
  cmso: 'http://purls.helmholtz-metadaten.de/cmso/',
  fabio: 'http://purl.org/spar/fabio/',
  doco: 'http://purl.org/spar/doco/',
  deo: 'http://purl.org/spar/deo/',
  po: 'http://www.essepuntato.it/2008/12/pattern#',
}

/** The leading `prefix:` of a CURIE-ish value (`schema:Dataset`, `zemr:m/{a}`). */
const CURIE_HEAD = /^([A-Za-z][\w.-]*):/

function renameHead(value: string, from: string, to: string): string {
  return value.startsWith(`${from}:`) ? `${to}:${value.slice(from.length + 1)}` : value
}

/**
 * Deterministic repair for prefixes the skeleton USES but never DECLARES — the
 * failure a weak model reliably produces (`schema:Dataset` with no `schema`
 * declaration), which the server evidence reports as "this will error on save"
 * while the kantan tier has no prefix table to fix it in.
 *
 * Two moves, both machine-knowable:
 *  - a name in {@link STANDARD_VOCAB_IRIS} is DECLARED with its real IRI;
 *  - any other name is folded onto this dataset's own minted prefix pair
 *    (`Foo:Bar` → `<ontology>:Bar`), i.e. treated as a new word of this
 *    dataset. An IRI is never invented for an unknown prefix.
 *
 * Returns null when nothing can (or needs to) change; the caller reports what
 * happened — a silent meaning change would be worse than the warning.
 */
export function resolveUndeclaredPrefixes(
  skeleton: MappingSkeleton,
  undeclared: Iterable<string>,
  info: Pick<
    DatasetNamespaceInfo,
    'ontology_prefix' | 'resource_prefix' | 'slug' | 'base'
  > | null,
): {
  skeleton: MappingSkeleton
  declared: string[]
  /** The subset of `declared` that is this dataset's OWN minted pair, not a
   *  standard vocabulary — a different sentence for the reader. */
  declaredOwn: string[]
  renamed: string[]
} | null {
  const declaredAlready = skeleton.prefixes ?? {}
  const names = [...new Set(undeclared)].filter((n) => n && !(n in declaredAlready)).sort()
  if (names.length === 0) return null

  const onto = info?.ontology_prefix ?? null
  const res = info?.resource_prefix ?? null
  // This dataset's OWN missing half. `xrd:` declared and `xrdr:` used but not
  // declared is the same one minted pair with one line missing — the machine
  // derived both names from the slug, so the absent one has a known IRI and is
  // simply written down. Folding it onto its twin instead (the unknown-word
  // path below) would move every resource IRI into the ontology namespace,
  // which is a different design, not a repair (live 2026-08-19: 「xrdr は AI が
  // 勝手に作った prefix？」— it was not; it was this dataset's own).
  const own: Record<string, string> = {}
  if (info?.slug && info.base) {
    const [dOnto, dRes] = derivePrefixPair(info.slug)
    const mint: Record<string, string> = {
      [onto ?? dOnto]: `${info.base}/datasets/${info.slug}/ontology#`,
      [res ?? dRes]: `${info.base}/datasets/${info.slug}/resource/`,
    }
    for (const name of names) if (name in mint) own[name] = mint[name]
  }
  const declared = names.filter((n) => n in STANDARD_VOCAB_IRIS || n in own)
  const unknown = names.filter((n) => !(n in STANDARD_VOCAB_IRIS) && !(n in own))
  // Without a minted pair there is nothing to fold unknown names onto.
  const renamed = onto || res ? unknown : []
  if (declared.length === 0 && renamed.length === 0) return null

  const prefixes: Record<string, string> = { ...declaredAlready }
  for (const name of declared) prefixes[name] = own[name] ?? STANDARD_VOCAB_IRIS[name]

  const fold = (value: string, target: string | null): string => {
    if (!target || !CURIE_HEAD.test(value) || value.startsWith('http://') || value.startsWith('https://')) {
      return value
    }
    let out = value
    for (const name of renamed) out = renameHead(out, name, target)
    return out
  }

  const maps = skeleton.maps.map((m) => ({
    ...m,
    subject: {
      ...m.subject,
      ...(m.subject.template !== undefined ? { template: fold(m.subject.template, res ?? onto) } : {}),
      ...(m.subject.constant !== undefined ? { constant: fold(m.subject.constant, res ?? onto) } : {}),
      ...(m.subject.classes
        ? { classes: m.subject.classes.map((c) => fold(c, onto ?? res)) }
        : {}),
    },
  }))

  const next = { ...skeleton, prefixes, maps }
  if (JSON.stringify(next) === JSON.stringify(skeleton)) return null
  return { skeleton: next, declared, declaredOwn: Object.keys(own).sort(), renamed }
}

/** Kebab slug for the minted-IRI dataset segment (same cleaning rule as the
 * server; non-ASCII — e.g. a Japanese name — cleans away, and the caller
 * treats an empty result as "keep the current slug"). */
export function slugifyDatasetName(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

/** The (ontology, resource) CURIE prefix names derived from `slug` — first
 * slug token, extended token-by-token on collision with reserved names or
 * `taken`, `ds`/`ds2`/… as the last resort; resource = ontology + 'r'.
 * NCName-safe (digit-leading candidates get a 'd' head). */
export function derivePrefixPair(slug: string, taken: Iterable<string> = []): [string, string] {
  const tokens = slug.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean)
  const candidates: string[] = []
  if (tokens.length > 0) {
    candidates.push(tokens[0])
    if (tokens.length > 1) candidates.push(tokens[0] + tokens[1])
    candidates.push(tokens.join(''))
  }
  candidates.push('ds')
  const bad = new Set([...RESERVED_PREFIX_NAMES, ...taken])
  for (let i = 0; ; i++) {
    const cand = i < candidates.length ? candidates[i] : `ds${i - candidates.length + 2}`
    const name = /^[0-9]/.test(cand) ? `d${cand}` : cand
    if (!bad.has(name) && !bad.has(`${name}r`)) return [name, `${name}r`]
  }
}

/** The minted pair as detected straight from the skeleton (no server round
 * trip — the gate shows the post-rename state instantly; `base_configured`
 * stays the server annotation's call). Mirror of the detection half of
 * `dataset_namespace_info`. */
export function detectDatasetNamespace(
  skeleton: MappingSkeleton,
): Omit<DatasetNamespaceInfo, 'base_configured'> | null {
  let slug: string | null = null
  let base: string | null = null
  let onto: string | null = null
  let res: string | null = null
  for (const [name, iri] of Object.entries(skeleton.prefixes ?? {})) {
    let m = /^(.*)\/datasets\/([^/#?]+)\/ontology#$/.exec(iri)
    if (m && onto === null) {
      onto = name
      base = base ?? m[1]
      slug = slug ?? m[2]
      continue
    }
    m = /^(.*)\/datasets\/([^/#?]+)\/resource\/$/.exec(iri)
    if (m && res === null) {
      res = name
      base = base ?? m[1]
      slug = slug ?? m[2]
    }
  }
  if (slug === null || base === null) return null
  return { slug, base, ontology_prefix: onto, resource_prefix: res }
}

/** Display-layer folding of the MINTED prefixes on the kantan tier (K4/K13):
 * the skeleton STATE always keeps full CURIEs — these four only translate at
 * the input boundary, so evidence/validate/continue see the same values as
 * the detail tier. Reused standard vocabularies (schema: …) stay visible:
 * only this dataset's own shorthand is notation-noise; a standard term is
 * information. */

/** `zemr:measurement/{…}` → `measurement/{…}` (only the minted resource
 * prefix folds; anything else — schema:, http…, plain — passes through). */
export function compactTemplate(
  value: string,
  info: Pick<DatasetNamespaceInfo, 'resource_prefix'> | null,
): string {
  const p = info?.resource_prefix
  return p && value.startsWith(`${p}:`) ? value.slice(p.length + 1) : value
}

/** Inverse of {@link compactTemplate} for edited input: a value whose head
 * (before any `{column}` placeholder) carries no prefix/scheme gets the
 * minted resource prefix back. Prefixed/absolute forms pass through, so the
 * detail-tier notation still works when typed here. */
export function expandTemplate(
  value: string,
  info: Pick<DatasetNamespaceInfo, 'resource_prefix'> | null,
): string {
  const p = info?.resource_prefix
  if (!p || value === '') return value
  const head = value.split('{')[0]
  return head.includes(':') ? value : `${p}:${value}`
}

/** `zem:Measurement` → `Measurement`; standard vocabularies stay as-is. */
export function compactClass(
  value: string,
  info: Pick<DatasetNamespaceInfo, 'ontology_prefix'> | null,
): string {
  const p = info?.ontology_prefix
  return p && value.startsWith(`${p}:`) ? value.slice(p.length + 1) : value
}

/** Inverse of {@link compactClass}: a bare name gets the minted ontology
 * prefix; `schema:Person` and friends pass through. */
export function expandClass(
  value: string,
  info: Pick<DatasetNamespaceInfo, 'ontology_prefix'> | null,
): string {
  const p = info?.ontology_prefix
  return p && value !== '' && !value.includes(':') ? `${p}:${value}` : value
}

/** Rename the skeleton's minted namespace to `rawName` (a human-friendly
 * dataset name): slug, IRI pair (under the SERVER's base — base fixes belong
 * to Settings), derived prefix pair, and every CURIE reference in the maps,
 * all in lockstep. Returns null when the name slugifies to nothing (keep the
 * current skeleton) or is a no-op. */
export function renameDatasetNamespace(
  skeleton: MappingSkeleton,
  info: Omit<DatasetNamespaceInfo, 'base_configured'>,
  rawName: string,
): MappingSkeleton | null {
  const slug = slugifyDatasetName(rawName)
  if (!slug) return null

  const oldNames = new Set(
    [info.ontology_prefix, info.resource_prefix].filter((n): n is string => n !== null),
  )
  const taken = Object.keys(skeleton.prefixes ?? {}).filter((n) => !oldNames.has(n))
  const [onto, res] = derivePrefixPair(slug, taken)
  const ontoIri = `${info.base}/datasets/${slug}/ontology#`
  const resIri = `${info.base}/datasets/${slug}/resource/`

  const rename = new Map<string, string>()
  if (info.ontology_prefix) rename.set(info.ontology_prefix, onto)
  if (info.resource_prefix) rename.set(info.resource_prefix, res)

  // Rebuild prefixes keeping the pair at its original position.
  const prefixes: Record<string, string> = {}
  let placed = false
  for (const [name, iri] of Object.entries(skeleton.prefixes ?? {})) {
    if (oldNames.has(name)) {
      if (!placed) {
        prefixes[onto] = ontoIri
        prefixes[res] = resIri
        placed = true
      }
      continue
    }
    prefixes[name] = iri
  }
  if (!placed) {
    prefixes[onto] = ontoIri
    prefixes[res] = resIri
  }

  const ren = (value: string): string => {
    for (const [old, next] of rename) {
      if (value.startsWith(`${old}:`)) return `${next}:${value.slice(old.length + 1)}`
    }
    return value
  }

  const maps = skeleton.maps.map((m) => ({
    ...m,
    subject: {
      ...m.subject,
      ...(m.subject.template !== undefined ? { template: ren(m.subject.template) } : {}),
      ...(m.subject.constant !== undefined ? { constant: ren(m.subject.constant) } : {}),
      ...(m.subject.classes ? { classes: m.subject.classes.map(ren) } : {}),
    },
  }))

  const next = { ...skeleton, prefixes, maps }
  return JSON.stringify(next) === JSON.stringify(skeleton) ? null : next
}
