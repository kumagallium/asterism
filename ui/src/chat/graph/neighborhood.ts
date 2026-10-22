// 局所の図（「この話の周り」）の材料を、全体の図から近傍だけに絞る純関数（PR 11h）。
// 核＝この話の用語・この話の提案（とその existing の相手）・選んだ点。核の 1 ホップまでを入れ、cap を超えたら核を優先して切る。
// 決定論（乱数・時刻なし。並びは IRI の順）。
import type { DraftSky, SkyData, SkyDataset, SkyKind, SkyTerm } from '../../sky/skyApi'
import { draftCardConcepts } from '../../sky/skyLayout'

export interface LocalFocus {
  /** この話で確認した用語の IRI。 */
  termIris: ReadonlySet<string>
  /** この話の提案（下書き）の id。 */
  draftIds: ReadonlySet<string>
  /** 地図か局所で押した点の id（`t|`・`k|`・`draft|`・`h|`）。 */
  selectedId: string | null
}

export interface LocalSky {
  sky: SkyData
  drafts: DraftSky[]
  proposedDatasets: SkyDataset[]
  /** 入りきらず地図に任せた用語の数。 */
  more: number
  /** 核が空（出すものが無い）。 */
  empty: boolean
}

const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0)

/** 点の id を分ける。`k|<datasetId>|<iri>` は最初の 2 つの `|` で切る。 */
function parseId(id: string | null): { t?: string; k?: { ds: string; iri: string }; d?: string; h?: string } {
  if (!id) return {}
  if (id.startsWith('t|')) return { t: id.slice(2) }
  if (id.startsWith('h|')) return { h: id.slice(2) }
  if (id.startsWith('k|')) {
    const rest = id.slice(2)
    const i = rest.indexOf('|')
    return i < 0 ? {} : { k: { ds: rest.slice(0, i), iri: rest.slice(i + 1) } }
  }
  if (id.startsWith('draft|')) {
    const rest = id.slice(6)
    const i = rest.indexOf('|')
    return { d: i < 0 ? rest : rest.slice(0, i) }
  }
  return {}
}

const kindKey = (ds: string, iri: string): string => `${ds}\u0000${iri}`

export function localSky(
  sky: SkyData,
  drafts: readonly DraftSky[],
  proposedDatasets: readonly SkyDataset[],
  focus: LocalFocus,
  cap = 30,
): LocalSky {
  const termByIri = new Map<string, SkyTerm>(sky.terms.map((t) => [t.iri, t]))
  const termBySlug = new Map<string, SkyTerm>(sky.terms.map((t) => [t.slug, t]))
  const sel = parseId(focus.selectedId)

  // ── 提案 ──
  const keepDrafts = drafts.filter((d) => focus.draftIds.has(d.draft_id) || d.draft_id === sel.d)
  const draftTerms: string[] = []
  for (const d of keepDrafts) {
    for (const c of d.concepts) {
      if (!c.use.startsWith('existing:')) continue
      const t = termBySlug.get(c.use.slice('existing:'.length))
      if (t) draftTerms.push(t.iri)
    }
  }
  const draftCards = keepDrafts.reduce((n, d) => n + draftCardConcepts(d).length, 0)

  // ── 核（選んだ用語 → 提案の相手 → この話の用語） ──
  const core: string[] = []
  const inCore = new Set<string>()
  const addCore = (iri: string | undefined) => {
    if (iri && termByIri.has(iri) && !inCore.has(iri)) {
      inCore.add(iri)
      core.push(iri)
    }
  }
  addCore(sel.t)
  for (const iri of [...draftTerms].sort(cmp)) addCore(iri)
  for (const iri of [...focus.termIris].sort(cmp)) addCore(iri)

  // ── 1 ホップ（知識の線・選んだ種類／視点に結ばれた用語） ──
  const score = new Map<string, number>()
  const bump = (iri: string, n: number) => {
    if (termByIri.has(iri) && !inCore.has(iri)) score.set(iri, (score.get(iri) ?? 0) + n)
  }
  for (const e of sky.edges ?? []) {
    if (inCore.has(e.from)) bump(e.to, e.from === sel.t ? 100 : 1)
    if (inCore.has(e.to)) bump(e.from, e.to === sel.t ? 100 : 1)
  }
  if (sel.k) {
    for (const t of sky.terms) if (t.members.some((m) => m.dataset_id === sel.k!.ds && m.iri === sel.k!.iri)) bump(t.iri, 50)
  }
  if (sel.h) {
    const hub = sky.hubs.find((h) => h.id === sel.h)
    const ms = new Set(hub?.members ?? [])
    for (const t of sky.terms) if (t.members.some((m) => ms.has(m.iri))) bump(t.iri, 50)
  }
  const hop = [...score.entries()].sort((a, b) => b[1] - a[1] || cmp(a[0], b[0])).map(([iri]) => iri)

  // ── cap（提案の札は切らない。残りを用語に割る） ──
  const candidates = [...core, ...hop]
  const budget = Math.max(0, cap - draftCards)
  const keptIris = candidates.slice(0, budget)
  const more = candidates.length - keptIris.length
  const keptTerms = keptIris.map((i) => termByIri.get(i) as SkyTerm)
  const keptSet = new Set(keptIris)

  // ── 種類（結ばれた用語が入っているものだけ。選んだ種類は残す） ──
  const kinds = new Set<string>()
  const kindIris = new Set<string>()
  for (const t of keptTerms) {
    for (const m of t.members) {
      kinds.add(kindKey(m.dataset_id, m.iri))
      kindIris.add(m.iri)
    }
  }
  if (sel.k) {
    kinds.add(kindKey(sel.k.ds, sel.k.iri))
    kindIris.add(sel.k.iri)
  }
  const stdIris = new Set<string>()
  for (const t of keptTerms) if (t.standard) stdIris.add(t.standard.iri)
  const narrow = (ds: SkyDataset): SkyDataset | null => {
    const ks: SkyKind[] = ds.kinds
      .filter((k) => kinds.has(kindKey(ds.id, k.iri)))
      .map((k) => ({ ...k, links: k.links.filter((l) => kinds.has(kindKey(ds.id, l))) }))
    for (const k of ks) if (k.standard) stdIris.add(k.standard)
    return ks.length === 0 ? null : { ...ds, kinds: ks }
  }
  const datasets = sky.datasets.map(narrow).filter((d): d is SkyDataset => d !== null)
  const outProposed = proposedDatasets.map(narrow).filter((d): d is SkyDataset => d !== null)

  const hubs = sky.hubs
    .map((h) => ({ ...h, members: h.members.filter((m) => kindIris.has(m)) }))
    .filter((h) => h.members.length > 0)

  const out: SkyData = {
    ...sky,
    datasets,
    terms: keptTerms,
    standards: sky.standards.filter((s) => stdIris.has(s.iri)),
    hubs,
    questions: [],
    edges: (sky.edges ?? []).filter((e) => keptSet.has(e.from) && keptSet.has(e.to)),
    recent: [],
    truncated: false,
  }
  return {
    sky: out,
    drafts: keepDrafts.slice(),
    proposedDatasets: outProposed,
    more,
    empty: keptTerms.length === 0 && draftCards === 0,
  }
}
