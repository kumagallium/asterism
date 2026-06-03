# Asterism ROADMAP

> 本リポジトリの**実行状態の単一ソース**。ADR（`docs/architecture/`）が*決定*を、本書が*進捗と次の一手*を持つ。
>
> **このリポジトリで作業するセッション（Claude Code / Cowork / 人）は、開始時に本書を読み、作業後に状態を更新すること。** 手順は [`CLAUDE.md`](../CLAUDE.md) 参照。

最終更新: 2026-06-02

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
| 18 | **汎用クエリ/Ask 層**（最小=SPARQL tool → NL→SPARQL、スキーマ非依存） | 未（**新オントロジー Ask の鍵**。現状 MCP は starrydata 形4ツールのみ） | core 設計→CC | — |
| 19 | **UI 一般化**（非CSVソース追加・mapping・ソース間リンク） | 未 | CC(UI)+core | — |
| — | linker（MP→RML化＋`normalize_host` 昇格 / MatPROV 連結候補） | MP 実証済・RML化未 | core | `experiments/mp-linking-poc/` |
| 10 | 来歴トレース表示＋データ品質の見せ場（表示 UI） | 一部（tool 済・UI 未） | CC(UI) | — |
| — | 統治・スケール（`fn-local` 名前空間・未対応変換ログ） | 設計済・未実装 | later | 同上 §5.5 |

## 直近の一手（順）

1. ~~関数ライブラリ v0 / #14 step0 RML 出力 / #15 materialize 人間ゲート~~ ✅ 完了。
2. **#18 汎用クエリ層**（まず SPARQL passthrough tool → NL→SPARQL）。新オントロジー Ask の鍵。
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

- 2026-06-02: 初版。Phase 5（設計→Ask 連結）実証＋関数ライブラリ v0 を受けて、汎用化（汎用 Ask・UI 一般化）まで含む実行状態を集約。
- 2026-06-02: `csv2rdf-mcp` → **Asterism** 改名決定（IRI 名前空間ごと一度で・実行は CC、spec=`handoff_to_claude_code_rename_to_asterism.md`）。
- 2026-06-03: **#14 完了・#15 S1-S4 完了**。宣言経路（propose §RML→materialize→T9）＋人間ゲート（draft 隔離投入→alignment→canonical 昇格）が一通り揃い、**実 Oxigraph で実投入・昇格まで検証**。残: #18 汎用 Ask 層、#15 運用化（本番 api に morph-kgc）、実 LLM dogfood。改名着地後に Asterism 名で実装した（#14/#15 の旧 csv2rdf commit は改名に内包済み）。
- 2026-06-03: **実環境ドッグフードで4バグ発見・修正**（実データ＋実 LLM＋実ブラウザで propose→materialize→ingest→promote を通した）。#85 SSE 一時切断で進捗ロスト（EventSource 自動再接続を殺していた）／#86 AI 生成 RML の FnO 名前空間ずれ（旧 fnml# → 新 w3id.org/rml に正規化＋propose §9 で明示＋ingest 500→422）／#87 Gallery のライフサイクル状態表示＋昇格ラベル平易化。**未対応: refine が大スキーマで出力省略（不完全 refine ガード要）**。**重要な気づき: 昇格データを Ask で問えない＝Ask ツールが starrydata 専用形＝#18 汎用 Ask 層が本筋の次の一手**（ユーザーが体験して確認）。
