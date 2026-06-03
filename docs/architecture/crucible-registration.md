# Crucible 登録レシピ

Crucible ([`kumagallium/Crucible`](https://github.com/kumagallium/Crucible)) は GitHub URL を貼ると **build + deploy + SSE 公開**まで自動でやってくれる self-hosted AI ツール管理プラットフォーム。asterism を Crucible に登録すると Graphium や Claude Code から discoverable な MCP サーバとして使えるようになる。

本プロジェクトの基本前提では「Crucible は registry (proxy ではない)」だが、Crucible 実装は **build と deploy も担当する**ため、本ドキュメントでは Crucible に登録される **togomcp 本体**と、外側で別途立ち上げる **Oxigraph** に役割を分けて運用する。

## アーキテクチャ (Crucible 登録時)

```
┌─ closed server ─────────────────────────────────────────────────────┐
│                                                                     │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  Crucible (registry + deployer)                            │    │
│  │   ├─ UI       (http://127.0.0.1:8081)                      │    │
│  │   └─ Deploy → docker run --network mcp-net <name>:latest   │    │
│  └─────────────────────┬──────────────────────────────────────┘    │
│                         │ docker network: mcp-net                   │
│         ┌───────────────┴────────────────────────┐                  │
│         ▼                                        ▼                  │
│  ┌──────────────┐                       ┌─────────────────────┐    │
│  │  oxigraph    │  ← user が手動で       │ asterism         │    │
│  │  (SPARQL)    │    mcp-net に起動       │ (togomcp deployed   │    │
│  │  :7878        │                       │   by Crucible)      │    │
│  │              │ ◀── SPARQL HTTP ────── │ EXPOSE 8000 → SSE   │    │
│  └──────────────┘                       └─────────────────────┘    │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐       │
│  │  AI client (Claude Code / Cursor / Graphium)             │       │
│  │  claude mcp add --transport sse asterism ...          │       │
│  └─────────────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────────┘
```

- Crucible は **togomcp wrapper のみ**を build & deploy する (Dockerfile: [`infra/togomcp/Dockerfile`](../../infra/togomcp/Dockerfile))
- **Oxigraph は user が事前に**`mcp-net` 上に手動で立ち上げる (Crucible で deploy せず別管理)
- togomcp は `OXIGRAPH_HOST` env var で Oxigraph を発見

## 前提

- Crucible が同じホストで起動中 (`docker compose up -d` 済み、UI が `127.0.0.1:8081` で見える)
- `mcp-net` Docker network が存在する (Crucible が初回起動時に作成)
- starrydata の Turtle が手元にある (もしくは Phase 1 ingester で生成済み)

## 手順

### 1. Oxigraph を `mcp-net` 上に手動で起動

Crucible の deployer は **1 image しか deploy しない**ので、SPARQL endpoint である Oxigraph は別に立てる必要がある。Crucible の network (`mcp-net`) に接続することで、Crucible-deployed な togomcp から DNS 名 `asterism-oxigraph` で参照できる。

```bash
docker run -d \
  --name asterism-oxigraph \
  --network mcp-net \
  -p 7878:7878 \
  -v asterism-oxigraph-data:/data \
  --restart unless-stopped \
  ghcr.io/oxigraph/oxigraph:latest \
  serve --location /data --bind 0.0.0.0:7878
```

ホスト側からも `http://localhost:7878/query` で叩けるようにポート公開しているが、togomcp ↔ Oxigraph は `mcp-net` 経由で接続する。

### 2. starrydata の Turtle を Oxigraph にロード

```bash
# host で ingester を実行 (Phase 1 README 参照)
asterism-starrydata-papers  /path/to/starrydata_papers.csv  /tmp/papers.ttl
asterism-starrydata-samples /path/to/starrydata_samples.csv /tmp/samples.ttl
asterism-starrydata-curves  /path/to/starrydata_curves.csv  /tmp/curves.ttl

# Turtle を Oxigraph に投入 (host から)
for f in papers samples curves; do
  curl -X POST --data-binary @/tmp/$f.ttl \
    -H 'Content-Type: text/turtle' \
    'http://localhost:7878/store?default'
done
```

### 3. Crucible UI から asterism を登録

Crucible UI (`http://127.0.0.1:8081`) の "Register Server" ダイアログで:

| Field | Value |
|---|---|
| **github_url** | `https://github.com/kumagallium/asterism` |
| **branch** | `main` (Phase 1 merge 後) または `feat/phase1-samples-curves` |
| **subdir** | `infra/togomcp` |
| **tool_type** | `mcp_server` (auto-detect されるので空でも OK) |
| **transport** | `auto` (Dockerfile に EXPOSE 8000 があるので `sse` 扱いになる) |
| **env_vars** | `OXIGRAPH_HOST=asterism-oxigraph` (mcp-net 上のホスト名) |

API 経由なら:

```bash
curl -X POST 'http://127.0.0.1:8080/api/servers' \
  -H 'Content-Type: application/json' \
  -d '{
    "github_url": "https://github.com/kumagallium/asterism",
    "branch": "main",
    "subdir": "infra/togomcp",
    "env_vars": {"OXIGRAPH_HOST": "asterism-oxigraph"}
  }'
```

Crucible が `mcp.json` を読み取って `name=asterism` / `display_name=asterism (Materials Knowledge Graph)` / `description` を自動補完する。`subdir=infra/togomcp` 指定により、build context は repo root のままで Dockerfile だけが subdir 内のものを使う (Dockerfile 側は `COPY data/togomcp ...` で repo root を期待した記述になっている)。

### 4. AI client から接続

```bash
# Claude Code
claude mcp add --transport sse asterism http://127.0.0.1:<port>/sse
# Crucible UI に表示される port を使う

# Cursor / Windsurf
# UI から SSE URL をコピペ

# Claude Desktop は SSE をネイティブサポートしないので mcp-remote 経由:
#   https://www.npmjs.com/package/mcp-remote
```

### 5. 動作確認

AI に「starrydata の Bi2Te3 サンプルを 3 件見つけて」と聞く → AI が `run_sparql` ツールで SPARQL を発行 → togomcp が Oxigraph (`asterism-oxigraph:7878`) に投げる → 結果が返る。

## 削除 / 更新

```bash
# Crucible UI から Stop / Remove

# auto_update が有効なら main ブランチ更新時に自動再デプロイ。
# 手動で再ビルドしたい場合: Stop → Re-register
```

## 既知の制約

1. **Crucible は volume mount を支援しない** ので、MIE / endpoints.csv の編集には image 再ビルド (= Crucible 再 deploy) が必要。`auto_update: true` を mcp.json で有効化すれば main 更新時に自動。
2. **Oxigraph の運用は Crucible 外**。データ永続化 (`asterism-oxigraph-data` volume) と起動順序 (Oxigraph を先に上げる) はユーザ管理。
3. **togomcp の dbcls 公式 image** が公開されたら、本 wrapper を介さず Crucible に直接 togomcp を登録 → env で MIE_DIR を渡す形に簡略化できる (Phase 3 候補)。

## なぜ Oxigraph を Crucible に登録しないか

Crucible の `mcp_server` 種別は SSE/stdio MCP プロトコルを想定しており、Oxigraph (純 SPARQL HTTP endpoint) は素直に乗らない。`cli_library` はメタデータのみで service として動かない。

将来 Crucible に **"service-only registration"** 種別 (Docker image を deploy するが MCP プロトコルは要求しない) が入れば Oxigraph も Crucible 経由で deploy できる。それまでは `mcp-net` 上に手動で起動する運用が現実解。

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `cp: cannot create directory '/var/togomcp-overlay'` | entrypoint が root 権限なしで /var に書こうとした | 古い image。再 build (`compose build` or Crucible re-deploy) |
| `Connection refused to asterism-oxigraph:7878` | Oxigraph が `mcp-net` に居ない | `docker network connect mcp-net asterism-oxigraph` |
| `404 from togomcp /` または `500` | TOGOMCP_DIR の overlay が壊れている | entrypoint ログを確認、image 再 build |
| AI から `run_sparql` 呼び出し時に 401 | sparql-proxy を経由する経路では起こり得るが Oxigraph では起こらないはず | endpoints.csv の URL を確認 |

## 関連ドキュメント

- [`option-b-architecture.md`](option-b-architecture.md) — 全体アーキ + 自前 vs 待ち判断
- [`phase05-decisions.md`](phase05-decisions.md) — backend / ingester 採用判断
- [Crucible 本体 README](https://github.com/kumagallium/Crucible/blob/main/README.md)
