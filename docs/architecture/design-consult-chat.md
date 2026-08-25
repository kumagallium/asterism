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
| D2 | スレッド | 設計セッション単位（データセット名があればその slug、無ければ `draft`）+ `general`（使い方相談）の 2 スロット。保存は既存 Ask スレッドと同じ機構を **namespace 違いで共有**（`ask` / `consult`） | 「今のデータについて」と「Asterism の使い方について」は別の関心事— 混ぜると履歴が読みにくい。保存機構の重複実装は避ける |
| D3 | API | `POST /api/design/consult`: 無状態（history はクライアント持参）・ツールなし・LLM 1 コール・非ストリーミング。`/api/propose` `/api/refine` と同じ流儀で provider/model/api_base/key をヘッダで受け、`_resolve_llm` で組み立てる（サーバにキーを保存しない、D7 と同じ） | 判断を軽くする相談窓口に、証跡の要らないジョブ管理・SSE は過剰。propose 系と同じ認証の流儀に揃えることで、意思決定を増やさない |
| D4 | 文脈の自動添付 | `{step, dataset, skeleton_summary, focus_column: {name, samples}}` を送信時に同梱。KantanWizard がモジュールスコープの小さなストア（`consultContext.ts`）に随時 `setConsultContext(patch)` で書き、ドロワーが読む | React Context の大配線をせずに済む。patch はマージなので、ステップ変化の更新と列フォーカスの更新が互いを消さない |
| D5 | 判断は代行しない | system prompt にガードレール（取り込む/取り込まないの裁定はユーザーがする、AI は説明と参考情報のみ）。回答をフォームへ自動書き込みしない | K22 の一貫適用。会話に判断力があるように見えても、実際に列の意味欄に書くのは常に人間の指 |

**非目標**: ツール実行・公開データへの質問（既存 Ask の領分）・ストリーミング・
回答からフォームへの自動転記。

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
  使う。2 スロット（D2）はスレッド本体ではなく「スロット→スレッド id」の
  小さな索引（`localStorage`、`asterism.consult.sessionIndex.v1`）で表現。
- **ui** `consult/consultContext.ts`: `setConsultContext(patch)` / `useConsultContext()`
  のモジュールストア（マージ更新）。`consult/consultApi.ts`: `POST /api/design/consult`
  の fetch ラッパー。`consult/ConsultDrawer.tsx` + `ConsultDrawer.css`: フローティング
  ボタン + 右ドロワー（IME 変換確定の Enter では送らないガード込み）。
- **ui** `KantanWizard.tsx`: step / データセット名 / 骨格要約が変わるたびに
  `setConsultContext` を呼ぶ useEffect と、S6 の意味編集欄の `onFocus` で列名 +
  実データ例 3 件を `setConsultContext({focusColumn})` する 1 行を追加。ウィザードの
  状態機械そのものは変更していない。
- **i18n**: 新 namespace `consult`（ja/en）。

## 4. 検証

- api: モック LLM で `/api/design/consult` を叩き、(a) 200 + reply、(b) messages 空
  400、(c) context（step/dataset/skeleton_summary/focus_column）がプロンプトに
  実際に織り込まれることをモックに渡った `user_message` で確認 — 3 テスト
  （`api/tests/test_design_consult.py`）。
- ui: `tsc -b && vite build` / `eslint .` / `check-i18n-parity.mjs` +
  `check-i18n-refs.mjs` を実行し、Ask 側の挙動・保存データ形状が変わっていないこと
  を型検査（`AskThread` 等の公開型が unchanged）で確認。ブラウザでの実 dogfood は
  今回未実施 — 次のセッションの持ち越し。
