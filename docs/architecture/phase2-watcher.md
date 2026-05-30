# Phase 2: drop CSV → auto reindex

設計プラン §1 成功基準 4 の最後の未達項目だった「watcher」を、Phase 2 で
最小実装する。Phase 1 の Oxigraph + togomcp ハイブリッドはそのまま、上に
**upload-api** という FastAPI service を追加する。

## 全体図

```
═══════════════════════════════════════════════════════════════════════
  外部入力 (Graphium / CLI / curl)
═══════════════════════════════════════════════════════════════════════
              │ POST /upload/{papers|samples|curves}    │ cp ... data/sources/csv/<kind>/
              │ (multipart/form-data)                    │ (direct drop, e.g. rsync)
              ▼                                          │
┌──────────────────────────────────────────────────────────────────┐
│  upload-api  (新規 / FastAPI + watcher、infra/upload-api)          │
│  ─────────────────────────────────────────────────────────────── │
│  routes:                                                         │
│   - POST /upload/{kind}: tmp file -> os.replace へ atomic 書込     │
│   - GET  /jobs?limit=N : jsonl tail                             │
│   - GET  /health        : Oxigraph ping                          │
│                                                                  │
│  background task (lifespan):                                     │
│   watchfiles.awatch(data/sources/csv/{papers,samples,curves}/)   │
│     → debounce settle → run ingest_{kind}() (rdflib, in thread)  │
│     → POST Turtle to Oxigraph (SPARQL Graph Store Protocol)       │
│     → append Job record to data/sources/jobs.jsonl               │
└──────────────────────────────────────────────────────────────────┘
              │ POST /store?default               │ READ data/sources/csv/<kind>/
              ▼                                    │ WRITE data/sources/rdf/...
┌──────────────────────┐         ┌────────────────────────────────┐
│ Oxigraph (Phase 1)   │         │ data/sources/ (bind mount)      │
│  http://oxigraph:7878 │         │  ├ csv/{papers,samples,curves}/ │
└──────────────────────┘         │  ├ rdf/starrydata/{...}/        │
                                 │  ├ errors/starrydata/{...}/     │
                                 │  └ jobs.jsonl                    │
                                 └────────────────────────────────┘
```

## なぜ 1 プロセス (API + watcher) にしたか

- watcher と API は同じディスク (`data/sources/`) を共有する。別プロセスにする
  と jobs.jsonl の writer が 2 系統に増えるか、IPC が必要になる
- どちらも asyncio で書ける。`FastAPI(lifespan=...)` に asyncio.create_task で
  突っ込むだけで済む
- 失敗モードの観察対象が 1 つ
- Phase 3 で「同期 ingest が必要な大規模 CSV」が来たら、その時に分離する

## ファイル配置の規約

- **drop**: `data/sources/csv/<kind>/<filename>.csv` — どんな名前でも可、kind は
  parent dir で決まる
- **生成 Turtle (audit / cache)**: `data/sources/rdf/starrydata/<kind>/<stem>.ttl`
- **行エラー (jsonl)**: `data/sources/errors/starrydata/<kind>/<stem>.jsonl`
- **ジョブ履歴 (jsonl)**: `data/sources/jobs.jsonl`

`data/sources/` は `.gitignore` 済 (runtime data として扱う)。compose で host
の同名 dir を `:/data/sources` に bind mount する。

## グラフ方針 (default graph)

**default graph に投入する** (`POST /store?default`)。MIE YAML が
`graphs: [default]` を宣言し、`sparql_query_examples` も GRAPH 句なしで書かれて
いるため、default graph に載せると AI のクエリ・Phase 1 smoke test の双方が
そのまま data を見られる (`{ ?s ?p ?o }` が 0 を返さない)。Phase 3 step0 が
生成・ロードする RDF も default graph 前提で揃えている。

per-kind 名前付きグラフ (legacy) が必要なら opt-in できる:

- watcher CLI: `--named-graphs`
- upload API: env `CSV2RDF_USE_DEFAULT_GRAPH=0`

その場合の投入先 IRI は `CSV2RDF_GRAPH_PREFIX` (既定
`https://kumagallium.github.io/csv2rdf-mcp/starrydata/graph/`) + `{papers,samples,curves}`。
ただし named graph は GRAPH 句付きクエリを要求するため、MIE クエリ例とは非互換になる。

