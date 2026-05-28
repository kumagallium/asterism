# togopackage (Virtuoso backend) 素振り — Phase 0.5b

Phase 0.5 で「Virtuoso 経路は未検証」と認めた宿題を潰す追検証 (2026-05-28)。
ユーザの指摘 (「LICENSE 不在より理由 2-4 が大きそう」「Virtuoso backend を試していない」)
を受けて、handoff §4.1 の項目を **Virtuoso 経路で**やり直した。

## 環境

- image: `ghcr.io/dbcls/togopackage:latest` (Phase 0.5 と同じ)
- `config.yaml`: `sparql_backend: virtuoso`, `mcp_server: togomcp`
- 入力: papers_100.ttl (3,715 triples)
- 直結 Virtuoso: `http://localhost:8890/sparql` (read), `/sparql-auth` (write, dba digest)
- sparql-proxy 経由: `http://localhost:10005/sparql` (read only)

## 検証項目と結果

### A. SPARQL 1.1 Update が live に通るか

| 経路 | 結果 |
|---|---|
| sparql-proxy POST application/sparql-update | **HTTP 415** (read-only proxy 設計) |
| sparql-proxy POST form-encoded `update=` | **HTTP 400** (proxy は `query=` だけ受ける) |
| Virtuoso :8890/sparql 匿名 SPARQL account | **403** (No permission, togopackage が起動時に revoke) |
| Virtuoso :8890/**sparql-auth** dba digest 認証 | **✅ HTTP 200** |

→ **togopackage 構成のままで live UPDATE が可能**。経路は Virtuoso 直結
   `/sparql-auth` + dba 認証。csv2rdf-mcp 側は MCP server の write tool だけが
   この経路を使い、read 系は sparql-proxy 経由でフィルタを通せる、という
   役割分担ができる。

### B. UPDATE 永続性 (container restart 後に消えないか)

1. `paper/v8890-dba` を `/sparql-auth` 経由で INSERT
2. `docker compose restart`
3. restart 後の SPARQL で同 IRI を ASK
4. → **`true`** (残った)

Virtuoso の自動 checkpoint で永続化される。`config.yaml` か source files の
hash が変わらない限り、togopackage は load.sql を再実行しない → UPDATE で
入れた triple は保持される。

### C. 複数 source

Phase 0.5 (QLever) と同じく、`config.yaml` の `source:` 配列に 2 個書けば
2 graph として同居。restart は要るが index は incremental ではなく VOS の
bulk load 経由。

### D. レイテンシ (POST form-encoded, 1 warmup + 3 measured, 各 5 クエリ)

| Query | direct mean (ms) | direct p95 (ms) | proxy mean (ms) | proxy p95 (ms) |
|---|---|---|---|---|
| Q1 COUNT(*) | 14.94 | 16.13 | 25.57 | 35.78 |
| Q2 ?p a sd:Paper LIMIT 100 | 21.61 | 25.68 | 29.95 | 42.25 |
| Q3 paper + title LIMIT 100 | 26.48 | 33.65 | 25.84 | 34.13 |
| Q4 authors GROUP BY | 19.41 | 21.16 | 25.48 | 27.27 |
| Q5 date filter (STR-based) | 17.70 | 23.26 | 32.45 | 34.71 |

INSERT DATA (5 iters via /sparql-auth dba digest):
mean = **7.45 ms**, p95 = **13.49 ms**

### E. 観察された Virtuoso の癖

bench を連続実行する条件下で、Virtuoso が **`define sql:big-data-const 0`** を
クエリ先頭に auto-prepend する挙動が観察された。これが我々の Q5 当初版
(`FILTER (?d >= "2015-01-01"^^xsd:date)`) と衝突し SP030 "Too many closing
parentheses" を投げる。同じクエリも単発実行では問題なく通るので、Virtuoso の
trigger heuristic と FILTER 構文の組み合わせ依存。

回避策: `FILTER ( STR(?d) >= "2015-01-01" )` のように xsd:date 比較を STR
比較に書き換える。ISO 8601 日付は文字列辞書順比較で意味論が保たれる。

これは Phase 1 で starrydata 用クエリテンプレートを書く際の **Virtuoso
向けの注意点**として記録に残す価値がある。

## 3 backend の正面比較 (同じ subset / 同じ 5 クエリ)

| backend | image | 初回 ready | 5 クエリ mean range (ms) | INSERT DATA | LICENSE |
|---|---|---|---|---|---|
| **Oxigraph** | 53.6 MB | ~6s | **2-9** (direct) | **2.35 ms** (SPARQL UPDATE 第一級) | Apache-2.0 |
| **togopackage (QLever)** | 2.16 GB | ~30s | 16-72 (proxy) | **HTTP 415** (sparql-proxy が block) | wrapper 不在 |
| **togopackage (Virtuoso)** | 2.16 GB | ~2-3 min | 14-32 (direct) / 25-37 (proxy) | **7.45 ms** (via /sparql-auth dba) | wrapper 不在 |

(togopackage の image / LICENSE は backend 切り替えで変わらない)

## Phase 0.5 の結論への影響

Phase 0.5 で togopackage を撤退とした 4 つの理由は、Virtuoso backend で
**理由 2 (live 追記不可) のみ解消**する:

| 理由 | QLever | Virtuoso | 影響 |
|---|---|---|---|
| 1. LICENSE 不在 | 〇 | 〇 | 残る (チーム内交渉で解消可能) |
| 2. restart-rebuild モデル / live 追記不可 | 〇 | **△→×** | **解消** (Virtuoso /sparql-auth で live UPDATE 通る) |
| 3. image 2.16 GB | 〇 | 〇 | 残る (Lab/Org scope では許容、Personal scope では重い) |
| 4. レイテンシ 3-hop | 〇 | 〇 | 残る (proxy 経由で 25-37 ms、Oxigraph 直結の 5-15 倍) |

理由 2 が「致命的」から「許容範囲」に降格したので、**全体としては再考の余地が
ある**。ただし理由 3-4 は残る。

## ハイブリッド構成の確認

togomcp の MIE YAML 仕様 (v1.1, [`/vendor/togomcp/docs/MIE_file_specs.md`])
を確認したところ、`schema_info.endpoint` は **任意の SPARQL endpoint URI** を
受け付ける (RDF Portal の chebi.yaml は `https://rdfportal.org/ebi/sparql` を
使っている)。

つまり以下の **ハイブリッド構成**が成立する:

```
[CSV] → [Python rdflib ingester] → Turtle → [Oxigraph (Apache-2.0, 53.6 MB)]
                                                  ↑ SPARQL 1.1 直結
                                            [togomcp standalone (MIT)]
                                                  ↑ MIE YAML (endpoint: http://oxigraph:7878/query)
                                              [AI client]
```

- Oxigraph: backend のスピードと UPDATE の素直さを得る
- togomcp: チームの MCP 設計 (MIE / shape_expressions / sample_rdf_entries /
  sparql_query_examples) を借りる。`pip install` で独立に動く
- togopackage wrapper は使わない (LICENSE / size / restart の 3 つを回避)
- rdf-config: 必要なら CLI として呼ぶ (model.yaml / ShEx / SPARQL 生成、MIT)

これが Phase 0.5 で言うべきだった「**チームの仕事を活かす × 設計プランの
ソブリン制約**」の両立解。

## ファイル

- [`compose.yaml`](compose.yaml) — Virtuoso backend で起動するための
  togopackage compose
- [`data/config.yaml`](data/config.yaml) — `sparql_backend: virtuoso`
- [`../scripts/bench_virtuoso.py`](../scripts/bench_virtuoso.py) — 計測
  スクリプト (requests.Session + form-encoded POST)
- [`run.log`](run.log) — 計測結果の生ログ

## Phase 1 への申し送り (補追)

Phase 0.5 (素振り) の結論は **暫定**。本セッションで Virtuoso 経路を追検証
した結果、最終的な選択肢は次の 3 つに整理される:

1. **Oxigraph + 自作 MCP** (Phase 0.5 当初の判断、最速で立ち上がる)
2. **Oxigraph + togomcp** (本セッションのハイブリッド推奨)
3. **togopackage (Virtuoso) のまま**, write は /sparql-auth dba (チームの
   ecosystem に完全に乗る、ただし image と LICENSE は受け入れる必要がある)

どれを選ぶかは **ユーザヒアリング** (チームとの調整、Personal scope の優先度、
sparqlist / grasp の必要性) を経て Phase 1 着手時に確定する。
