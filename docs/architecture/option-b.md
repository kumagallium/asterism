# Option B (Oxigraph + togomcp ハイブリッド) のアーキテクチャと役割分担

Phase 0.5 / 0.5b の結論として有力候補となった「**B 案: Oxigraph backend + togomcp standalone**」を Phase 1 で採用する前提で、依存関係と役割分担を明文化する。

関連: [`phase05-decisions.md`](phase05-decisions.md) §7 / [`../../experiments/phase05b/togopackage-virtuoso/README.md`](../../experiments/phase05b/togopackage-virtuoso/README.md)

---

## 1. 全体図

```
═══════════════════════════════════════════════════════════════════════
   AI クライアント (ユーザの PC または Crucible 連携経由)
═══════════════════════════════════════════════════════════════════════
   ┌──────────────────────────────────────────────────────────────┐
   │  Claude Desktop / Graphium (Tauri) / Cline / 他 MCP client    │
   └──────────────────────────────────────────────────────────────┘
                                ▲
                                │ MCP protocol (stdio or HTTP/SSE)
                                │ tools: sparql_query / describe / etc.
                                │
═══════════════════════════════════════════════════════════════════════
   asterism deployment (closed server, 単一 docker compose スタック)
═══════════════════════════════════════════════════════════════════════

   ┌──────────────────────────────────────────────────────────────┐
   │  togomcp (standalone)                            ← dbcls/togomcp │
   │  ─────────────────────────────────────────────────────────── │
   │  ・MCP プロトコルを話す                                       │
   │  ・MIE YAML を読み込み schema_info / shape_expressions /      │
   │    sample_rdf_entries / sparql_query_examples を AI に提示     │
   │  ・SPARQL クエリ実行ツールを露出 (任意の endpoint に向く)     │
   │                                                              │
   │  License: MIT   Install: pip / Docker   Lang: Python 3.11+   │
   └──────────────────────────────────────────────────────────────┘
            ▲ (1) MIE YAML を読む           │ (2) SPARQL HTTP
            │   data/togomcp/mie/starrydata.yaml │ POST application/sparql-query
            │                                │ POST application/sparql-update
            │                                ▼
   ┌──────────────────────┐   ┌──────────────────────────────────┐
   │ MIE YAML (本リポジトリ) │   │ Oxigraph (SPARQL endpoint)        │
   │ data/togomcp/mie/    │   │ ────────────────────────────────  │
   │ starrydata.yaml      │   │ ・SPARQL 1.1 Query + Update + GSP │
   │                      │   │ ・ストア: 1 ファイル / store/        │
   │ ── 共有契約 ──        │   │ ・追記は IRI 由来 triple は冪等   │
   │  (詳細は §3)         │   │                                  │
   │                      │   │ License: Apache-2.0              │
   │                      │   │ Install: Docker (53.6 MB) / cargo │
   │                      │   │ Lang: Rust                        │
   └──────────────────────┘   └──────────────────────────────────┘
                                ▲
                                │ POST Turtle (Graph Store Protocol)
                                │
   ┌──────────────────────────────────────────────────────────────┐
   │  ingester (Python rdflib + 自作スキーマ変換)                  │
   │  ─────────────────────────────────────────────────────────── │
   │  ・data/sources/csv/ を watcher で監視                       │
   │  ・papers / samples / curves を sd:* に変換                  │
   │  ・JSON 埋め込み列 (author / issued / x, y) は手で展開する    │
   │  ・PROV-O (sd:IngestionActivity) を毎回出す                  │
   │                                                              │
   │  License: Apache-2.0   Lang: Python 3.11+ (uv)               │
   └──────────────────────────────────────────────────────────────┘
                                ▲
═══════════════════════════════════════════════════════════════════════
   外部入力
═══════════════════════════════════════════════════════════════════════
                                │
            ┌───────────────────┴────────────────────┐
            │  CSV files                             │
            │  - Graphium の Upload UI 経由           │
            │  - REST API (upload_api) 経由           │
            │  - data/sources/csv/ に直接ドロップ      │
            └────────────────────────────────────────┘
```

