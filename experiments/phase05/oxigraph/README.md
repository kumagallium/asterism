# Oxigraph 素振り (Phase 0.5)

設計プラン §10 / §11 致命的リスク 1 / handoff §4.2 に基づく素振り。

## 環境

- image: `ghcr.io/oxigraph/oxigraph:latest`
- image size: **53.6 MB**
- container: `asterism_phase05_oxigraph` (port 7878)
- 入力: `../data/papers_100.ttl` (3,715 triples, 100 papers の subset)

## 実行手順

```bash
# 1. 依存セットアップ (一度だけ)
cd ..
uv venv .venv --python 3.11
. .venv/bin/activate
uv pip install rdflib

# 2. subset と Turtle 生成 (一度だけ)
python scripts/make_subset.py "<path>/starrydata_papers.csv"
python scripts/csv_to_ttl.py data/papers_100.csv data/papers_100.ttl

# 3. Oxigraph 起動 + ベンチ
cd oxigraph
docker compose up -d
cd ..
python scripts/bench_oxigraph.py | tee oxigraph/run.log

# 4. 後片付け
cd oxigraph
docker compose down -v
```

## 計測結果 (run.log 抜粋)

| 項目 | 値 |
|---|---|
| Docker image サイズ | 53.6 MB |
| 起動時間 (compose up) | ~6 秒 (cold), ~1 秒 (warm) |
| 初回ロード (100 papers / 3,715 triples) | **0.32 s** |
| SPARQL クエリ平均 (5 本 / 各 5 回) | 2-22 ms |
| SPARQL 1.1 Update `INSERT DATA` | **2.35 ms** (delta +2 triples) |
| 同じ Turtle 再投入 (incremental) | 0.15 s, delta **+300** (= 100 paper × 3 triple の bnode 分だけ重複) |

5 クエリそれぞれの mean/p95 (ms):

| Query | mean | p95 |
|---|---|---|
| Q1 COUNT(*) | 2.96 | 3.83 |
| Q2 ?p a sd:Paper LIMIT 100 | 2.34 | 3.04 |
| Q3 paper + title LIMIT 100 | 9.15 | 21.70 |
| Q4 authors per paper GROUP BY | 4.05 | 10.46 |
| Q5 date >= 2015 FILTER | 2.00 | 2.80 |

## 検証項目の所感 (handoff §4.2)

- [x] **pull → 起動**: 一発成功。warm-up なしで listening。
- [x] **Turtle を `oxigraph load` で投入**: HTTP `POST /store?default Content-Type: text/turtle` 経由でも問題なくロード可。CLI から直接ロードする方法も存在 (`oxigraph load`)。
- [x] **SPARQL 1.1 Update で追記**: `INSERT DATA` が 2-3 ms で完了。差分追記が **第一級でサポート**されている。
- [x] **同じ subset の追加投入で再構築不要**: IRI で識別される triple は **set semantics で冪等** (delta=0)。blank node のみ重複するが、これは RDF 標準の挙動 (bnode は scope が limited)。**Phase 1 の ingester は bnode を使わず IRI で命名する**方針にすれば、CSV 追加投入は完全に冪等で済む。
- [x] **検索性能**: 100 papers サイズでは LIMIT 100 のクエリが <22 ms p95。Phase 1 で starrydata 全件 (papers 56k + samples 144k + curves 233k = ~2-5M triples 見込み) をロードした上で再計測予定。

### Bnode 重複への対応 (Phase 1 への申し送り)

`csv_to_ttl.py` では `schema:Periodical` を blank node にしている。Phase 1 では:

- container_title (journal 名) は hash-IRI または slug-IRI で命名 (例: `sdr:periodical/acc-chem-res`) する
- 同様に Person も SID/index で命名済みのため bnode は登場しない設計に揃える

これにより「同じ CSV を再投入しても triple 数が増えない」性質が成立し、watcher による re-ingest が安全になる。

## 結論 (詳細は phase05-decisions.md)

- **SPARQL 1.1 Update で部分追記が素直に動く** — QLever の静的インデックス前提のフルリビルド問題 (§11 致命的リスク 1) を完全に回避できる。
- **Docker image 53.6 MB / single binary** — 配布性が極めて良い。Crucible 同居の closed server に置く前提と相性が良い。
- **設定の複雑度**: 起動オプションは `serve --location /data --bind 0.0.0.0:7878` のみ。`compose.yaml` 13 行で完了。**学習コストほぼゼロ**。
