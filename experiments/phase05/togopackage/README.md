# togopackage 素振り (Phase 0.5)

設計プラン §10 / §11 致命的リスク 1-3 / handoff §4.1 に基づく素振り。

## 環境

- image: `ghcr.io/dbcls/togopackage:latest` (digest: d0a12f7f5eec)
- image size: **2.16 GB**
- container: `csv2rdf_phase05_togopackage` (ports 10005 / 7001 / 8890)
- 入力: `../data/papers_100.ttl` (3,715 triples)

## 実行手順

```bash
# 1. Turtle を sources/ にコピー
cp ../data/papers_100.ttl data/sources/
cp ../data/papers_100.ttl data/sources/papers_100_b.ttl  # 2 個目の source 検証用

# 2. config.yaml は本ディレクトリの data/config.yaml を参照 (2 source 登録済み)

# 3. 起動
UID=$(id -u) GID=$(id -g) docker compose up -d

# 4. 起動完了まで ~30 秒 (QLever index ビルド + 各サービス起動)
sleep 30

# 5. ベンチ
cd ..
. .venv/bin/activate
python scripts/bench_togopackage.py | tee togopackage/run.log

# 6. 後片付け
cd togopackage
docker compose down -v
```

## 計測結果 (run.log 抜粋)

| 項目 | 値 |
|---|---|
| Docker image サイズ | **2.16 GB** (Oxigraph 53.6 MB の 40 倍) |
| 初回起動 (`compose up` から SPARQL 応答まで) | ~30 秒 (cold), pull は別途数分 |
| 初回 QLever インデックス構築 | prepare-data: **1.0 秒** (100 papers / 3,715 triples) |
| Restart (config.yaml に source 追加) | ~4 秒で SPARQL 復帰 (prepare-data: 0.8 秒) |
| SPARQL クエリ平均 (5 本 / 各 5 回) | **16-72 ms** (Caddy → sparql-proxy → QLever の hop で +14-50 ms) |
| SPARQL UPDATE (POST sparql-update) | **HTTP 415 Unsupported Media Type** (sparql-proxy が block) |
| MIE YAML | `/togo/defaults/togomcp/mie/` に 20+ 個の実例あり (chebi.yaml = 627 行) |
| `togo-mcp-admin` コマンド | container 内に存在せず (handoff §4.1 の項目で挙げられたが実装が見当たらない) |
| LICENSE ファイル | **無い** (handoff §11.3 の警告通り、GitHub API は `license: null` を返す) |

5 クエリそれぞれの mean/p95 (ms):

| Query | mean | p95 |
|---|---|---|
| Q1 COUNT(*) | 16.69 | 47.61 |
| Q2 ?p a sd:Paper LIMIT 100 | 54.71 | 72.55 |
| Q3 paper + title LIMIT 100 | 29.05 | 57.68 |
| Q4 authors per paper GROUP BY | 18.15 | 48.29 |
| Q5 date >= 2015 FILTER | 26.77 | 55.45 |

→ Oxigraph と比較して概ね **5-10 倍遅い**。Caddy + sparql-proxy + QLever の 3 hop が
   サブミリ秒の処理に対して固定オーバーヘッドを乗せている形。

## 検証項目の所感 (handoff §4.1)

- [x] **pull & 起動**: 一発成功。すべてのサービス (qlever / sparql-proxy / sparqlist / grasp / togomcp / tabulae) が立ち上がる。
- [x] **/sparql が叩ける**: `curl -G http://localhost:10005/sparql --data-urlencode 'query=...'` で正常応答 (COUNT=3,715)。
- [x] **複数 source**: `config.yaml` の `source:` 配列で 2 個登録 → 異なる `graph:` で QLever に同居。`GRAPH ?g { ... }` で graph 単位の COUNT が取れる。
- [x] **reload / 部分再インデックス API**: **存在しない**。`config.yaml` か source files を変更 → `docker compose restart` → `/togo/runtime/setup/qlever.sh` が input_hash を計算し変化があれば QLever index を **フルリビルド**する設計 (README "Generated Artifacts" 節)。
  - 100 papers / 3,715 triples では restart wallclock 4 秒。
  - **starrydata 全件 (~2-5M triples) では分単位** になる見込み (handoff §11 致命的リスク 1)。
  - watcher で CSV を取り込んで自動再インデックスする csv2rdf-mcp のユースケース (Quickstart "drop file → 自動再インデックス") とは相性が悪い。
