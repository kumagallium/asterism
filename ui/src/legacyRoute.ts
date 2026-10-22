// 旧い画面の hash（#/home・#/cards/… など）の読み書き。App の殻は chat/chatRoute.ts の 3 つの hash だけになったが、
// 旧 component（cards/*・vocabQuestion など）がこの `Route` の型と parseHash に依存している。11e で旧 component と一緒に消す。
import type { DetailTab } from './redesignTarget'
import { type DecisionFilter, decisionsQuery, parseDecisionsQuery } from './decide/decisionsModel'
import { type WriteRequest, homeHash, parseHomeQuery } from './sky/skyRoute'

export type Tab =
  | 'home'
  | 'workbench'
  | 'ask'
  | 'gallery'
  | 'vocab'
  | 'crosswalk'
  | 'map'
  /** 「決めたこと」（人が決めた関係の記録）。左ナビには出さない——用語のページと
   *  星の横からだけ辿る。 */
  | 'decisions'
  | 'sparql'
  /** 裏タブ（NAV_ITEMS には出さない）。カード描画器 3 つの見本ページ・スクショ用。 */
  | 'cardsdemo'
  /** 「1 件／絞り込み × カード」の使う画面（object-cards-ui.md）。既定ルート
   *  （契約メモ contract_pr_e.md §3）— 左ナビ「見る」から入る。 */
  | 'cards'

// ---- hash ルーティング -------------------------------------------------------
// リロードで常にホームへ戻る／ディープリンク不可だった問題への最小のルータ。
//   #/home … #/sparql        画面タブ
//   #/datasets/<id>          データセット詳細（一覧⇄詳細の往復でも選択が消えない）
//   #/datasets/<id>/<tab>    詳細内タブ（structure/tools/files/connect/design）
//   #/ask/<threadId>         質問する: チャットのスレッド（#/ask だけなら新しいチャット）
// hash が唯一の真実源: 画面遷移は navigate() が hash を書き、hashchange で state
// に反映する（ブラウザの戻る/進むもそのまま効く）。

