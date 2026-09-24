import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { formatSetTitle } from './setTitle'
import type { Route } from '../App'
import { addSubjectAndPersist, useSubjects } from './subjectStore'
import { DatasetPage } from './DatasetPage'
import { FirstScreen } from './FirstScreen'
import { PlaceView } from './PlaceView'
import { SetPage } from './SetPage'
import { SubjectPage } from './SubjectPage'

export interface CardsViewProps {
  route: Route
  navigate: (route: Route, opts?: { replace?: boolean }) => void
  /** ページ最下部の 1 行「<label> に聞く」から Ask へ（契約メモ §6.3）。 */
  onAsk: (question: string) => void
  /** topbar の見出しに使う「選んだ対象のラベル」を App へ上げる（契約メモ §3:
   *  「CardsView/SubjectPage が持つ resolved label を App に上げる」）。1 件・
   *  絞り込みのどちらも未選択（`#/cards`・`#/cards/place`）のときは null。 */
  onLabel: (label: string | null) => void
  /** データセットのページの「定義を直す」「続きから」（契約メモ §2.2・§2.6）。 */
  onDefine: (datasetId: string) => void
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
export function CardsView({ route, navigate, onAsk, onLabel, onDefine }: CardsViewProps) {
  const { t } = useTranslation('cards')
  const subjects = useSubjects()

  // 今どの対象を見ているかのラベル（topbar 用）。ここで一元管理する — 各画面
  // （SubjectPage/SetPage）はサーバから改めて解決した名前を持つが、担当外
  // ファイルにつき触れない。rail に載せたときに控えた label（subjectStore）を
  // 代わりに使う（notes 参照: 絞り込みの「条件の要約」は現状 class_label 止まり）。
  // データセットのページ（`datasetPageId`）は DatasetPage 自身が summary の
  // label を `onLabel` へ上げる（契約メモ §2.2）ので、ここでは何もしない。
  useEffect(() => {
    if (route.datasetPageId) return
    if (!route.subjectKey) {
      onLabel(null)
      return
    }
    const stored = subjects.find((s) => s.subject_key === route.subjectKey)
    onLabel(stored?.label ?? null)
  }, [route.datasetPageId, route.subjectKey, subjects, onLabel])

  if (route.datasetPageId) {
    // datasetSub（`.../define`・`.../details[/…]`）は App.tsx が WorkbenchTier/
    // GalleryView を直接 mount する（契約メモ contract_pr_f5.md §1.1）。ここは
    // データセットのページ単体（子ルート無し）だけを受け持つ。
    if (route.datasetSub) return null
    return (
      <DatasetPage
        datasetId={route.datasetPageId}
        // DatasetPage.tsx の navigate は汎用型 `(route: {tab:string, ...}) => void`
        // を受ける（PlaceView.tsx と同じ理由 — 並行実装の間は実 Route 型に
        // 依存しない）。ここで実 Route にブリッジする。
        navigate={(r) => navigate(r as unknown as Route)}
        onDefine={onDefine}
        onLabel={onLabel}
      />
    )
  }

  if (route.setNew) {
    if (!route.setDatasetId) return <p className="subtitle">{t('page.pick_hint')}</p>
    // SetPage.tsx（ui-page）の新規作成モード（契約メモ §2.4）— 既存の SetForm/
    // SetPage の作成経路をそのまま再利用する。カード関連の props はこのモードで
    // は呼ばれない（作成が終わるまで `spec` が無く、カード一覧の分岐に届かない）
    // が、SetPageProps では必須のまま — 空振りの no-op を渡す。
    return (
      <SetPage
        newFor={{ datasetId: route.setDatasetId, classIri: route.setClassIri }}
        navigate={(r: { tab: string; [key: string]: unknown }) => navigate(r as unknown as Route)}
        onSelectCard={() => {}}
        onCloseCard={() => {}}
        onOpenSubject={(iri) => navigate({ tab: 'cards', subjectKey: `i:${iri}` })}
        onFiltersChanged={() => {}}
        onAsk={onAsk}
        onEditDefinition={(datasetId) =>
          navigate({ tab: 'gallery', datasetId, detailTab: 'design' })
        }
        onOpenDataset={(datasetId) => navigate({ tab: 'cards', datasetPageId: datasetId })}
      />
    )
  }

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
        onOpenDataset={(datasetId) => navigate({ tab: 'cards', datasetPageId: datasetId })}
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
            label: formatSetTitle(result.title, t),
            class_label: result.title.class_label,
            source: 'open',
            card_count: null,
            match: null,
            subject_key: `s:${result.set_id}`,
            spec: result.spec,
            created_at: new Date().toISOString(),
            // 条件を変えても種類（class）は同じ（SetForm は classIri を固定で
            // 受ける）— 親のデータセットは元の項目からそのまま引き継ぐ。
            dataset_id: stored.dataset_id,
            dataset_label: stored.dataset_label,
          })
          navigate({ tab: 'cards', subjectKey: `s:${result.set_id}` })
        }}
        onAsk={onAsk}
        onEditDefinition={(datasetId) =>
          navigate({ tab: 'gallery', datasetId, detailTab: 'design' })
        }
        onOpenDataset={(datasetId: string) => navigate({ tab: 'cards', datasetPageId: datasetId })}
      />
    )
  }

  // #/cards だけ（まだ何も選んでいない）。初回の入口 2 つ＋見本（契約メモ §3）。
  return <FirstScreen navigate={navigate} onAsk={onAsk} />
}

