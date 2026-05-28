# Phase 0.5 採用判断

実施日: 2026-05-27
担当: Claude Code (Opus 4.7, 1M context) on behalf of @kumagallium

---

## 結論

- **SPARQL backend**: **Oxigraph** を採用
- **Ingester**: **Python rdflib** を Phase 1 で採用。Morph-KGC + YARRRML は **Phase 3 (汎用 CSV) で再評価**
- **togopackage**: **撤退**。ただし togomcp の **MIE YAML 書式**と sparqlist の SPARQL テンプレ方式は **資料として保持**し、必要になったら個別に取り込む
- **次の Phase 1 への影響**:
  - 設計プラン §11「代替アーキテクチャ (撤退路)」を **正路に昇格**
  - §4 / §5 / §6 / §11 を本ドキュメントの結論に沿って Phase 1 着手時に書き換える (修正提案は本書 §5 に列挙)

---

## 1. 比較メトリクス (handoff §4.4)

100 papers / 3,715 triples の subset で計測。

| 項目 | togopackage (QLever) | Oxigraph | Morph-KGC |
|---|---|---|---|
| **Docker image サイズ** | 2.16 GB | **53.6 MB** | N/A (Python pip パッケージ) |
| **初回ロード時間** (TTL 投入完了まで) | ~30 秒 (pull 除く; QLever index ビルド + 全サービス起動含む) | **0.32 秒** | 1.1 秒 (CSV 直読み) / 0.67 秒 (JSON 直読み) |
| **SPARQL レイテンシ (5 クエリ平均 mean)** | 16-72 ms (Caddy→sparql-proxy→QLever の 3 hop) | **2-22 ms** | N/A (バッチ変換用途) |
| **追記コスト (100 papers 相当の追加投入)** | restart 4 秒 + index 再ビルド (subset では fast、starrydata 全件では数分以上の見込み) | **0.15 秒** (SPARQL POST、IRI 由来 triple は冪等) | 該当なし (新規 RDF 生成のみ) |
| **SPARQL 1.1 Update (INSERT DATA)** | **HTTP 415** (sparql-proxy が block) | **2.35 ms** (第一級サポート) | 該当なし |
| **設定の複雑度** | config.yaml 数十行 + データディレクトリ規約多数。ports 3 つ。compose ≒ 14 行だが内部に supervisor + 6 サービス | compose 13 行、起動オプションは `serve --location /data --bind 0.0.0.0:7878` のみ | RML mapping を Turtle で書く (1 mapping ≒ 数十行)、config.ini ≒ 10 行 |
| **LICENSE** | **不在** (handoff §11.3 警告通り) | Apache-2.0 | Apache-2.0 |

---

## 2. 詳細な検証メモ

### 2.1 togopackage

- **起動**: 一発成功。`compose up -d` で qlever / sparql-proxy / sparqlist / grasp / togomcp / tabulae / caddy がまとめて立ち上がる。
- **複数 source**: `config.yaml` の `source:` 配列で 2 個登録 → 異なる `graph:` 値で QLever に同居可能。`GRAPH ?g {…}` で per-graph COUNT が取れた。
- **reload / 部分再インデックス API**: **存在しない**。`config.yaml` か source files の hash が変化すると、container restart 時に QLever index が **フルリビルド**される。100 papers では restart 4 秒で完了するが、設計プラン §11 致命的リスク 1 のとおり starrydata 全件 (papers 56k + samples 144k + curves 233k 行 ≒ 数 M triples) では分単位を覚悟する設計。
- **MIE 書式の所感**: `togo-mcp-admin` は container 内に **見つからない** (handoff §4.1 の項目で挙げられたが現行 image に未収録)。代わりに `/togo/defaults/togomcp/mie/*.yaml` に 20+ 個の完成された参考実装があり、`schema_info` / `shape_expressions` / `sample_rdf_entries` / `tools` の構造で書く必要があると分かる (例: chebi.yaml = 627 行)。**書式自体は学習価値が高い**。
- **LICENSE**: **無い**。GitHub の `dbcls/togopackage` 直下に LICENSE ファイル無し、GitHub License API が `null` を返す。vendor submodule (sparql-proxy / sparqlist / grasp / togomcp / rdf-config) は **すべて MIT** だが、togopackage wrapper 本体の権利が不明なため Apache-2.0 リポジトリから依存することにリスクがある。
- **SPARQL UPDATE**: sparql-proxy が POST application/sparql-update を **HTTP 415** で block。QLever 直接ポート (7001) を叩けば PERSIST_UPDATES 設定次第で通るが、togopackage の入力差分検出 (`.loaded-input-hash`) との整合性が崩れる。