### 補足図: MIE YAML を「共有契約」とした依存方向

```
   asterism 側                       dbcls 側
   ─────────────                        ────────
                                                  
   ingester           ─writes─►  RDF (Oxigraph)   
                                       │          
                                       │ SPARQL    
                                       ▼          
   starrydata.yaml    ─read by─►  togomcp ◄─── dbcls/togomcp の release
        ▲                              ▲          
        │ author                       │ author    
        │                              │          
   asterism owner              dbcls チーム      
        │                              │          
        └─── 共有契約は MIE Spec v1.1 ────┘          
            (dbcls/togomcp/docs/MIE_file_specs.md)
```

---

## 2. レイヤー別の役割分担

| 層 | 実装 | リポジトリ | License | 主担当 | 副担当 (PR 経路) |
|---|---|---|---|---|---|
| AI クライアント | Claude Desktop / Graphium | (各 vendor) | (各) | ユーザ自身 | — |
| MCP server | **togomcp** | `dbcls/togomcp` | MIT | **dbcls チーム** | asterism 側が PR 可 |
| MCP 設定 (schema 記述) | **MIE YAML** (starrydata.yaml) | `kumagallium/asterism` | Apache-2.0 | **asterism 側** | dbcls チームがレビュー / PR 可 |
| SPARQL endpoint | **Oxigraph** | `oxigraph/oxigraph` | Apache-2.0 | asterism 側 (deploy / config) | (Oxigraph 本体は upstream) |
| Ingester | **Python rdflib + 自作** | `kumagallium/asterism` | Apache-2.0 | **asterism 側** | dbcls チームがレビュー可 |
| Watcher / API | **upload_api + watcher** | `kumagallium/asterism` | Apache-2.0 | **asterism 側** | — |
| (将来) Vault / Web UI | **asterism-vault** (別リポジトリ予定) | — | Apache-2.0 | asterism 側 | — |
| (任意) RDF 設計補助 | **rdf-config** (CLI) | `dbcls/rdf-config` | MIT | dbcls チーム | asterism 側は CLI 利用のみ |

### 「主担当 / 副担当」とは

- **主担当**: そのコンポーネントの設計・実装・リリースを決める権限を持つ
- **副担当**: PR / Issue / 提案で改善を働きかけられる (main branch への merge 権限は主担当が持つ)

---

## 3. 共有契約: MIE YAML (`starrydata.yaml`)

MIE YAML は **dbcls/togomcp が定義する規格**で、AI に「このデータベースをどう使うか」を説明するメタデータ。asterism 側は自分のスキーマ用にこの規格に従う 1 ファイルを書く。

### ファイル配置

```
asterism/
└── data/
    └── togomcp/
        └── mie/
            └── starrydata.yaml        ← asterism が書く
```

togomcp 本体は起動時に `/data/togomcp/mie/*.yaml` を読み込む (togopackage の慣例と同じディレクトリ規約)。

### YAML の内容 (MIE Spec v1.1 準拠)

