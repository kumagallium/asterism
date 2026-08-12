# ADR: ローカルファースト配布 — 同一ソフトの 3 つの置き場所と、その間の受け渡し

状態: **着手**（2026-08-12）— Phase 1（`asterism-local`）を本 PR で実装。Phase 2（デスクトップシェル）/ Phase 3（snapshot exchange）は本 ADR を設計の正として後続。

関連: [`instance-iri-base.md`](instance-iri-base.md)（IRI の所有と `.invalid` 既定）/
[`store-mcp-split.md`](store-mcp-split.md)（トポロジ A/B・露出プロファイル・publish=Crucible 橋渡し）/
[`production-deployment.md`](production-deployment.md)（ホスト型 1-box の正）/
[`crucible-registration.md`](crucible-registration.md)（Crucible 登録の手動レシピ）/
[`kantan-mode-two-tier-ux.md`](kantan-mode-two-tier-ux.md)（研究者ひとりで完走する UX）。

## 0. 一行サマリ

Asterism は 1 つのソフトのまま **手元（ラップトップ）／常時稼働（組織サーバー・共有ホスト）** のどこにでも置け、手元は「作る場所」・常時稼働は「引用できる置き場」と役割分担する。Phase 1 は `asterism-local` ＝ Docker なし 1 コマンドのループバック専用起動。

## 1. 文脈

サポートされるインストールは `docker compose up`（README）で、体験するにはサーバーが要る。
ホストすると VPN/パスワードの壁で共有しづらく、なにより主要ユーザー（未公開の実験データを持つ研究者）は**データを外部サーバーへアップロードできない**。一方で実装はもともとローカル寄りに出来ている:

- api は既定 127.0.0.1 バインド・完全シングルテナント・全パス env 化（`main.py` `Settings`）。
- ホスト運用の関心事（TLS・ログイン・SPA 配信・トークン注入）は caddy+authgate 層に隔離済み。
  **FastAPI に SPA を配信するコードは無い**（`ui/vite.config.ts` の「prod は FastAPI が配信 (M0c)」コメントは stale）。
- ストアは外部 Oxigraph HTTP サーバ（組み込みモード無し）。
- ローカル LLM は openai-compatible クライアントで対応済み（Ollama / LM Studio を docstring で名指し）。

## 2. 決定: 3 つの置き場所は「同一ソフトの構成違い」

| 置き場所 | 役割 | 実体 |
|---|---|---|
| 手元（ローカルモード → 将来デスクトップシェル） | **作る場所**。未公開データを外に出さず設計・取り込み・検証 | `asterism-local`（本 ADR Phase 1） |
| 常時稼働（組織サーバー / 共有ホスト） | **引用できる置き場**。安定 IRI・`/describe`・SPARQL/MCP を常時応答 | 既存 `compose.prod.yaml`（変更なし） |
| Crucible | その上の**登録簿**。エンドポイント+メタデータのみ、データは持たない | 既存 ADR（store-mcp-split §7 / crucible-registration） |

手元は電源が落ちる＝公開の窓口にはなれない。「引用できる事実」は常時稼働層に置いてはじめて引用できる。**3 层は別プロダクトではなく、同じコードベースの配布形態**（README の設計原則 "Self-hostable, single deployment" の延長）。

### 用語の整理（既存 2 軸に第 3 の軸を追加）

| 軸 | 意味 | 決定の場所 |
|---|---|---|
| promote（昇格） | インスタンス**内**で draft→canonical（引用可能化） | 既存（lifecycle ADR） |
| publish（外部公開） | MCP フロントを外部に出す（Asterism→Crucible 橋渡し） | 既存（store-mcp-split §7） |
| **exchange（受け渡し）** | **インスタンス間**でデータセット版を移す（手元→常時稼働、NAS 経由共有） | **本 ADR §5（設計のみ）** |

## 3. 決定: ローカルモード `asterism-local`（Phase 1・本 PR）

新モジュール `api/src/asterism_api/local.py` + console script。**`main.py` は無改修** — `build_app` が返す app を外から装飾する。

