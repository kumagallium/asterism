# 設計中の AI 相談チャット — 右ドロワー

Status: accepted (2026-08-25)

Related: [`kantan-mode-two-tier-ux.md`](kantan-mode-two-tier-ux.md)（K22: 判断は人間）,
[`ask-chat-threads.md`](ask-chat-threads.md)（スレッド永続化の元の設計）,
[`app-data-on-disk.md`](app-data-on-disk.md)（D1/D5: localStorage ⟷ 単一ユーザー時サーバ disk）

## 0. 問題 — 判断は人間の原則の下で、判断のための「理解」を支援する相手がいない

かんたんウィザードの S6「項目の意味」では、ドメインエキスパートでも列の意味で
手が止まることがある。XRD カードの `Quality` / `RIR(I/Ic)` / `Subfile` のような
略語は、専門家でも一目で意味が分かるとは限らない。現状は Google 検索や同僚への
質問など画面外に離脱するしかなく、離脱の間に「いま見ていたデータ・いま考えて
いたこと」の文脈が失われる。

K22（列の意味・単位・取り込む/除外するの最終判断は常に人間）は変えない。今回
足すのは、その判断を人間が**下すための理解**を支援する相手 — 隣に座って質問に
答える専門家のような存在で、判断そのものは代行しない。

## 1. 決定事項