```yaml
schema_info:
  title: Starrydata (Thermoelectric / Battery / Magnetic Materials)
  description: |
    Curated measurement curves digitized from thermoelectric, battery, and
    magnetic materials publications. 56k papers / 144k samples / 233k curves.
  endpoint: http://oxigraph:7878/query    # ← asterism の Oxigraph を指す
  base_uri: https://kumagallium.github.io/asterism/starrydata/ontology#
  graphs:
    - https://kumagallium.github.io/asterism/starrydata/graph/papers
    - https://kumagallium.github.io/asterism/starrydata/graph/samples
    - https://kumagallium.github.io/asterism/starrydata/graph/curves
  kw_search_tools: ["sparql"]              # 全文検索が要れば後で拡張
  version:
    mie_version: "1.1"                     # dbcls/togomcp の規格バージョン
    mie_created: "2026-06-XX"
    data_version: "starrydata 2026-05"
    update_frequency: "irregular"
  license:
    data_license: "CC BY 4.0"              # starrydata 本体の許諾を確認
    license_url: "https://creativecommons.org/licenses/by/4.0/"
  access:
    rate_limiting: "none (self-hosted)"
    max_query_timeout: "60 seconds"
    backend: "Oxigraph"                    # togomcp は backend 表示のみで挙動は変えない

shape_expressions: |
  PREFIX sd: <...>
  <PaperShape> { a [ sd:Paper ] ; schema:name xsd:string ; ... }
  <SampleShape> { ... }
  <CurveShape> { ... }

sample_rdf_entries:
  - title: "Sample Paper: Snyder 2014 thermoelectric review"
    rdf: |
      @prefix sd: <...> .
      sdr:paper/1 a sd:Paper ; schema:name "..." ; schema:author sdr:person/1/0 .
  - title: "Sample Curve: Bi2Te3 Seebeck (300-650 K)"
    rdf: |
      sdr:curve/79 a sd:Curve ; sd:propertyY "Seebeck coefficient" ; sd:yMax 0.00035 .

sparql_query_examples:
  - title: "Find curves by composition substring"
    description: "AI が「Bi2Te3 系の Seebeck カーブを探して」と言われたとき使う想定"
    query: |
      PREFIX sd: <...>
      SELECT ?curve ?propY WHERE { ... }

cross_references: []
architectural_notes: |
  Phase 1 では curve の x/y 配列は JSON literal + 集約値 (xMin/xMax/yMin/yMax)
  で保持。範囲クエリは集約値で当て、詳細取得は MCP ツール側で。詳しくは
  docs/ontology/diagram.md と docs/ontology/starrydata.ttl を参照。

data_statistics:
  papers: 56390
  samples: 144091
  curves: 233104

anti_patterns: |
  - x が 300-400 K の領域での y のピーク のような 2 次元局所範囲クエリは
    集約値だけでは答えられない。MCP ツール (template_curve_fetch) で
    カーブ全体を取得して x[]/y[] を補間する。

common_errors: |
  - schema:datePublished の FILTER で Virtuoso 経路に切替えると
    define sql:big-data-const 0 の auto-prepend と xsd:date 構文が
    衝突することがある (Oxigraph 経路では発生しない)。
```

(実物は Phase 1 着手時に MIE Spec v1.1 を引きながら詰める。長さは 300-500 行を想定 — chebi.yaml の 627 行を参考に。)

### なぜこれが「契約」か

- **togomcp 側はこのファイルしか読まない**: コードを asterism 側で変更しても togomcp は影響を受けない
- **asterism 側はこのファイルさえ書けば AI から扱える**: togomcp 本体を変更する必要がない
- **両側ともこのファイルにレビューを入れられる**: PR で議論可、main へのマージ権限は asterism owner

---

## 4. 依存方向と更新経路

### リポジトリ依存

```
kumagallium/asterism                    
       │                                    
       ├─ pip dependency ──► dbcls/togomc​p ─┐
       │                                    │
       └─ Docker pull   ──► oxigraph/oxigraph
                                            │
                            (各 upstream は ←────── 我々が触る必要なし)
                            独立に開発される
```

- asterism は togomcp を **pip パッケージとして固定バージョン pinning** する (`pyproject.toml` に `togomcp == 3.0.0` 等)
- togomcp 側のリリースに合わせて asterism は **pin を上げる PR** を作る (CI で MIE Spec の互換性を検証)
- Oxigraph も同様 (Docker image tag を `:0.4.X` でピン)

### 更新の流れ (代表 3 ケース)

**A. dbcls チームが togomcp に新機能を追加するケース** (例: 全文検索ツール追加)

