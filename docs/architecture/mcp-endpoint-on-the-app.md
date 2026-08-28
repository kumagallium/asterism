# ADR: アプリ本体が MCP を出す（`/mcp`）＋外部 AI への配り方

状態: **採択** — ローカル / 単一ユーザーモード（デスクトップアプリ・`asterism-local`）に
限って、api 本体が `/mcp` で MCP streamable-HTTP を提供する。ホスト配備は
[`store-mcp-split.md`](store-mcp-split.md) の MCP フロント分離のまま、**一切変えない**。

関連: [`store-mcp-split.md`](store-mcp-split.md)（露出プロファイル）/
[`local-first-distribution.md`](local-first-distribution.md)（3層モデル）/
[`app-data-on-disk.md`](app-data-on-disk.md)（データホーム）。

## 1. 文脈 — 「繋げる」が事実上不可能だった

MCP ツールはすでにデスクトップ同梱物に入っていた。にもかかわらず、実際に AI から
繋ぐには **この source tree を読まないと書けない stdio 起動コマンド**を人間が手で
組み立てる必要があった。実測した内訳:

| 人間が埋めるもの | なぜ埋められないか |
|---|---|
| インタプリタのパス | 同梱コンソールスクリプト `bin/asterism` の shebang が**ビルドマシンのパス**を指す（`/Users/runner/work/...`）ので、そのままでは起動不能。`bin/python3 -m asterism_mcp.server` に読み替える知識が要る |
| `CSV2RDF_REGISTRY_ROOT` | 正解は `<データホーム>/**sources**/registry`。データホーム直下ではない（[`local.py`](../../api/src/asterism_api/local.py) `local_env`） |
| `CSV2RDF_OXIGRAPH_URL` | Oxigraph のポートは `_free_port()` が**毎起動ランダムに取る**。クライアント設定に書いた瞬間から、次回起動で必ず腐る |

3つ目が致命的で、**「一度書けば動く設定」が原理的に存在しない**。手順書の問題ではなく
設計の問題なので、設定を人間に書かせるのをやめる。

## 2. 決定

**アプリが自分で MCP を出す。** SPA を配っているのと同じ FastAPI・同じ固定ポートに
`/mcp` を生やす（[`mcp_mount.py`](../../api/src/asterism_api/mcp_mount.py)）。

```
claude mcp add --transport http asterism http://127.0.0.1:8765/mcp
```

上の3つは**一つも聞かれない**。アプリが握っている値だから、アプリが使えばよい。
「アプリが起動している＝MCP が生きている」という、人に説明できる一対一対応にもなる。

### 2.1 なぜローカル限定か

ホスト配備では **ストアと MCP を別プロセスに割ったこと自体がセキュリティ境界**
（[`store-mcp-split.md`](store-mcp-split.md)）。api に MCP を同居させるとその境界が消える。
ローカルは事情が逆で、loopback・単一ユーザー・ストアの持ち主＝操作者なので、
`ASTERISM_EXPOSE_RAW_SPARQL=1` を既定にしている配備と同じ posture に収まる。
よって `build_local_app(mcp=True)` だけが本 ADR の対象で、`build_app` は無改修。

### 2.2 露出するツール

`build_server()` をそのまま使う。つまり **registry 由来の型付きツール＋
`find_datasets` / `schema_summary` / `provenance_of` / `template_curve_fetch`**、
および露出プロファイルが開いているときだけ `sparql_query`。ローカル用に別の
ツール集合を作らない（作ると「MCP 経由だけ答えが違う」が発生する）。

### 2.3 止める手段

`asterism-local --no-mcp`。ローカルでも MCP を出したくない運用のための逃げ道で、
既定は ON。

## 3. 実装上の罠（再発防止）

| 罠 | 症状 | 対処 |
|---|---|---|
| **SPA の catch-all が `/mcp` を食う** | `GET /mcp` が **200 で index.html** を返し、`POST` は 405。ブラウザで叩くと「ある」ように見えて、MCP としては存在しない | `/mcp` のルートを SPA マウントより**前**に積む。`test_mcp_is_not_shadowed_by_the_spa` で固定 |
| **`Mount("/mcp")` は `/mcp` 単体にマッチしない** | Starlette の Mount は `^/mcp(?P<path>/.*)$`。素の `/mcp` は `redirect_slashes` に落ちて **307** になる | 完全一致の `Route("/mcp")` を別に積み、`_FixedPathApp` が scope の path を書き換えて委譲。`/mcp` と `/mcp/` の両方をテストで固定 |
| **lifespan を繋がないと動かない** | streamable-HTTP のセッションマネージャは lifespan 内でしか起動しない。繋ぎ忘れると初回リクエストで落ちる | `app.router.lifespan_context` を包んで合成する |
| **fastmcp が無い venv** | `.[local]` extra 無しの api venv | `attach_mcp` は ImportError を握って False を返し、警告だけ出して api は起動する |

## 4. 外部 AI クライアントへの配り方（設定 UI）

URL は1本でも、**貼り先はクライアントごとに違う**。設定画面がクライアントを選ばせ、
そのクライアント用の文字列をそのまま出す（人間が変換しない）。

| クライアント | 方式 | 渡すもの |
|---|---|---|
| Claude Code | CLI | `claude mcp add --transport http asterism <URL>` |
| Claude Desktop | **設定→コネクタ→カスタムコネクタ** | URL のみ（`claude_desktop_config.json` は stdio しか検証しないので、JSON を配ってはいけない） |
| Cursor | `~/.cursor/mcp.json` | `{"mcpServers": {"asterism": {"url": "<URL>"}}}` |
| VS Code (Copilot) | `.vscode/mcp.json` | `{"servers": {"asterism": {"type": "http", "url": "<URL>"}}}` |
| その他 | 汎用 | URL のみ |

URL の組み立ては **SPA 自身の origin** から行う（`window.location.origin + "/mcp"`）。
8765 が他プログラムに取られてフォールバックしたときも、表示が実際の待受と必ず一致する
——ここに定数を書くと、ずれた瞬間に「コピーしたのに繋がらない」になる。

## 5. 検証

- `api/tests/test_local_mcp.py` — `/mcp` `/mcp/` 両方の `initialize`、SPA に食われないこと、
  `tools/list` に `find_datasets` が出ること、`--no-mcp` で消えること。
- 実機: 隔離ホームで `asterism-local --port 8792` を起動し、
  `claude mcp add --transport http … http://127.0.0.1:8792/mcp` → `claude mcp list` が
  **✓ Connected**（2026-08-28）。
