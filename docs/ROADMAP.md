# Asterism ROADMAP

> 本リポジトリの**実行状態の単一ソース**。ADR（`docs/architecture/`）が*決定*を、本書が*進捗と次の一手*を持つ。
>
> **このリポジトリで作業するセッション（Claude Code / Cowork / 人）は、開始時に本書を読み、作業後に状態を更新すること。** 手順は [`CLAUDE.md`](../CLAUDE.md) 参照。

最終更新: 2026-06-04

## 北極星

構造化ソース（CSV を第一の入力型に、JSON/API も）を、**信頼でき・引用でき・再現できる RDF** に変換し、SPARQL と MCP で AI から問える形にする。**starrydata に閉じない汎用基盤**を目指す。

## ワークストリームと状態

（`#` は Cowork タスク widget の番号。最終的には本書が正。）

| # | ワークストリーム | 状態 | 担当 | 参照 |
|---|---|---|---|---|
| — | substrate（ソース非依存の宣言的取り込み: RML+Morph-KGC） | 実証済み | core/CC | `architecture/phase5-declarative-substrate.md` |
| — | 関数ライブラリ v0（`functions.py`・閉じた検証済み集合） | ✅ main 入り（#73）。Tier0=8 関数（+2入力 `float_array_count`） | CC | 同上 §4 |
| 14 | step0 が宣言 RML を出力 | ✅ **完了**（#74-77: propose §RML 生成→materialize 抽出→`rml_check`→validate T9 閉集合検証） | CC | `architecture/step0-rml-emission.md` |
| 15 | ワークベンチ materialize（人間ゲート） | ✅ **S1-S4 完了**（#78-80 + 本PR）。substrate→投入 API→UI ゲート→draft→canonical 昇格+alignment。**実 Oxigraph で実投入・昇格を検証ずみ** | CC(UI) | `architecture/phase5-workbench-materialize-gate.md` |
| 18 | **汎用クエリ/Ask 層**（最小=SPARQL tool → NL→SPARQL、スキーマ非依存） | ✅ **完了（実 LLM dogfood 実証済）**。土台(LLM-free)=MCP `schema_summary`＋`sparql_query`＋demo-agent `/demo/schema`・`/demo/sparql`。escape=`/demo/ask` を**型付き優先＋自動フォールバック**化（引用ゼロ→LLM が schema_summary 接地で read-only SPARQL→`sparql_query` 実行→接地回答＋引用＋使用SPARQL 開示）。UX=Ask-view にキー欄＋「使用した SPARQL」開示パネル。**dogfood: 実 Oxigraph に非 starrydata スキーマ(材料硬さ lab:)を投入→実 LLM が schema 内省→正しい SPARQL 生成→最硬 WC-Co を実 IRI 引用付きで回答（2026-06-03）** | core 設計→CC | — |
| 20 | **オントロジー/canonical ライフサイクル + starrydata 脱結合**（層整理・CRUD/版・core から starrydata を example へ降格・typed ツール一般化） | 🟢 **ADR 合意済・P2 完了**。P2-1=汎用ヘルパを `asterism.text` へ抽出。P2-2a=starrydata identity を `datasets/starrydata/dataset.toml` に宣言＋汎用ローダ。**P2-2b 完了**（PR #97-99・方針=実装コスト度外視/プロダクト理想）= api/mcp/watcher の定数 import 撤去（descriptor 経由）＋**datasets/ を 3 image 同梱**（本番も content が唯一の正）＋QUDT 表を `datasets/starrydata/qudt_map.yaml` 唯一の正へ（engine `qudt.py` は core 据え置き・graceful degrade）＋seed を `datasets/starrydata/seed/` へ物理移動。ingest 100/mcp 29/api 33/demo 9 緑。**残=P3（ライフサイクル CRUD/版・ontology graph 投影）・P4（per-dataset typed ツール＋QUDT 表一般化）・並行=2個目の非 starrydata dataset 投入(#19)** | core 設計→CC | `architecture/ontology-canonical-lifecycle.md` |
| 19 | **UI 一般化**（非CSVソース追加・mapping・ソース間リンク） | 未（#20 の P2 と接続: 2個目の非 starrydata dataset 投入） | CC(UI)+core | — |
| — | **UI プロダクト品質化（forest 再設計）** | 🟡 **Phase 1 着地（PR 進行中）**。基盤（forest トークン/Google Fonts/タイポ/border-box）＋新IA骨格（つくる/つかう/管理・SPARQL 降格・3星ブランド）＋Ask 全面刷新（2カラム・来歴を常設右パネル化・引用カード）＋アクティビティ restyle＋共通アトム/状態(skeleton/empty/error)。**契約不変・表示のみ**。残=**Phase 2**（ホーム/共有の語彙/カタログ データセット主役化/データ追加3ステップ化・非CSVソースは見た目のみ） | CC(UI) | `design/asterism-ux/` |
| — | linker（MP→RML化＋`normalize_host` 昇格 / MatPROV 連結候補） | MP 実証済・RML化未 | core | `experiments/mp-linking-poc/` |
| 10 | 来歴トレース表示＋データ品質の見せ場（表示 UI） | 一部（tool 済・UI 未） | CC(UI) | — |
| — | 統治・スケール（`fn-local` 名前空間・未対応変換ログ） | 設計済・未実装 | later | 同上 §5.5 |

