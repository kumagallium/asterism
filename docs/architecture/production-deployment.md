# 本番デプロイ構成（Private 完結・1箱バンドル）

Asterism を「本番と同じように運用」するための構成。`compose.prod.yaml` とその周辺
（`infra/caddy/`・`asterism.env`）の決定記録。実行手順も兼ねる。

## 方針：1箱 = 配布できる Asterism インスタンス1つ

ユーザー（DB 提供側）に Asterism サーバーを渡すときの**管理対象を増やさない**ため、
**1台に製品一式＋Private Crucible を載せた自己完結ユニット**を「1デプロイ」とする。
組織横断の【限定公開】Crucible（外部多者・国外ツール）は per-user 配布物ではなく
**別ホスト（crucible-prod）に隔離**するプラットフォーム共通基盤。本 ADR は前者を扱う。

```
                         Internet (443 / 80)
                              │
                    ┌─────────▼──────────┐
                    │  caddy             │  TLS + 全サイト Basic 認証 + SPA + ルーティング
                    │  (唯一の公開面)     │  ← ここだけがホストポートを持つ
                    └───┬───────────┬────┘
              /api,/jobs │           │ /demo
                         ▼           ▼
                    ┌────────┐  ┌────────────┐
              web ──│  api   │  │ demo-agent │── web
                    └───┬────┘  └─────┬──────┘
                        │  data       │ data
              ┌─────────┼─────────────┼──────────┐
              ▼         ▼             ▼          (caddy は data 網に居ない)
        ┌──────────┐ ┌──────────┐
        │ oxigraph │ │ docling  │   ← どちらも内部のみ・ホストポートなし
        │ (store)  │ │ (PDF ML) │
        └──────────┘ └──────────┘
```

## サービスとポート

| サービス | コンテナ内 | 公開 | 役割・接続 |
|---|---|---|---|
| caddy | 80/443 | **80/443** | TLS・Basic 認証・`ui/dist` 静的配信・`/api`/`/jobs`→api・`/demo`→demo-agent |
| api（asterism-api） | 8080 | なし | フルワークベンチ＋watcher。`CSV2RDF_OXIGRAPH_URL`・`ASTERISM_DOCLING_URL` |
| demo-agent | 8090 | なし | 接地 Ask。registry を read 共有・Anthropic キーは毎リクエスト持参 |
| oxigraph | 7878 | **なし** | SPARQL ストア。認証なし＝**絶対に公開しない**。`oxigraph-data` volume |
| docling | 8090 | なし | PDF→構造の ML サイドカー（CPU torch・OCR オフ・同時1）。落ちても非PDFは無影響 |

ネットワーク2枚（`web`・`data`）で **oxigraph/docling を公開面（caddy）から隔離**する。

## TLS / 認証モデル

- **TLS は env 駆動**（`infra/caddy/Caddyfile`）。`ASTERISM_DOMAIN` に**サーバ IP**を入れると
  その IP の自己署名証明書（DNS 不要・即起動・ブラウザ警告）、**FQDN**を入れ `ASTERISM_TLS_EMAIL`
  をメールにすると Let's Encrypt。自己署名→実証明書の切替は**env 2本のみ・ファイル編集不要**。
  Caddy グローバル `default_sni {$ASTERISM_DOMAIN}` が必須＝**IP アクセスは TLS が SNI を送らない**ため、
  これが無いとハンドシェイクが `internal error` で落ちる。
- **サイト全体を Basic 認証で保護**（Private 完結＝登録利用者のみ）。`basic_auth`。
- **api の write/設計トークンは別ヘッダ `X-Asterism-Token`**（`Authorization: Bearer` も可）
  ＝ caddy の `Authorization: Basic` と**衝突しない**。SPA は [`ui/src/authToken.ts`](../../ui/src/authToken.ts) で
  `X-Asterism-Token` を送る。Anthropic キーは `X-API-Key`。3ヘッダが分離している。