```
1. dbcls/togomcp に PR → main merge → release v3.1.0
2. asterism 側: pyproject.toml の pin を 3.0.0 → 3.1.0 に上げる PR
3. 必要なら MIE YAML に新機能を使うエントリを追加 (kw_search_tools: ["fulltext", "sparql"])
4. asterism 側でテスト → merge
```

**B. asterism 側が starrydata のスキーマを変えたいケース** (例: PROV のフィールド追加)

```
1. asterism の ingester を更新 (RDF 出力に新フィールド)
2. data/togomcp/mie/starrydata.yaml の shape_expressions と sample_rdf_entries を更新
3. asterism 側でテスト → merge
4. togomcp 本体は触らない (再起動だけ)
```

**C. dbcls チームが MIE Spec を v1.1 → v1.2 に進化させるケース** (例: 新セクション追加)

```
1. dbcls/togomcp の docs/MIE_file_specs.md と loader を更新 → release v3.2.0
2. asterism 側: togomcp pin を上げると同時に starrydata.yaml を v1.2 形式に migrate
   (新セクションが optional なら触らなくて良い)
3. asterism 側でテスト → merge
```

---

## 5. dbcls チームが触れる範囲

「**開発エフォートが asterism owner だけに集中しない**」ためのチェック。**A は dbcls チームが自然に作業できる、B は両側で議論できる、C は asterism owner が一義に決める**。

| 領域 | dbcls チームが触れるか | 触り方 |
|---|---|---|
| **A. togomcp 本体 (MCP server impl)** | ◎ 自然に作業 | dbcls/togomcp 本体への PR / release。asterism 側は pin を上げて取り込む |
| **A'. togomcp の新ツール / バグ修正 / 性能改善** | ◎ 自然に作業 | 上に同じ |
| **A''. MIE Spec の進化 (v1.1 → v1.2)** | ◎ 自然に作業 | docs/MIE_file_specs.md と loader を改訂。asterism 側は migrate するだけ |
| **B. starrydata MIE (starrydata.yaml) の改善** | ○ レビュー / PR で議論 | asterism リポジトリへの PR (shape_expressions の文法、sparql_query_examples の追加など)。**dbcls チームは MIE の使い方の専門家なので、レビュアーとして特に価値が出る** |
| **B'. starrydata MIE を dbcls/togomcp に mirror する** | ◎ 共同所有 | `dbcls/togomcp/mie/starrydata.yaml` への PR を asterism 側が出す。マージ後は dbcls チームが他 MIE と同じく管理。両側で同期する運用も可 (詳細は §7) |
| **C. asterism の ingester (Python)** | △ レビュー可、コミットは asterism owner | rdflib 周りや PROV-O 出力の議論に dbcls チームが入る価値あり |
| **C'. Oxigraph の deploy / 運用** | △ 提案 / 議論 | compose.yaml や運用 tips の議論に dbcls チームが参加可 |
| **D. Vault Web UI (将来)** | × asterism owner が決める | デザイン議論にはもちろん参加可だが、実装エフォートは asterism 側 |
| **E. rdf-config の CLI 用法** | ◎ 自然に作業 | dbcls/rdf-config 本体への PR。asterism 側は CLI を呼ぶだけ |

### 「両側でできる」ことの具体例 (Phase 1 〜 3)