export interface Route {
  tab: Tab
  /** `#/home?deep=1`（`tab: 'home'`）— 星図の同じ場所に「中身まで見る」（値の網）を出す。
   *  旧 `#/network` はここへ置き換える。 */
  deep?: boolean
  /** `#/home?new=<iri>,<iri>`（`tab: 'home'`）— 公開直後の星図で、その種類・用語を 1 度だけ灯す。 */
  newIris?: string[]
  datasetId?: string
  detailTab?: DetailTab
  /** `#/crosswalk/new` — the guided "make a connection" flow, deep-linkable so other
   *  screens can send someone straight into it and back/forward still work. */
  create?: boolean
  /** `#/ask/<id>` — the open chat thread (reload / back / forward keep it). */
  threadId?: string
  /** `#/cards/place` — 「データを置く」（object-cards-ui.md 契約メモ §6.2）。
   *  旧 URL 互換のためだけに残す — 開いたら `#/datasets/add` に置き換える
   *  （契約メモ contract_pr_f8.md §1.2）。新しい入口は下の `add`。 */
  place?: boolean
  /** `#/home?add=1`（`tab: 'home'`）— データが入る唯一の入口。ホームの右の領域に、かんたんウィザードの
   *  S1（ファイルを置く）から出す `WorkbenchTier` を描く（左の会話は残る。UI 再設計 PR 8c）。旧 `#/datasets/add`・
   *  `#/data?add=1` もここへ読み替える。「同じ形なら設計なしで追加」の近道は S1 の帯が担う。
   *  `tab: 'cards'` のときは `#/cards/add`（オブジェクトを追加・契約メモ contract_pr_f9.md §1-3）を指す —
   *  同じフィールドを tab ごとに読み替える（`datasetId`/`subjectKey` 等と同じ流儀）。 */
  add?: boolean
  /** `#/home?write=1[&q=…][&resume=…]`（`tab: 'home'`）— 左の会話を「問いを書く」で開く。旧 `#/questions?new=1…`
   *  （質問するから持ち込む題・続きの下書き）も、ここへ読み替える。 */
  write?: WriteRequest
  /** `#/questions/term/<slug>`（`tab: 'vocab'`）— 用語のページ。 */
  termSlug?: string
  /** `#/questions/<theme>`（`tab: 'vocab'`）— そのテーマのカードを見せる。 */
  themeId?: string
  /** `#/cards/add?kind=<class_iri>` — 種類のページの「＋ 追加」から、その種類を
   *  開いた状態で追加画面に入る（PR F9）。 */
  addClassIri?: string
  /** `#/cards/k/<encoded class_iri>` — 種類のページ（契約メモ
   *  contract_pr_f9.md §1-4）。 */
  classPageIri?: string
  /** `#/cards/place?dataset=<id>` | `#/home?add=1&dataset=<id>` — かんたん
   *  ウィザードから「ページに戻る」で入ってきたときの、既に棚にあるデータセット
   *  （契約メモ §6.3 の `KantanWizard.tsx` の `returnTo + datasetId`）。`place`
   *  または `add` と組で使う。 */
  placeDatasetId?: string
  /** `#/cards/i/<encoded iri>` | `#/cards/s/<set_id>` — 契約メモ §1 の subject_key
   *  文字列表現をそのまま持つ（`i:<iri>` | `s:<set_id>`）。URL 変換は
   *  parseHash/routeToHash が担う。 */
  subjectKey?: string
  /** `.../c/<card_id>` — カード詳細。subjectKey と組み合わせて使う。 */
  cardId?: string
  /** `#/cards/i/<iri>?solo=1` — 束ねずに単独のページを見る逃げ道（契約メモ
   *  §1.3）。`?` は `i/<iri>` の直後にも `c/<cardId>` の後ろにも付けられる。 */
  solo?: boolean
  /** `#/cards/d/<dataset_id>` — データセットのページ（契約メモ contract_pr_f2.md
   *  §2.2・§2.4）。「作る」と「使う」の合流点。 */
  datasetPageId?: string
  /** `#/cards/d/<dataset_id>/define` | `#/cards/d/<dataset_id>/details[/<detailTab>]`
   *  — 「意味を定義する」「詳しい情報」を見るの枠（SubjectRail・topbar）の中で
   *  開く子ルート（契約メモ contract_pr_f5.md §1.1）。無ければ従来どおり
   *  データセットのページだけ。 */
  datasetSub?: 'define' | 'details'
  /** `#/cards/s/new?dataset=<id>&class=<iri>` — 「条件で集める」の新規作成
   *  （契約メモ §2.4）。`setDatasetId`/`setClassIri` と組で使う。 */
  setNew?: boolean
  setDatasetId?: string
  setClassIri?: string
  /** `#/workbench?returnTo=<hash>` — かんたんウィザードの完了・中止の戻り先
   *  （契約メモ §2.6: 「returnTo をルートで持つ」）。データセットのページから
   *  「定義を直す」で入ったときだけ立てる — 置く画面から入る既存の経路
   *  （`goBuildShelf`/`autoInspect`）はウィザード内部の別の returnTo をそのまま
   *  使うので触らない。 */
  returnTo?: string
  /** `#/data?tab=activity`（`tab: 'gallery'`）— データの 3 つ目のタブ「記録」（取り込みの履歴）。
   *  旧 `#/jobs`・`#/decisions?tab=activity` はここへ置き換える。 */
  activity?: boolean
  /** `#/decisions?filter=perspective` — 「決めたこと」の絞り込み（無ければ「すべて」）。 */
  decisionsFilter?: DecisionFilter
  /** `#/decisions?about=<IRI>` — その種類・用語に掛かる決めたことだけ。 */
  decisionsAbout?: string
}

const TABS: readonly Tab[] = [
  'home',
  'workbench',
  'ask',
  'gallery',
  'vocab',
  'crosswalk',
  'map',
  'decisions',
  'sparql',
  'cardsdemo',
  'cards',
]
const DETAIL_TABS: readonly DetailTab[] = ['structure', 'tools', 'files', 'connect', 'design']

