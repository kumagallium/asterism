import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { formatSetTitle } from './setTitle'
import type { Route } from '../App'
import { buildRailTree } from './railTree'
import { addSubjectAndPersist, useSubjects } from './subjectStore'
import { AddObjectView } from './AddObjectView'
import { ClassPage } from './ClassPage'
import { DatasetPage } from './DatasetPage'
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
  // 種類のページ（`classPageIri`）は ClassPage 自身が種類の名前を `onLabel` へ
  // 上げる（契約メモ contract_pr_f9.md §1-7）ので、ここでは一旦 null に戻す
  // だけ（App.tsx の topbar は未取得のあいだ `topbar.unselected` に倒れる）。
  // 追加画面（`add`）は未選択のまま（見出しは App.tsx が `topbar.add` で出す）。
  useEffect(() => {
    if (route.datasetPageId) return
    if (route.classPageIri || route.add) {
      onLabel(null)
      return
    }
    if (!route.subjectKey) {
      onLabel(null)
      return
    }
    const stored = subjects.find((s) => s.subject_key === route.subjectKey)
    onLabel(stored?.label ?? null)
  }, [route.datasetPageId, route.classPageIri, route.add, route.subjectKey, subjects, onLabel])

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

  // `#/cards/add` — オブジェクトを追加（契約メモ contract_pr_f9.md §1-3）。
  // AddObjectView.tsx（ui-page）の navigate は汎用型
  // `(route: {tab:string, ...}) => void` を受ける（DatasetPage.tsx と同じ理由 —
  // 並行実装の間は実 Route 型に依存しない）。ここで実 Route にブリッジする。
  if (route.add) {
    return <AddObjectView navigate={(r) => navigate(r as unknown as Route)} initialClassIri={route.addClassIri} />
  }

  // `#/cards/k/<class_iri>` — 種類のページ（契約メモ §1-4）。ClassPage.tsx も
  // 同じ汎用 navigate 型。
  if (route.classPageIri) {
    return (
      <ClassPage
        classIri={route.classPageIri}
        navigate={(r) => navigate(r as unknown as Route)}
        onLabel={onLabel}
        onDefine={onDefine}
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
        onEditDefinition={(datasetId: string) =>
          navigate({ tab: 'gallery', datasetId, detailTab: 'design' })
        }
        onOpenClass={(classIri) => navigate({ tab: 'cards', classPageIri: classIri })}
      />
    )
  }

  // `#/cards/place` は旧 URL 互換のためだけに残す — データが入る唯一の入口は
  // `#/datasets/add`（契約メモ contract_pr_f8.md §1.2）。開いたらそちらへ
  // 置き換える（replace: 履歴を汚さない）。レンダー中に navigate を呼ぶと
  // 「レンダー中に他コンポーネントの state を更新した」警告になるため effect で。
  if (route.place) return <PlaceRedirect placeDatasetId={route.placeDatasetId} navigate={navigate} />

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
        onEditDefinition={(datasetId: string) =>
          navigate({ tab: 'gallery', datasetId, detailTab: 'design' })
        }
        onOpenClass={(classIri) => navigate({ tab: 'cards', classPageIri: classIri })}
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
        onEditDefinition={(datasetId: string) =>
          navigate({ tab: 'gallery', datasetId, detailTab: 'design' })
        }
        onOpenClass={(classIri: string) => navigate({ tab: 'cards', classPageIri: classIri })}
      />
    )
  }

  // #/cards だけ（まだ何も選んでいない）。オブジェクトがあれば先頭のページへ
  // replace navigate、無ければ追加画面（契約メモ contract_pr_f9.md §1-6）。
  return <DefaultLanding subjects={subjects} navigate={navigate} />
}

/** 既定ルート（`#/`・`#/cards`）の着地先を決める（契約メモ §1-6）。「先頭」は
 *  レールに並ぶ順（`railTree.ts` の種類ごとの木・名前順→各グループ内は新しい
 *  方が上）と同じ — `isSample` はここでは要らないので `datasets: []` で組む
 *  （並びは `class_iri`/`class_label`/`created_at` だけで決まる）。レンダー中に
 *  他コンポーネントの state を更新できないため effect で navigate する
 *  （`PlaceRedirect` と同じ形）。 */
function DefaultLanding({
  subjects,
  navigate,
}: {
  subjects: ReturnType<typeof useSubjects>
  navigate: (route: Route, opts?: { replace?: boolean }) => void
}) {
  const tree = buildRailTree({ datasets: [], subjects })
  const first = tree.kinds[0]?.children[0] ?? tree.other[0] ?? null

  useEffect(() => {
    if (first) navigate({ tab: 'cards', subjectKey: first.subjectKey }, { replace: true })
  }, [first, navigate])

  if (first) return null
  return <AddObjectView navigate={(r) => navigate(r as unknown as Route)} />
}

/** `#/cards/place`（旧 URL）→ `#/datasets/add`（データが入る唯一の入口）への
 *  置き換え（契約メモ contract_pr_f8.md §1.2）。何も描かない — 着地先は
 *  App.tsx の PlaceView（gallery タブ）。 */
function PlaceRedirect({
  placeDatasetId,
  navigate,
}: {
  placeDatasetId?: string
  navigate: (route: Route, opts?: { replace?: boolean }) => void
}) {
  useEffect(() => {
    navigate({ tab: 'gallery', add: true, placeDatasetId }, { replace: true })
  }, [placeDatasetId, navigate])
  return null
}