### 2.2 Oxigraph

- **起動**: `ghcr.io/oxigraph/oxigraph` を pull → `serve --location /data --bind 0.0.0.0:7878` で即座にリッスン。
- **Turtle 投入**: HTTP `POST /store?default Content-Type: text/turtle` で 3,715 triples を **0.32 秒**でロード。
- **SPARQL 1.1 Update**: `INSERT DATA` が **2.35 ms** で完了。これは csv2rdf-mcp の「CSV 1 行追加 → 数秒で検索可能」というユースケースに完全に合致する。
- **同じ Turtle 再投入**: **IRI で識別される triple は冪等** (set semantics)。blank node のみ重複した (100 paper × 3 triples = 300 増加)。**Phase 1 の ingester は bnode を使わず IRI で命名**すれば、watcher による re-ingest が安全。
- **検索性能**: 100 papers サイズでは LIMIT 100 のクエリが <22 ms p95。starrydata 全件での再計測は Phase 1 で実施。
- **資料・LICENSE**: Apache-2.0、Rust 単一バイナリ、Docker image 53.6 MB、SPARQL 1.1 Query / Update / Protocol / Graph Store 完全準拠。

### 2.3 Morph-KGC + YARRRML

- **インストール**: `uv pip install morph-kgc` で 1 コマンド。
- **平易な列 (SID / DOI / URL / title 等) は宣言的に変換可**: papers.csv をそのまま読み、`rr:template "…/paper/{SID}"` のような RML mapping で 1,279 triples を生成 (1.1 秒)。
- **JSON 埋め込み列 (author / issued / project_names) は直接展開できない**:
  - RML の `rml:referenceFormulation` は logical source 単位で固定 (CSV か JSONPath か XPath か SQL のいずれか 1 つ)。CSV の **セル内 JSON をネスト iterate する書式が標準に無い**。
  - **FnO (Function Ontology) 経由の試み**は **Morph-KGC が `KeyError: 'object_map'` で内部破綻**。FnO サポートは partial で、安定して動く保証なし。
  - **前処理で CSV → JSON 配列に展開**してから morph-kgc を当てれば動く (584 authors → 2,336 triples、0.67 秒)。**だが宣言性は前処理 Python に吸い取られる**。Python が必須なら最初から Python rdflib で書いた方が短くデバッグしやすい。
- **starrydata の現実**: papers / samples / curves のすべてに JSON 列があり、**RML 単独で完結する経路は無い**。

---

## 3. 採用判断の根拠

### 3.1 なぜ Oxigraph か (3 つの観点)

1. **追記コストの非対称性**: csv2rdf-mcp の核は「CSV を放り込めば検索可能になる」自動再インデックスフロー (設計プラン §1 成功基準 4)。Oxigraph は SPARQL 1.1 Update を 2-3 ms で消化し、Turtle re-load も既存 IRI を set semantics で冪等にする。一方 QLever は static index 前提で、starrydata 全件規模ではフルリビルドが分単位 (handoff §11 致命的リスク 1)。**ユースケースとアーキテクチャが正面衝突**するため Oxigraph を採用する以外に合理的選択肢が無い。

2. **配布性とソブリン哲学の整合**: csv2rdf-mcp は self-hostable / closed server / personal-lab-org の階層運用 (設計プラン §0.1) を前提にしている。Oxigraph は **53.6 MB の単一バイナリ Docker image** で、個人 PC から研究室サーバまで均一に転がせる。togopackage の 2.16 GB は personal scope では実用上重い (40 倍)。