## 直近の一手（順）

1. ~~関数ライブラリ v0 / #14 step0 RML 出力 / #15 materialize 人間ゲート~~ ✅ 完了。
2. ~~**#18 汎用クエリ層**（土台 + escape + Ask-view UX + 実 LLM dogfood）~~ ✅ **完了**。新オントロジー Ask の鍵が揃った。
3. ~~**#20 starrydata 脱結合 P2**（ヘルパ抽出 / identity descriptor / 定数 import 撤去 / datasets/ image 同梱 / QUDT 表 content 化 / seed 移動）~~ ✅ **P2 全完了**（PR #94-99）。~~不完全 refine ガード~~ ✅ **完了**（PR #101）。
4. **次の本丸＝#20 P3**（ライフサイクル CRUD/版: 再昇格・**retract=tombstone**・delete・dataset version ＋ §2 ontology named graph 投影）。citable-facts 方針に直結（引用安定性を壊さず撤回）。**P4**=per-dataset typed ツール＋QUDT 表一般化。**並行**=2個目の非 starrydata dataset 投入で P2 を実証（#19 接続）。
5. 候補=#19 UI 一般化 / **UI 品質化 Phase 2**（着地済 PR #100・残=非CSV実接続）。UI は活発 WIP のためバックエンド作業と分離。
3. #15 運用化: 本番 compose の api イメージに `asterism-ingest[substrate]`（morph-kgc）を入れる（現 docker api は morph-kgc 無し）。実 LLM dogfood（propose §RML の安定性）。
4. #19 UI 一般化（非CSVソース・mapping・ソース間リンク）。

## 確定事項（恒久）

- **名称 = Asterism（2026-06-02 決定・改名実施中）。** ユーザー不在＝外部が IRI を参照し始める前の最安の窓で、`csv2rdf-mcp` から一度きりで改名。repo `csv2rdf-mcp`→`asterism`、IRI 名前空間 `…/csv2rdf-mcp/…`→`…/asterism/…`。哲学（散在データを共有オントロジーで繋いで意味を作る＝星を線で結ぶ星群）を名に。`v0.1.0` の旧 IRI は歴史スナップショットとして据え置き、以降は新 IRI。実行 spec = `handoff_to_claude_code_rename_to_asterism.md`。**完了後この識別子は安定 ── これ以上改名しない。**
- **生成コードを実行しない**。宣言経路は Tier 0 関数（`asterism.functions`）のみ参照。

## 実行メモ（ハーネス選択）

- 大きく fan-out する作業（多数データセットの並列オンボーディング、リポ全体の audit/port 等）は、CC の **dynamic workflows**（subagent を 10–100 並列で束ねる Claude Code 機能・research preview）が適。
- Cowork は dynamic workflows 非対応 ── subagent/並列タスクで fan-out する。
- 線形・限定の作業（例: Asterism 改名 change-set）は通常実行で十分。

## 更新 log