1. **ユーザーデータディレクトリ**が既定（macOS `~/Library/Application Support/Asterism`、Linux XDG、Windows `%APPDATA%`。`--data-dir` / `ASTERISM_LOCAL_HOME` で変更）。compose の `/data/sources` 既定のままだと laptop では lifespan 冒頭の `ensure_dirs()` が `PermissionError` で死ぬ＝env 既定値は化粧ではなく必須。
2. **書き込みトークンの自動発行**（`<home>/write_token`、0600）と、**ループバック限定のサーバー側注入 middleware**。本番 caddy の `header_up X-Asterism-Token` のプロセス内等価物で、**replace 意味論**（クライアント発の `Authorization`/`X-Asterism-Token` を先に落とす。`require_write_auth` は `Authorization` 優先なので、迷い込んだ Bearer が注入トークンを影に隠すため）。ユーザーはトークンを見ない・貼らない。
3. **`--host` は意図的に無い**。ループバック注入と広いバインドは両立しない（LAN の誰でも書けることになる）。LAN 共有をしたければそれは常時稼働層（compose）の仕事。
4. **SPA 配信**は caddy の分割を再現: `/assets/*` のミスは**素の 404**（`vite:preloadError` の stale-chunk 自己修復が index.html フォールバックで壊れる — 2026-07 の実障害）、それ以外は index.html フォールバック（SPA はハッシュルーティングなのでこれで十分）+ `Cache-Control: no-cache`。`/api/*`・`/jobs`・`/health`・`/describe`・`/upload/*` は先に登録済みなので影にならない。`ui/dist` が無ければ API のみで起動し、ビルド方法を案内。
5. **Oxigraph 子プロセス**: `oxigraph serve --location <home>/oxigraph_store --bind 127.0.0.1:<空きポート>`（固定 argv・シェル無し・ログは `<home>/logs/oxigraph.log`・終了時 terminate→kill）。バイナリ探索は `ASTERISM_OXIGRAPH_BIN` → PATH。見つからなければ**インストール手順つきの明確な失敗**（brew / cargo / releases）。既に動いているストアがあれば `--oxigraph-url` / `CSV2RDF_OXIGRAPH_URL` で子プロセスを起こさない。
6. **env 既定（`setdefault`＝ユーザー設定が勝つ）**: データパス一式のほか
   `ASTERISM_EXPOSE_RAW_SPARQL=1`（単一ユーザーのループバック箱は露出プロファイルの「同居・開」端。SPARQL ビューは製品の一部）と
   `ASTERISM_ALLOW_PRIVATE_LLM_BASE=1`（ローカル Ollama/LM Studio をモデル一覧に出す）。
7. **`asterism_api.main` の import は env 設定の後**（`ASTERISM_MAX_UPLOAD_BYTES` が import 時読みのため）。

### 縮退（すべて既存の明示 4xx に乗る）

- Docling 未設定 → PDF のみ 4xx（既存）。pandoc 無し → .docx のみ 4xx(既存)。
- morph-kgc 未 install → materialize/ingest が失敗するため、extras `asterism-api[local]`（= `asterism-ingest[substrate]` + `asterism-mcp-tools`）を用意。
- **Ask は実接続**（Phase 1 追補で実装）: demo-agent を子プロセス起動（`uvicorn app:app --app-dir demo-agent`・空きポート・loopback）し、`/demo/*` を同一オリジン中継（本番 caddy `reverse_proxy /demo/*` の等価物）。中継は Ask 契約ヘッダ（`X-API-Key`/`X-LLM-*`）だけを転送し、**注入された `X-Asterism-Token` は子に渡さない**。`/demo/ask` は内部タイムアウト無しの LLM ループがあり得るため read timeout 無制限。子の `/health` は `/demo/*` 外で api 自身の `/health` と衝突するため中継せず、親が直接ポーリング。SPA は `VITE_DEMO_MODE=live VITE_DEMO_AGENT_URL=/` でビルド（本番イメージと同じ）。mcp 依存が無い/子が起動しない場合は警告して Ask のみ縮退（`--no-ask` で明示無効化も可）。

### セキュリティ意味論

「ローカルマシンに居る者＝所有者」モデル。127.0.0.1 バインド + ループバック注入 + umask 0o077（`asterism-api` と同じ private-by-default at-rest）。これは本番の「caddy セッションの内側＝トークン注入」と同じ信頼構造を 1 台に畳んだもの。

## 4. IRI — 新しい決定は無い（既存 ADR がそのまま効く）

[`instance-iri-base.md`](instance-iri-base.md) の設計はローカルモードのためにあったかのように噛み合う:

- 手元の既定は `ASTERISM_IRI_BASE` 未設定 = **`https://asterism.invalid`**。RFC 2606 により「まだ公開の住所を持たない」ことが識別子自身に書いてある。ゼロ設定の試用はこのままで正しい。
- **出版する瞬間が実ベースを決める瞬間**（既存 ADR の文言どおり）。公開先が最初から分かっているなら、手元でも `ASTERISM_IRI_BASE=<公開先のベース>` を設定してから設計する＝exchange 時のリベースが不要になる（推奨）。
- IRI 不変性も既存のまま: ベース変更は以後の設計にのみ効く。既に**引用された** IRI は書き換えない。