// parseHash/routeToHash は App.tsx から export して routes.test.ts が単体テストする
// （契約メモ §6.4）。react-refresh の「コンポーネントだけ export しろ」規約とは
// ぶつかるが、テスト容易性のため意図して許容する。
export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  // 既定ルート（`''`・`#/`）は「見る」（cards）— ユーザー指示。既存の各画面
  // （`#/home` 等）を明示した場合はそのまま尊重し、これは hash が空のときだけ効く。
  if (parts.length === 0) return { tab: 'cards' }
  // 新しい綴り（UI 再設計 PR 1）。画面名＝綴り: 知りたいこと=#/questions、
  // データ=#/data（つながりは ?tab=connections）、見る=#/browse。内部の Tab id は
  // そのまま（vocab / gallery / crosswalk / cards）。旧 `#/vocab…` も同じ結果（互換）。
  if (parts[0] === 'questions' || parts[0] === 'vocab') {
    // `#/questions/term/<slug>` — 用語のページ。`#/questions/<theme>` — テーマのカードへ。
    if (parts[1] === 'term' && parts[2]) {
      return { tab: 'vocab', termSlug: decodeURIComponent(parts[2].split('?')[0]) }
    }
    if (parts[1] && parts[1] !== 'term') {
      return { tab: 'vocab', themeId: decodeURIComponent(parts[1].split('?')[0]) }
    }
    return { tab: 'vocab' }
  }
  if (parts[0]?.startsWith('questions?') || parts[0]?.startsWith('vocab?')) {
    const query = parts[0].slice(parts[0].indexOf('?') + 1)
    if (new URLSearchParams(query).get('new') !== '1') return { tab: 'vocab' }
    // 作る操作は作業場（ホームの会話）に寄せた。題・続きの下書き・問いを足すテーマは会話へ運ぶ。
    const params = new URLSearchParams(query)
    const extend = params.get('extend')
    return {
      tab: 'home',
      write: { q: params.get('q') ?? '', resume: params.get('resume') ?? '', extend: extend ? { id: extend, label: params.get('label') ?? '' } : null },
    }
  }
  if (parts[0] === 'data') return { tab: 'gallery' }
  if (parts[0]?.startsWith('data?')) {
    const params = new URLSearchParams(parts[0].slice('data?'.length))
    if (params.get('tab') === 'connections') return { tab: 'crosswalk' }
    if (params.get('tab') === 'activity') return { tab: 'gallery', activity: true }
    if (params.get('add') === '1') return { tab: 'home', add: true }
    return { tab: 'gallery' }
  }
  if (parts[0] === 'browse' || parts[0]?.startsWith('browse?')) return { tab: 'cards' }
  // 星図（ホーム）。`#/home?deep=1` = 中身まで見る・`#/home?new=…` = 生まれた輪。
  // 旧 `#/network`（全体グラフ）はホームの「中身まで見る」に統合した。
  if (parts[0] === 'network' || parts[0]?.startsWith('network?')) return { tab: 'home', deep: true }
  if (parts[0]?.startsWith('home?')) {
    const q = parseHomeQuery(parts[0].slice('home?'.length))
    const r: Route = { tab: 'home' }
    if (q.deep) r.deep = true
    if (q.newIris.length > 0) r.newIris = q.newIris
    if (q.write) r.write = q.write
    if (q.add) {
      r.add = true
      if (q.dataset) r.placeDatasetId = q.dataset
    }
    return r
  }
  if (parts[0] === 'datasets' && parts[1]) {
    // `#/datasets/add[?dataset=<id>]` — 旧 URL。データを置くのはホームの右の領域になった（`#/home?add=1`）ので、
    // そちらへ読み替える。`add` は予約語として `<id>` の一般形より先に見る
    // （`add` という id は実在しないが、`cards/place`・`cards/s/new` と同じ流儀で明示的に区別する）。
    if (parts[1] === 'add') return { tab: 'home', add: true }
    if (parts[1].startsWith('add?')) {
      const query = parts[1].slice('add?'.length)
      const placeDatasetId = new URLSearchParams(query).get('dataset')
      return placeDatasetId
        ? { tab: 'home', add: true, placeDatasetId }
        : { tab: 'home', add: true }
    }
    const detailTab = DETAIL_TABS.includes(parts[2] as DetailTab)
      ? (parts[2] as DetailTab)
      : undefined
    return { tab: 'gallery', datasetId: decodeURIComponent(parts[1]), detailTab }
  }
  // 旧 URL 互換: #/datasets はタブ名 gallery の別名
  if (parts[0] === 'datasets') return { tab: 'gallery' }
  if (parts[0] === 'crosswalk' && parts[1] === 'new') return { tab: 'crosswalk', create: true }
  if (parts[0] === 'ask' && parts[1]) return { tab: 'ask', threadId: decodeURIComponent(parts[1]) }
  // `#/workbench?returnTo=<encoded hash>` — かんたんウィザードの完了・中止の
  // 戻り先（契約メモ §2.6）。クエリ無しの `#/workbench` は下の TABS.includes に
  // そのまま落ちる。
  if (parts[0]?.startsWith('workbench?')) {
    const query = parts[0].slice('workbench?'.length)
    const returnTo = new URLSearchParams(query).get('returnTo')
    return returnTo ? { tab: 'workbench', returnTo: decodeURIComponent(returnTo) } : { tab: 'workbench' }
  }
  if (parts[0] === 'cards') {
    // `#/cards/add` — オブジェクトを追加する唯一の入口（契約メモ
    // contract_pr_f9.md §1-3）。`add`/`k` は予約語として `<class_iri>` の一般形
    // より先に見る（`add`・`k` という主語は実在しないが、`place`・`d`・`s/new`
    // と同じ流儀で明示的に区別する）。
    if (parts[1] === 'add') return { tab: 'cards', add: true }
    if (parts[1]?.startsWith('add?')) {
      const kind = new URLSearchParams(parts[1].slice('add?'.length)).get('kind')
      return kind ? { tab: 'cards', add: true, addClassIri: kind } : { tab: 'cards', add: true }
    }
    // `#/cards/k/<encoded class_iri>` — 種類のページ（契約メモ §1-4）。
    if (parts[1] === 'k' && parts[2]) {
      return { tab: 'cards', classPageIri: decodeURIComponent(parts[2]) }
    }
    if (parts[1] === 'place') return { tab: 'cards', place: true }
    // `#/cards/place?dataset=<id>` — parts は '/' でしか割っていないので
    // クエリ文字列は parts[1] の末尾にくっついたまま届く（`place?dataset=…`）。
    if (parts[1]?.startsWith('place?')) {
      const query = parts[1].slice('place?'.length)
      const datasetId = new URLSearchParams(query).get('dataset')
      return datasetId ? { tab: 'cards', place: true, placeDatasetId: datasetId } : { tab: 'cards', place: true }
    }
    // `#/cards/d/<id>` — データセットのページ（契約メモ §2.2）。
    // `.../define` | `.../details[/<detailTab>]` — 見るの枠の中で開く子ルート
    // （契約メモ contract_pr_f5.md §1.1）。
    if (parts[1] === 'd' && parts[2]) {
      const datasetPageId = decodeURIComponent(parts[2])
      if (parts[3] === 'define') return { tab: 'cards', datasetPageId, datasetSub: 'define' }
      if (parts[3] === 'details') {
        const detailTab = DETAIL_TABS.includes(parts[4] as DetailTab) ? (parts[4] as DetailTab) : undefined
        return { tab: 'cards', datasetPageId, datasetSub: 'details', detailTab }
      }
      return { tab: 'cards', datasetPageId }
    }
    // `#/cards/s/new?dataset=<id>&class=<iri>` — 「条件で集める」の新規作成
    // （契約メモ §2.4）。`s/<set_id>` の一般形より先に見る（`new` という set_id
    // は実在しないが、`place` と同じ流儀で明示的に区別する）。
    if (parts[1] === 's' && (parts[2] === 'new' || parts[2]?.startsWith('new?'))) {
      const qIdx = parts[2].indexOf('?')
      const query = qIdx === -1 ? '' : parts[2].slice(qIdx + 1)
      const params = new URLSearchParams(query)
      const setDatasetId = params.get('dataset') ?? undefined
      const setClassIri = params.get('class') ?? undefined
      return { tab: 'cards', setNew: true, setDatasetId, setClassIri }
    }
    if ((parts[1] === 'i' || parts[1] === 's') && parts[2]) {
      // `?solo=1` — 束ねずに単独のページを見る逃げ道（契約メモ §1.3）。
      // encodeURIComponent された IRI 自体には生の `?` は現れないので、
      // 見つかった `?` はこのクエリのもの（`c/<cardId>` の後ろに付くこともある）。
      const iriRaw = parts[2]
      const iriQIdx = iriRaw.indexOf('?')
      const iriPart = iriQIdx === -1 ? iriRaw : iriRaw.slice(0, iriQIdx)
      const subjectKey = `${parts[1]}:${decodeURIComponent(iriPart)}`
      let queryStr = iriQIdx === -1 ? undefined : iriRaw.slice(iriQIdx + 1)
      let cardId: string | undefined
      if (parts[3] === 'c' && parts[4]) {
        const cardRaw = parts[4]
        const cardQIdx = cardRaw.indexOf('?')
        cardId = decodeURIComponent(cardQIdx === -1 ? cardRaw : cardRaw.slice(0, cardQIdx))
        if (cardQIdx !== -1) queryStr = cardRaw.slice(cardQIdx + 1)
      }
      const solo = queryStr ? new URLSearchParams(queryStr).get('solo') === '1' || undefined : undefined
      return { tab: 'cards', subjectKey, cardId, solo }
    }
    return { tab: 'cards' }
  }
  // `#/questions?q=<題>` ・ `#/questions?scope=…&dataset=<id>` — 「知りたいこと」画面への
  // 持ち込み（上で解く）。クエリは画面（ThemesView）が自分で hash から読む。
  // 旧 `#/jobs`（アクティビティ）は「データ」の 3 つ目のタブ「記録」へ。
  if (parts[0] === 'jobs') return { tab: 'gallery', activity: true }
  if (parts[0] === 'decisions' || parts[0]?.startsWith('decisions?')) {
    const query = parts[0].includes('?') ? parts[0].slice(parts[0].indexOf('?') + 1) : ''
    // 旧 `#/decisions?tab=activity` も「記録」へ。
    if (new URLSearchParams(query).get('tab') === 'activity') return { tab: 'gallery', activity: true }
    const q = parseDecisionsQuery(query)
    return {
      tab: 'decisions',
      ...(q.filter !== 'all' ? { decisionsFilter: q.filter } : {}),
      ...(q.about ? { decisionsAbout: q.about } : {}),
    }
  }
  if (TABS.includes(parts[0] as Tab)) return { tab: parts[0] as Tab }
  return { tab: 'home' }
}