| # | 論点 | 決定 | 理由 |
|---|------|------|------|
| D1 | 置き場所 | 全画面共通の右ドロワー。右下「相談する」ボタン→スライドイン | かんたんウィザードの中に限定すると、Gallery や Ask で列名を思い出せない場面を拾えない |
| D2 | スレッド | ~~設計セッション単位（データセット名の slug/`draft`）+ `general` の 2 スロット自動束縛~~ → **スレッドはユーザーが自由に作るフラットなリスト。自動の区分けはしない**（2026-08-25 ユーザー裁定で旧案を破棄）。Graphium と同じ「単なるスレッドの一覧」: 開いたときは最後に触ったスレッド（無ければ新規）、一覧から選ぶ・「+ 新しいチャット」で作る、それだけ。保存は既存 Ask スレッドと同じ機構を **namespace 違いで共有**（`ask` / `consult`）のまま | 「今のデータについて」と「使い方について」の区分けを機械が決め打ちすると、実際にはその境界を跨ぐ相談（例: この列の意味を聞きながら Asterism の別機能も聞く）が起きた瞬間に会話が分裂する。区分けが要るかどうかも含めてユーザーの裁量に委ねる方が単純で壊れない |
| D3 | API | `POST /api/design/consult`: 無状態（history はクライアント持参）・ツールなし・LLM 1 コール・非ストリーミング。`/api/propose` `/api/refine` と同じ流儀で provider/model/api_base/key をヘッダで受け、`_resolve_llm` で組み立てる（サーバにキーを保存しない、D7 と同じ） | 判断を軽くする相談窓口に、証跡の要らないジョブ管理・SSE は過剰。propose 系と同じ認証の流儀に揃えることで、意思決定を増やさない |
| D4 | 文脈の自動添付 | `{step, dataset, skeleton_summary, focus_column: {name, samples}}` を送信時に同梱。KantanWizard がモジュールスコープの小さなストア（`consultContext.ts`）に随時 `setConsultContext(patch)` で書き、ドロワーが読む。**D2 改訂後もこれは維持** — どのスレッドで送っても、いま見ている画面の文脈は変わらず付く（文脈の添付とスレッドの選び方は独立の軸）。**拡張（2026-08-25 ユーザー裁定）**: S6「項目の意味」の 2 つの表——`pendingColumns`（まだ取り込んでいない項目、列名+実データ例）と `columns`（意味が確定している項目、列名+意味+単位）——も、**画面が表示しているのと同じデータ**（`droppedColumns`/`valueRows`+`readMeaning`）から S6 の間だけ自動で付く。S6 を離れたら両方 null で消える | 実 LLM dogfood で「まだ取り込んでいない項目（17 件）の意味を答えられますか」と聞いたら AI が「列名を教えてください」と聞き返した——判断表に見えている情報が文脈に入っていなかった。人間がスクロールして見えているものは、AI にも自動で見えているべき（読み上げさせる手間を人間に負わせない） |
| D5 | 判断は代行しない | system prompt にガードレール（取り込む/取り込まないの裁定はユーザーがする、AI は説明と参考情報のみ）。回答をフォームへ自動書き込みしない | K22 の一貫適用。会話に判断力があるように見えても、実際に列の意味欄に書くのは常に人間の指 |
| D6 | UI の作法 | Graphium の AI チャットパネル（`~/Graphium/src/features/ai-assistant/panel.tsx`）に**構造だけ**準拠: 送信ボタンは送信可アイコン⇄送信中は停止アイコンに切替、ヘッダにチャット履歴一覧（新しいチャット・更新日時降順・タイトル＝先頭発言・メッセージ数・削除）、Cmd/Ctrl+Enter で送信（素の Enter は改行）、assistant 応答は ReactMarkdown+remarkGfm、ローディングはスピナー＋「考え中…」、エラーは destructive トーン。**色/間隔/角丸は Asterism 自身のデザイントークン（`index.css` の `:root`）を使う — Graphium の Tailwind クラスは持ち込まない** | 2026-08-25 ユーザーレビュー「まだまだ」判定（送信ボタンにアイコンが無い／チャット一覧が見えない／会話が続いていなそう／Graphium を参考に）への直接対応。実績のある会話 UI のパターンを流用し、UX をゼロから再発明しない |
| D7 | メッセージの編集・再生成 | Graphium ChatBubble（714-1040 行）に準拠: ①user バブルに hover で出る編集ボタン→インライン textarea→確定でその発言を書き換え、**それ以降の履歴を切り捨てて再送信** ②assistant バブルに再生成ボタン→**直前の user までの履歴**で再送信し、その応答を置き換え。送信中（`busy`）は両アクションとも不可。分岐（fork = 編集後の元スレッドを別スレッドとして残す動作）はしない — 書き換えたら元の続きは失われる、Graphium より一段シンプルな挙動 | 「聞き方を間違えた」「もう一度別の言い回しで」に画面外の操作（新しいチャットを作り直す等）を要求しない。分岐は履歴 UI がもう一段複雑になる上、この相談窓口の役割（軽い相談）を超える |
| D8 | 導線の知識＝`manual/` を単一の真実源に | ~~`CONSULT_SYSTEM_PROMPT` にハードコードした導線カタログ~~ → **人間向けヘルプと AI の知識を同一ファイルにする**（2026-08-25 ユーザー裁定・Graphium の `manual/` に倣う）。リポジトリルート直下 `manual/ja/`（`getting-started.md`=6 ステップの使い方、`screens.md`=画面別の導線リファレンス）を人間もマニュアルとして読める形で書き、`_load_consult_manual()` がプロセス起動時に 1 回連結して `CONSULT_SYSTEM_PROMPT` に注入する。マニュアルの表記規約（ボタンは「文言」ボタン、タブは「文言」タブ、サイドバー項目はメニューの「文言」の形で書く）を UI 文言との**照合テスト**（`api/tests/test_design_consult.py::test_manual_ui_names_exist_in_ui_locales`）が正規表現で機械チェックし、UI 変更にマニュアルが追従していない箇所を CI で検出する。ここにも「## いま見ている画面」にも無いボタン・メニュー・画面名を AI に発明させない — 該当が無ければ「いまの画面に見えているボタンの名前を教えてください」と聞き返させるガードレールは維持 | ハードコードのカタログは UI が変わるたびに人力で追随しなければ陳腐化する（実 LLM dogfood 2026-08-25 で「左側メニューの『データ設計』」「プロジェクト一覧の『設定をリセット』」という**存在しない UI**を案内する事故が発生）。マニュアルを真実源にすれば、①人間向けヘルプと AI の知識が同じ文章になり二重管理が要らない ②表記規約を機械可読にすることで陳腐化そのものを CI が検出できる（人力レビュー頼みにしない） |
| D9 | チャット検索 | 一覧ビュー先頭に検索ボックス。タイトル＋全メッセージ本文の小文字化のみの部分一致でスレッドをフィルタ（fuzzy 検索はしない） | スレッドが増えると「+新しいチャット」で埋もれる（D2 のフラット化とセットで必要になった導線） |
| D10 | 相談→表への反映導線 | assistant 応答は、列の意味/単位の候補を出すとき応答末尾に ` ```asterism-suggestions ``` ` コードブロック（`{"suggestions": [{"column", "meaning", "unit"}], "kinds": [{"map", "name"}]}`、両フィールドとも任意）を添えてよい（`column`/`map` は画面の実名を一字一句）。UI はこのブロックを検出・パースして**非表示**にし、いま画面にある列/マップ（S6: `ConsultContext.pendingColumns`/`columns` の名前、S4: `kinds` のうち種類名が空のマップ）と一致した件数だけを数えて「この候補を表に反映 (N 件)」ボタン（`skeleton-evidence-revert` と同じ content-width の控えめボタン）を出す。反映は `consultApply.ts`（`consultContext.ts` と同じモジュールスコープの橋、React では結合しない）経由で、いま登録されている画面（S6 または S4）の applier を呼ぶ。**適用は空欄だけ**——S6 の判断表(droppedColumns)は「取り扱い」を変えず label/unit だけ埋める(既存 `updateColumnDecision`)、意味の表(valueRows)は既存の `commitMeta` と同じ保存経路、S4 は「1 件が表すもの」セルが空のマップだけ既存の `onSkeletonEdited` 経路(手入力と同じ再検査つき)で埋める。反映結果は「N 件を反映しました (M 件は入力済みのためスキップ)」の 1 行、applier 未登録の画面では「この画面では反映できません」。**拡張 A（2026-08-25・実 LLM dogfood）**: S6 の「意味の表」は元々 meaning が空の行を `_render_confirmed_columns` が黙って除外していたため、「意味が空欄の列の候補を」という相談に AI が列名を聞き返す穴があった → 同じ `columns` データから「意味が未入力の項目 (N 件): 列名 (例: 実データ)」を追加で描画（`ConsultColumn.samples` を新設）。**拡張 B**: S4「データの数えかた」ゲートの「1 件が表すもの」欄にも同じ導線を拡張——`ConsultContext.kinds`（SkeletonGate と同じ計算=テンプレートの `{列名}` → keyColumns、`subject.classes` → 種類名）を「データの種類: map (ID: 列+列, 種類名: 未入力/名前)」として描画し、S4 の applier は ID の作り方や取り込み裁定には手を出さず種類名だけを埋める | 実 LLM dogfood で相談チャットが良い候補を言葉で説明しても、それを表に書き写すのは人間の手作業のままだった。D5(採用と確定は人間)は変えず、**転記**だけを機械に任せる——空欄しか触らないので、既に書かれた判断を上書きする事故は原理的に起きない。「意味が確定している項目」しか見せていなければ「空欄の列」という質問自体に答えようがない、③のクラス名も同じ穴を持つ構造だったため、両方を同じ設計で塞いだ |