3. **LICENSE と運用の透明性**: Oxigraph は Apache-2.0。togopackage は wrapper repo に LICENSE が無く、再配布権が暗黙。csv2rdf-mcp を Apache-2.0 として OSS 公開する以上、依存先のライセンスは明示的であることが必須。

### 3.2 なぜ Python rdflib ingester か (特に JSON 列対応の体感)

- starrydata の 3 つの CSV すべてに JSON 埋め込み列がある (papers: `author` / `issued` / `project_names`、samples: `sample_info`、curves: `x` / `y`)。
- Morph-KGC + YARRRML は **JSON 埋め込み列**の宣言的展開を直接サポートしない。FnO 経由は Morph-KGC のサポートが不安定 (`KeyError`)。前処理で CSV→JSON 分割すれば動くが、Python 前処理が必須なら最初から Python rdflib で書いた方が **コード量・デバッグ容易性・テスタビリティ**で勝る。
- `experiments/phase05/scripts/csv_to_ttl.py` (134 行) が **すべての papers 列**を 3,715 triples に変換できることを確認済み。これを叩き台にして `ingest/src/csv2rdf/starrydata.py` を Phase 1 で書ける。

### 3.3 togopackage を切る判断、その代わりに使うもの

- **sparql_backend**: Oxigraph に統一。
- **mcp_server**: 自作 (薄い proxy + csv2rdf-mcp に固有のツール)。
- **sparqlist (SPARQL テンプレート機構)**: 現時点では不要。Phase 2 で必要になったら独立に導入する (MIT、git submodule か Docker compose の追加サービスとして)。
- **grasp (GraphQL)**: 不要。MCP 経由のツール提供で代替。
- **togomcp の MIE 書式**: **資料として保持**。Phase 2 で自作 MCP を書くときに `schema_info` / `shape_expressions` / `sample_rdf_entries` / `tools` の構造を借用 (例 chebi.yaml は [`experiments/phase05/togopackage/mie_sample_chebi.yaml`](../../experiments/phase05/togopackage/mie_sample_chebi.yaml) に保存済み)。

---

## 4. 残ったリスク

1. **Oxigraph の starrydata 全件性能未検証**: 100 papers (3,715 triples) では速いが、papers 56k + samples 144k + curves 233k 行を変換した Turtle (見積もり数 M triples、サイズ数百 MB) でも同じ p95 が出るかは Phase 1 で再ベンチ必須。Oxigraph 公式ベンチでは数億 triples 規模も走るが、自分の手で確認するまでは「快適」と断言しない。
2. **curve の x/y 配列**: 設計プラン §4 で議論したとおり、Phase 1 では JSON literal + 集約値 (xMin/xMax/yMin/yMax/pointCount) で済ませる予定。Oxigraph の string literal は数 MB まで問題ないが、curves.csv 全件 (155 MB) を triple に展開した時の **store サイズ**は要監視。
3. **MCP server を自作する負担**: togopackage の togomcp / sparqlist / grasp を捨てる代わりに、自作 MCP に最低限のツール (`sparql_query`, `list_predicates`, `schema_diagram`, `template_curve_fetch`) を実装する必要がある。設計プラン §10 Phase 2 で計上済み (2-3 日)。
4. **IRI 永続化**: 本素振りでは `http://localhost/csv2rdf/...` をプレースホルダで使った。Phase 1 で **GitHub Pages URL に切り替える**判断 (`https://kumagallium.github.io/csv2rdf-mcp/...`) が必要。設計プラン §4.0 と整合させる。**注**: handoff §2 では owner を `m-kumagai` 想定で書かれていたが、本セッションで `kumagallium` に変更 ([`decisions.md`](decisions.md))。最終 IRI のホスト名は Phase 1 着手時に再確認する。
5. **Morph-KGC を完全に捨てたわけではない**: Phase 3 で汎用 CSV (JSON 列が無いプレーンな表) を扱うときに、`manifest.yaml` から RML/YARRRML を自動生成する経路が再浮上する可能性がある。

---

## 5. 設計プランへの修正提案

Phase 1 の実装着手時に `docs/internal/design-plan.md` を以下のように改訂する (PR 内では本ドキュメントを変更ログとして残し、design-plan.md の実書き換えは別 PR で):

