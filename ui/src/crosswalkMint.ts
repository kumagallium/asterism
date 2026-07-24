// How a crosswalk's own words are minted from a concept key. The single source of
// truth for the client side: a concept named `crystal_system` gets the kind
// `xw:CrystalSystem` and the linking field `xw:hasCrystalSystem`.
//
// These IRIs end up IN the stored data, so two implementations drifting apart would
// split one concept into two — hence one module, imported by both the manual builder
// and the guided flow. For a DISCOVERED candidate the server has already minted them
// and its values win; these functions cover what a human types by hand.

/** The crosswalk hub vocabulary namespace (matches the runtime's `XW`). */
export const XW_NS = 'https://kumagallium.github.io/asterism/crosswalk/ontology#'

/** PascalCase an ascii concept key for an IRI localname ("crystal_system" →
 * "CrystalSystem"). Returns '' when the key has no ascii alnum (e.g. pure Japanese),
 * so the caller can require an ascii key and keep the minted IRI clean + citable. */
export function pascalCase(key: string): string {
  return key
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join('')
}

/** The hub class IRI minted for a concept key (xw:<PascalCase>). */
export function classIriForConcept(key: string): string {
  const p = pascalCase(key)
  return p ? `${XW_NS}${p}` : ''
}

/** The hub link-predicate IRI minted for a concept key (xw:has<PascalCase>). */
export function linkPredicateForConcept(key: string): string {
  const p = pascalCase(key)
  return p ? `${XW_NS}has${p}` : ''
}

/** A crosswalk id (slug) from a human name. Falls back to a generated id when the
 * name has no ascii (e.g. a Japanese name) so the id stays IRI-safe. */
export function perspectiveIdFromName(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return slug || `p-${Date.now().toString(36)}`
}

/** An id that does not collide with the crosswalks that already exist. Building onto
 * an existing id REPLACES it, so a second connection on the same concept has to be
 * offered a free id rather than silently overwriting the first. */
export function uniqueCrosswalkId(base: string, taken: Iterable<string>): string {
  const used = new Set(taken)
  if (!used.has(base)) return base
  let n = 2
  while (used.has(`${base}-${n}`)) n += 1
  return `${base}-${n}`
}