**非目標**: ツール実行・公開データへの質問（既存 Ask の領分）・ストリーミング・
回答からフォームへの自動転記・@ メンション・grounding scope・メッセージ分岐
（fork）・ノート反映系アクション・ナレッジ化・Composer（いずれも Graphium の
対応機能だが、この相談窓口の役割＝判断材料の提示を超える）。

## 2. Ask との領分の違い

| | Ask | 設計相談チャット |
|---|---|---|
| 対象 | すでに公開されたデータへの質問（引用できる事実） | 設計中の画面・列・使い方についての相談 |
| 基調 | LLM-free（typed 優先、LLM は escape） | 常に LLM 1 コール（相談自体がその役目） |
| 判断 | 質問に答えるだけ（判断対象がない） | K22 により回答は判断材料であって判断そのものではない |
| ツール | schema_summary / sparql_query 等 | なし（D3） |
| 置き場所 | 独立タブ | 全画面共通の右ドロワー（D1） |

## 3. 実装

- **api** `main.py`: `POST /api/design/consult`（`ConsultBody` = messages + context）。
  `CONSULT_SYSTEM_PROMPT`（日本語、かんたん 6 ステップの説明 + D5 ガードレール）を
  `_render_consult_prompt()` で messages/context と合成し `llm.complete()` を 1 回呼ぶ。
  `/api/propose` `/api/refine` と同じく write-auth ゲートの**外**（生成のみで
  データセットの読み書きをしない）。messages 空は 400、1 件 8k 文字上限、20 件超は
  古い方を切る。LLM 失敗は 502。
- **appdata** `appdata.py`: `read_threads` / `write_thread` / `delete_thread` を
  `namespace` パラメタ化（既定 `ask` — 呼び出し側を変えない限り挙動不変）。
  `main.py` に `/api/appdata/consult/threads`（GET/PUT/DELETE、ask と同契約）を追加。
- **ui** `threadStore.ts`: Ask のスレッド永続化ロジック（localStorage ⟷ 単一ユーザー
  server disk、debounce flush、cross-tab sync）を `createThreadStore<TResult>()`
  ファクトリへ抽出。`askThreads.ts` はこの上の薄いラッパー（公開 API・保存キー・
  挙動は不変）。`consult/consultThreads.ts` が `consult` namespace で同じファクトリを
  使う（D2 改訂によりスロット索引は撤去 — 3.2 参照）。
- **ui** `consult/consultContext.ts`: `setConsultContext(patch)` / `useConsultContext()`
  のモジュールストア（マージ更新）。`consult/consultApi.ts`: `POST /api/design/consult`
  の fetch ラッパー。`consult/ConsultDrawer.tsx` + `ConsultDrawer.css`: フローティング
  ボタン + 右ドロワー（IME 変換確定の Enter では送らないガード込み）。
- **ui** `KantanWizard.tsx`: step / データセット名 / 骨格要約が変わるたびに
  `setConsultContext` を呼ぶ useEffect と、S6 の意味編集欄の `onFocus` で列名 +
  実データ例 3 件を `setConsultContext({focusColumn})` する 1 行を追加。ウィザードの
  状態機械そのものは変更していない。
- **i18n**: 新 namespace `consult`（ja/en）。

### 3.1 D6 追補（2026-08-25 レビュー対応）

- **依存追加**: `lucide-react@^0.577.0`（Graphium と同一メジャー系列）。送信 (`Send`)
  ／送信中の停止 (`Square`, fill) の 2 アイコンのみに使用 — 一覧トグル・削除・
  閉じるは既存の Asterism 自前アイコン（`ThreadsIcon`/`TrashIcon`/`CloseIcon`）を
  流用し、依存範囲を最小化。`react-markdown`/`remark-gfm` は既に依存済みだったため
  追加なし。