- **§3「アーキテクチャ」**: 全体図とコンテナ表から togopackage を撤去し、Oxigraph + 自作 MCP に差し替え。
- **§4「RDF スキーマ設計」**: 変更なし (PROV-O 中心の方針は維持)。ただし `csv_to_ttl.py` で確認した「**bnode を使わず IRI で命名**」方針を §4.1 か §5 に明記し、Oxigraph re-ingest の冪等性を保証する。
- **§5「CSV → RDF 変換の実装」**: ingester は Python rdflib で確定。rdf-config は使わない (model.yaml / SPARQL 生成を含め採用見送り)。理由は togopackage を切ったため rdf-config のもう一段の依存を抱える意義が薄れたこと、および Morph-KGC が「rdf-config の代替としても十分」だが §3.2 の結論で Phase 1 では使わないこと。**ShEx 検証だけは rdf-config の出力を借用する選択肢を残す** (Phase 2 で再評価)。
- **§6「SPARQL endpoint と MCP」**:
  - `compose.yaml` の togopackage を **Oxigraph + 自作 MCP** に書き換え。
  - 公開 MCP ツール一覧から sparqlist 由来のものを削除 (template_* は自作で実装)。
  - `nl_to_sparql` は handoff の方針通り **Phase 2 でも実装しない** (Claude 側で SPARQL 生成 → `sparql_query` で実行)。
- **§7「Graphium 連携」**: 変更なし (IRI ベースの引用と PROV-O 中心の方針は維持)。
- **§10「ロードマップ」**: Phase 1 のスタート点が **Oxigraph 採用前提**であることを明記。「Phase 0.5 で採用したバックエンド」を「Oxigraph」に確定書き換え。
- **§11「既知のリスクと未確定事項」**:
  - 致命的リスク 1 (QLever 静的インデックス) は **解消** (Oxigraph 採用で回避)。
  - 致命的リスク 2 (togopackage ロックイン) は **解消** (撤退で回避)。
  - 致命的リスク 3 (togopackage LICENSE) は **解消** (依存を切ったため)。
  - 「代替アーキテクチャ (撤退路)」を **正路に昇格**し、§11 の代わりに §3 で正式記述する。

---

## 6. 次の Phase 1 への申し送り

1. **着手順序**: papers ingester (`ingest/src/csv2rdf/starrydata.py::ingest_papers`) から。`experiments/phase05/scripts/csv_to_ttl.py` を叩き台にして、JSON 列の展開ロジックを **bnode 排除版**に書き直す。
2. **コンテナ構成**: `compose.yaml` を最小構成で書く (Oxigraph + 自作 MCP のみ)。upload_api / ingest watcher は Phase 1 後半。
3. **ベンチ環境**: Phase 0.5 で書いた `scripts/bench_oxigraph.py` を Phase 1 でも使い回せる形に refactor (各 dataset に対して `pytest -m bench` で走る形が望ましい)。
4. **IRI 確定**: 設計プラン §4.0 を踏襲し GitHub Pages を Phase 1 で立てる (`kumagallium.github.io/csv2rdf-mcp/...`)。w3id.org への PR は Phase 2 以降。
5. **rdf-config の扱い**: いまは依存しない。Phase 2 で ShEx 検証が欲しくなったら **CLI から呼ぶだけ**で済ませる (リポジトリには取り込まない)。

---

## 7. 補追検証 (2026-05-28): Virtuoso backend / ハイブリッド構成

ユーザのレビュー (「LICENSE 不在より理由 2-4 が大きそう」「Virtuoso backend を試していない」) を受け、Phase 0.5 で未検証だった経路を潰した。詳細は [`experiments/phase05b/togopackage-virtuoso/README.md`](../../experiments/phase05b/togopackage-virtuoso/README.md) 参照。

### 7.1 Virtuoso backend で SPARQL UPDATE は通った

| 経路 | 結果 |
|---|---|
| sparql-proxy POST `application/sparql-update` | HTTP 415 (proxy が block) |
| Virtuoso :8890/sparql 匿名 | 403 (togopackage が起動時に revoke) |
| **Virtuoso :8890/sparql-auth + dba digest** | **HTTP 200**、INSERT DATA mean 7.45 ms |
| container restart 後の永続性 | 残る (Virtuoso checkpoint で保持) |

