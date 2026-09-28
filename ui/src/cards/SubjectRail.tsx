import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Route } from '../App'
import { listClasses, listDatasets, type CardsDatasetSummary, type ClassEntry } from './cardsApi'
import { buildRailTree, type RailChild, type RailKindNode } from './railTree'
import './rail.css'
import { useSubjects } from './subjectStore'

export interface SubjectRailProps {
  /** いま開いているページ（種類のページの現在地判定に使う）。 */
  route: Route
  navigate: (route: Route, opts?: { replace?: boolean }) => void
}

/** 左の一覧（契約メモ contract_pr_f9.md §1-2）。種類（class）ごとにオブジェクト
 *  を並べる — データセットではない。オープンデータ／自分のデータの区切りも
 *  無い。木の組み立ては `railTree.ts` の純関数。 */
export function SubjectRail({ route, navigate }: SubjectRailProps) {
  const { t } = useTranslation('cards')
  const all = useSubjects()
  const [datasets, setDatasets] = useState<CardsDatasetSummary[]>([])
  const [classes, setClasses] = useState<ClassEntry[]>([])

  useEffect(() => {
    let cancelled = false
    listDatasets()
      .then((d) => {
        if (!cancelled) setDatasets(d)
      })
      .catch(() => {
        /* best-effort: 取れなければ見本の印が出ないだけ */
      })
    // PR F16 §1.5: ハブの印（`rail.hub_pill`）のためだけに取る — 取れなくても
    // 見出しに印が出ないだけで木は組める。
    listClasses()
      .then((c) => {
        if (!cancelled) setClasses(c)
      })
      .catch(() => {
        /* best-effort: 取れなければハブの印が出ないだけ */
      })
    return () => {
      cancelled = true
    }
  }, [])

  const tree = buildRailTree({ datasets, subjects: all, classes })
  const isEmpty = tree.kinds.length === 0 && tree.other.length === 0

  function openChild(child: RailChild) {
    navigate({ tab: 'cards', subjectKey: child.subjectKey })
  }

  function openKind(kind: RailKindNode) {
    navigate({ tab: 'cards', classPageIri: kind.classIri })
  }

  return (
    <div className="rail">
      <div className="rail-section">
        <h4 className="rail-section-title">{t('rail.objects_title', { defaultValue: 'オブジェクト' })}</h4>
        {isEmpty ? (
          <p className="rail-empty">
            {t('rail.objects_empty', {
              defaultValue: 'まだありません。「＋ オブジェクトを追加」から選びます',
            })}
          </p>
        ) : (
          <div className="rail-datasets">
            {tree.kinds.map((kind) => (
              <KindRow
                key={kind.classIri}
                node={kind}
                isCurrent={kind.classIri === route.classPageIri}
                onOpenKind={() => openKind(kind)}
                onOpenChild={openChild}
              />
            ))}
          </div>
        )}
      </div>

      {tree.other.length > 0 && (
        <div className="rail-section">
          <h4 className="rail-section-title">{t('rail.other_section', { defaultValue: 'その他' })}</h4>
          <div className="rail-list">
            {tree.other.map((child) => (
              <RailChildButton key={child.subjectKey} child={child} onClick={() => openChild(child)} />
            ))}
          </div>
        </div>
      )}

      <button
        type="button"
        className="rail-add-object"
        onClick={() => navigate({ tab: 'cards', add: true })}
      >
        {t('rail.add_object', { defaultValue: '＋ オブジェクトを追加' })}
      </button>

      <div className="rail-legend">
        <span className="rail-legend-item">
          <span className="rail-dot rail-dot--link" aria-hidden="true" />
          {t('rail.legend_linked')}
        </span>
        <span className="rail-legend-item">
          <span className="rail-dot rail-dot--own" aria-hidden="true" />
          {t('rail.legend_own_only')}
        </span>
        <span className="rail-legend-item">
          <span className="rail-dot rail-dot--amb" aria-hidden="true" />
          {t('rail.legend_ambiguous')}
        </span>
        <span className="rail-legend-item">
          <span className="rail-dot rail-dot--set" aria-hidden="true" />
          {t('rail.legend_set')}
        </span>
      </div>
    </div>
  )
}

function dotVariantForChild(child: RailChild): 'link' | 'own' | 'amb' | 'set' {
  if (child.kind === 'set') return 'set'
  if (child.match === 'ambiguous') return 'amb'
  if (child.match === 'own_only') return 'own'
  return 'link'
}

function RailChildButton({ child, onClick }: { child: RailChild; onClick: () => void }) {
  const { t } = useTranslation('cards')
  const kindLabel = child.kind === 'set' ? t('rail.kindSet', { defaultValue: '一覧' }) : null
  return (
    <button type="button" className="rail-item" onClick={onClick}>
      <span className={`rail-dot rail-dot--${dotVariantForChild(child)}`} aria-hidden="true" />
      <span className="rail-item-label">{child.label || t('rail.unlabeled')}</span>
      {child.isSample && (
        <span className="rail-item-sample">{t('rail.sampleBadge', { defaultValue: '見本' })}</span>
      )}
      {kindLabel && <span className="rail-item-kind">・{kindLabel}</span>}
    </button>
  )
}

/** 1 種類の行 + オブジェクト（契約メモ §1-2）。見出し行のクリック＝種類のページ
 *  （`#/cards/k/<class_iri>`）、子の行のクリック＝そのオブジェクトのページ。
 *  常に開いたまま出す（旧: データセット単位の折りたたみは廃止 — 種類の数は
 *  通常少ないため折りたたみが要らない）。 */
function KindRow({
  node,
  isCurrent,
  onOpenKind,
  onOpenChild,
}: {
  node: RailKindNode
  /** いま開いている種類のページか。 */
  isCurrent: boolean
  onOpenKind: () => void
  onOpenChild: (child: RailChild) => void
}) {
  const { t } = useTranslation('cards')
  return (
    <div className="rail-dataset">
      <div className="rail-dataset-row">
        <button
          type="button"
          className="rail-dataset-name"
          onClick={onOpenKind}
          aria-current={isCurrent ? 'page' : undefined}
        >
          {node.label || t('rail.unlabeled')}
        </button>
        {node.isHub && (
          <span className="rail-hub-pill">{t('rail.hub_pill', { defaultValue: 'つながり' })}</span>
        )}
      </div>
      {node.children.length > 0 && (
        <div className="rail-list rail-dataset-children">
          {node.children.map((child) => (
            <RailChildButton key={child.subjectKey} child={child} onClick={() => onOpenChild(child)} />
          ))}
        </div>
      )}
    </div>
  )
}