export function routeToHash(r: Route): string {
  if (r.tab === 'home') {
    return homeHash({ deep: r.deep, newIris: r.newIris, write: r.write, add: r.add, dataset: r.placeDatasetId })
  }
  // 旧形（`tab: 'gallery', add`）を作る呼び出しが残っても、ホームの hash に直す
  if (r.tab === 'gallery' && r.add) return homeHash({ add: true, dataset: r.placeDatasetId })
  if (r.tab === 'gallery' && r.datasetId) {
    const base = `#/datasets/${encodeURIComponent(r.datasetId)}`
    return r.detailTab && r.detailTab !== 'structure' ? `${base}/${r.detailTab}` : base
  }
  if (r.tab === 'gallery' && r.activity) return '#/data?tab=activity'
  if (r.tab === 'gallery') return '#/datasets'
  if (r.tab === 'vocab') {
    if (r.termSlug) return `#/questions/term/${encodeURIComponent(r.termSlug)}`
    if (r.themeId) return `#/questions/${encodeURIComponent(r.themeId)}`
    return '#/questions'
  }
  if (r.tab === 'decisions') {
    return `#/decisions${decisionsQuery(r.decisionsFilter ?? 'all', r.decisionsAbout)}`
  }
  if (r.tab === 'crosswalk' && r.create) return '#/crosswalk/new'
  if (r.tab === 'ask' && r.threadId) return `#/ask/${encodeURIComponent(r.threadId)}`
  if (r.tab === 'workbench' && r.returnTo) return `#/workbench?returnTo=${encodeURIComponent(r.returnTo)}`
  if (r.tab === 'cards') {
    if (r.add) return r.addClassIri ? `#/cards/add?kind=${encodeURIComponent(r.addClassIri)}` : '#/cards/add'
    if (r.classPageIri) return `#/cards/k/${encodeURIComponent(r.classPageIri)}`
    if (r.place) {
      return r.placeDatasetId
        ? `#/cards/place?dataset=${encodeURIComponent(r.placeDatasetId)}`
        : '#/cards/place'
    }
    if (r.datasetPageId) {
      const base = `#/cards/d/${encodeURIComponent(r.datasetPageId)}`
      if (r.datasetSub === 'define') return `${base}/define`
      if (r.datasetSub === 'details') {
        return r.detailTab && r.detailTab !== 'structure' ? `${base}/details/${r.detailTab}` : `${base}/details`
      }
      return base
    }
    if (r.setNew) {
      const params = new URLSearchParams()
      if (r.setDatasetId) params.set('dataset', r.setDatasetId)
      if (r.setClassIri) params.set('class', r.setClassIri)
      const qs = params.toString()
      return qs ? `#/cards/s/new?${qs}` : '#/cards/s/new'
    }
    if (r.subjectKey) {
      const kind = r.subjectKey.startsWith('s:') ? 's' : 'i'
      const base = `#/cards/${kind}/${encodeURIComponent(r.subjectKey.slice(2))}`
      const withCard = r.cardId ? `${base}/c/${encodeURIComponent(r.cardId)}` : base
      return r.solo ? `${withCard}?solo=1` : withCard
    }
    return '#/cards'
  }
  return `#/${r.tab}`
}