- caddy が SPA・`/api`・`/demo` を**同一オリジン**で配るので、ブラウザ↔backend は CORS 不要。
- **store 境界**：oxigraph は `data` 網のみ。将来 Private Crucible が deploy する公開フロントは
  raw oxigraph でなく **api の read-only `/api/sparql`** を読む（`ASTERISM_EXPOSE_RAW_SPARQL` 既定 open）。

## シークレット / 設定（`asterism.env`）

`asterism.env`（gitignore・テンプレ = `asterism.env.example`）を **`env_file` でコンテナに注入**する。
**落とし穴**：新しい Compose は **env_file の値も `${}` 補間する**ので、bcrypt ハッシュ中の `$` は変数参照と誤解され壊れる。
→ **ハッシュは `$`→`$$` にエスケープして書く**（Compose が `$$`→`$` に戻す）。生成と同時にエスケープ:
`docker run --rm caddy:2-alpine caddy hash-password --plaintext 'PW' | sed 's/\$/$$/g'`。
必須：`ASTERISM_API_TOKEN`（未設定で write 系 503）・`ASTERISM_BASIC_AUTH_HASH`。
トークンは `$` を含まない値（`openssl rand -hex 32`）にすればエスケープ不要。

## 永続データ（volumes）

| volume | 内容 |
|---|---|
| `oxigraph-data` | SPARQL ストア（昇格済みグラフ等） |
| `asterism-data` | `/data/sources`：registry・取り込み元・jobs.jsonl・append inbox |
| `caddy-data` / `caddy-config` | ACME 証明書 / ローカル CA（再起動で証明書を再取得しないため） |

## ビルド / デプロイ手順（x86_64 ホスト上で）

> **arch 注意**：api/docling は amd64 wheel（CPU torch 等）を焼く。**arm64 ノートでビルドしない**。
> 本番機（asterism-prod = Ubuntu 22.04 / amd64）で直接 build & up する。

```bash
# 1. コードを本番機へ（公開リポジトリなので clone でよい）
git clone https://github.com/kumagallium/asterism.git
cd asterism && git checkout <この構成のブランチ/タグ>

# 2. 設定
cp asterism.env.example asterism.env
#   - ASTERISM_API_TOKEN を openssl rand -hex 32 で
#   - ASTERISM_BASIC_AUTH_HASH を `docker run --rm caddy:2-alpine caddy hash-password --plaintext '...'` で
#   - （ドメインがあれば）ASTERISM_DOMAIN / ASTERISM_TLS_EMAIL を設定
$EDITOR asterism.env

# 3. 起動（docling のビルドはモデル DL 込みで初回数分かかる）
docker compose -f compose.prod.yaml up -d --build

# 4. 確認
docker compose -f compose.prod.yaml ps
curl -k https://localhost/api/health    # caddy 越し（Basic 認証が要るので -u も）
```

## 検証（本番機で end-to-end）

1. ブラウザで `https://<host>/` → Basic 認証 → UI 表示。
2. 設定で `ASTERISM_API_TOKEN` を入力 → CSV/JSON を設計→materialize→ingest→promote。
3. **PDF をアップロード → docling 変換 → 文まで構造化 → 引用**（PDF 経路の本番確認）。
4. Ask（Anthropic キー入力）で接地回答＋使用 SPARQL 開示。
5. `docker compose restart` 後もデータ（昇格済み・証明書）が残ること。

## Private Crucible（次フェーズ）

製品が回ってから、**同一ホスト上で Crucible 自前 compose を隣接起動**（別プロダクトなので
本 compose に混ぜない）。共有 Docker 網で api と疎通し、deploy する公開フロントは
`/api/sparql` を読む。外部多者向けの【限定公開】Crucible は別ホスト（crucible-prod）へ。

## 既知の留意点 / TODO

- oxigraph/イメージは現状 `:latest`。本番は**タグ固定**が望ましい（追って pin）。
- 全 starrydata 数百万トリプルの一括取り込みは対話 ingest 向きでない（本番バッチ側）。
- Basic 認証は MVP。組織 SSO/forward-auth へ寄せる余地あり。
- root 実行（upload-api イメージのコメント参照）の非 root 化は別途。
