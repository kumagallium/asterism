# ADR: 取り込み元の切り替えと MCP クライアント（呼ぶ側）

状態: **採択**（2026-10-23・利用者の裁定）。実装は PR 3 本に分ける:
**A1**＝クライアントの核と設定タブ（本 ADR §2〜§4）・**A2**＝取り込み元の切り替えと
`source_origins`（§5〜§6）・**A3**＝✓ の後の送り込み（§7）。

関連: [`mcp-endpoint-on-the-app.md`](mcp-endpoint-on-the-app.md)（出す側）/
[`app-data-on-disk.md`](app-data-on-disk.md)（`settings.json`・秘密は書かない）/
[`local-first-distribution.md`](local-first-distribution.md)（単一ユーザーモード）/
[`sky-chart-two-entries.md`](sky-chart-two-entries.md) §2.4g（チャットの 📎 と ✓）。

## 1. 文脈 — 「書く物も表も別のアプリに入れ、Asterism は知識グラフに徹したい」

利用者の問い（2026-10-23）: ノートや元の表はノートのアプリ（Graphium）に置き、
Asterism は知識グラフの作成と提供に徹する形にできないか。単体でも使えるよう、
元データの**向き先**を選べるように。双方向参照のために Asterism に MCP クライアントを
載せてもよい。

検討した 3 案:

| 案 | 中身 | 捨てた理由 |
|---|---|---|
| 保存場所を動かす | Asterism のデータホームをノートのアプリの木の中に置く | Oxigraph の二進データは同期フォルダと相性が悪い（既に `syncWarning` がある）。2 つのアプリが 1 つの木を書くと、相手の索引（`media/` の件数で再構築を決める）が Asterism の `meta.json` を拾う。`registry/<id>/source/` の写しは「版の根拠」で、動かせない |
| **取り込み元を切り替える（採択）** | 保存場所はそのまま。📎 の**入口**を「この PC のファイル／MCP サーバのファイル」で選べるようにし、選んだファイルは同じ設計の列に流す。原本の SHA-256 と**家**（どのサーバのどのファイルか）を記録する | — |
| チャットの AI に外部の道具を渡す | LLM が `search_notes` 等を呼ぶ | 取り込み元は決定論の UI 操作で、LLM の判断を挟む理由がない。LLM が道具を呼ぶ場所は api でなく demo-agent（別プロセス）で、差し込み点も違う。別の課題として残す（§9） |

決まった構図（メモリ「二重保持・向き先・MCP クライアント」）: **正本は 3 つ**＝
ノートのアプリ（本文・来歴・主張）・元ファイル（表）・Asterism（型 `sv:`・設計・承認済み
知識グラフ）。Asterism のストアは作り直せる写し。連携時は相手が**家**で**入口はどちらでも**
よい。単体時は Asterism が家。

## 2. 決定 — 汎用の MCP クライアントを載せ、相手に「取り込み元プロトコル」を実装させる

Asterism はこれまで MCP を**出す**だけだった（`mcp_mount.py`）。ここに**呼ぶ側**
（[`mcp_client.py`](../../api/src/asterism_api/mcp_client.py)）を足す。

- **Graphium 固有のコードは置かない。** Asterism は §3 のプロトコルの項目だけ読み、道具名
  の既定は空。Graphium の道具名（`list_media`／`get_media_file`／`add_material`）と起動の形は
  設定画面の**連携のプリセット**（`ui/src/settings/mcpPresets.ts`。`connectRecipes` と同じ性質の
  データ）として置く。利用者が見るのは「Graphium と連携する」の ☑ 1 つで、入れるのは
  `graphium-mcp.mjs` のパスだけ（node は api の `/api/mcp-servers/locate` が候補と PATH から探し、
  vault は既定 `~/Documents/Graphium`）。汎用の登録フォームは「ほかの MCP サーバ（くわしい設定）」
  に畳む（利用者の裁定 2026-10-23: 手段でなく目的を前に出す）。
- **読み取りの道具を呼ぶ側**であり、書き込みの道具は `add` の 1 つだけ。しかも ✓ の送り込み
  （§7）の内部コードからだけ呼ぶ。HTTP からは呼べない。この例外は利用者の裁定「Asterism に
  落とした原本は既定で相手へ送る」に由来する。