- **チャット一覧**: `consultThreads.ts` の複数スレッド対応（`useConsultThreads()`
  が既にストア内の全スレッドを返す）をそのまま使い、`ConsultChatList`
  （`ConsultDrawer.tsx`）で更新日時降順・タイトル＝最初のユーザー発言の先頭
  40 字・日時・メッセージ数・削除ボタンを表示。選択すると
  `appendConsultMessage` 経由で**その具体的なスレッド id**へ直接追記できるよう、
  スロット間接（`sendToSlot`）に加えて「既知のスレッド id へ直接送る」経路
  （`appendConsultMessage`）を追加。「新しいチャット」は `startNewInSlot` が
  現在のスロットの束縛を新しいスレッドへ**差し替え**る（古いスレッドは一覧
  から引き続き開ける）。
- **会話継続バグの修正**（**2026-08-25 の 2 回目のレビューで D2 自体を撤去した
  ため、この節は歴史的経緯として残す — 3.2 参照**）: 送信時に**そのスレッドの
  完全な履歴**を `/api/design/consult` へ渡す処理自体は元から正しかった
  （`historyOf(thread)` が完了済みターンを全部積んで送信）。当時の実際の欠陥
  は、データセット名が `draft`（無名）から実名へ変わる瞬間、セッションスロット
  のキーが `'draft'` → `'my-dataset'` のように変わり、スロット→スレッド id の
  索引には新しいキーの束縛が無いため、それまでの会話が黙って迷子になっていた
  ことだった（「会話が続いていなそう」の実体）。`rebindSlot()` で当座しのいだが、
  ユーザー裁定でスロット概念ごと撤去したため、この不整合クラスは 3.2 の変更で
  原理的に消滅した（迷子になる「スロット」がそもそも存在しない）。
- **Cmd/Ctrl+Enter 送信**に変更（素の Enter は改行）。IME 変換確定の Enter は
  従来どおり無視。
- **Markdown**: 新規 `consult/ConsultMarkdown.tsx`。Graphium の
  `buildMarkdownComponents` と同じ余白方針を Asterism の CSS クラス
  （`ConsultDrawer.css` の `.consult-md-*`）に移植。ユーザー発言は引き続き
  plain text（判断を促す短文が多く、装飾で読みにくくする理由がないため）。

### 3.2 D2 改訂 + D7 追補（2026-08-25 2回目のレビュー対応）

- **送信ボタンのアイコンが見えないバグの原因**: `ConsultDrawer.css` の
  `.consult-send`（送信/停止の円形ボタン）が `width/height: 36px` を指定して
  いたのに **`padding` をリセットしていなかった**。`index.css` のグローバル
  `button` ルールは既定で `padding: 0.5em 1.1em`（約 8px/17.6px）を持つため、
  border-box の固定 36px 円の中でコンテンツ領域がほぼ潰れ、lucide の `Send`
  アイコン（塗りなしの細いストロークだけで構成された紙飛行機シルエット、
  サイズ 12px）が視認できないほど小さく／潰れた領域に描画されていた（原因は
  「色の衝突」でも「lucide のバンドル欠落」でもない — グローバル `button`
  ルールの `padding` が円形アイコンボタンで未リセットだったことを実際の
  CSS カスケードで確認: `.consult-send` の `color`/`background` は元々
  グローバル既定と同値で、ビルド済み JS バンドルにも `Send`/`Square` の
  path データは正しく含まれていた）。修正 = `padding: 0; line-height: 0;`
  を明示リセット + ボタン 36px・アイコン 16px に拡大 + disabled 時の
  `opacity` を 0.45→0.6 に引き上げ（薄くても形が判別できるように）。
- **D2 改訂の実装**: `consultThreads.ts` からスロット関連の全関数
  （`GENERAL_SLOT`/`sendToSlot`/`threadForSlot`/`rebindSlot`/
  `startNewInSlot`/`unbindThreadEverywhere`、`asterism.consult.sessionIndex.v1`
  索引）を削除。代わりに `latestConsultThreadId()`（全スレッド中
  `updatedAt` 最大のもの）で「開いたら最後に触ったスレッド」を実現。
  `threadStore.ts` に `getAllThreads()`（非 hook の一括読み取り）を追加。
  `ConsultDrawer.tsx` から「この設計について」/「使い方について」タブ UI を
  削除し、ヘッダの履歴アイコン→一覧→選択、の単純な導線一本に。D4 の文脈
  自動添付（`useConsultContext()` → `consult()` 呼び出し）はスレッド選択と
  無関係な経路のまま変更なし。
- **D7 の実装**: `threadStore.ts` に `editUserTurn(threadId, userTurnId,
  newText)`（対象の user ターンを書き換え、以降を切り捨てて新しい pending
  assistant スロットを追加）と `regenerateFrom(threadId, assistantTurnId)`
  （対象の assistant ターン以降を切り捨てて新しい pending スロットを追加）
  を追加 — Ask 側は呼ばないため挙動不変。`ConsultDrawer.tsx`:
  `historyOf(thread, beforeTurnId)` に askThreads.historyFor と同じ
  「指定ターンの手前まで」カットオフ引数を追加し、編集後・再生成後に送る
  `messages` を過不足なく組み立てる。user バブルは hover で鉛筆アイコン
  （既存 `PencilIcon`）→ インライン編集（Cmd/Ctrl+Enter 確定・Escape
  キャンセル）。assistant バブルは hover で再生成アイコン（既存
  `RetryIcon`）。両方とも `busy`（スレッド内に pending な応答がある間）は
  disabled。分岐はしない（非目標）。