- 2026-06-04: **不完全 refine 出力ガード（PR #101・別件バグ修正）**。大スキーマで refine が出力上限で途中停止し model.yaml/MIE/ingester/RML ブロックを落とす→切り詰め schema が次工程に流れ materialize で artifact 欠落していた既知バグを修正。`refine_schema` が入力 vs 出力で materialize 抽出可能な artifact 集合を比較し truncation を検出、`RefinementResult.effective_schema_md`（不完全なら**前の完全版を保持**）＋`complete`/`missing_artifacts`/`warnings` を追加。CLI=不完全時に前の完全版を `--output` へ・生出力を `.incomplete.md` 退避。api `/api/refine` は guard フィールドを後方互換で返却（UI 配線は WIP のため追って）。step0 135/api 33 緑。
- 2026-06-04: **#20 P2-2b 完了 — starrydata 物理移動・定数 import 撤去（プロダクト理想版）**。方針=ユーザー確定「実装コスト度外視・プロダクトの理想で設計」（恒久方針として memory 化）＝engine/content の完全分離＋**content が本番の唯一の正**。3 PR（依存順・各 CI green でマージ）: **#97** = api `Settings`/mcp `tools.py`/`watcher.py` が `asterism.starrydata` の `DEFAULT_*` 定数 import をやめ汎用ローダ `load_dataset("starrydata")` 経由（env override 維持・wheel-only fallback）＋**3 image（upload-api/asterism-mcp/demo-agent）に `COPY datasets`＋`ASTERISM_DATASETS_ROOT`**（本番でも descriptor・QUDT 表が live）＋`.dockerignore` で生成 seed 除外。**#98** = QUDT 表 `qudt_map.yaml` を `datasets/starrydata/` の唯一の正へ（**engine `qudt.py` は core 据え置き**＝`functions.py` Tier0 が既に core engine として import／ローダ経由読み込み・不在時は警告＋空 map で graceful degrade／pkg `artifacts` 同梱コピー廃止）。**#99** = seed（`load.py`/.gitignore）を `demo-agent/seed/`→`datasets/starrydata/seed/` へ物理移動＋参照（compose.demo.yaml/make_demo_subset.py/verify_demo.py/DEMO.md）更新、watcher の graph/ontology/resource 既定を descriptor 由来に。設計判断: typed 4 ツール（`sd:` 前提）と QUDT 表の per-dataset 化は **P4**（typed ツール一般化と合流）。ingest 100/mcp 29/api 33/demo-agent 9 緑。残=P3（CRUD/版・ontology graph 投影）・P4・並行で2個目の非 starrydata dataset 投入（#19）。
- 2026-06-04: **UI プロダクト品質化 Phase 1 着地（forest 再設計）**。デザインハンドオフ `docs/design/asterism-ux/`（forest 方向採用）を既存 `ui`（React+TS+Vite）の流儀で実装。段階方針（Phase 1=基盤+Ask+アクティビティ、Phase 2=新規画面+データ追加3ステップ化）をユーザー確定。Phase 1 内容: (1) **基盤** = forest 改訂トークン（surfaceAlt/faint/borderStrong/primarySoft/**accent=amber**/entity/activity/radius 13-8-18/shadow）を `index.css` に集約・旧トークンは後方互換 alias、Google Fonts（Hanken Grotesk/Zen Kaku Gothic New/Noto Sans JP/IBM Plex Mono）、**universal box-sizing:border-box**（mobile 横溢れ修正）。(2) **新IA骨格** = 動詞主導ナビ（つくる→データを追加／つかう→質問する・カタログ／管理→アクティビティ）・SPARQL を最下部「開発者向け」へ降格・3星ブランドマーク・eyebrow(amber)/title/sub ヘッダ・グラフ稼働インジケータ。(3) **Ask 全面刷新** = 2カラム（質問+回答／**来歴トレースを常設右パネル化**＝旧ドロワー廃止）・回答カード（根拠バッジ+display本文）・引用カード（色帯+kind+出どころ）・データ品質メモ(amber)・SPARQL 開示(activity)。(4) **アクティビティ** restyle（意味色 成功=entity）。(5) **共通アトム/状態** = Btn kinds/Card/Term/skeleton(shimmer)/空/エラー。**契約不変・純表示**（`ask`/`provenance`/`galleryApi` 等そのまま）。build/lint 緑、mock プレビューで desktop/mobile 実ブラウザ確認（横溢れ無し・console エラー無し）。残=Phase 2（ホーム/共有の語彙/カタログ データセット主役化/データ追加3ステップ化・非CSVソースは見た目のみ）。