- ライブラリは `fastmcp.Client`（既に出す側で使っている `fastmcp` の同じ版）。
  `StdioTransport(keep_alive=False)` と `StreamableHttpTransport`。下限 `fastmcp>=2.6`
  （2.3 で `call_tool_mcp` と `StreamableHttpTransport`、2.6 で `keep_alive` と
  `init_timeout` が揃う。PyPI の wheel で確認・2026-10-23）。mcp SDK v2 の改名
  （`isError`→`is_error`・`inputSchema`→`input_schema`）は両名を見て吸収する。

## 3. 取り込み元プロトコル（Asterism 側の仕様）

設定 `sourceProvider: {list, get, add}`（道具名。既定は空）。通信の形は 3 道具とも
**JSON 文字列を `content[0].text` に 1 本**返し、失敗は `isError: true` と `{error}`。
Asterism は JSON として読めない本文を失敗として扱う。

| 道具 | 入力 | 返り（Asterism が読む項目だけ） |
|---|---|---|
| `list` | `{exts?: string[], folder?, limit?}` | `{items: [{id, name, mimeType, size?, uploadedAt?, folder?}]}`。並びは `uploadedAt` の新しい順・`limit` 既定 100 |
| `get` | `{id, inline?}` | `{id, name, mimeType, size, bytes_base64?}`。`bytes_base64` は `inline: true` かつ **32 MiB 以下** |
| `add` | `{bytes_base64, name, mimeType?}` | `{id, duplicate, indexed?}` |

- Asterism は `exts` に `chat_attach.TABLE_EXTS`（`.csv .tsv .xlsx .xls .json`＝ドット付き小文字）
  をそのまま渡す。**比較は大文字小文字を区別しない**。相手の種類の分類（CSV は `data`・
  xlsx は `document`・json は `other`）は噛み合わないので使わない。
- `get` の bytes は 32 MiB まで。超えたら「大きすぎて取れません」で止める（`path` の直読みは
  v1 では実装しない: LLM クライアントに任意のローカルファイルを読ませる口になる）。

## 4. 信頼境界（利用者の裁定 ②）

設定画面から **子プロセスを起こせる**ようになるので、境界を 1 か所
（[`mcp_client_routes.py`](../../api/src/asterism_api/mcp_client_routes.py)）に集める。

| 規則 | 形 |
|---|---|
| **単一ユーザーモード限定** | `cfg.single_user` が偽か `cfg.appdata_root` が None なら `/api/mcp-servers/*` は 404（`_appdata_root_or_404` と同じ条件）。共有 api から他人の PC で子を起こす口にならない |
| **登録しただけでは動かない** | `enabled` の既定は false。画面に**コマンド全文**を出し、利用者が確かめてから有効化する。未有効のサーバには `tools`・`call`・`source-items` のどれも繋がない（409） |
| **コマンドは設定からだけ読む** | リクエストでコマンド・引数・URL を受ける口は無い。`settings.json` を書けるのは UI の `putAppSetting` だけで、api は読むだけ（サーバ側から `settings.json` を書かない） |
| **`/call` は allowlist 2 名** | `sourceProvider.list` と `.get` だけ。`add` は ✓ の後処理からだけ（403） |
| **`locate` は読むだけ** | `POST /api/mcp-servers/locate {command, candidates}` は実行ファイルの存在と実行可能の検査だけで、起動も保存もしない。`command` は名前（パス区切りを拒否）。単一ユーザー限定 |
| **起動を伴う経路は POST** | GET は状態を変えない前提（`local.py`）。`GET /api/mcp-servers` は設定の写しと直近の呼び出しを返すだけで繋がない |
| **子を残さない** | `keep_alive=False`。接続はリクエストごとに開いて閉じる。一覧は短く、bytes を運ぶ取得は長いタイムアウト |
| **秘密を設定に入れない** | `headers`／`auth` は保存前に拒否（`_strip_secrets` は `Authorization` を落とさないので平文で残ってしまう）。`env` の名前が秘密に見えるものは警告（api が保存時に黙って落とすため）。HTTP の認証ヘッダは v1 の範囲外 |
| **`/call` の引数も縛る** | 通す引数は §3 の入力だけ（`list`: `exts`/`folder`/`limit`・`get`: `id`/`inline`）。相手が `path` のような引数を解釈しても HTTP から任意のファイルを読ませる口にならない |
| **呼び出しを見せる** | 直近の呼び出し（サーバ・道具・結果の件数か理由・時刻）を設定画面に出す。中身（bytes・一覧の本文）は持たない |