### 3.3 D8 導線カタログ + D9 検索（2026-08-25 実 LLM dogfood 対応）

- **導線カタログ**（**2026-08-25 の 2 回目の対応で `manual/` へ移設・この節は歴史的経緯
  として残す — 3.4 参照**）: `CONSULT_SYSTEM_PROMPT`（`main.py`）に「実在する画面の導線
  カタログ」ブロックを追加。13 項目、すべて ja i18n ロケール/`GalleryView.tsx` から実在
  確認した文言のみ。ガードレールに「カタログと『## いま見ている画面』に無い名前を発明
  しない」「該当が無ければ『いまの画面に見えているボタンの名前を教えてください』と聞き
  返す」を追加。英語応答時はボタン名を日本語表記のまま＋簡単な英訳併記するよう明記
  （UI の実文言と応答言語が一致しない問題を回避）。ハードコードだったため陳腐化に
  人力レビューでしか気づけず、3.4 でマニュアル方式に置き換えた。
- **api テスト**: `CONSULT_SYSTEM_PROMPT` を直接 import し、カタログ内の実文言 8 件と
  ガードレール文言がプロンプトに含まれることをピン留めするテストを追加（プロンプト
  全文一致ではなく含有 assert — 既存の `_MockLLM.captured["system"]` は元々どのテストも
  読んでいなかったため、既存テストへの影響はゼロ）。このテストも 3.4 で置き換えた。
- **D9 検索**: `ConsultDrawer.tsx` の `ConsultChatList` にローカル state の検索クエリを
  追加し、一覧描画前に `matchesQuery(thread, query)`（タイトル or いずれかのターンの
  本文に小文字化した部分一致）でフィルタ。スレッドが 1 件も無いときは検索欄自体を
  出さない（探すものが無い）。ヒット 0 件は「一致するチャットはありません。」/
  "No matching chats." を通常の空状態文言と出し分け。

### 3.4 D8 改訂: ハードコードのカタログを `manual/` へ移設（2026-08-25 ユーザー裁定）

- **`manual/ja/`**（リポジトリルート直下、Graphium の `manual/` と同じ位置づけ）:
  `getting-started.md`（かんたんモード 6 ステップの使い方。各ステップで確かめること・
  進む/戻るボタン）、`screens.md`（3.1 で消した導線カタログ 13 項目を画面別リファレンス
  として文書化）。両ファイル冒頭の HTML コメントに表記規約を明記——**ボタンは
  「文言」ボタン、タブは「文言」タブ、サイドバー項目はメニューの「文言」の形で書く**。
  この規約が下記の照合テストの前提。en 版は今回作らない（非目標）。
- **api** `main.py`: `_find_consult_manual_dir()`（`ASTERISM_MANUAL_DIR` env var、
  無ければ `main.py` から親方向に `manual/ja` を探索——ホスト配布・asterism-local・
  dev checkout のどのレイアウトでも repo-root の `manual/ja/` に届く）→
  `_load_consult_manual()`（`manual/ja/*.md` をソートして連結、プロセス起動時に
  モジュールレベルの `CONSULT_MANUAL_TEXT` として 1 回だけ読む——リクエスト毎の I/O
  はしない）→ `_build_consult_system_prompt(manual_text)` が役割紹介・6 ステップ概要・
  マニュアル本文・ガードレールを組み立てる。マニュアル dir が見つからないときは
  `CONSULT_MANUAL_TEXT = ""` で**静かに劣化**（プロンプトからマニュアル節が丸ごと
  消えるだけ——ガードレール文自体は残るので「発明しない」は効き続ける、エラーには
  しない）。
- **照合テスト**（`api/tests/test_design_consult.py`）: `_manual_ui_phrases()` が
  `manual/ja/*.md` から HTML コメントを除いたうえで正規表現
  `「([^」]+)」(?:ボタン|タブ)` / `メニューの「([^」]+)」` を全文言抽出し、
  `test_manual_ui_names_exist_in_ui_locales` が各文言を `ui/src/i18n/locales/ja/*.json`
  の全文字列値と部分一致で照合——見当たらなければ「manual の UI 名が UI に見当たらない」
  という趣旨のメッセージで、どのファイルのどの文言かを名指しして落ちる。
  `test_consult_system_prompt_includes_manual` は `CONSULT_MANUAL_TEXT` が実際に読み込ま
  れ `CONSULT_SYSTEM_PROMPT` に含まれることと、ガードレール文言の存在をピン留め。
  旧 `test_consult_system_prompt_names_real_navigation`（3.1 で追加したハードコード
  カタログ向けテスト）はこの 2 本に置き換えて削除。

### 3.5 D4 拡張: S6 の列テーブルを自動で見せる（2026-08-25 ユーザー裁定・別 worktree）