- [x] **MIE YAML 書式**: `togo-mcp-admin` コマンドは見つからないが、`/togo/defaults/togomcp/mie/*.yaml` に **完成された参考実装 20+ 個** がある (chebi.yaml = 627 行)。構造:
  - `schema_info`: title / description / endpoint / base_uri / graphs / version / license / access
  - `shape_expressions`: ShEx 定義
  - `sample_rdf_entries`: 代表エンティティの Turtle スニペット
  - `tools`: sparql_query テンプレ
  → Phase 1 で starrydata 用 MIE を書くなら chebi.yaml をコピペベースで叩き台にできる (本ディレクトリの [`mie_sample_chebi.yaml`](mie_sample_chebi.yaml) を参照)。
- [x] **LICENSE ファイル**: **無い** (handoff §11.3 の警告そのもの)。`GET /repos/dbcls/togopackage/license` → 404。vendor の submodule (sparql-proxy, sparqlist, grasp, togomcp, rdf-config) は **すべて MIT**。togopackage wrapper 本体は LICENSE 不在のため、**Apache-2.0 として再配布する権利が不明**。
  - イメージを単純に `docker pull` して使うだけならグレー (利用権の暗黙黙認に依存) だが、`compose.yaml` や config テンプレを csv2rdf-mcp 側でフォークして公開する場合は確実に問題になる。
  - 設計プラン §11 致命的リスク 3 の「LICENSE 確認次第で依存を切る」判断が現実化。

## SPARQL UPDATE が通らない件の補足

- sparql-proxy は GET / POST application/sparql-query を許可するが、POST application/sparql-update は **無条件で 415** を返す (read-only proxy 設計)。
- QLever 側で `PERSIST_UPDATES: true` を立てれば直接 7001 ポートに対しては update が通るが、その場合も restart で失われない保証はなく、togopackage の入力差分検出 (`.loaded-input-hash`) との整合性も崩れる。
- csv2rdf-mcp は **CSV を canonical source** にして毎回 RDF 再生成→投入する設計 (handoff / 設計プラン §5) なので、SPARQL UPDATE は本質的に必要ない。だが「subset を 1 行だけ追加した時の延長時間」を最小化する観点では restart 型は不利。

## バックエンドだけ差し替える選択肢

設計プラン §11「代替アーキテクチャ (撤退路)」は次の通り:

```
[upload_api] → [ingest: Python rdflib] → Turtle → [Oxigraph] → [自作 MCP (薄い proxy)]
```

togopackage の利点は **sparqlist / grasp / tabulae の同梱**にあるが、本プロジェクトの第一フェーズで必要なのは SPARQL endpoint + MCP のみ。**sparqlist / grasp / tabulae を必要に応じて後付け**でも csv2rdf-mcp のソブリン設計と整合する。

## 結論 (詳細は [`../../../docs/internal/phase05-decisions.md`](../../../docs/internal/phase05-decisions.md))

- **採用継続は推奨しない**。主因は (1) LICENSE 不在による法的不確実性、(2) restart-rebuild 型の更新モデルが csv2rdf-mcp の auto-reindex ユースケースと相性が悪い、(3) image 2.16 GB の配布コスト。
- **togomcp の MIE 書式と defaults/mie/ の例**は **資料として有用**。Phase 1 で csv2rdf-mcp が自作 MCP を書くときの schema_info / shape_expressions / sample_rdf_entries 構造の **設計参考**として残す価値あり。
- **sparqlist の SPARQL テンプレート機構**は将来 (Phase 2 以降) で必要になったら **独立に導入**できる (MIT)。togopackage 全体を抱える必要はない。