→ Phase 0.5 で「致命的」と書いた "live UPDATE 不可" は **解消**。csv2rdf-mcp の write tool だけが `/sparql-auth` を使い、read は sparql-proxy 経由でフィルタを通す役割分担が成立する。

### 7.2 残った理由 3, 4 の重み

| 理由 | 状態 |
|---|---|
| 1. LICENSE 不在 | 残る (チーム内交渉で解消可能) |
| 2. live 追記不可 | **解消** |
| 3. image 2.16 GB | 残る (Lab/Org scope では許容、Personal scope では重い) |
| 4. レイテンシ 3-hop (25-37 ms vs Oxigraph 直結 2-9 ms) | 残る (実用上問題なし、但し AI が多数 SPARQL を叩くと積み上がる) |

→ 「致命的」だった 2 が降格したので、**togopackage 採用の余地は再浮上**。

### 7.3 ハイブリッド構成が成立する

togomcp の MIE 仕様 ([`/vendor/togomcp/docs/MIE_file_specs.md`]) で `schema_info.endpoint: uri` は **任意の SPARQL endpoint URI** を受け付けると確認 (例: chebi.yaml は `https://rdfportal.org/ebi/sparql` を指す)。

つまり以下が成立する:

```
CSV → Python rdflib ingester → Turtle → Oxigraph (Apache-2.0, 53.6 MB)
                                            ↑ SPARQL 1.1 直結 (read + write)
                                       togomcp standalone (MIT, pip install)
                                            ↑ MIE YAML (endpoint: oxigraph)
                                       AI client
```

これにより:
- **Oxigraph のスピードと UPDATE の素直さ**を取る
- **チームの MCP 設計 (MIE / ShEx / sparql_query_examples)** を借りる
- **togopackage wrapper** は使わない (LICENSE / size / restart の 3 つを回避)
- **rdf-config** は必要時に CLI で呼ぶだけ (model.yaml / ShEx / SPARQL 生成、MIT)

これが「**チームの仕事を活かす × ソブリン制約**」の両立解。設計プランの Vault (§13) フェーズで Web UI を被せるときも、togomcp の MIE を真ん中に置けば再利用しやすい。

### 7.4 最終的な選択肢 (Phase 1 着手時に確定)

| 案 | 利点 | 欠点 | Phase 1 までの工数 |
|---|---|---|---|
| **A**: Oxigraph + 自作 MCP | 最速で立ち上がる / 完全に自前 | チームの MIE 設計を活かさない | 最短 |
| **B**: Oxigraph + togomcp ハイブリッド (**Phase 0.5b 推奨**) | チームの設計を借りつつ Oxigraph の長所も取る | togomcp の MIE を 1 ファイル書く必要あり | + 半日 |
| **C**: togopackage (Virtuoso) で行く | チームの ecosystem に完全に乗る / sparqlist / grasp も同梱で使える | LICENSE / image size / read latency が残る | + 1 日 (Virtuoso 起動が遅いことの運用受容含む) |

**推奨**: ユーザヒアリング (チームとの調整、Personal scope の優先度、sparqlist / grasp の現実的な必要性) を経て、Phase 1 着手時に確定する。**初版 (§1) の暫定結論はそのまま Phase 1 のスタート点として使えるが**、本補追を踏まえて B または C に切り替える余地を明示しておく。

### 7.5 副産物: Virtuoso 向けクエリ注意点

bench を連続実行する条件下で、Virtuoso が `define sql:big-data-const 0` を auto-prepend し、`FILTER (?d >= "2015-01-01"^^xsd:date)` 構文と衝突 (SP030 "Too many closing parentheses") する挙動を観察。**回避**: `FILTER ( STR(?d) >= "2015-01-01" )` のように xsd:date 比較を文字列比較に置き換える (ISO 8601 は辞書順で意味論が保たれる)。Phase 1 で Virtuoso 経路を採用するなら template クエリ集の **方言レイヤ**で対応する必要がある。
