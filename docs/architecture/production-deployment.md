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
| caddy | 80/443 | **80/443** | TLS・`forward_auth`→authgate・`ui/dist` 静的配信・`/api`/`/jobs`→api・`/demo`→demo-agent |
| authgate | 9000 | なし | セッション Cookie ログイン（標準ライブラリのみ）。caddy が `/__auth/verify` を検証 |
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
- **サイト全体をセッション Cookie ログインで保護**（Private 完結＝登録利用者のみ）。caddy が
  `forward_auth` で全リクエストを `authgate`（`infra/authgate/`・標準ライブラリのみの極小サービス）の
  `/__auth/verify` に問い合わせ、**HMAC 署名付き・期限付き・HttpOnly・Secure・SameSite=Lax Cookie**を検証。
  **なぜ Basic 認証でないか**：SPA＋自己署名では**ブラウザ native Basic が更新/ナビ毎に再プロンプト**する
  （資格情報を保持しない）。Cookie は全リクエスト（ページ・fetch・SSE）に自動で乗り、IP/自己署名でも
  どこからでも動く。`/__auth/*`（ログイン画面・verify・logout）のみ非ゲート。失敗時ディレイでブルートフォース緩和。
- **api の write/設計ルートは `ASTERISM_API_TOKEN` で fail-closed**（`X-Asterism-Token` ヘッダ）。
  **caddy が「Cookie ゲートを通った＝認証済み」リクエストにこのトークンを `header_up` で注入**するので、
  ユーザーはトークンを UI に入れる必要がない（＝**ログイン1回が唯一の認証**）。トークンは caddy↔api の
  サーバ内共有秘密（`asterism.env`）でブラウザには出ない。`header_up` は SET なのでクライアント詐称も不可。
  Anthropic キーは別の `X-API-Key`（ユーザーが毎回持参・Ask/propose 用）。
- caddy が SPA・`/api`・`/demo` を**同一オリジン**で配るので、ブラウザ↔backend は CORS 不要。
- **store 境界**：oxigraph は `data` 網のみ。将来 Private Crucible が deploy する公開フロントは
  raw oxigraph でなく **api の read-only `/api/sparql`** を読む（`ASTERISM_EXPOSE_RAW_SPARQL` 既定 open）。

## シークレット / 設定（`asterism.env`）

`asterism.env`（gitignore・テンプレ = `asterism.env.example`）を **`env_file` でコンテナに注入**する。
**落とし穴**：新しい Compose は **env_file の値も `${}` 補間する**ので、値に `$` があると変数参照と誤解される
（`$`→`$$` でエスケープ）。必須：`ASTERISM_API_TOKEN`（未設定で write 系 503）・`ASTERISM_GATE_PASSWORD`
（ログインパスワード）・`ASTERISM_GATE_SECRET`（Cookie 署名 HMAC 鍵）。いずれも `openssl rand -hex 32` 等
**`$` を含まない値**にすればエスケープ不要。

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

# 2. シークレット生成（強乱数を自動充填し chmod 600 で asterism.env を作る）
#    引数 = clients が使うホスト（サーバ IP で自己署名／FQDN なら実証明書）。
#    ログインパスワードと API トークンが端末に表示されるので保存すること。
./scripts/init-secrets.sh 203.0.113.10
#    実証明書にする場合: ASTERISM_TLS_EMAIL=you@example.com ./scripts/init-secrets.sh asterism.example.com
#    （手で作る場合は cp asterism.env.example asterism.env して編集でも可）

# 3. 起動（docling のビルドはモデル DL 込みで初回数分かかる）
docker compose -f compose.prod.yaml up -d --build

# 4. 確認（NAT ヘアピンを避け --resolve で自ホストを叩く）
#    ホスト名は必ず asterism.env の ASTERISM_DOMAIN と一致させる（理由は下の罠）。
docker compose -f compose.prod.yaml ps
HOST=$(grep -oP '^ASTERISM_DOMAIN=\K.*' asterism.env)
curl -sk --resolve "$HOST:443:127.0.0.1" "https://$HOST/"   # -> 302 (/__auth/login へ)
```

### 更新デプロイ（2 回目以降）

```bash
cd ~/asterism
git pull --ff-only origin main
docker compose -f compose.prod.yaml up -d --build   # 変更のない層はキャッシュで即終了
docker compose -f compose.prod.yaml ps              # 全コンテナ Up を確認
docker logs --since 5m asterism_prod_api | grep -iE 'error|traceback' # 空なら健全
```

> **⚠️ 確認時の罠：`--resolve` のホスト名が `ASTERISM_DOMAIN` と違うと「壊れて見える」。**
> Caddy の site block は `ASTERISM_DOMAIN` 1 つ。別の名前（例：FQDN 運用中にサーバ IP）で
> 叩くと TLS SNI が site に一致せず、**全パスが `200` かつ `content-length: 0`** で返る
> （caddy のアクセスログは `"msg":"NOP"` / `"status":0`）。SPA も 404 も出ないので
> *デプロイでフロントが壊れた* ように見えるが、実際はホスト名違いなだけ。
> **健全な応答は未ログイン時 `302` → `location: /__auth/login`**（`/__auth/login` が `200`）。
> 迷ったら `docker logs asterism_prod_caddy | grep NOP` ── 出ていればホスト名違い。

### コンテナ内から直接叩く（API の切り分け）

authgate を経由せず api だけを確かめたいとき。**api の待受は `8080`**（`8000` ではない）。
`docker exec` は **`-i` を付けないと標準入力が渡らず**、heredoc が無反応で終わる。

```bash
docker exec -i asterism_prod_api python3 - <<'PY'
import os, json, urllib.request
req = urllib.request.Request("http://127.0.0.1:8080/api/datasets")
req.add_header("X-Asterism-Token", os.environ.get("ASTERISM_API_TOKEN", ""))
with urllib.request.urlopen(req, timeout=30) as r:
    print(r.status, len(json.loads(r.read()).get("datasets", [])), "datasets")
PY
```

## 検証（本番機で end-to-end）

1. ブラウザで `https://<ASTERISM_DOMAIN>/` → `/__auth/login` にリダイレクト →
   ログイン（authgate のセッション Cookie）→ UI 表示。
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
- authgate のセッション Cookie ログインは MVP。組織 SSO/forward-auth へ寄せる余地あり。
- root 実行（upload-api イメージのコメント参照）の非 root 化は別途。