- **ui** `consultContext.ts`: `ConsultContextState` に `pendingColumns?:
  {name, samples}[] | null` と `columns?: {name, meaning?, unit?}[] | null`
  を追加（`focusColumn` と同じ null=明示クリア/undefined=変更なしの規約）。
- **ui** `KantanWizard.tsx`: S6 の `droppedColumns`（まだ取り込んでいない項目の
  行）と `valueRows` + `readMeaning`（意味の表そのもの）から**別経路で作り直さず**
  組み立てる新しい `useEffect` を追加（既存の D4 ステップ effect とは別。
  `droppedColumns`/`valueRows` は毎レンダー新しい配列になる非メモ化の派生値
  なので、それ自体ではなく元になる state（`rules`/`sourceSamples`/
  `columnSamples`）を deps にして、実際にデータが変わった時だけ発火するように
  している——`react-hooks/exhaustive-deps` はこの codebase の既存の流儀どおり
  `eslint-disable-next-line` で明示）。`step !== 6` では両方 `null` にして
  クリア。ウィザードの状態機械・保存処理は無変更。
- **ui** `consultApi.ts`: `pending_columns`/`columns` をリクエスト body に追加
  （空配列は省略）。
- **api** `main.py`: `ConsultPendingColumn`/`ConsultColumn` を `ConsultContext`
  に追加。`_render_pending_columns`/`_render_confirmed_columns`/
  `_render_consult_columns` が「## いま見ている画面」に
  「まだ取り込んでいない項目 (N 件): …」「意味が確定している項目: …」の2行を
  追加で描画。入力ガード＝列は最大 40 件・samples 各最大 3 件・文字列は 80 字
  （単位は 20 字）で `…` 切り。合計およそ 2,000 文字を超えたら各行を
  按分して `、`区切りの境界でしか切らず「(ほか N 列)」を付す。

### 3.6 D10: 相談→表への反映導線（2026-08-25 ユーザー要望）

- **api** `main.py`: `CONSULT_SUGGESTIONS_FENCE = "asterism-suggestions"` を
  定数化し、`CONSULT_SYSTEM_PROMPT` に提案ブロックの書式（`column` は画面の
  実列名を一字一句・確信が持てない列は含めない・具体的な提案が無い応答には
  付けない）と D5 の一文（「採用と確定は必ずユーザーが行う」）を追記。
- **ui** `consult/consultApply.ts`（新規、`consultContext.ts` と同じモジュール
  スコープの橋）: `parseSuggestionsBlock(text)`（fenced block を検出・JSON
  パース。パース失敗/ブロック無しは通常応答として `displayText` をそのまま
  返す——**エラーにしない**）、`registerSuggestionApplier(fn)`/
  `applySuggestions(suggestions)`（画面側が登録した applier を呼ぶだけ。ドロワー
  は `kantan/` を一切 import しない）。
- **ui** `ConsultDrawer.tsx`: 各 assistant バブルでブロックを検出・非表示化し、
  `useConsultContext()` の `pendingColumns`/`columns` の名前と一致した件数
  だけを数えて（画面に無い列は捨てる）「この候補を表に反映 (N 件)」ボタンを表示
  （マッチが 0 なら——S6 以外にいれば自動的にそう——ボタン自体を出さない）。
  クリックで `applySuggestions()` を呼び、返った `{applied, skipped}` を
  「N 件を反映しました (M 件は入力済みのためスキップ)」の1行で表示。
  `applySuggestions()` が `null`（applier 未登録）なら「この画面では反映できません」。
  ボタンは `.consult-msg-col`（flex column, `align-items: flex-start`）で
  バブルの下に積み、ボタン自身も `align-self: flex-start` — `.skeleton-evidence-revert`
  と同じ「flex column の裸のボタンは全幅化する」罠を踏まないための対処。
- **ui** `KantanWizard.tsx`: S6 の間だけ applier を登録する新しい `useEffect`。
  意味の表（`valueRows`）は列名が一致し `readMeaning(p)`/`p.unit` が空のときだけ
  既存の `commitMeta`（手入力の onBlur と同じ保存経路）を呼ぶ。判断表
  （`droppedColumns`）は「取り扱い」(`action`) を一切変更せず、`label`/`unit`
  が空のときだけ既存の `updateColumnDecision` を呼ぶ（ローカル下書き state —
  手入力と同じ、確定は既存の「確定」操作で送信されるまで別）。反映した列名は
  `consultAppliedColumns`（新 state、`reflectChanged` と同じ「（更新）」バッジ
  ＝`kz-map-note`+`updatedBadge` を両方の表で表示、AI reflect の集計とは独立）。
- **i18n**: `consult:suggestions.{apply,applied,appliedWithSkipped,noApplier}`
  を ja/en 追加。

### 3.7 D10 拡張 A/B: 意味未入力列の可視化 + S4 の種類名（2026-08-25 実 LLM dogfood 対応）