1. **starrydata MIE を共同編集する** (B + B')
   - `dbcls/togomcp/mie/` に PR → 材料系の MIE が dbcls の登録 DB に初めて加わる
   - chebi.yaml や uniprot.yaml と同じ運用に乗る (CI で文法検証など)
   - 改善は両側どちらからでも PR 可能
2. **togomcp の "external Oxigraph endpoint mode" を強化する** (A)
   - 現在の togomcp は RDF Portal の hosted endpoint を主眼としているが、self-hosted Oxigraph を 1st-class に扱うための小さい改善があれば dbcls チーム側で実装
3. **MIE Spec を materials-science friendly に拡張する** (A'')
   - 例: `data_statistics` に curves / samples / measurement units の section を足すなど
   - asterism 側はその拡張を最初に使う実例として starrydata.yaml で実証
4. **togo-mcp-admin (MIE 生成 / 検証ツール) の改善** (A)
   - asterism の Phase 3 で「任意 CSV → schema 推論 → MIE 自動生成」を考える時、togo-mcp-admin に「外部スキーマからの MIE 雛形生成」機能があれば便利
   - dbcls チームが admin 側を伸ばし、asterism が利用する

---

## 6. 開発エフォートの想定割合 (Phase 1)

Phase 1 (starrydata 固定スキーマで E2E) を 2-3 日で立ち上げる前提:

| 作業 | 工数 | 担当 |
|---|---|---|
| ingester (papers / samples / curves) を Python rdflib で実装 | 1-1.5 日 | asterism owner |
| Oxigraph の compose.yaml 整備 + 初期データロード | 0.5 日 | asterism owner |
| starrydata.yaml (MIE) を chebi.yaml をベースに書く | 0.5-1 日 | asterism owner (dbcls チームレビュー推奨) |
| togomcp standalone を pip install + compose に組み込み | 0.25 日 | asterism owner |
| AI client (Claude Desktop) から E2E 確認 | 0.25 日 | asterism owner |
| **(dbcls チーム側) togomcp 本体に必要な改善があれば PR** | 任意 | dbcls チーム |

**初期は asterism owner が 9 割、dbcls チームは starrydata MIE のレビューが主**。Phase 2 以降で togomcp 側の改善需要が出てきたタイミングで、自然に dbcls チーム側にもエフォートが分散する想定。

長期的には:
- **dbcls チーム**: togomcp 本体 / MIE Spec / 関連ツール (admin / rdf-config) の進化
- **asterism owner**: ingester / Oxigraph 運用 / starrydata MIE のメンテナンス / Vault Web UI 等の上層

---

## 7. starrydata MIE の所有モデル (両側 mirror か単独か)

MIE YAML をどこに置くかで 3 つの選択肢:

| 選択肢 | 利点 | 欠点 |
|---|---|---|
| **(i) asterism 側のみに置く** | 編集が早い / リリースサイクルが独立 | dbcls の MIE エコシステムから孤立 |
| **(ii) dbcls/togomcp 側のみに置く** | 他の MIE と同じ運用に乗る / dbcls の CI で文法検証される | asterism 側のリリースが dbcls 側の release pace に縛られる |
| **(iii) 両側に置き、git submodule / symlink / sync script で同期** | 良いとこ取り | 同期の運用が必要 |

**推奨: (i) から始めて、安定してきたら (iii) に移行**。Phase 1 では starrydata.yaml が頻繁に変わるので asterism 側で速く回す。Phase 2 で安定 (≒ shape_expressions が固まる) してから dbcls/togomcp に upstream で PR を出して mirror する。

---

## 8. 撤退路 (B が成立しなかった場合)

万一 togomcp が arbitrary endpoint で実用に耐えないことが Phase 1 着手時に判明した場合:

```
B (Oxigraph + togomcp) ──失敗 →   A (Oxigraph + 自作 MCP)
```

- Oxigraph 部分はそのまま残せる
- togomcp を外し、`asterism_mcp/` ディレクトリに最小の自作 MCP server を Python で書く (handoff §10 Phase 2 当初案)
- starrydata.yaml は捨てる、または別ライブラリで読み込んで自作 MCP の中で使う

**Phase 1 着手の最初の半日で integration test** (Oxigraph + togomcp + Claude Desktop で E2E が通るか) を行うことで、撤退判断を早期に下せる。具体的な test 手順は Phase 1 の PR で別途定義。

---

## 9. 自前で進める範囲 vs dbcls 待ちにする範囲

開発エフォート分散は望ましいが、**dbcls 側の作業を待って開発がブロックされない
こと**を優先する。以下の決定基準で運用する (2026-05-28 ユーザ確認):

| 項目 | 方針 | 補足 |
|---|---|---|
| **togomcp の Docker image** | **自前 build** ([`infra/togomcp/Dockerfile`](../../infra/togomcp/Dockerfile)) | 公式 image が未公開。commit SHA を Dockerfile の `ARG TOGOMCP_REF` で pin して manual upgrade。公式 image が出たら `compose.yaml` の `build:` を `image:` に切り替えるだけ |
| **togomcp 本体のバグ修正** | **必要時に fork → PR は async** | SHA pin で逃げられるので、Phase 1+ の即時 unblock を優先 |
| **MIE Spec の進化** | **async、待たない** | v1.1 を使い続ければ機能停止しない。新セクションが必要になったときに dbcls 側で議論 |
| **starrydata MIE を `dbcls/togomcp/mie/` に upstream PR** | **async、ユーザが async で実施** | 我々の repo 内 (`data/togomcp/mie/starrydata.yaml`) で動くので待ちにならない |
| **連絡・打診** | **ユーザが async で実施** | チーム関係性はユーザ主導 |
| **`dbcls/togopackage` LICENSE** | **無視** | togopackage 自体を採用していないため影響なし |
| **rdf-config の改善** | **必要時に CLI として呼ぶだけ** | リポジトリには取り込まないので待ちにならない |

### togomcp container の起動順序問題への対処

Oxigraph image は scratch-like で `wget` / `curl` / `sh` を含まないため、
compose の healthcheck で SPARQL endpoint の生死を判定できない。代わりに
**togomcp container 側の ENTRYPOINT** ([`infra/togomcp/entrypoint.sh`](../../infra/togomcp/entrypoint.sh))
で `oxigraph:7878` の TCP がオープンになるまで Python `socket.create_connection`
で polling する。これで `depends_on` (start 順) のみ指定で済む。

### `TOGOMCP_DIR` overlay 戦略

`server.py` の `TOGOMCP_DIR` を完全に上書きすると、package 同梱の `docs/` や
`resources/MIE_prompt.md` などのデフォルトファイルが読めなくなり、togomcp が
500 を返す経路ができる。entrypoint で次の overlay を作って回避:

1. `/data/togo_mcp/data/` (package デフォルト) を `${HOME}/togomcp-overlay/` に copy
2. compose で bind mount された `/data/togomcp/` (我々の差分) を上に merge
3. `endpoints.csv` だけは特例で「package 版 + 我々の追加行」を append で結合
4. `TOGOMCP_DIR=${HOME}/togomcp-overlay` を export して togomcp 本体を exec

これにより **asterism の repo には差分の最小ファイルだけ** (本 PR では
`data/togomcp/mie/starrydata.yaml` と `data/togomcp/resources/endpoints.csv`
の 2 つ) を持てば動く。

## 用語

- **MIE (Metadata Interoperability Exchange)**: dbcls/togomcp の独自規格。「AI にデータベースの取り扱いを教える YAML」 ([spec](https://github.com/dbcls/togomcp/blob/main/docs/MIE_file_specs.md))
- **ShEx (Shape Expressions)**: RDF データの構造制約を書く W3C 標準 ([spec](https://shex.io/))
- **PROV-O**: 来歴を RDF で表す W3C 標準 ([spec](https://www.w3.org/TR/prov-o/))
- **SPARQL 1.1 Graph Store Protocol**: HTTP POST で Turtle を直接ストアに送り込む規約 ([spec](https://www.w3.org/TR/sparql11-http-rdf-update/))
- **closed server**: ソブリン制約 (本プロジェクトの基本前提) における「境界の内側」のサーバ。インターネットから直接アクセスされない self-hosted 環境
