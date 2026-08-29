# CLAUDE.md — Asterism

本リポジトリで作業する AI セッション（Claude Code / Cowork）向けの指示。公開リポジトリ（Apache-2.0）。

## セッションの作法（必須）

- **開始時**: [`docs/ROADMAP.md`](docs/ROADMAP.md)（実行状態の単一ソース）と関連 ADR（`docs/architecture/`）を読む。
- **作業後**: 進捗・新タスク・決定を `docs/ROADMAP.md` に反映する（状態列・直近の一手・更新 log）。
- 役割分担: **決定は ADR**（`docs/architecture/*.md`）、**実行状態は ROADMAP**。
- **UI を変えたら `manual/` も同じ PR で直す。** 画面の文言・手順・順序を変えたら、
  [`manual/ja/`](manual/ja/) の該当章とスクリーンショットを更新し、
  `python scripts/build_manual_site.py` で [`docs/manual/`](docs/manual/) を作り直してコミットする
  （GitHub Pages で公開している実物: <https://kumagallium.github.io/asterism/manual/>）。
  マニュアルは人間向けヘルプであると同時に**設計相談チャットに注入される知識**なので、
  古いままだと AI が実在しない操作を案内する。照合テスト
  （`api/tests/test_design_consult.py`）は UI 名の陳腐化を検出するが、
  **手順の順序や説明文までは見ていない**（実例: #453 がステップを入れ替えたのに
  `manual/` を更新せず、古い順序のまま公開された）。

## 不変条件（破らない）

- **名称 = Asterism。IRI 名前空間 = `https://kumagallium.github.io/asterism/...`。** これは 2026-06-02 にユーザー不在の今のうちに一度だけ実施した意図的な破壊的改名（旧 `csv2rdf-mcp`）。**以降この識別子は安定 ── これ以上改名しない**（IRI はデータ同一性）。改名 spec=`handoff_to_claude_code_rename_to_asterism.md`。
- **生成コードを実行しない。** 取り込みは宣言的（RML/Morph-KGC）＋ **閉じた検証済み関数ライブラリ（`asterism.functions` の Tier 0）のみ**参照。新しい変換は人間が一度 vet してライブラリに足す（`docs/architecture/ingestion-execution-safety.md`, `phase5-declarative-substrate.md` §5）。
- **自リポジトリ単体で完結させる。** 他プロダクト依存や社内固有の文脈を本リポジトリの「意味の前提」にしない。シークレットをコミットしない。

## 技術スタック

- Python（`rdflib` / Morph-KGC）、Oxigraph（SPARQL 1.1）、FastMCP（MCP サーバ）。
- テスト `pytest`、lint/format `ruff`、パッケージ `uv`。リリースは tagpr（`VERSION` は手で上げない）。

## 構成の入口

- `docs/ROADMAP.md` — 実行状態（まずここ）。
- `docs/architecture/` — 決定（ADR）。
- `ingest/`（`asterism` パッケージ）/ `mcp/`（MCP サーバ・typed tools）/ `step0/`（AI 支援スキーマ設計 CLI）/ `experiments/`（スパイク）。
