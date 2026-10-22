// 地図で押した点の id → 「何の中身か」（PR 11k）。sky/skyLayout.ts の termId・kindId・datasetNodeId・hubNodeId・draftNodeId の逆引き。
import type { SkyDataset } from '../sky/skyApi'
import type { SkyLoad } from './useSky'
import type { ContentsKey } from './chatApi'

/** 引けない提案の点（まだ行が無い）。 */
export const UNPUBLISHED = 'unpublished' as const
export type ContentsTarget = ContentsKey | typeof UNPUBLISHED

type OkSky = Extract<SkyLoad, { status: 'ok' }>

const datasetOfKind = (datasets: readonly SkyDataset[], label: string): string | null => {
  for (const d of datasets) if (d.kinds.some((k) => k.label === label)) return d.id
  return null
}

/**
 * - null → 何も押していない（この話の最後の 📎）
 * - `t|<iri>` 用語／`k|<dataset>|<iri>` 種類（未公開のデータセットならそのデータセット）／`d|<id>` データセット
 * - `h|<id>` 視点（最初の member の種類）
 * - `draft|<draftId>|<key>` 提案: existing: の相手があればその用語。種類は添えた表。引けなければ UNPUBLISHED
 */
export function contentsKeyOf(pickedId: string | null, okSky: OkSky | null, sessionId: string | null): ContentsTarget | null {
  if (!pickedId) return sessionId ? { session: sessionId } : null
  const [head, ...rest] = pickedId.split('|')
  const body = rest.join('|')
  if (head === 't') return { term: body }
  if (head === 'd') return { dataset: body }
  if (head === 'k') {
    const i = body.indexOf('|')
    if (i < 0) return null
    const ds = body.slice(0, i)
    const iri = body.slice(i + 1)
    const thin = okSky?.proposedDatasets.some((d) => d.id === ds) && !okSky.sky.datasets.some((d) => d.id === ds)
    return thin ? { dataset: ds } : { kind: iri }
  }
  if (head === 'h') {
    const hub = okSky?.sky.hubs.find((h) => h.id === body)
    return hub && hub.members[0] ? { kind: hub.members[0] } : null
  }
  if (head === 'draft') {
    const i = body.indexOf('|')
    if (i < 0) return UNPUBLISHED
    const draftId = body.slice(0, i)
    const key = body.slice(i + 1)
    const concept = okSky?.proposed.find((d) => d.draft_id === draftId)?.concepts.find((c) => c.key === key)
    if (!concept || !okSky) return UNPUBLISHED
    if (concept.use.startsWith('existing:')) {
      const term = okSky.sky.terms.find((x) => x.slug === concept.use.slice('existing:'.length))
      return term ? { term: term.iri } : UNPUBLISHED
    }
    if (concept.kind === 'class') {
      const ds = datasetOfKind(okSky.proposedDatasets, concept.label)
      if (ds) return { dataset: ds }
    }
    return UNPUBLISHED
  }
  return null
}