HTTP の対応表: 未登録 404・設定が読めない 400・未有効 409・allowlist 外 403・繋げない 502・
道具の失敗と形違い 502（本文に道具の文言）・時間切れ 504。

既知の弱さ: 時間切れの判定は fastmcp の版で例外の型と code が違う（3.x は `McpError(408)`・4.x は `MCPError(-32001)`）ため、`TimeoutError` の連鎖と「timed out」の文言で見分けている。相手の道具が返した無関係なエラー文に「timed out」が入ると 504 に誤分類される。

不変条件: **出す側（`mcp_mount.py`・`mcp_write_tools.py`・`asterism_mcp.server`）から
`mcp_client` を import しない**。相手→Asterism→相手の輪を作らない。

## 5. 取り込み元の切り替え（A2）

- 新エンドポイント `POST /api/chat/sessions/{id}/attach-from-source {server, ids}` を
  `chat_routes.py` の中（`_start_attach_job` と同じクロージャ）に置き、`chat_attach_files`
  と同じ検査（表の拡張子・`_ai_ready`・容量上限・手番の追加）を共通化する。api が `get` で
  bytes を取り、`_start_attach_job(..., homes=[...])` → `run_attach` → `_stage_place_and_read`
  に `homes` を通し、`st`（`turn["attach_run"]`）にも保存する（📎 の再開で失われないため）。
- `POST /api/staging` に任意のフォーム項目 `source_homes`（JSON。名前→home）を足し、
  `create_staging` が **raw/ に保存した bytes から自分で SHA-256 を計算**して
  `meta["origins"]` に書く（呼び手の sha256 は信じない。ブラウザ経由の 📎 でも効く）。
- 形: `origins: [{name, sha256: "sha256:<hex>", home: {server, id, name} | {server: "local"}}]`。
  **`sources` は既存キー（表名の配列）で意味を変えない。新項目は `origins`**（同名にすると
  `str(dict)` で表を探して 404）。`create_staging` のローカル変数 `origins`（xlsx の派生 CSV→
  ブック名）と名前を衝突させない（フォーム引数名も `origins` にしない）。
- UI の 📎 は「この PC／<サーバ名> のファイル」の 2 択（`sourceHome` 未設定なら今のまま）。

## 6. dataset の `source_origins`（A2）

- `attach_source` が `_persist_source_uploads` の後に `registry.update_meta_atomic(...,
  {"source_origins": [...]})` で重ねる。取り込み（attach）時点の原本の記録で、再 attach
  （`rmtree` で置き換え）では置き換える。`mark_source_saved` は未知のキーを消さない。
- `recount_dataset` が staging を作るときは、dataset の `source_origins` のうち残っている
  ファイル名の分を staging の `origins` にコピーする（`recount_names` と同じ絞り方）。
- `append_rows` では更新しない。`resync_rows`（循環の ADR）は `source/` を全置換した後、
  置き換えたファイル名の行だけ sha256 を新しい中身で作り直す（home は付けない＝ローカル扱い）。
- **被りの通知**: staging 時に全 dataset の `source_origins[].sha256` を引き、一致があれば
  設計の返事に「この表は既にデータセット X の取り込み元です」と 1 行足す（設計は続行。
  注記に `dataset_id` を添えるが、v1 の画面は名前を出すだけで X へ飛ぶ導線は無い＝チャット
  だけの画面にはデータセットのページが無いため。`choice` は使わない: 未回答の選択がある手番は
  「公開」が隠れる）。ローカルの 📎 でも `origins`（`home: {server: "local"}`）が書かれるので
  単体でも効く。名前でなく中身で合流する。

## 7. 送り込み（A3・利用者の裁定 ③: ✓ の後・表だけ・既定オン）

- 置き場所は `chat_routes.py` の `_finish_attach`（`chat_confirm` がロックを出てから呼ぶ。
  `if commit:` が promote 成功の目印）。**📎 と ✓ は別の時**なので、bytes は staging でなく
  `registry/<id>/source/` の保存ファイルから読む。設計をやめた物（`discard_dataset`）は
  送られない。