- **api** `main.py`: `ConsultColumn` に `samples: list[str] = []` を追加。
  `_render_name_and_samples()` を共通ヘルパーに切り出し（droppedColumns も
  meaning-blank confirmed columns も「name (例: a、b、c)」という同じ形で
  読めるべきという判断）、`_render_missing_meaning_columns()`（meaning が
  空の `columns` 行だけを対象）を追加。新規 `ConsultKind` モデル
  （`map`/`source`/`key_columns`/`kind_name`）+ `ConsultContext.kinds`
  （既定 `[]`、旧クライアントとの後方互換）+ `_render_kinds()`
  （`"データの種類: peak (ID: No+(hkl), 種類名: 未入力), …"`）。
  `_render_consult_columns()` が pending/confirmed/missing/kinds の 4 行を
  まとめて同じ文字数予算で描画。プロンプトの提案ブロック指示に「意味が未入力
  の項目」候補の許可と、`kinds` フィールド（ID の作り方・取り込み裁定は提案
  しない、種類名だけ）を追記。
- **ui** `consultContext.ts`: `ConsultColumn.samples?` 追加、新規
  `ConsultKind`（`map`/`source`/`keyColumns`/`kindName`）+
  `ConsultContextState.kinds?`（`focusColumn` と同じ null=明示クリア規約）。
- **ui** `KantanWizard.tsx`: 既存の S6 供給 effect に `columnSamples[column]`
  由来の `samples` を追加。新規 effect が S4 の間だけ、SkeletonGate と同じ
  計算（`m.subject.template`/`constant` の `{列名}` 抽出 → keyColumns、
  `annotations.maps[m.name].key_columns` へのフォールバック、
  `compactClass(c, nsDetected)` → 種類名）から `kinds` を組み立てて
  `setConsultContext` する。S4 以外に移ったら null でクリア。
- **ui** `consultApply.ts`: `Applier` のシグネチャを
  `(payload: {suggestions, kinds}) => {applied, skipped}` に変更（S6/S4 が
  同じ登録スロットを共有しつつ、それぞれ自分の関心（suggestions / kinds）
  だけを見て相手のフィールドは無視する）。`parseSuggestionsBlock` が
  `kinds: ConsultKindSuggestion[]`（`map`/`name`）も返すよう拡張。
- **ui** `KantanWizard.tsx`: 新規 S4 の applier 登録 effect。`kinds` の各候補に
  ついて、対応する map の `subject.classes` が空のときだけ
  `expandClass(name, nsDetected)` した 1 要素配列を `subject.classes` にセット
  し、`onSkeletonEdited({...skeleton, maps: nextMaps})` を呼ぶ——手入力の
  「1 件が表すもの」欄と全く同じ経路（デバウンス付き再検査つき）。ID
  テンプレート・取り扱いには一切触れない。
- **ui** `ConsultDrawer.tsx`: `emptyKindMaps`（`ctx.kinds` のうち kindName が
  空のマップ名集合）を `screenColumnNames` と並べて計算し `ConsultBubble` へ
  渡す。`parseSuggestionsBlock` の返り値から `matchedSuggestions`（列名一致）
  と `matchedKinds`（空マップ一致）を両方求め、件数の合計をボタンに表示、
  クリックで両方まとめて `applySuggestions({suggestions, kinds})` する
  （S6/S4 は排他的にしか文脈を持たないため、実際には常にどちらか一方だけが
  非空）。
- **api テスト**: 意味未入力列のレンダー・kinds のレンダー（ID 表示・種類名
  未入力/入力済み双方）・`kinds` キー無しでも従来どおり動く後方互換・
  プロンプトが kinds のガードレール文言（ID の作り方/取り込み裁定は対象外）
  を含むこと、の 4 本を追加。
### 3.8 マニュアルの全章化とプロンプト注入の基本セット限定（2026-08-25）

- **`manual/ja/` をフルマニュアル化**: 2 ファイル（getting-started / screens）から
  11 ファイルへ拡充 — `index.md`（目次）、`add-data.md`（対応形式と読み取りの確認）、
  `datasets.md`（公開・追記・見直し）、`crosswalk.md`（つながり）、`ask.md`（質問する）、
  `vocab-and-grounding.md`（共通の言葉と標準）、`consult.md`（相談チャット自身）、
  `settings.md`（設定 5 タブ）、`desktop.md`（デスクトップ版と自動更新）を追加。
  章立ては Graphium の `manual/`（はじめに → 日常 → 発展 → 環境 → リファレンス）に倣う。
  スクリーンショットは `manual/screenshots/` に置き、詳細章だけが参照する。
- **注入は基本セットに限定**: 全章を `CONSULT_SYSTEM_PROMPT` に注入すると本文だけで
  3 万字規模になり、相談 1 コールごとのコスト・応答遅延・「発明しない」ガードレールの
  希釈が起きる。`_load_consult_manual()` に `_CONSULT_MANUAL_CORE =
  ("getting-started.md", "screens.md")` のホワイトリストを入れ、**注入はこの 2 本だけ**
  とした。`screens.md` が「全機能の導線を 1〜2 行ずつ持つ画面別リファレンス」で
  あり続けることが前提 — **章を足すときの作法**: 詳細は新しい章に書き、`screens.md`
  にはその機能の導線（どのメニュー・どのボタンか）を 1〜2 行足す。これで AI は
  新機能の場所を知り続け、プロンプトは肥大しない。