- 2026-06-02: 初版。Phase 5（設計→Ask 連結）実証＋関数ライブラリ v0 を受けて、汎用化（汎用 Ask・UI 一般化）まで含む実行状態を集約。
- 2026-06-02: `csv2rdf-mcp` → **Asterism** 改名決定（IRI 名前空間ごと一度で・実行は CC、spec=`handoff_to_claude_code_rename_to_asterism.md`）。
- 2026-06-03: **#14 完了・#15 S1-S4 完了**。宣言経路（propose §RML→materialize→T9）＋人間ゲート（draft 隔離投入→alignment→canonical 昇格）が一通り揃い、**実 Oxigraph で実投入・昇格まで検証**。残: #18 汎用 Ask 層、#15 運用化（本番 api に morph-kgc）、実 LLM dogfood。改名着地後に Asterism 名で実装した（#14/#15 の旧 csv2rdf commit は改名に内包済み）。
- 2026-06-04: **#20 P2-2a 着地**。starrydata の identity を **content として `datasets/starrydata/dataset.toml` に宣言**し、汎用ローダ `asterism.datasets.load_dataset()`（env `ASTERISM_DATASETS_ROOT` or 親方向探索・best-effort）を追加。`asterism.starrydata` の `DEFAULT_ONTOLOGY`/`DEFAULT_RESOURCE`/`SOFTWARE_AGENT_IRI` を descriptor 由来に（不在時リテラル fallback＝wheel install 対応）。contract test で descriptor＝SoT を固定。**将来の非 starrydata dataset は dataset.toml を置くだけで identity 宣言＝engine ハードコード不要**。ingest 98/mcp 29/demo-agent 9 緑。残=P2-2b（api/mcp の starrydata 定数 import 撤去・seed/QUDT/watcher を datasets/ へ物理移動）。
- 2026-06-04: **#20 ADR 合意 + P2-1 着地**。要決定 4 件をユーザー確定（すべて推奨どおり: 別 ontology graph 投影／IRI 不変+dataset 版+retract=tombstone／starrydata 降格 P2 先行／typed=content 主+生成 補助）、ADR status=合意済み。実装の発見: `asterism.starrydata` は ~840 行で starrydata ingester と汎用ヘルパが同居・多数 module が import＝P2 は untangling。**P2-1**: 汎用ヘルパ（slugify/parse_issued/parse_float_array/strip_quoted/safe_url）を新 `asterism.text` へ抽出、`starrydata` は後方互換 re-export、Tier0 `functions.py` は `asterism.text` を直接参照（汎用 core が starrydata module 非依存に）。ingest 94/mcp 29/demo-agent 9 緑。残=P2-2（DEFAULT_ONTOLOGY 既定の core からの除去・seed/QUDT を datasets/ へ）。
- 2026-06-03: **#20 起案 — ADR `ontology-canonical-lifecycle.md`（ドラフト）**。開発者の3疑問（オントロジー vs canonical のレイヤー／starrydata 特化への懸念／CRUD ライフサイクル不明）を受け、(1) 2軸固定（TBox/ABox レイヤー × draft/canonical 状態）、(2) TBox の居場所（content ファイル一次＋昇格時に ontology graph へ任意投影・Ask は ABox 逆算 baseline 維持）、(3) ライフサイクル明文化（再昇格・retract・delete・版＝IRI 不変＋dataset version）、(4) **starrydata を core 既定→`datasets/starrydata/` example へ降格**（北極星「starrydata に閉じない」）、(5) typed ツールの per-ontology 一般化（content 宣言 a 主／生成 b 補助）を設計。要ユーザー確定 4 件。前提 ADR（ontology-mapping-boundary・workbench-materialize-gate）と整合。
- 2026-06-03: **#18 完了 — 実 LLM dogfood 実証**。実 Oxigraph(:7878) の default graph に**非 starrydata の材料硬さスキーマ**(`lab:Specimen`/`lab:Measurement`/`lab:hardnessHV`/`lab:ofSpecimen` ＋ 4 specimens) を投入。demo-agent を real 起動(api/.venv に `asterism_mcp` 追加)。LLM-free 経路をライブ確認(`/demo/schema` が lab クラスを内省、`/demo/sparql` が硬さランキングを返す、`/demo/ask` キー無し→型付き該当ゼロでフォールスルー＋ヒント)。**実 LLM escape(実 Anthropic キー)**: 「最も硬い材料は？」→ 型付きフォールスルー → LLM が schema 内省 → 正しい read-only SPARQL(`?m a lab:Measurement; lab:ofSpecimen ?s; lab:hardnessHV ?hv. ?s rdfs:label ?l ORDER BY DESC(?hv)`) 生成・実行 → **最硬 WC-Co(2200 HV) を実 IRI `lab:spec-wc` 引用付きで回答＋比較表**。生成 SPARQL は `sparql` フィールドで開示。スキーマ非依存の汎用 Ask が実環境で一周。
- 2026-06-03: **#18 Ask-view UX 仕上げ**。Ask 画面に (1) 一般質問用の任意 API キー欄（workbench と共通の sessionStorage `asterism.apiKey`・非保存、型付き定番質問はキー不要と明示）＋(2) 「使用した SPARQL」開示パネル（escape が生成した read-only クエリを `<details>` で表示・「読み取り専用」タグ）。backend は使用 SPARQL を `notes` 重複させず `sparql` フィールド専用に整理。`ask(question, apiKey?)` がキーを送出。mock モードで実ブラウザ確認済（escape 例→回答＋引用＋SPARQL パネル、ZT 型付き例→パネル無し、横溢れ無し・console エラー無し）。残=実 LLM dogfood のみ。
- 2026-06-03: **#18 LLM NL→SPARQL escape を実装**（土台に続く後半）。demo-agent `/demo/ask` を「**型付き(starrydata)優先 → 引用ゼロなら自動フォールバック**」化。escape = `schema_summary` で実在語彙を接地 → Anthropic tool-use ループ（`run_sparql`→`sparql_query` read-only 経由・1回以上の自己修正可・最終 `submit_answer` を tool_choice で強制）→ 接地回答＋引用＋**使用 SPARQL 開示**（`notes`＋`sparql` フィールド）。キー = api と同じ user-brought per-request（`X-API-Key`・非保存）。UI `ask()` は workbench の sessionStorage キーを自動再利用（新 UI 面なし）。core API は Claude-free 維持（escape は消費層のみ）。テスト = fake Anthropic を注入し rdflib 実 SPARQL で fallback/実行/結果フィードバック/キー無しヒント/型付き短絡を検証（demo-agent 9 緑、mcp 29 緑、ui build/lint 緑）。残 = Ask-view の UX 仕上げ（一般質問のキー欄・SPARQL 開示パネル）＋実 LLM dogfood。
- 2026-06-03: **#18 汎用 Ask 層の土台（LLM-free）を実装**。方針 = product_direction（決定論・型付き主役／探索 LLM は escape／Ask は LLM-free／後から探索拡張可）に沿い、ユーザー判断で「**決定論土台→LLM escape の段階式**・初回は土台のみ」。実装: `asterism_mcp.tools` に (1) `schema_summary`（store の実在語彙＝class/predicate/per-class shape を usage count 付きで内省、starrydata 非依存）＋(2) `sparql_query`（read-only SELECT/ASK、update 形は `_SPARQL_UPDATE` で拒否＝api `/api/sparql` と同契約、結果を `{columns,rows,count,truncated}` に平坦化）。MCP server に両登録。consuming 層 demo-agent に `/demo/schema`・`/demo/sparql` を passthrough 配線（mock/real 両対応、LLM 不在）。テスト = MockTransport 単体 + rdflib 実 SPARQL 統合（mcp 29 / demo-agent 6 緑、ruff 緑）。残 = **LLM NL→SPARQL escape**（schema_summary を context に SELECT を起こし sparql_query で実行）を demo-agent `/demo/ask` に。core API は Claude-free 維持。
- 2026-06-03: **実環境ドッグフードで4バグ発見・修正**（実データ＋実 LLM＋実ブラウザで propose→materialize→ingest→promote を通した）。#85 SSE 一時切断で進捗ロスト（EventSource 自動再接続を殺していた）／#86 AI 生成 RML の FnO 名前空間ずれ（旧 fnml# → 新 w3id.org/rml に正規化＋propose §9 で明示＋ingest 500→422）／#87 Gallery のライフサイクル状態表示＋昇格ラベル平易化。**未対応: refine が大スキーマで出力省略（不完全 refine ガード要）**。**重要な気づき: 昇格データを Ask で問えない＝Ask ツールが starrydata 専用形＝#18 汎用 Ask 層が本筋の次の一手**（ユーザーが体験して確認）。