- **ローカル原本の定義は 1 つ: `home` が無い、または `home.server == "local"`**。対象は
  そのうち表（`.csv .tsv .xlsx .xls`。json は送らない）。送る前に sha256 が `source_origins`
  と一致することを確かめ、`add({bytes_base64, name, mimeType})` を呼ぶ。返った `id` を
  `update_meta_atomic` で `source_origins[].home` に書き戻す（home 済み＝`server` が
  `"local"` 以外）。32 MiB 超は送らず通知。**失敗しても公開は取り消さない**（通知だけ）。
- 通知は表ごとに 1 行「「〈置いた名前〉」を 〈サーバ名〉 にも送りました／に既にありました（同じ中身）」
  （注記 `sent_home`）、送れなければ `send_failed`。短いロックで手番に足す。相手と注記と `home.name`
  に渡す名前は**利用者が置いた名前**（`meta.source_names`）で、保存名（`source-<hash>.csv`）ではない。
  繋げない／時間切れ／未有効は残りの表も同じなので 1 行で打ち切る（✓ の応答を件数ぶん待たせない）。
  **送れなかった原本を後から送り直す経路は v1 に無い**（✓ は手番ごとに 1 回。`home` は local のまま
  残るので、次の版で「送り直す」を足せる）。
- 設定 `sendOriginalsToHome`（既定 true）で切れる。**`add` の呼び手はここだけ**
  （`attach_source`・`_persist_source_uploads`・ingest・`resync_rows` からは呼ばない）。
  かんたんウィザードの公開は範囲外。

## 8. 設定の形（`appdata/settings.json`）

```jsonc
"mcpServers": {
  "<name>": {"transport": "stdio", "command": "/usr/local/bin/node", "args": ["…/graphium-mcp.mjs"],
             "env": {"GRAPHIUM_ROOT": "…"}, "enabled": false}
  //      | {"transport": "http", "url": "http://127.0.0.1:9999/mcp", "enabled": false}
},
"sourceHome": {"server": "<name>"} | null,
"sourceProvider": {"list": "", "get": "", "add": ""},
"sendOriginalsToHome": true
```

サーバ名は `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`（設定のキーであり URL のパスにも入る）。
秘密に見える名前は api が落とすので、サーバ名にも env の名前にも使えない。

## 9. 範囲外（別の ADR か次のセッション）

出典の本文を読む（Asterism の答えの出典から相手のノート本文を引く）／チャットの LLM に
外部の道具を渡す（差し込み点は demo-agent）／かんたんウィザードの公開からの送り込み／
相手のフォルダを「追う」追記／被りの通知からの「追記にする」切り替え／1 回ごとの「送らない」
選択／`add` の `path`・`folder`（v2）／`get` の `path` 直読み／HTTP サーバの認証ヘッダ／
保存場所の移動。

## 10. 制限（利用者に見せる）

- 相手の `add` が足した直後のファイルは、相手の索引に乗るまで（アプリの再起動まで）
  `list` にも相手の被り判定にも出ないことがある（相手の実装の制限）。
- stdio は利用者の PC に相手の実行環境（例: Node 20.16+ と相手のソースのビルド）が要る。
  デスクトップ版の Asterism は Node を同梱しない。URL 型なら不要。
- 32 MiB 超は取れない・送れない。json は送らない。`folder` は未対応。HTTP の認証なし。

## 11. `find_datasets` が `origins` を返す（2026-10-27）

§6 の `source_origins` は、これまで dataset の `meta.json` にしか無く、外へ出す MCP
（`/mcp`・`mcp-endpoint-on-the-app.md`）からは見えなかった。Graphium のチャット（Copilot）
に「在庫の表はどのファイルから来た？」と聞くと、`provenance_of` を 5 回引いて「不明」と
答えた（実機・`docs/examples/source-switch/README.md` の続き）。

`find_datasets` の各データセットに `origins` を足す（`ingest/src/asterism/catalog.py` の
`_origins`）。形は `meta.json` の `source_origins` と同じで、壊れた要素は 1 つずつ捨てる:

```json
"origins": [{"name": "stock.csv", "sha256": "sha256:254fb006…",
             "home": {"server": "graphium", "id": "b3824b93-…", "name": "stock.csv"}}]
```

新しい道具は足さない。AI はどの問いでも最初に `find_datasets` を呼ぶので、そこに載せれば
「どこから来た表か」は 1 回の呼び出しで分かり、`provenance_of`（IRI 1 つの来歴）と役割が
重ならない。道具の説明文にも「出どころの問いはここ」と書いた。§9 の「出典の本文を読む」
（相手のノート本文を引く）は引き続き範囲外。

