import { useTranslation } from 'react-i18next'
import type { Route } from '../App'
import { addSubjectAndPersist, useSubjects } from './subjectStore'
import { PlaceView } from './PlaceView'
import { SetPage } from './SetPage'
import { SubjectPage } from './SubjectPage'

export interface CardsViewProps {
  route: Route
  navigate: (route: Route, opts?: { replace?: boolean }) => void
  /** ページ最下部の 1 行「<label> に聞く」から Ask へ（契約メモ §6.3）。 */
  onAsk: (question: string) => void
}

/**
 * route を見て「データを置く」／1 件のページ／絞り込みのページを出し分ける器
 * （契約メモ §6.3）。カード詳細（`…/c/<card_id>`）は SubjectPage/SetPage が
 * `cardId` prop を受けて自分の中で出す（両者とも `CardDetail` を内蔵している）
 * ので、ここでは分岐しない。
 *
 * この器自体は画面の中身を持たない — 各画面は別担当（c2-place/c2-pages）が
 * 並行実装したファイルを import するだけ。Props の実際の形は各ファイルが
 * 実在してから判明したため、ここでの配線はその実物に合わせてある（notes 参照）。
 */
export function CardsView({ route, navigate, onAsk }: CardsViewProps) {
  const { t } = useTranslation('cards')
  const subjects = useSubjects()

  if (route.place) {
    return (
      <PlaceView
        // KantanWizard から「ページに戻る」で入ってきたときの、既に棚にある
        // データセット（契約メモ §6.3・App.tsx parseHash の placeDatasetId）。
        datasetId={route.placeDatasetId}
        // PlaceView.tsx の navigate は汎用型 `(route: {tab:string, ...}) => void`
        // を受ける（統合の疎結合のため）。ここで実 Route にブリッジする。
        navigate={(r) => navigate(r as unknown as Route)}
        onPlaced={(placedSubjects, placedSet) => {
          // place/commit は個体側をすでに完全な SubjectItem（label/class_label/
          // card_count/subject_key/created_at 込み）で返す（c1-place notes 参照）
          // — そのまま積む。絞り込み側（set）はこのレスポンスに SubjectItem の
          // 形が無い（set_id/spec だけ）ため、ここで組み立てる。
          for (const s of placedSubjects) addSubjectAndPersist(s)
          addSubjectAndPersist({
            kind: 'set',
            id: placedSet.set_id,
            label: null,
            class_label: null,
            source: 'own',
            card_count: null,
            match: null,
            subject_key: `s:${placedSet.set_id}`,
            spec: placedSet.spec,
            created_at: new Date().toISOString(),
          })
        }}
      />
    )
  }

  if (route.subjectKey?.startsWith('i:')) {
    const iri = route.subjectKey.slice(2)
    return (
      <SubjectPage
        iri={iri}
        cardId={route.cardId}
        onSelectCard={(cardId) => navigate({ tab: 'cards', subjectKey: route.subjectKey, cardId })}
        onCloseCard={() => navigate({ tab: 'cards', subjectKey: route.subjectKey })}
        onOpenSubject={(nextIri) => navigate({ tab: 'cards', subjectKey: `i:${nextIri}` })}
        onAsk={onAsk}
        onEditDefinition={(datasetId) =>
          navigate({ tab: 'gallery', datasetId, detailTab: 'design' })
        }
      />
    )
  }

  if (route.subjectKey?.startsWith('s:')) {
    const setId = route.subjectKey.slice(2)
    const stored = subjects.find((s) => s.subject_key === route.subjectKey)
    if (!stored?.spec) {
      // 直リンク等で spec が手元に無い（私の一覧に無い絞り込み）。Phase 1 は
      // set_id だけから spec を復元する経路が無い — 一覧から入り直してもらう。
      return <p className="subtitle">{t('page.pick_hint')}</p>
    }
    return (
      <SetPage
        setId={setId}
        spec={stored.spec}
        cardId={route.cardId}
        onSelectCard={(cardId) => navigate({ tab: 'cards', subjectKey: route.subjectKey, cardId })}
        onCloseCard={() => navigate({ tab: 'cards', subjectKey: route.subjectKey })}
        onOpenSubject={(iri) => navigate({ tab: 'cards', subjectKey: `i:${iri}` })}
        onFiltersChanged={(result) => {
          addSubjectAndPersist({
            kind: 'set',
            id: result.set_id,
            label: result.title.class_label,
            class_label: result.title.class_label,
            source: 'open',
            card_count: null,
            match: null,
            subject_key: `s:${result.set_id}`,
            spec: result.spec,
            created_at: new Date().toISOString(),
          })
          navigate({ tab: 'cards', subjectKey: `s:${result.set_id}` })
        }}
        onAsk={onAsk}
        onEditDefinition={(datasetId) =>
          navigate({ tab: 'gallery', datasetId, detailTab: 'design' })
        }
      />
    )
  }

  // #/cards だけ（左の一覧からまだ何も選んでいない）。
  return <p className="subtitle">{t('page.pick_hint')}</p>
}
