# 取り込み元の切り替えと MCP クライアント — 実機の記録

ADR [`source-switch-mcp-client.md`](../../architecture/source-switch-mcp-client.md) の受け入れ条件
（§8）の記録。**Graphium の道具（`list_media`／`get_media_file`／`add_material`）は別セッションで
実装中**のため、本記録の相手は同じプロトコルを実装した **偽の stdio サーバ**（Python の
FastMCP スクリプト・`FAKE_ROOT` のファイルを一覧・取得・追加する）。Graphium の `dist-mcp` が
できたら同じ手順で取り直す（§4）。

## 1. 構成（2026-10-23）

| 項目 | 値 |
|---|---|
| Asterism | branch `feat/source-switch-origins`（A1＋A2）・`asterism-local`（単一ユーザー）・UI は `VITE_DEMO_AGENT_URL=/` でビルド |
| AI | `_manual-capture/llm_stub.py`（OpenAI 互換の固定応答）。列の意味は「〈列〉の値」 |
| 取り込み元 | `settings.json` の `mcpServers.graphium`＝`{transport: "stdio", command: "<venv>/bin/python", args: ["fake_source_server.py"], env: {FAKE_ROOT: "<一時フォルダ>"}, enabled: true}`・`sourceHome: {server: "graphium"}`・`sourceProvider: {list: "list_media", get: "get_media_file", add: "add_material"}` |
| 一時フォルダの中身 | `価格表.csv`（4 行）・`店の一覧.csv`（2 行）・`価格表のコピー.csv`／`価格表の控え.csv`（`価格表.csv` と同じ bytes） |

## 2. A1 — 登録・有効化・道具の一覧

- 設定 › 「MCP サーバ」タブで「例から入れる」→「追加する」→ 一覧にコマンド全文
  `/usr/local/bin/node /path/to/Graphium/dist-mcp/graphium-mcp.mjs` と `GRAPHIUM_ROOT=…` が出る・
  `enabled: false` で `settings.json` に保存された（スクリーンショット `manual/screenshots/settings-mcp-servers.png`）。
- 偽の stdio サーバを有効にして `POST /api/mcp-servers/graphium/source-items {"exts": [".csv"]}`:

```json
{"server":"graphium","items":[
  {"id":"価格表.csv","name":"価格表.csv","mimeType":"text/csv","size":86,"uploadedAt":"2026-10-23T12:37:24","folder":null},
  {"id":"店の一覧.csv","name":"店の一覧.csv","mimeType":"text/csv","size":36,"uploadedAt":"2026-10-23T12:37:24","folder":null}]}
```

- 呼び出しのたびに子プロセスが起き、返事のあと残らない（`keep_alive=False`。pytest
  `test_stdio_server_is_started_from_settings_and_the_child_does_not_linger` でも固定）。

## 3. A2 — 📎 の 2 択 → 一覧 → 設計 → `source_origins` → 被りの通知

1. 📎 を押すと「この PC のファイル」「graphium のファイル」の 2 択（`manual/screenshots/attach-from-source.png`）。
2. 「graphium のファイル」→ 一覧（名前・1 KB・日付）→ `価格表.csv` に ☑ →「添える」。
3. 人の手番「📎 価格表.csv」→ スタブ AI で設計 → 返事「表「価格表」を読みました（4 行・1 行＝価格表 の 1 行）。ID＝食材。」
4. データセットの `meta.json`（実物）:

```json
"source_origins": [
  {
    "name": "source-3637d45e.csv",
    "sha256": "sha256:ff4c144262611c0be7d4cbe103a741c9ffb6b26fd6d6be024375710470a48ad6",
    "home": {"server": "graphium", "id": "価格表.csv", "name": "価格表.csv"}
  }
]
```

   `name` は保存名（日本語のファイル名は slug 化される）。`home.name` に置いた名前が残る。
5. 同じ bytes の別名 `価格表の控え.csv` を添える → 返事の 1 枚に注記が **データセットごとに 1 行**
   出て、設計はそのまま続く（「公開」も押せる。`manual/screenshots/attach-duplicate.png`）:

   > 「価格表の控え.csv」は既にデータセット「価格表」の取り込み元です（同じ中身）。

   名前でなく中身（sha256）で合流していることの確認。

## 4. 残り（Graphium の道具ができてから）

- [ ] Graphium の `dist-mcp/graphium-mcp.mjs` を stdio で登録（`command` は `which node` のフルパス・
  `env` に `GRAPHIUM_ROOT=<一時 vault>`）→ `tools` に `list_media` が出る。
- [ ] Graphium に落とした `price.csv` が 📎 の一覧に出て、設計が走り、`source_origins[0].home.id` が
  Graphium の fileId になる。
- [ ] A3: Asterism に落とした CSV を ✓ → Graphium の `media/` に 1 つ現れ、再起動で索引に出る時刻を記録。
  もう一度落として ✓ しても増えない。
