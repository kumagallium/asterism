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
| D4 | 文脈の自動添付 | `{step, dataset, skeleton_summary, focus_column: {name, samples}}` を送信時に同梱。KantanWizard がモジュールスコープの小さなストア（`consultContext.ts`）に随時 `setConsultContext(patch)` で書き、ドロワーが読む。**D2 改訂後もこれは維持** — どのスレッドで送っても、いま見ている画面の文脈は変わらず付く（文脈の添付とスレッドの選び方は独立の軸） | React Context の大配線をせずに済む。patch はマージなので、ステップ変化の更新と列フォーカスの更新が互いを消さない |
| D5 | 判断は代行しない | system prompt にガードレール（取り込む/取り込まないの裁定はユーザーがする、AI は説明と参考情報のみ）。回答をフォームへ自動書き込みしない | K22 の一貫適用。会話に判断力があるように見えても、実際に列の意味欄に書くのは常に人間の指 |
| D6 | UI の作法 | Graphium の AI チャットパネル（`~/Graphium/src/features/ai-assistant/panel.tsx`）に**構造だけ**準拠: 送信ボタンは送信可アイコン⇄送信中は停止アイコンに切替、ヘッダにチャット履歴一覧（新しいチャット・更新日時降順・タイトル＝先頭発言・メッセージ数・削除）、Cmd/Ctrl+Enter で送信（素の Enter は改行）、assistant 応答は ReactMarkdown+remarkGfm、ローディングはスピナー＋「考え中…」、エラーは destructive トーン。**色/間隔/角丸は Asterism 自身のデザイントークン（`index.css` の `:root`）を使う — Graphium の Tailwind クラスは持ち込まない** | 2026-08-25 ユーザーレビュー「まだまだ」判定（送信ボタンにアイコンが無い／チャット一覧が見えない／会話が続いていなそう／Graphium を参考に）への直接対応。実績のある会話 UI のパターンを流用し、UX をゼロから再発明しない |
| D7 | メッセージの編集・再生成 | Graphium ChatBubble（714-1040 行）に準拠: ①user バブルに hover で出る編集ボタン→インライン textarea→確定でその発言を書き換え、**それ以降の履歴を切り捨てて再送信** ②assistant バブルに再生成ボタン→**直前の user までの履歴**で再送信し、その応答を置き換え。送信中（`busy`）は両アクションとも不可。分岐（fork = 編集後の元スレッドを別スレッドとして残す動作）はしない — 書き換えたら元の続きは失われる、Graphium より一段シンプルな挙動 | 「聞き方を間違えた」「もう一度別の言い回しで」に画面外の操作（新しいチャットを作り直す等）を要求しない。分岐は履歴 UI がもう一段複雑になる上、この相談窓口の役割（軽い相談）を超える |
| D8 | 導線カタログ＝実文言を単一の真実源に | `CONSULT_SYSTEM_PROMPT` に載せる画面遷移の案内は、UI コードと ja i18n ロケールから実在確認した文言だけをカタログ化して埋め込む。ここに無いボタン・メニュー・画面名を AI に発明させない — 該当が無ければ「いまの画面に見えているボタンの名前を教えてください」と聞き返させる | 実 LLM dogfood（2026-08-25）で「左側メニューの『データ設計』」「プロジェクト一覧の『設定をリセット』」という**存在しない UI**を案内する事故が発生。6 ステップの一言説明だけでは実在の導線を知らず、AI が尤もらしい名前を生成してしまう。文言のソースを UI/i18n 1 箇所に固定すれば、UI が変わってもカタログを直すだけで整合が保てる |
| D9 | チャット検索 | 一覧ビュー先頭に検索ボックス。タイトル＋全メッセージ本文の小文字化のみの部分一致でスレッドをフィルタ（fuzzy 検索はしない） | スレッドが増えると「+新しいチャット」で埋もれる（D2 のフラット化とセットで必要になった導線） |

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

- **導線カタログ**: `CONSULT_SYSTEM_PROMPT`（`main.py`）に「実在する画面の導線カタログ」
  ブロックを追加。13 項目、すべて ja i18n ロケール/`GalleryView.tsx` から実在確認した
  文言のみ（根拠は `api/tests/test_design_consult.py::test_consult_system_prompt_names_real_navigation`
  のコメントに file:line 付きで列挙・返却メッセージにも記載）。ガードレールに
  「カタログと『## いま見ている画面』に無い名前を発明しない」「該当が無ければ
  『いまの画面に見えているボタンの名前を教えてください』と聞き返す」を追加。
  英語応答時はボタン名を日本語表記のまま＋簡単な英訳併記するよう明記（UI の実文言と
  応答言語が一致しない問題を回避）。
- **api テスト**: `CONSULT_SYSTEM_PROMPT` を直接 import し、カタログ内の実文言 8 件と
  ガードレール文言がプロンプトに含まれることをピン留めするテストを追加（プロンプト
  全文一致ではなく含有 assert — 既存の `_MockLLM.captured["system"]` は元々どのテストも
  読んでいなかったため、既存テストへの影響はゼロ）。
- **D9 検索**: `ConsultDrawer.tsx` の `ConsultChatList` にローカル state の検索クエリを
  追加し、一覧描画前に `matchesQuery(thread, query)`（タイトル or いずれかのターンの
  本文に小文字化した部分一致）でフィルタ。スレッドが 1 件も無いときは検索欄自体を
  出さない（探すものが無い）。ヒット 0 件は「一致するチャットはありません。」/
  "No matching chats." を通常の空状態文言と出し分け。

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