## 冪等性

`docs/architecture/phase05-decisions.md` §2.2 と整合する設計。

- 主要 entity (paper/sample/curve/periodical/person/descriptor) は安定 IRI
- Oxigraph は IRI ベースで triple を dedupe (set semantics)
- 再 ingest は新しい `sd:IngestionActivity` ノード 1 つを追加するだけ

これにより `cp /path/to/papers.csv data/sources/csv/papers/` を何度繰り返しても
RDF は壊れない。

## ジョブ記録 (`jobs.jsonl`)

1 行 = 1 ingest pass。フィールド:

```json
{
  "kind": "papers",
  "csv_path": "/data/sources/csv/papers/foo.csv",
  "ttl_path": "/data/sources/rdf/starrydata/papers/foo.ttl",
  "rows_in": 100, "rows_ok": 99, "rows_err": 1,
  "triples_out": 4711, "bytes_uploaded": 312456,
  "status": "partial",           // ok / partial / error
  "error": null,                  // or repr() of the exception
  "started_at": "2026-05-28T12:34:56+00:00",
  "ended_at":   "2026-05-28T12:34:57+00:00"
}
```

`partial` は「ingester が一部行で失敗したが Turtle は upload された」状態。
詳細は同名の `data/sources/errors/starrydata/<kind>/<stem>.jsonl` に。

## エンドポイント

| Method | Path | 戻り値 |
|---|---|---|
| GET  | `/health`           | `{status, oxigraph}` (oxigraph=false → HTTP 503) |
| POST | `/upload/{kind}`    | `{kind, saved_to, bytes, queued}` |
| GET  | `/jobs?limit=N`     | `{count, jobs: [...]}` (N は 1..500) |

`POST /upload/{kind}` のファイル名検証:

- `[A-Za-z0-9._-]{1,128}\.csv$` を full match (path traversal 防止)
- `.csv` 以外は 400
- multipart で `file=` part が必須

## 安全策

- `os.replace` で atomic write → watcher は完成済 file だけ見る
- watcher は debounce (`CSV2RDF_SETTLE_S`、default 300 ms) で部分書込を回避
- Oxigraph POST は 3 retry × 指数 backoff (200/400/800 ms)
- 失敗は Job に記録され API/CLI から観察可。watcher は停止しない

## 環境変数 (compose の `upload-api` service)

| 変数 | default | 説明 |
|---|---|---|
| `CSV2RDF_OXIGRAPH_URL` | `http://oxigraph:7878` | Oxigraph SPARQL endpoint |
| `CSV2RDF_DROP_ROOT`    | `/data/sources/csv` | watcher が監視する root |
| `CSV2RDF_RDF_ROOT`     | `/data/sources/rdf/starrydata` | 生成 Turtle の出力先 |
| `CSV2RDF_ERROR_ROOT`   | `/data/sources/errors/starrydata` | 行エラー jsonl の出力先 |
| `CSV2RDF_JOBS_LOG`     | `/data/sources/jobs.jsonl` | ジョブ履歴 |
| `CSV2RDF_USE_DEFAULT_GRAPH` | `1` | `1`=default graph 投入 (既定)。`0` で per-kind 名前付きグラフ (legacy) |
| `CSV2RDF_GRAPH_PREFIX` | `https://kumagallium.github.io/csv2rdf-mcp/starrydata/graph/` | 名前付きグラフ prefix (`CSV2RDF_USE_DEFAULT_GRAPH=0` 時のみ有効) |
| `CSV2RDF_SETTLE_S`     | `0.3` | watcher debounce 秒数 |

## Phase 2 で意図的に out of scope

- 認証 / mTLS: closed server / mcp-net 内部のみ前提
- マルチテナント: Phase 4 で scope モデル本格化
- 大規模 CSV の incremental ingest: 現状は CSV 単位の差し替え
- watcher の永続キュー: 死んだ場合は再投入で復元可能 (冪等)

## 撤退路

upload-api が機能停止しても、ingester の CLI (`csv2rdf-starrydata-*`) と
Oxigraph 単体は Phase 1 と同じく稼働する。