- **照合テストの対象は全章のまま**: `test_manual_ui_names_exist_in_ui_locales` は
  従来どおり `manual/ja/*.md` 全ファイルを対象にする。注入されない章の UI 名も
  CI で実 UI 文言と照合され続ける（人間向けヘルプとしての陳腐化も同じ仕組みで検出）。

## 4. 検証

- api: モック LLM で `/api/design/consult` を叩き、(a) 200 + reply、(b) messages 空
  400、(c) context（step/dataset/skeleton_summary/focus_column）がプロンプトに
  実際に織り込まれることをモックに渡った `user_message` で確認 — 3 テスト
  （`api/tests/test_design_consult.py`）。D6 追補では api を変更していないため
  再実行は行っていない（変更差分に api の変更なしを確認済み）。
- ui: `tsc -b && vite build` / `eslint .` / `check-i18n-parity.mjs` +
  `check-i18n-refs.mjs` を実行し、Ask 側の挙動・保存データ形状が変わっていないこと
  を型検査（`AskThread` 等の公開型が unchanged）で確認。ブラウザでの実 dogfood は
  今回も未実施 — ユーザー本人によるレビューが前提のため、次のレビューサイクルへ
  持ち越し。
- 2 回目のレビュー対応（D2 改訂・D7・送信アイコン修正）も同じ4コマンド
  （`tsc -b && vite build` / `eslint .` / `check-i18n-parity.mjs` +
  `check-i18n-refs.mjs`）で緑を確認。api は無変更のため pytest 再実行なし。
  送信ボタンの実際の見た目（アイコンが判別できるか）はブラウザでの目視が
  最終確認であり、CSS カスケードの静的解析で原因を特定・修正したのみ —
  次のレビューで確認。
- 導線カタログ + 検索（3.3）: api `uv run pytest -q`（526 passed、新規 1 テスト
  含む）+ `uv run ruff check .`（clean）。ui `tsc -b && vite build` / `eslint .`
  （既存 warning のみ）/ `check-i18n-parity.mjs` + `check-i18n-refs.mjs`（緑）。
  `git diff --check` 緑。実 LLM での再 dogfood（カタログ外の UI を案内しなく
  なったか）は次のレビューで確認。
- `manual/` 化（3.4）: `test_manual_ui_names_exist_in_ui_locales` の照合ロジックを
  わざと壊し（`screens.md` の「設計を見直す」→「設計をリセット」）、実際に
  `AssertionError`（`screens.md: 「設計をリセット」` を名指し）で落ちることを確認して
  から元に戻した。`ASTERISM_MANUAL_DIR=/nonexistent/path` で劣化動作（`manual` 節が
  丸ごと消え、ガードレール文だけ残る）も確認。api `uv run pytest -q`（527 passed）+
  `uv run ruff check .`（clean）。ui は無変更のため `check-i18n-parity.mjs` +
  `check-i18n-refs.mjs` のみ再実行し緑を確認。`git diff --check` 緑。
- S6 列テーブルの自動添付（3.5・別 worktree `feat/consult-screen-columns`）:
  api `uv run pytest -q`（528 passed、新規 1 テスト含む）+ `uv run ruff check .`
  （clean）。ui `npm run build`（成功）/ `npm run lint`（既存 warning のみ）/
  `npm run lint:i18n`（parity/refs 緑、i18n 追加なし）。`git diff --check` 緑。
  `_render_consult_context` の実出力を手元で確認（列名・実データ例・意味・単位が
  正しく整形されることと、40 列超/長文字列での按分切り＋「(ほか N 列)」の挙動）。
- D10 相談→表への反映（3.6・branch `feat/consult-screen-columns`、PR #413）:
  api 新規テストを含め `uv run pytest -q`（529 passed）+ `uv run ruff check .`
  （clean）。ui `npm run build`（成功）/ `npm run lint`（既存の1件のみ——新しい
  applier 登録 effect が出した2件目の exhaustive-deps warning は
  `eslint-disable-next-line` で既存の流儀どおり明示済み）/ `npm run lint:i18n`
  （parity/refs 緑）。`git diff --check` 緑。D9（チャット検索）と番号が衝突する
  ため、今回の提案→反映決定は D10 として追加。
- D10 拡張 A/B（3.7・branch `feat/consult-screen-columns`）: api 新規4テスト
  含め `uv run pytest -q`（533 passed）+ `uv run ruff check .`（clean）。
  `_render_consult_context` の実出力を手元で確認（「意味が未入力の項目 (N 件):
  …」「データの種類: peak (ID: No+(hkl), 種類名: 未入力), sample (ID: No,
  種類名: 試料)」の両方が仕様どおりの書式）。ui `npm run build`（成功）/
  `npm run lint`（既存の1件のみ、新設 S4 applier effect も
  `eslint-disable-next-line` で明示済み）/ `npm run lint:i18n`
  （parity/refs 緑、i18n 追加なし）。`git diff --check` 緑。
