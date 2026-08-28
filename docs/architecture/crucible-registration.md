# Crucible 登録レシピ — typed MCP フロントを外部 AI クライアントへ出す

[Crucible](https://github.com/kumagallium/Crucible) は GitHub URL を貼ると **clone → build →
deploy → SSE/HTTP 公開**まで自動でやる self-hosted なツール管理基盤。Asterism を Crucible に
登録すると、Claude Code / Cursor / Graphium などから discoverable な MCP サーバーとして
使えるようになる。

登録するのは **`infra/asterism` の typed MCP フロント 1 つだけ**。store（Oxigraph）は
Crucible に載せない — 稼働中の Asterism 本番スタックのものを内部ネットワーク越しに読む。

> 旧版（〜2026-08）はこのレシピを `infra/togomcp`（Phase 1/2 の togomcp wrapper）で書いていた。
> 現在の製品は typed MCP フロントを持つので、そちらは対象外。

---

## 全体像

```
┌─ asterism-prod（1 箱バンドル・ADR production-deployment.md）─────────────┐
│                                                                          │
│  ┌── compose #1: Asterism 本番スタック ──────────────────────────┐      │
│  │  caddy(443) → api / demo-agent / authgate                     │      │
│  │  ┌─ network: asterism-prod_data（外部公開なし）──────────┐   │      │
│  │  │   oxigraph:7878   ← store。ここが唯一の真実            │   │      │
│  │  └────────────────────────────────────────────────────────┘   │      │
│  └───────────────────────────────────────────────────────────────┘      │
│                              ▲                                           │
│                              │ SPARQL（内部ネットのみ）                  │
│  ┌── compose #2: Crucible ───┼───────────────────────────────────┐      │
│  │  registry-ui(8081) / registry-api(8080) / socket-proxy        │      │
│  │            │ deploy                                            │      │
│  │            ▼                                                   │      │
│  │  ┌─ network: mcp-net ──────────────────────────────────┐      │      │
│  │  │  asterism（typed MCP front・container:8000）         │──────┼──────┘
│  │  │  ASTERISM_EXPOSE_RAW_SPARQL=false                    │      │
│  │  └──────────────────────────────────────────────────────┘      │
│  └────────────────────────────────────────────────────────────────┘      │
│                              │ 127.0.0.1:<割当ポート>/mcp                │
└──────────────────────────────┼───────────────────────────────────────────┘
                               │ SSH ポートフォワード
                               ▼
                      AI クライアント（Claude Code / Graphium ほか）
```

**compose は 2 枚**（Asterism 本番と Crucible は別プロダクト・混ぜない）。同一ホストに
隣接させ、deploy されたフロントだけを両方のネットワークに載せる。

---

## 前提

- Asterism 本番スタックが `compose.prod.yaml` で稼働している（`docs/architecture/production-deployment.md`）
- Docker / Docker Compose が入っている
- ホストの **8080 / 8081 が空いている**（Asterism 本番は 80/443 のみ host 公開。api の 8080 は
  コンテナ内部ポートなので衝突しない）

> ⚠️ **稼働中のホストで Crucible の `setup-server.sh` を実行しないこと。** クリーンな Ubuntu を
> 前提に SSH ポート変更・ファイアウォール・Docker iptables まで書き換えるので、既存の caddy や
> SSH セッションを壊す。隣接起動では compose だけを手で立てる（下記手順 1）。

---

## 1. Crucible を隣接起動する

Crucible は private リポジトリなので、手元のクローンから `registry/` だけを転送する。

```bash
# 手元（開発機）から
rsync -az \
  --exclude 'api/.venv' --exclude 'ui/node_modules' --exclude 'ui/.next' \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.env' \
  ~/Crucible/registry/ asterism-prod:~/crucible-registry/
```

サーバー側で `.env` を作る（**キーはサーバー上で生成する**）。

```bash
cd ~/crucible-registry
API_KEY=$(openssl rand -hex 32)
ENC_KEY=$(openssl rand -hex 32)
cat > .env <<EOF
CRUCIBLE_HOST=127.0.0.1
CRUCIBLE_API_PORT=8080
CRUCIBLE_UI_PORT=8081
CRUCIBLE_BASE_URL=http://127.0.0.1
REGISTRY_API_KEY=${API_KEY}
TOKEN_ENCRYPTION_KEY=${ENC_KEY}
AUTO_UPDATE_INTERVAL=3600
MCP_NETWORK=mcp-net
EOF
chmod 600 .env

docker network create mcp-net          # deploy 先ネットワーク
docker compose up -d --build           # ui / api / socket-proxy の 3 つ
```

- `CRUCIBLE_HOST=127.0.0.1` なので UI/API もデプロイ先も**ループバックのみ**。外から見るには
  SSH ポートフォワードを使う: `ssh -L 8081:127.0.0.1:8081 asterism-prod`
- **Dify は使わない。** `DIFY_EMAIL` / `DIFY_PASSWORD` を未設定にしておけば deployer は
  「Dify 登録をスキップします」とログを出して素通りする。`docker-compose.dify.yml` も使わない。

---

## 2. Asterism を登録する

Crucible UI（`http://127.0.0.1:8081`）の登録フォーム、または API から。

| Field | Value |
|---|---|
| `github_url` | `https://github.com/kumagallium/asterism`（public なのでトークン不要） |
| `branch` | `main` |
| `subdir` | `infra/asterism` |
| `transport` | `auto`（Dockerfile に `EXPOSE` があるので HTTP 扱い） |
| `dify_auto_register` | `false` |
| `env_vars` | 下表 |

| env | 値 | 意味 |
|---|---|---|
| `CSV2RDF_OXIGRAPH_URL` | `http://asterism_prod_oxigraph:7878` | store の在り処。既定は `http://oxigraph:7878`（本番 compose のサービス名と同じ）なので省略しても当たるが、明示する |
| `ASTERISM_EXPOSE_RAW_SPARQL` | `false` | **任意 SPARQL の escape を出さない**。typed tool だけを公開する |
| `ASTERISM_BUNDLED_TOOLS` | `1` | 同梱データセット（starrydata ほか）の宣言クエリツールを公開する。省略時は出ない（下記「宣言クエリツール」参照） |

```bash
KEY=$(grep '^REGISTRY_API_KEY=' ~/crucible-registry/.env | cut -d= -f2)
curl -X POST http://127.0.0.1:8080/api/servers \
  -H "X-API-Key: ${KEY}" -H 'Content-Type: application/json' \
  -d '{
    "github_url": "https://github.com/kumagallium/asterism",
    "branch": "main",
    "subdir": "infra/asterism",
    "transport": "auto",
    "dify_auto_register": false,
    "auto_update": false,
    "env_vars": {
      "CSV2RDF_OXIGRAPH_URL": "http://asterism_prod_oxigraph:7878",
      "ASTERISM_EXPOSE_RAW_SPARQL": "false",
      "ASTERISM_BUNDLED_TOOLS": "1"
    }
  }'
```

202 と `job_id` が返る。進捗は `GET /api/jobs/{job_id}`（同じ `X-API-Key` が要る）。

### この 2 点は Crucible 側の固定仕様

`infra/asterism/` はこれに合わせてある。**他の Dockerfile を足すときも同じ制約がかかる。**

1. **コンテナ側ポートは 8000 固定** — deployer は `-p <host-port>:8000` で公開し、`/mcp` を
   そこに向けてヘルスチェックする。`infra/asterism/Dockerfile` は `EXPOSE 8000` /
   `CMD --port 8000`。CLI 既定の 8002 のままだと build は通るのにヘルスチェックで
   タイムアウトする（コンテナは動いているのに registry 上は error 表示になる）。
2. **`mcp.json` は subdir の中を見る** — repo root の `mcp.json` は `subdir` 指定時には
   読まれない。`infra/asterism/mcp.json` を置いてある。無い場合は登録リクエストで `name` を
   明示すれば deployer が生成して進む。

---

## 3. store に繋ぐ

deploy されたコンテナは `mcp-net` にだけ載る。store は別ネットワーク（`asterism-prod_data`）に
あるので、**1 回だけ明示的に接続する**。

```bash
docker network connect asterism-prod_data asterism
```

これを「運用側が明示的に許可する 1 手」として残してある。`MCP_NETWORK` を
`asterism-prod_data` にすれば自動化できるが、**Crucible が deploy する全コンテナが store と
同じネットワークに載る**ことになるので採らない（blast radius を広げない）。

再 deploy（`auto_update` や手動 update）のたびにコンテナは作り直されるため、**接続もやり直す**。

---

## 4. AI クライアントから使う

```bash
# 手元から SSH ポートフォワード（<port> は Crucible UI が表示する割当ポート）
ssh -L 8100:127.0.0.1:8100 asterism-prod

# Claude Code
claude mcp add --transport http asterism http://127.0.0.1:8100/mcp
```

FastMCP の HTTP transport はパス `/mcp`。SSE ではなく Streamable HTTP。

---

## 5. 動作確認

```bash
docker exec asterism python -c "import urllib.request;print(urllib.request.urlopen('http://asterism_prod_oxigraph:7878/query?query=ASK%20%7B%3Fs%20%3Fp%20%3Fo%7D',timeout=8).status)"
# → 200 なら store に届いている
```

MCP 側は `tools/list` を叩いて**出ているツールの顔ぶれ**を見るのが早い。`sparql_query` が
無ければ露出プロファイルが効いている。`schema_summary`（引数なしで呼べる）を実行すると、
store に実際に入っているクラスと件数が返る。

---

## 露出プロファイル

`ASTERISM_EXPOSE_RAW_SPARQL=false` のとき、公開されるのは以下だけ:

| ツール | 引数 | 用途 |
|---|---|---|
| `schema_summary` | （なし） | store の語彙・件数を introspect。**starrydata を仮定しない** |
| `template_curve_fetch` | `curve_iri` **必須** | 曲線の x[]/y[] と単位・サンプル IRI |
| `provenance_of` | `iri` **必須** | PROV チェーン（curve → sample → paper → digitization → ingestion） |

`sparql_query`（任意 read-only SPARQL の escape）は**登録されない**。ADR
`store-mcp-split.md` の「controlled exposure」— 機微な store は raw SPARQL の面を出さず、
vet 済みの typed tool だけを渡す、という姿勢の実体がこれ。

### 宣言クエリツール（検索の入口）

`template_curve_fetch` / `provenance_of` は **IRI を必須引数に取る**。IRI を探す手段が無いと
自然文の問いから始められないので、検索系は宣言クエリツール（`query_tools.yaml`）で足す。
供給源は 2 つ:

| 供給源 | 有効化 | 中身 |
|---|---|---|
| repo 同梱 `datasets/<name>/query_tools.yaml` | `ASTERISM_BUNDLED_TOOLS=1` | starrydata の `property_ranking` / `sample_search` など。デモ・開発用 |
| ワークベンチ registry `<root>/<id>/query_tools.yaml` | `CSV2RDF_REGISTRY_ROOT=<path>` | ユーザーがカタログで設計・昇格したデータセットのツール |

**後者は Crucible 経由の deploy では現状使えない**（下記「既知の制約」1）。ユーザー設計
データセットは `schema_summary` には現れる（store に入っているので）が、専用の検索ツールは
出ない。

---

## 既知の制約

1. **Crucible は volume mount を支援しない。** そのため `CSV2RDF_REGISTRY_ROOT` にワークベンチの
   registry ディレクトリを渡せず、**ユーザー設計データセットの宣言クエリツールは公開できない**。
   同梱ツール（`ASTERISM_BUNDLED_TOOLS=1`）はイメージに焼かれているので使える。恒久策は
   Crucible 側に volume mount 対応を足すか、registry を read-only API 越しに読む形にすること。
2. **store の接続は手作業 1 手**（手順 3）。再 deploy のたびに必要。
3. **ADR の `/api/sparql` 経由は未実装。** `production-deployment.md` は「Crucible が deploy する
   フロントは raw oxigraph でなく `/api/sparql`（認証・read-only・scope 済）を叩く」と書いているが、
   MCP フロントの `OxigraphClient` は素の `/query` を叩く。`/api/sparql` は書き込み認証トークンで
   ゲートされているため、寄せるにはクライアント改修とトークン配布が要る。**第三者ツールが
   相乗りする【限定公開】Crucible を立てる段では、この境界を実装すること。**
4. **Asterism 側からの publish UI は未実装**（ROADMAP「Asterism→Crucible publish UI」）。現状は
   Crucible の画面／API を直接触る。

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `mcp.json が見つかりません` | `subdir` の中に `mcp.json` が無い | `infra/asterism/mcp.json` を置く／登録時に `name` を明示する |
| ビルドは通るのに `ヘルスチェックがタイムアウトしました` | コンテナが 8000 以外で listen している | `docker logs <name>` で listen ポートを確認。Dockerfile を `--port 8000` に |
| `Connection refused` / store が空に見える | `asterism-prod_data` に繋いでいない | `docker network connect asterism-prod_data <name>` |
| ツールが 3 つしか出ない | 検索系は宣言ツール。既定では出ない | `ASTERISM_BUNDLED_TOOLS=1` を env に足して再 deploy |
| `sparql_query` が出てしまう | 露出プロファイルが緩い | `ASTERISM_EXPOSE_RAW_SPARQL=false` を確認（既定は開いている） |
| Registry API が 401 | `X-API-Key` 未指定 | `.env` の `REGISTRY_API_KEY` を送る |

---

## 関連ドキュメント

- [`store-mcp-split.md`](store-mcp-split.md) — store と MCP フロントの分離・露出スイッチの設計
- [`production-deployment.md`](production-deployment.md) — 1 箱バンドルと Private Crucible の位置づけ
- [`option-b.md`](option-b.md) — 全体アーキ
- [`phase05-decisions.md`](phase05-decisions.md) — backend / ingester の採用判断
