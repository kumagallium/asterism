// 地図で押した点の id → 「何の中身か」（PR 11k）。sky/skyLayout.ts の termId・kindId・datasetNodeId・hubNodeId・draftNodeId の逆引き。
import type { SkyLoad } from './useSky'
import type { ContentsKey } from './chatApi'

/** 引けない提案の点（まだ行が無い）。 */
export const UNPUBLISHED = 'unpublished' as const
export type ContentsTarget = ContentsKey | typeof UNPUBLISHED

type OkSky = Extract<SkyLoad, { status: 'ok' }>

/**
 * - null → 何も押していない（この話の最後の 📎）
 * - `t|<iri>` 用語／`k|<dataset>|<iri>` 種類（データセットと種類の両方）／`d|<id>` データセット
 * - `h|<id>` 視点（最初の member の種類）
 * - `draft|<draftId>|<key>` 提案: existing: の相手があればその用語。種類は添えた表（最後の 📎 のデータセット）。札が消えていればチャットの表。引けなければ UNPUBLISHED
 */
export function contentsKeyOf(pickedId: string | null, okSky: OkSky | null, sessionId: string | null, attachedDatasetId: string | null = null): ContentsTarget | null {
  const rest0: ContentsTarget | null = sessionId ? { session: sessionId } : null
  if (!pickedId) return rest0
  const [head, ...rest] = pickedId.split('|')
  const body = rest.join('|')
  if (head === 't') return { term: body }
  if (head === 'd') return { dataset: body }
  if (head === 'k') {
    const i = body.indexOf('|')
    if (i < 0) return null
    const ds = body.slice(0, i)
    const iri = body.slice(i + 1)
    return { dataset: ds, kind: iri }
  }
  if (head === 'h') {
    const hub = okSky?.sky.hubs.find((h) => h.id === body)
    return hub && hub.members[0] ? { kind: hub.members[0] } : null
  }
  if (head === 'draft') {
    const i = body.indexOf('|')
    if (i < 0) return rest0
    const draftId = body.slice(0, i)
    const key = body.slice(i + 1)
    const concept = okSky?.proposed.find((d) => d.draft_id === draftId)?.concepts.find((c) => c.key === key)
    // 札が消えた（✓ や戻す）: チャットの表に落とす
    if (!concept || !okSky) return rest0
    if (concept.use.startsWith('existing:')) {
      const term = okSky.sky.terms.find((x) => x.slug === concept.use.slice('existing:'.length))
      return term ? { term: term.iri } : UNPUBLISHED
    }
    // 添えた表（この話の最後の 📎）の札。同じ IRI の種類があればその種類も
    if (concept.kind === 'class' && attachedDatasetId) {
      const ds = okSky.proposedDatasets.find((d) => d.id === attachedDatasetId)
      return ds?.kinds.some((k) => k.iri === concept.key) ? { dataset: attachedDatasetId, kind: concept.key } : { dataset: attachedDatasetId }
    }
    return UNPUBLISHED
  }
  return null
}