## 5. 設計骨子: snapshot exchange（Phase 3・本 PR では実装しない）

実装は後続だが、取り決めをここに固定する（部品は全て既存: `sparql_construct` の Turtle ダンプ / Graph Store POST / append の冪等化 / 平文レジストリ / 版グラフ+`liveGraph` ポインタ）。

- **単位 = データセットの版**。スナップショット = ①canonical グラフの Turtle ダンプ ②レジストリ一式（meta/model/mie/mapping/diagram + 履歴）③マニフェスト（dataset id・slug・**発行元インスタンスと `iri_base`**・版・トリプル数・ハッシュ）。不変・追記オンリー。
- **受け入れ側**が新データセット（または既存の新版）として取り込み、`data_seq` は**受け入れ側で採番**（単調性はインスタンス内不変条件のまま）。
- **IRI の跨ぎ方**: マニフェストの `iri_base` が受け入れ側と同じならそのまま。`.invalid`（未公開）や別ベースなら、`…/datasets/<slug>/(ontology#|resource/)` 形を受け入れ側ベースへ**決定論リベース**（K13 `normalize_dataset_namespace` が設計に対して既にやっていることの、データグラフへの拡張。prefix 置換で機械的に可能）。**公開済みベースの IRI はリベースしない**（引用の同一性）。
- **NAS 共有は同じスナップショットの置き先違い**。ライブの data root を SMB/NFS で複数人共有するのは**不可**（RocksDB は単一 LOCK ライタ・`meta.json` はロックレス RMW・watcher は inotify で遠隔書込を見ない）。共同編集が要るなら常時稼働層を 1 つ立てる。
- store-mcp-split §7 の publish（MCP フロント公開）とは**別物**。exchange はデータが動く、publish はエンドポイントが公開される。

## 6. 検証（本 PR）

- 単体（`api/tests/test_local.py`）: 注入 middleware（追加・strip・非ループバック不注入・`require_write_auth` を素通しで通る end-to-end・非ループバックは 401 のまま）／SPA 分割（`/assets` 実 404・fallback no-cache・`/health` `/jobs` 非遮蔽）／env 既定・トークン 0600 冪等／oxigraph 不在の明確な exit 2。
- 実機: `asterism-local`（実 Oxigraph +ビルド済み SPA）でブラウザ起動 → `/health` ok → トークン無しの書き込み系呼び出しがループバックから通ることを確認。
- 副次修正: 4 パッケージの `readme = "../README.md"`（プロジェクト外パス）を撤去 — 最新 hatchling が拒否するため**新規 venv 作成（=CI）が repo 全体で壊れていた**。

## 7. スコープと残

- **Phase 2（デスクトップシェル）**: **v1 実装済 = `desktop/`（Tauri v2）**。シェルの契約は 1 つだけ＝`asterism-local` を spawn（起動器が Oxigraph/demo-agent の孫を監督）→空きポートの HTTP readiness を待つ→そのループバック URL でネイティブウィンドウを開く。終了時は **SIGTERM**（SIGKILL は孫をみなしごにする）。起動器の解決順= `ASTERISM_LOCAL_CMD` → 実行ファイルから祖先を遡って `api/.venv/bin/asterism-local`（`tauri dev` と repo 内ビルドの .app を両カバー）→ PATH。バックエンドログは app log dir（`~/Library/Logs/com.kumagallium.asterism/backend.log`）。**残**= Python ランタイム/oxigraph バイナリ/SPA の自己完結バンドル（Graphium の sidecar 配置 `externalBin`+`resources` を踏襲）・署名/公証/updater（Graphium の tauri-action 構成と同一 secret 名を流用・workflow 追加は別 PR＝workflow-only PR は CI が回らない罠）・Docling のオプショナルダウンロード・`ASTERISM_DATASETS_ROOT` と demo-agent/app.py の wheel 同梱解決。※Ask 実接続は Phase 1 追補で実装済（§3）。
- **Phase 3（exchange 実装)**: §5 のマニフェスト形式の確定・export コマンド・import 経路（append 冪等化の再利用）・かんたん UI への「書き出す/受け取る」導線。
- 残課題（本 ADR の外）: SSRF ガードの非対称（モデル一覧のみ private 拒否・ジョブ経路は素通し）の整理／env 名 `CSV2RDF_*`→`ASTERISM_*` 統一（既存の残課題）。
