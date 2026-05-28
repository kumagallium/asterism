# csv2rdf-mcp — 設計プラン

> CSV を放り込めば RDF 化して SPARQL/MCP で検索できる、Graphium / Crucible 連携を見据えた単独リポジトリの設計ドキュメント。
> 題材は starrydata（熱電・電池・磁性材料データ）、最終的には任意 CSV を受け付ける汎用ツールへ。
> 作成日: 2026-05-27

---

## 0. 設計の前提（ソブリン制約）

Crucible と Graphium はどちらも **ソブリン（self-sovereign）** を重視している:
- **Crucible**: closed server / 閉じた PC 上で動かす MCP インフラ
- **Graphium**: ユーザの PC 上で動くデスクトップアプリ（Tauri）

csv2rdf-mcp および Vault もこの哲学に従う。すなわち:

1. **データは境界を越えない**: ユーザのデータ・ノート・RDF はすべて閉じた環境内に留まる
2. **外向きの経路は明示的**: Zenodo への graduate（公開リリース）、外部 SPARQL endpoint への read-only fetch — これらだけが境界を跨ぐ
3. **すべてのコンポーネントは self-hostable**: マルチテナント SaaS としては作らない
4. **Crucible は registry（カタログ）であって proxy ではない**: データの通り道ではなく、発見の道具
5. **Graphium はデスクトップから直接 MCP に接続**: 中継サーバ不要

### 全体アーキテクチャ（3 層）

```
┌─ PUBLIC INTERNET ──────────────────────────────────────────────┐
│  Zenodo / RDF Portal / togomcp.rdfportal.org / w3id.org        │
└─────────────▲────────────────────────────────────▼─────────────┘
              │ graduate (export)        read-only fetch
              │                                    │
┌─ CLOSED SERVER（ソブリン境界、self-hosted）─────────────────────┐
│                                                                │
│  ┌──────────────┐   ┌────────────────────────────────────────┐ │
│  │  Crucible    │──▶│ MCP servers（並走デプロイ）            │ │
│  │  Registry    │   │  ┌─────────────┐ ┌──────────────────┐ │ │
│  │  (catalog)   │   │  │ csv2rdf-mcp │ │ Vault (任意 UI)  │ │ │
│  │              │   │  │ + Oxigraph  │ │ + multi-DS 管理  │ │ │
│  │              │   │  └─────────────┘ └──────────────────┘ │ │
│  │              │   │  ┌─────────────────────────────────┐  │ │
│  │              │   │  │ 他の MCP（filesystem, slack...）│  │ │
│  │              │   │  └─────────────────────────────────┘  │ │
│  └──────────────┘   └────────────────────────────────────────┘ │
└────────────▲────────────────────────▲──────────────────────────┘
             │ discovery               │ MCP / SPARQL（直接接続）
             │                         │
┌─ USER'S PC ─────────────────────────────────────────────────────┐
│  Graphium（Tauri デスクトップ、複数 MCP 同時接続）              │
└─────────────────────────────────────────────────────────────────┘
```

**重要な含意**:
- csv2rdf-mcp は **Crucible に「載せる」のではなく、同じ closed server で並走**する独立 MCP サーバ。Crucible はそれを registry に載せるだけ。
- csv2rdf-mcp 単体で立てて Crucible 無しで使うこともできる（Graphium から直接接続）。
- Vault も SaaS ではなく、csv2rdf-mcp の **同居 Web UI 層**として再定義する（§13）。

### 0.1 マルチスコープ運用（Personal / Lab / Org のハイブリッド）

ソブリン世界の自然な帰結として、**同じユーザが複数の Crucible deployment を同時に使う**ことが現実的な運用形態になる。

| スコープ | 場所 | 例 | アクセス制御 |
|---|---|---|---|
| **Personal** | 個人の PC（自分の Mac / Linux） | `http://localhost:7000` | 本人のみ |
| **Lab / Team** | 研究室や小チームのサーバ | `https://lab.kumagai.local` | チームメンバー |
| **Org** | 全学/全社の IT 管理サーバ | `https://corp.example.com` | 全員（SSO / 監査） |
| **External** | Zenodo / RDF Portal など | `https://zenodo.org/...` | 世界公開 |

**設計が compositional なので追加コンポーネント不要で対応可**。Crucible は registry（カタログ）であって proxy ではないので、複数あっても矛盾しない。MCP クライアントは複数 MCP を同時接続できる仕様なので、Graphium は personal / lab / org の 3 つの Crucible をすべて参照しつつ、それぞれが指す MCP サーバに直接接続する。

#### graduate の階段（マルチスコープから自然に出てくる概念）

```
Personal Vault → Lab Vault → Org Vault → Zenodo
(個人 WIP)    (ラボ共有)  (全社共有)  (世界公開)
```

データは段階的に外向きに昇格できる。各昇格は明示的なゲートウェイ動作として PROV-O に記録される（`sd:GraduationActivity`）。

```turtle
sdr:curve/42  prov:wasGeneratedBy [
  a sd:GraduationActivity ;
  prov:atTime "2026-06-10T10:00:00Z" ;
  sd:fromScope <https://localhost/csv2rdf/> ;
  sd:toScope   <https://lab.kumagai.local/csv2rdf/> ;
  prov:wasAssociatedWith sdr:user/m-kumagai
] .
```

これにより「ある curve がいつ個人領域からラボに昇格し、いつ全社に上がり、いつ Zenodo に出たか」が PROV チェーンとして残る。論文撤回や引用元追跡が境界を越えて辿れる。

#### 追加で明文化する規約

| 項目 | 内容 |
|---|---|
| **IRI スコープ命名規約** | スコープが IRI のホスト名から読める。例: `http://localhost/csv2rdf/...`（personal）、`https://lab.<group>.local/csv2rdf/...`（lab）、`https://corp.example.com/csv2rdf/...`（org）、`https://w3id.org/csv2rdf/...`（public） |
| **スコープ宣言マニフェスト** | 各 csv2rdf-mcp deployment が `manifest.yaml` に `scope:` と `parent_scope_url:` を持つ。Vault の graduate 時に「親スコープ」を自動で発見できる |
| **IRI redirect 戦略** | 昇格時は新 IRI を発行し、旧 IRI から HTTP 301 で繋ぐ。古い PROV チェーンは旧 IRI を保持したまま辿れる |
| **MCP クライアントの multi-Crucible 設定** | Graphium 側で複数 Crucible を登録できる UI。接続済み MCP に `@personal` / `@lab` / `@org` のタグを表示 |
| **名前衝突の disambiguation** | 同名 MCP（personal と org の両方に `csv2rdf` がある）の場合、AI に渡すツール名を `csv2rdf@personal` のように qualified にする |
| **クロススコープ参照のフォールバック** | ノートが org IRI を引用していてユーザがオフラインで org に届かない時の表示: PROV snapshot から復元、または `unavailable` 表示 |

#### よくある運用パターン

- **大学院生**: Personal（PC）＋ Lab（研究室サーバ）の 2 段。卒業時にラボ側へ全データを graduate する
- **会社員（厳格）**: Org のみ。Personal Crucible は禁止または許可制
- **クロスラボ共同研究者**: Personal ＋ Lab A ＋ Lab B の 3 つ。各ラボの IRI を引用しながら個人ノートで議論
- **オープンサイエンス志向**: Personal → Lab → Zenodo を頻繁に往復し、撤回・追記も PROV で公開

---

## 1. 目的とスコープ

### ゴール
- CSV を投入すると自動で RDF 化され、SPARQL endpoint と MCP サーバ経由で AI から検索できる仕組みを作る。
- 第一フェーズは **starrydata 固定スキーマ**で完成させる。
- 第二フェーズで **任意 CSV → スキーマ推論 → RDF 化**の汎用パイプラインへ拡張する。
- Graphium の UI から CSV をアップロードできるようにし、Graphium 上の AI が自データセットを検索可能にする。

### 非ゴール（少なくとも初期フェーズでは扱わない）
- starrydata 以外の大規模な化学・材料 ontology との完全アラインメント
- 認可・マルチテナント対応（社内 / 個人用途を前提）
- バックアップ・HA 構成

### 成功基準（MVP）
1. starrydata の papers / samples / curves が漏れなく RDF 化されている（行数突合）。
2. `http://localhost:10005/sparql` で代表的な 5 クエリ（後述）が結果を返す。
3. MCP クライアント（Claude Desktop / Crucible 経由）から自然言語で「composition X の Seebeck 係数のカーブを持つ sample を探して」が動く。
4. README に「`docker compose up` → CSV を `data/sources/csv/` に置く → 自動再インデックス」のフローが書いてある。

---

## 2. リポジトリ構成

### 命名案
`csv2rdf-mcp`（または `togotable`, `tabular-rdf`）。GitHub 公開リポジトリとして単独で立てる。
ライセンスは Apache-2.0 推奨（依存先の rdf-config が MIT、togopackage は LICENSE 未確認なので確認次第合わせる）。

### ディレクトリ構造

```
csv2rdf-mcp/
├── README.md                    # クイックスタート（英語）
├── docs/                        # 外部向けドキュメント（英語）
│   ├── architecture.md
│   ├── csv-conventions.md
│   └── mcp-tools.md
├── docs/internal/               # 内部設計メモ（日本語可、.gitignore）
├── compose.yaml                 # docker compose の本体
├── data/                        # ランタイムデータ（.gitignore）
│   ├── config.yaml              # togopackage の設定
│   ├── sources/
│   │   ├── csv/                 # アップロード CSV の置き場（監視対象）
│   │   └── rdf/                 # 生成 Turtle の置き場
│   ├── rdf-config/              # スキーマ定義 YAML 群
│   │   └── starrydata/
│   │       ├── prefix.yaml
│   │       ├── model.yaml
│   │       ├── endpoint.yaml
│   │       ├── sparql.yaml
│   │       └── convert.yaml     # CSV→RDF マッピング（rdf-config）
│   ├── togomcp/
│   │   └── mie/
│   │       └── starrydata.yaml  # MCP 公開用 MIE
│   └── qlever/                  # QLever index（自動生成、.gitignore）
├── ingest/                      # CSV→RDF 変換のコード
│   ├── pyproject.toml
│   ├── src/csv2rdf/
│   │   ├── __init__.py
│   │   ├── cli.py               # `csv2rdf ingest <dataset>` の入口
│   │   ├── starrydata.py        # 固定スキーマ用の変換器
│   │   ├── generic.py           # 汎用 CSV → RDF（Phase 2）
│   │   ├── schema_inference.py  # 型・関係推論（Phase 2）
│   │   └── watcher.py           # data/sources/csv/ の inotify
│   └── tests/
├── api/                         # CSV アップロード受け口 (FastAPI)
│   ├── pyproject.toml
│   └── src/upload_api/
│       ├── main.py
│       └── routes.py
├── mcp/                         # 自作 MCP サーバ（togomcp で足りない部分）
│   ├── pyproject.toml
│   └── src/csv2rdf_mcp/
│       ├── server.py
│       ├── tools/
│       │   ├── sparql.py
│       │   ├── nl2sparql.py
│       │   ├── templates.py
│       │   └── fulltext.py
│       └── prompts/
└── .githooks/
    └── pre-commit               # ruff / mypy / RDF lint
```

### Crucible / Graphium との関係
- csv2rdf-mcp は **独立リポジトリ**。
- Crucible の MCP Registry には `mcp.json` 経由で接続情報を登録する（SSE もしくは stdio）。
- Graphium 側には UI ボタン（"Upload CSV dataset"）を追加し、csv2rdf-mcp の `api/` にファイルを POST する。

---

## 3. アーキテクチャ

### 全体図（テキスト）

```
┌─────────────────────────────────────────────────────────────────────┐
│  Graphium (BlockNote エディタ + AI)                                  │
│    ├─ "Upload CSV" UI ───────────────────────────────┐               │
│    └─ AI チャット ─→ MCP クライアント                  │               │
└──────────────────────┬────────────────────────────────┼──────────────┘
                       │ MCP/SSE                        │ HTTP multipart
                       ▼                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  csv2rdf-mcp (docker compose で1スタック)                            │
│                                                                     │
│  ┌──────────────┐  drop file  ┌──────────────┐  TTL    ┌─────────┐ │
│  │ upload_api   │────────────▶│ ingest       │────────▶│ data/   │ │
│  │ (FastAPI)    │             │ (Python      │         │ sources/│ │
│  │              │             │  rdflib +    │         │ rdf/    │ │
│  └──────────────┘             │  rdf-config) │         └────┬────┘ │
│                                └──────────────┘              │      │
│                                                              ▼      │
│                                ┌──────────────────────────────────┐│
│                                │ togopackage (1 コンテナ)         ││
│                                │  ├─ QLever  (SPARQL backend)     ││
│                                │  ├─ sparql-proxy                 ││
│                                │  ├─ sparqlist (テンプレ API)     ││
│                                │  ├─ grasp (GraphQL)              ││
│                                │  ├─ togomcp (MCP /mcp, /sse)     ││
│                                │  └─ rdf-config-mcp (代替 MCP)    ││
│                                └──────────────────────────────────┘│
│                                                              │      │
│  ┌──────────────────────────────────────────┐               │      │
│  │ csv2rdf_mcp (拡張 MCP, Python)           │◀──────────────┘      │
│  │  - SPARQL 実行                           │  proxy to /sparql    │
│  │  - NL → SPARQL（Claude Haiku 経由）      │                       │
│  │  - 事前定義テンプレート（sparqlist 連携）│                       │
│  │  - 全文検索（QLever の text-index 利用）│                       │
│  └──────────────────────────────────────────┘                       │
└─────────────────────────────────────────────────────────────────────┘
```

### コンテナと役割

| サービス | コンテナ | 役割 | ポート |
|---|---|---|---|
| `upload_api` | python:3.11-slim ベース自作 | CSV アップロード受け / ジョブ投入 | 8080 |
| `ingest` | 同上 | watcher が `data/sources/csv/` を監視、TTL 生成 | - (常駐) |
| `togopackage` | `ghcr.io/dbcls/togopackage:latest` | SPARQL + MCP + sparqlist + grasp | 10005 / 7001 / 8890 |
| `csv2rdf_mcp` | 自作 | LLM 向けに拡張 MCP ツールを提供 | 8090 (SSE) |

> **CSV 監視方式**: inotify ベースの watcher を `ingest` 側に常駐させ、新規 CSV を検知したらジョブを実行 → `data/sources/rdf/` に TTL を書き出し → togopackage の reload エンドポイントを叩く（無ければ docker compose restart）。

### データの流れ（CSV 到着から検索可能まで）

1. ユーザが Graphium UI / API / `data/sources/csv/` 直書きのいずれかで CSV を投入。
2. `ingest` watcher が新規ファイルを検知。
3. dataset が `starrydata` （ファイル名のサフィックスまたはマニフェストで判定）なら `csv2rdf.starrydata` を呼ぶ。それ以外は `csv2rdf.generic`（Phase 2）。
4. Turtle ファイルを `data/sources/rdf/<dataset>/*.ttl(.gz)` に書き出す。
5. togopackage の `config.yaml` の `sources:` に `<dataset>` が既登録なら QLever が差分インデックス、未登録なら configure を書き換えて togopackage を再起動。
6. MCP クライアントから検索可能になる。

---

## 4. RDF スキーマ設計（starrydata 用）

### 名前空間

| prefix | URI | 用途 |
|---|---|---|
| `sd` | `https://<user>.github.io/csv2rdf-mcp/starrydata/ontology#` | starrydata 独自クラス・プロパティ（Phase 1） |
| `sdr` | `https://<user>.github.io/csv2rdf-mcp/starrydata/resource/` | インスタンス URI 名前空間（Phase 1） |
| `schema` | `https://schema.org/` | Paper / Person |
| `prov` | `http://www.w3.org/ns/prov#` | 来歴（Graphium と相性◎） |
| `qudt` | `http://qudt.org/schema/qudt/` | 単位 |
| `unit` | `http://qudt.org/2.1/vocab/unit/` | 単位インスタンス（QUDT 2.1 系を使用） |

> **IRI の永続化戦略**: §4.0 を参照。Phase 1 では GitHub Pages、Phase 2 で w3id.org への PR で `https://w3id.org/csv2rdf/...` を取得し、GitHub Pages を redirect target にする。インスタンス URI 側は変えずに済む。

### 4.0 IRI 永続化戦略

RDF の IRI は **将来にわたって解決できる URL** であるべきなので、ドメイン選定は重要。当面の方針:

| 段階 | IRI 戦略 | 備考 |
|---|---|---|
| **Phase 1（MVP）** | GitHub Pages: `https://<user>.github.io/csv2rdf-mcp/...` | 即時に取得可。`docs/` を `gh-pages` ブランチに公開し、`/ontology` 配下に Turtle / HTML を静的配信。 |
| **Phase 2（公開時）** | w3id.org への PR で `https://w3id.org/csv2rdf/...` を取得し、GitHub Pages を redirect target に設定 | w3id は `perma-id/w3id.org` リポジトリへの PR ベース。条件は緩いが community review あり。 |
| **Phase 3（将来）** | 自前ドメイン（例: `csv2rdf.dev`）を持てれば w3id を切り替え | ここまで来れば社内/外問わず安心して引用してもらえる |

**重要な設計判断**: インスタンス URI（`...resource/paper/SID-12345` など）は **Phase 1 から Phase 3 まで一意に保つ**。理由は、Graphium のノートが特定 IRI を引用したあとに IRI を変えると、すべてのノートで参照が壊れるため。Phase 2 で w3id を導入するときは **redirect で受ける**、つまり IRI 文字列そのものは Phase 1 で出した GitHub Pages の URL を維持し、w3id は対外露出用のショートカットとして並走させるのが安全。

> **代替案として starrydata.org の運営に提案する選択肢**: starrydata の本家（科学技術振興機構系）に「公式 IRI 名前空間を切ってほしい」と提案できれば、最終的にはそれが王道。ただし合意形成に時間がかかるため、Phase 1〜2 は自前ドメインで進める。
| `bibo` | `http://purl.org/ontology/bibo/` | 学術論文補完 |
| `dcterms` | `http://purl.org/dc/terms/` | DOI / 日付 |
| `xsd` | `http://www.w3.org/2001/XMLSchema#` | リテラル型 |

### クラス

- `sd:Paper` ← schema:ScholarlyArticle のサブクラス
- `sd:Sample`
- `sd:Curve` ← prov:Entity
- `sd:DataPoint`（カーブ上の各点。後述の通り個別ノード化）
- `sd:Composition`（組成式を構造化）
- `sd:DigitizationActivity` ← prov:Activity（WebPlotDigitizer / StarryDigitizer 由来）

### プロパティ（抜粋）

```
sd:Paper
  ─ dcterms:identifier (SID)
  ─ schema:identifier (DOI)
  ─ schema:url
  ─ schema:datePublished
  ─ schema:name (title)
  ─ schema:author → schema:Person+ (authors の JSON を展開)
  ─ schema:isPartOf → schema:Periodical (journal)
  ─ bibo:volume / bibo:issue / bibo:pageStart / bibo:pageEnd
  ─ schema:publisher
  ─ dcterms:created
  ─ sd:projectName (multivalued)

sd:Sample
  ─ dcterms:identifier (sample_id)
  ─ schema:name (sample_name)
  ─ sd:hasComposition → sd:Composition
  ─ sd:compositionString (raw)
  ─ sd:fromPaper → sd:Paper
  ─ sd:hasDescriptor → sd:Descriptor (sample_info の JSON 展開)
  ─ dcterms:created / dcterms:modified

sd:Descriptor
  ─ sd:descriptorName (literal, 例: "MaterialFamily")
  ─ sd:descriptorCategory (literal)
  ─ sd:descriptorComment (literal)
  ─ sd:descriptorExtracted (literal)

sd:Curve
  ─ dcterms:identifier (figure_id)
  ─ sd:figureName
  ─ sd:ofSample → sd:Sample
  ─ sd:propertyX (literal, 例: "Temperature")
  ─ sd:propertyY (literal, 例: "Seebeck coefficient")
  ─ sd:unitX → qudt:Unit
  ─ sd:unitY → qudt:Unit
  ─ sd:dataPoints → (sd:DataPoint の連番、後述)
  ─ prov:wasGeneratedBy → sd:DigitizationActivity
  ─ sd:comments
```

### x/y 配列の表現方針（重要）

curves.csv の `x`/`y` は JSON 配列。データ点は 1 カーブあたり 5〜数百個、全体で 数千万件規模になりうる。これを愚直に `sd:DataPoint` ノードにすると爆発する。

3 つの選択肢:

| 方式 | サイズ | クエリ性 | 採用 |
|---|---|---|---|
| (A) 個別ノード化（`sd:DataPoint`） | 大 | SPARQL で範囲検索可 | △ Phase 2 |
| (B) `rdf:List` で配列保持 | 中 | SPARQL で扱いづらい | ✗ |
| (C) JSON literal を `xsd:string` で保持＋集約値（min, max, len, mean）を別プロパティ化 | 小 | 範囲検索は集約値で | **◯ Phase 1** |

**Phase 1 では (C) を採用**: `sd:xValuesJSON` `sd:yValuesJSON` をそのまま文字列で持ちつつ、`sd:xMin` `sd:xMax` `sd:yMin` `sd:yMax` `sd:pointCount` を Curve 直下のリテラルとして展開。範囲クエリは集約値で当て、詳細は MCP ツール側でカーブ取得→クライアントで配列を読む形にする。Phase 2 で利用状況を見て (A) に切り替える。

> **既知の限界**: 「x が 300〜400 K の領域での y のピーク」のような **2 次元の局所範囲クエリ** は集約値だけでは答えられない。代表クエリ A2 が `yMax > 0.0003` に簡略化されているのはこの限界の表れ。
> 実用上の頻出パターン（"温度 300 K での Seebeck 係数を比較"）は Phase 2 で (A) に切り替えるか、MCP ツール側でカーブを取得して x[] / y[] を補間する関数を持たせる。どちらが先かは Phase 1 のユーザヒアリングで決める。

### 単位の正規化

`unit_x`, `unit_y` には `K`, `V*K^(-1)`, `S/cm` などが入る。対応表を `data/rdf-config/starrydata/unit_map.yaml` に持ち、QUDT の `unit:` インスタンス URI に変換する。未マッチはリテラルとして `sd:unitStringRaw` に残す（情報損失を避ける）。

### PROV-O との接続（Graphium 連携の中心線）

PROV-O は PROV-DM の RDF 表現そのものなので、Graphium が PROV-DM を中核に据えている以上、**csv2rdf-mcp 側で出す RDF は最初から PROV-O 互換で出しておく**。これにより Graphium 側との橋渡しコードがほぼ不要になり、ノート ↔ データセットの相互引用が自然に PROV グラフとして繋がる。

#### クラス対応

| csv2rdf-mcp 側のクラス | PROV-O との関係 | 意味 |
|---|---|---|
| `sd:Paper` | `prov:Entity` の subclass | 学術論文（不変の Entity） |
| `sd:Sample` | `prov:Entity` の subclass | 実験で作られた試料 Entity |
| `sd:Curve` | `prov:Entity` の subclass | 図から抽出された測定曲線 Entity |
| `sd:DigitizationActivity` | `prov:Activity` の subclass | WebPlotDigitizer / StarryDigitizer による数値化作業 |
| `sd:IngestionActivity` | `prov:Activity` の subclass | csv2rdf-mcp 自身による CSV→RDF 変換作業 |
| `schema:Person` | `prov:Agent` | 論文の著者・データ抽出者 |
| `prov:SoftwareAgent` | そのまま | WebPlotDigitizer や csv2rdf-mcp 自身 |

#### 典型的な PROV パターン（starrydata 側で生成されるもの）

```turtle
# Curve は Digitization Activity によって生成された
sdr:curve/79  a sd:Curve, prov:Entity ;
  prov:wasGeneratedBy sdr:digitization/79 ;
  prov:wasDerivedFrom sdr:paper/SID-6 ;
  prov:wasAttributedTo sdr:person/extractor/... .

sdr:digitization/79  a sd:DigitizationActivity, prov:Activity ;
  prov:used sdr:paper/SID-6 ;
  prov:wasAssociatedWith <https://automeris.io/WebPlotDigitizer> ;
  prov:atTime "2017-09-01T18:19:39+09:00"^^xsd:dateTime .

# CSV→RDF 変換の来歴（再現性のため）
sdr:ingestion/run-2026-05-27T00-00-00Z  a sd:IngestionActivity, prov:Activity ;
  prov:used sdr:source/starrydata_curves.csv ;
  prov:wasAssociatedWith <https://github.com/<user>/csv2rdf-mcp> ;
  prov:atTime "2026-05-27T00:00:00Z"^^xsd:dateTime ;
  prov:endedAtTime "2026-05-27T00:42:11Z"^^xsd:dateTime .

sdr:curve/79  prov:wasGeneratedBy sdr:ingestion/run-2026-05-27T00-00-00Z .
```

二重の `wasGeneratedBy` は PROV-O 的に問題ない（複数 Activity が同じ Entity を生成しうる）。Curve は **物理的に「論文の図を数値化したもの」であると同時に「CSV を取り込んだ RDF レコード」**でもあるので、両方の来歴を持つのが正確。

#### Graphium との相互引用

Graphium のノートが Curve を引用するとき、Graphium 側は以下のような PROV を出すと想定:

```turtle
# Graphium のノート側（Graphium の出力）
gx:note/abc123/block/42  a gx:Citation, prov:Entity ;
  prov:wasDerivedFrom sdr:curve/79 ;        # ← csv2rdf-mcp の IRI を直接引用
  prov:wasGeneratedBy gx:edit/xyz789 .

gx:edit/xyz789  a prov:Activity ;
  prov:wasAssociatedWith gx:user/m-kumagai ;
  prov:atTime "2026-05-27T10:00:00Z"^^xsd:dateTime .
```

この時点で、csv2rdf-mcp の SPARQL endpoint で `sdr:curve/79` を `prov:wasDerivedFrom` の object に持つ Entity を逆引きすれば、**「この Curve はどのノートのどのブロックで使われているか」**が即座に取れる（Graphium 側の SPARQL endpoint を統合 / federation する場合に限る）。

#### Federation の選択肢

| 方式 | 利点 | 欠点 |
|---|---|---|
| **(α) Graphium が自身の PROV を RDF で吐き、csv2rdf-mcp の SPARQL endpoint に push** | クエリが 1 endpoint に集約され速い | Graphium の編集量がそのまま endpoint へ書き込みになり、ストア肥大化 |
| **(β) Graphium と csv2rdf-mcp が別 endpoint を持ち、SPARQL 1.1 SERVICE で federation** | 各リポジトリが独立、責務分離が綺麗 | 横断クエリの latency が増える、両方の endpoint が生きている前提 |
| **(γ) Graphium 側に csv2rdf-mcp の IRI を「外部リファレンス」として持つだけ、逆引きはしない** | 実装が一番軽い | 「この curve はどのノートで使われたか」が答えられない |

**Phase 1〜2 は (γ)**、Phase 3 で (β) を実装、push 型 (α) は最後の選択肢、というロードマップが妥当。(γ) でも Graphium のノートから csv2rdf-mcp の MCP ツールを呼べば curve データは取れるので、ユーザ体験は損なわれない。

#### 補足: なぜ PROV を真面目にやるか

- starrydata の curve は「人が論文の図を見て数値化した」**手作業由来の派生データ**。来歴を持たないとサイエンスの世界では信用されない。
- Graphium と組み合わせると「論文 → curve → ノート → 解釈」という 4 段の派生チェーンが PROV で繋がる。これは AI for Science の文脈で非常に強い武器になる（再現性・引用可能性）。
- PROV-O は W3C 標準で、SHACL / ShEx で形状検証もできる。将来 ELN / LIMS と繋ぐときも翻訳が要らない。

---

## 5. CSV → RDF 変換の実装

### 方針
- **rdf-config の `convert.yaml` は採用しない**（リソース調査で CSV ローダーの成熟度が確認できないため）。
- **rdf-config は「モデル定義 + SPARQL 生成 + ShEx 検証」のために使う**（`model.yaml` を書く価値はある）。
- **CSV → RDF 本体は Python (rdflib) で書く**。理由: JSON 埋め込みカラム（authors, sample_info, x/y arrays）の展開が必須で、表形式マッピングだけでは捌けない。

### Python パッケージ

`ingest/src/csv2rdf/starrydata.py` の API:

```python
def ingest_papers(csv_path: Path, out_path: Path, graph_iri: str) -> Stats: ...
def ingest_samples(csv_path: Path, out_path: Path, graph_iri: str) -> Stats: ...
def ingest_curves(csv_path: Path, out_path: Path, graph_iri: str) -> Stats: ...
```

- ストリーミングで CSV を読み（pandas でなく `csv` モジュール、メモリ節約のため）、レコードごとに rdflib `Graph.add()`。
- 100k レコードごとに `serialize(format="nt")` で `data/sources/rdf/starrydata/<table>-NNNN.nt.gz` に分割書き出し（QLever は複数ファイル可）。
- 失敗行は `data/logs/starrydata/<run-id>/errors.jsonl` に記録、処理は止めない。

### 検証
- `shexc` （ShEx）で生成 RDF を検証。rdf-config から `--shex` で生成して使う。
- 期待件数（papers: 56,390 / samples: 144,091 / curves: 233,104）と RDF 内のクラス出現件数を SPARQL で突合するスモークテスト。

### Phase 2: 汎用化のメモ
- CSV → スキーマ推論は以下の段階で:
  1. 型推論（pandas-profiling 風の列ごと統計）
  2. 主キー候補の検出（一意な int / UUID カラム）
  3. 外部キー候補の検出（他 CSV と命名・型が一致する列）
  4. ユーザに `manifest.yaml` のテンプレを返して確認させる（半自動）
- `manifest.yaml` には class 名、id カラム、外部キー、JSON 列の展開ルールを書く。
- 確認済みになったら generic ingester が rdf-config の `model.yaml` を自動生成 + RDF 出力。

---

## 6. SPARQL endpoint と MCP

### togopackage の起動

`compose.yaml` 抜粋:

```yaml
services:
  togopackage:
    image: ghcr.io/dbcls/togopackage:latest
    user: "${UID:-1000}:${GID:-1000}"
    ports:
      - "10005:10005"   # sparql-proxy / dashboard
      - "7001:7001"     # sparqlist
      - "8890:8890"     # virtuoso (optional)
    volumes:
      - ./data:/data
    environment:
      - SPARQL_BACKEND=qlever
```

### `data/config.yaml`（togopackage 設定）

```yaml
sparql_backend: qlever
mcp_server: togomcp
sources:
  - name: starrydata
    files:
      - sources/rdf/starrydata/*.nt.gz
    rdf_config: rdf-config/starrydata
```

### 公開 MCP ツール一覧

| ツール名 | 引数 | 戻り値 | 提供元 |
|---|---|---|---|
| `sparql_query` | `query: str` | JSON results | 自作 (proxy → /sparql) |
| `sparql_describe` | `iri: str` | TTL | 自作 |
| `nl_to_sparql` | `question: str, dry_run?: bool` | SPARQL + 結果 | 自作（Claude Haiku 経由） |
| `template_paper_search` | `keywords: str, author?: str, year_from?: int, year_to?: int` | papers list | sparqlist テンプレ呼び出し |
| `template_sample_search` | `composition_substring: str, descriptor?: dict` | samples list | sparqlist |
| `template_curve_search` | `prop_x: str, prop_y: str, x_range?: [min,max]` | curves list | sparqlist |
| `template_curve_fetch` | `curve_iri: str` | x[], y[], units | 自作 |
| `fulltext_search` | `q: str, class?: str` | hit list | QLever text index |
| `list_classes` | - | class IRIs と件数 | 自作 |
| `list_predicates` | `class?: str` | predicate IRIs | 自作 |
| `schema_diagram` | - | SVG（rdf-config 出力） | 自作 |

> `nl_to_sparql` は `window.cowork.askClaude` の流儀で軽量モデル（Haiku 系）に投げる構成にし、stub プロンプトをリポジトリに同梱して再現性を担保する。失敗時は `sparql_query` と `list_predicates` を呼ぶフォールバックを書く。

### togomcp の使い方
- togopackage 同梱の togomcp は **MIE YAML が無いと endpoints 0 件で起動**するので、`data/togomcp/mie/starrydata.yaml` を必ず置く。
- `togo-mcp-admin` コマンド（コンテナ内）で MIE のスケルトンを生成 → 手で endpoint URL / query templates を埋める。
- ライフサイエンス系の TogoID 変換や PubMed 検索は **togomcp ホスト版** (`https://togomcp.rdfportal.org/`) を Crucible 経由でそのまま使えばよく、自前ホストする必要はない。

### 自作 MCP (`csv2rdf_mcp/`) が必要な理由
- togomcp は SPARQL の生実行＋テンプレ呼び出しが中心で、`nl_to_sparql` のような LLM 介在ツールは持たない。
- `template_curve_fetch` のように JSON literal をパースして配列に戻すなど、データに密着した整形が必要なツールがある。
- 将来の任意 CSV 対応（dataset の動的追加）を考えると、自作 MCP に管理系ツール（`list_datasets`, `register_dataset`）を載せる方が拡張しやすい。

---

## 7. Graphium 連携

Graphium は PROV-DM をネイティブに扱うエディタなので、**csv2rdf-mcp 側が PROV-O を出すこと自体が最大の連携**になる。UI 拡張はその上に乗る薄い層にすぎない。

### 7.1 設計の前提（中心線）

- **データ層は PROV で繋ぐ**: §4 で述べた通り、csv2rdf-mcp 側で `sd:Curve a prov:Entity` などを出しておけば、Graphium はそれを `prov:wasDerivedFrom` の object として参照するだけで PROV グラフが繋がる。
- **IRI を共通言語にする**: Graphium のノートに埋め込む引用は **csv2rdf-mcp の IRI 文字列**（`https://w3id.org/csv2rdf/starrydata/resource/curve/79` など）。これさえ保存しておけば、SPARQL 横断クエリ・MCP 経由のデータ取得・人間がブラウザで開くこと、すべてが同じ識別子で動く。
- **Federation は段階導入**: Phase 1〜2 は Graphium 側に IRI を保存するだけ（§4 の Federation (γ)）。逆引きが要りそうになった時点で (β) を実装。

### 7.2 Graphium 側の UI 拡張

#### A. Datasets セクション（navigation）
- 左サイドバーに新規セクション "Datasets" を追加。
- csv2rdf-mcp の `list_datasets` MCP ツールで一覧取得 → 件数・最終更新・スキーマ図リンクを表示。

#### B. CSV Upload UI
- "Upload CSV" ボタン → ファイルダイアログ → `upload_api` に multipart POST → ジョブ ID を表示 → 完了後に Datasets 一覧へ反映。
- アップロードログ（成功/失敗行）を Graphium のノートとして開けるオプション。これも PROV: `gx:upload_note prov:wasGeneratedBy sdr:ingestion/run-...`。

#### C. Insert citation（PROV 連携の本丸）
- ノート編集中に `/cite` スラッシュコマンドを開く → csv2rdf-mcp の `template_paper_search` / `template_sample_search` / `template_curve_search` を呼んで結果一覧を表示 → 選択するとブロックに埋め込み。
- 埋め込むブロックは Graphium 側で **PROV を保持する Citation ブロック**として扱う（後述）。

#### D. AI チャット連携
- チャット入力時に "include dataset X" を選ぶと、システムプロンプトに dataset の `schema_diagram` と代表クエリテンプレが自動挿入される。
- AI が応答内で curve を引用したら、`Insert as citation` ボタンが出て、そのまま PROV 付きで貼り付け可能。

### 7.3 Graphium 側のデータモデル変更

#### 新規ブロック型: `citation` ブロック

```ts
// src/lib/document-types.ts への追加（optional フィールドで後方互換）
type CitationBlock = {
  type: 'citation';
  props: {
    iri: string;              // 必須: 引用先 IRI（例: sdr:curve/79）
    label: string;            // 表示ラベル（例: "Bi2Te3 Seebeck (Snyder 2014)"）
    sourceEndpoint?: string;  // SPARQL endpoint URL（任意、デフォルトは「現在の dataset」）
    snapshot?: {              // 引用時点のスナップショット（再現性のため）
      capturedAt: string;     // ISO datetime
      summary: string;        // human-readable な内容（例: "300-650 K, V/K")
    };
  };
};
```

#### `NoteIndexEntry` への追加
- `citedIRIs: string[]`（必要なら）— 検索/逆引きの高速化用キャッシュ。
- 追加するなら **`INDEX_SCHEMA_VERSION` をインクリメント必須**（`Crucible/CLAUDE.md` 規約）。
- ただし、検索を csv2rdf-mcp 側の SPARQL に投げるなら index に持たなくても良い → 持たない方針が初期は楽。**Phase 4 で逆引き需要が見えてから入れる**。

#### Graphium → RDF エクスポート（オプション機能）
- ノート全体を `prov-generator` で RDF 化する既存パイプラインを拡張し、citation ブロックから `prov:wasDerivedFrom` triple を出す。
- これにより Graphium のノートが他システムでも PROV グラフとして読める。

### 7.4 共通プロパティ・URI 規約

Graphium と csv2rdf-mcp で **URI と PROV の使い方を揃える**。両側で勝手な独自表現にすると federation 時に困る。

| プロパティ | 用途 | 値の型 |
|---|---|---|
| `prov:wasDerivedFrom` | ノート/ブロックがデータセット由来であることを示す | IRI |
| `prov:wasGeneratedBy` | ノート編集 / CSV 取り込み Activity | IRI |
| `prov:atTime` | 編集 / 取り込みの時刻 | xsd:dateTime |
| `prov:wasAssociatedWith` | エージェント（人間 or ソフトウェア） | IRI |
| `dcterms:created` / `dcterms:modified` | 時刻系の汎用補完 | xsd:dateTime |

`gx:` 名前空間（Graphium 側）と `sd:` / `sdr:` 名前空間（csv2rdf-mcp 側）は **互いに知らなくてもよい**。共通言語は **PROV-O + dcterms + schema.org** だけ。

### 7.5 Graphium 側の作業計画

- `Crucible/CLAUDE.md` の **worktree 運用フロー**に従う。
- 変更ファイル候補:
  - `src/features/datasets/`（新規）
  - `src/features/navigation/` に Datasets 項目追加
  - `src/lib/document-types.ts` に `citation` ブロック追加（optional）
  - `src/features/document-provenance/` に citation → PROV triple のジェネレータを追加
  - AI チャットの MCP 接続 UI
- **docs 同期チェック**（`Crucible/CLAUDE.md` で必須）:
  - `provnote/docs/CONCEPT.md` — Datasets 機能と PROV 連携の章を追加
  - `provnote/docs/ARCHITECTURE.md` §3.2 — citation ブロックの PROV 生成
  - `provnote/docs/DATA_MODEL.md` §1〜§3 — citation ブロックの型定義
  - `provnote/docs/DATA_MODEL.md` §5 — もし `NoteIndexEntry` に `citedIRIs` を足すなら `INDEX_SCHEMA_VERSION` table も更新
- **破壊的変更チェック**: citation を optional フィールドとして導入する限り後方互換。既存ノートは触らない。

### 7.6 連携シナリオ（具体例）

ユーザが Graphium で「Bi2Te3 系熱電材料」のレビューノートを書いている場面を想定:

1. ノートで `/cite Bi2Te3 Seebeck` と打つ → csv2rdf-mcp の `template_curve_search` が呼ばれ、候補 20 件が表示される。
2. ユーザが Snyder 2014 由来の curve を選ぶ → ブロックに `citation` が挿入される（IRI と snapshot を保持）。
3. ユーザが AI に「これらの curve の平均値の傾向を要約して」と頼む → AI が `template_curve_fetch` を IRI ごとに呼んで x[]/y[] を取得 → 要約を生成。
4. 後日、Snyder 論文に撤回が出る → csv2rdf-mcp 側で paper Entity に `prov:invalidatedAtTime` を追加 → Graphium がノートを開いた時に "この citation の元データが撤回された" 警告を出せる（Phase 3 の発展機能）。

このシナリオは PROV を真面目に出していれば全部「データの形」で実現できるので、Graphium 側に特殊ロジックを書く必要がない。これが PROV-O を中心線に据える効用。

---

## 8. セキュリティ・運用

- **認証**: MVP は localhost バインドのみ。リモート公開する場合はリバースプロキシ（Caddy）で Basic 認証 or OAuth2 を追加。
- **シークレット**: `.env.example` に `NCBI_API_KEY`（togomcp 連携時）, `ANTHROPIC_API_KEY`（nl_to_sparql 用）, `UPLOAD_API_TOKEN` を例示。`.env` は `.gitignore`。
- **ファイル検証**: 受け付ける CSV は拡張子 + magic byte で MIME 検査、上限 1 GB（starrydata の curves が 155 MB なので余裕を見て）。
- **rate limit**: `nl_to_sparql` は IP あたり 1 分 10 リクエスト（高コストツールのため）。
- **ログ**: 構造化 JSON ログを stdout に出す → docker logs 経由で集約。

---

## 9. テスト戦略

- 単体: `ingest/` の各関数（特に JSON 展開・単位正規化）を pytest で。
- 統合: `compose.yaml up --wait` → 小さい CSV を投入 → SPARQL で件数突合。
- MCP: `pytest-mcp` 風に Mock LLM を立て、`nl_to_sparql` の入出力を E2E。
- 性能: starrydata 全件投入 → QLever index 構築時間と SPARQL p95 を計測。

CI は GitHub Actions:
- lint (ruff, mypy, prettier)
- pytest
- compose の up → smoke test → down（メモリ余裕がなければ self-hosted runner）

---

## 10. 段階的ロードマップ

### Phase 0（半日）: リポジトリ雛形と CI
- `csv2rdf-mcp` リポジトリ作成、Python パッケージ初期化、ruff/mypy/pytest 設定。
- `.env.example`, `.gitignore`, `LICENSE`(Apache-2.0)。

### Phase 0.5（1〜2日）: 依存技術の素振り（**採用判断を確定させる**）
このフェーズは **採用ロックインを避けるため必須**。Phase 1 以降は素振り結果で軌道修正する。

- togopackage を実際に起動し、以下を確認:
  - 複数 `source` を `config.yaml` で扱えるか
  - 部分再インデックス（reload API）の有無 — **無ければ QLever 採用を見直す**
  - togomcp の MIE YAML 書式（`togo-mcp-admin` でスケルトン生成 → 実物確認）
  - ライセンス（README にバッジが無いので LICENSE ファイルを確認）
- **代替 SPARQL backend の比較**: starrydata の 233k curves / 数 GB Turtle を Oxigraph と Fuseki(TDB2) に投入し、初回ロード時間・追記性能・SPARQL p95 を計測。判断基準は「CSV 1 本追加で QLever フルリビルドの数十分が許容できるか」。
- **RML 系の比較**: starrydata の papers.csv だけを Morph-KGC + YARRRML で RDF 化してみて、JSON 埋め込み（authors）の展開がどこまで宣言的にできるかを評価。Python 実装と比較し採用判断。
- 成果物: `docs/internal/phase05-decisions.md`（採用バックエンド・ingester 方式の決定根拠）。

### Phase 1（2〜3日）: starrydata 固定スキーマで E2E
- Phase 0.5 で採用したバックエンド（QLever or Oxigraph or Fuseki）でスタックを構築。
- rdf-config の `prefix.yaml` / `model.yaml` / `sparql.yaml` を書く。
- `ingest/src/csv2rdf/starrydata.py` 実装（papers → samples → curves の順）。
- SPARQL endpoint と MCP から確認できる状態にする。
- 代表クエリ 5 本を `sparqlist` または静的 `.rq` ファイルとして固定。

### Phase 2（2〜3日）: 自作 MCP（最小構成）
- `csv2rdf_mcp/` を **最小ツールセット**で実装: `sparql_query`, `list_predicates`, `schema_diagram`, `template_curve_fetch`。
- `nl_to_sparql` は **作らない**。Claude 側で SPARQL を直接生成させ、`sparql_query` に渡す。MCP に LLM を抱えると二重コスト。
- 全文検索ツールは Phase 0.5 で QLever text index が使えると分かった場合のみ追加。それ以外は保留。
- README とドキュメント整備、Crucible registry に登録。

### Phase 3（2〜3日）: 汎用 CSV 対応
- `csv2rdf.generic` 実装（スキーマ推論 → manifest 半自動生成）。
- `register_dataset` MCP ツール。
- **Graphium UI より先に汎用化を固める**（UI を先に作ると generic 確定後に作り直し）。

### Phase 4（3〜5日）: Graphium 連携
- Graphium 側に "Datasets" 機能（worktree 運用、`Crucible/CLAUDE.md` 準拠）。
- Upload UI と MCP 接続。`NoteIndexEntry` を変えるなら `INDEX_SCHEMA_VERSION` インクリメント。
- docs 同期 (`CONCEPT.md` / `ARCHITECTURE.md` / `DATA_MODEL.md`)。

---

## 11. 既知のリスクと未確定事項

### 致命的リスク（Phase 0.5 で必ず検証する）

1. **QLever は静的インデックス前提** — 公式は `IndexBuilderMain` でフルビルドが標準。CSV 追加のたびに数十分のリビルドが必要になる可能性が高い。「自動再インデックス」を売り文句にする以上、許容できなければ **Oxigraph または Jena Fuseki (TDB2) に切り替える**。Oxigraph は SPARQL 1.1 update + 部分追記が素直で、Rust 製で軽い。Fuseki は枯れていて運用知見が豊富。
2. **togopackage のロックイン** — 1 コンテナに SPARQL + MCP + sparqlist + grasp + virtuoso がまとまっているのは便利だが、構成要素の置換が難しい。Phase 0.5 で「`compose.yaml` を Oxigraph + 自作 MCP に組み替えてみる」も並行検証し、ロックインを最小化する撤退路を確保する。
3. **依存先のライセンス未確認**（togopackage）— LICENSE ファイルを実物で確認。互換でなければ依存を切る判断を Phase 0.5 で。

### 検証必要事項

4. **rdf-config の `convert.yaml` 仕様未確認** — Python 実装に倒すことで回避方針。Phase 0.5 で `convert.yaml` がもし成熟していたら方針変更も検討。
5. **togomcp の MIE YAML 書式** — `togo-mcp-admin` で実物を確認。
6. **QLever の text index 設定** — 全文検索ツールの実現性は QLever のドキュメント要確認。代替として Oxigraph + 別途 Tantivy を後段に置く案あり。
7. **curve のデータ点表現** — Phase 1 は JSON 文字列、Phase 2 で `sd:DataPoint` ノード化。後者はストアのインデックスサイズと相談。
8. **Graphium の `INDEX_SCHEMA_VERSION`** — Datasets 連携で `NoteIndexEntry` に何か足すならインクリメント必須。読み込み専用の参照のみなら触らない。
9. **大規模 CSV のメモリ** — curves は 155 MB / 233k 行で済むが、汎用化したときに GB クラス CSV を想定すると Polars + lazy 評価への切り替えが必要。

### 代替アーキテクチャ（撤退路）

**もし Phase 0.5 で togopackage / QLever 採用に難があった場合の B プラン**:

```
[upload_api] → [ingest: Python rdflib  または  Morph-KGC + YARRRML]
                 → Turtle ファイル → [Oxigraph] (SPARQL 1.1)
                                      → [自作 MCP（薄い proxy）]
```

- ストアを Oxigraph に変更（Rust 単一バイナリ、Docker 1 コンテナ、SPARQL update で追記が素直）。
- ingester は **rdflib (Python)** か **Morph-KGC + YARRRML**（R2RML/RML 標準）。後者は宣言的で再利用性が高い反面、JSON 配列展開の練度が必要。Phase 0.5 で両方試して決める。
- rdf-config は **モデル定義と SPARQL 生成のみ**に使う（Ruby ランタイム依存を最小化）。
- togomcp は **使わない**（自データ MCP は薄い自作で十分。togomcp ホスト版は Crucible 経由で外部 DB アクセスにだけ使う）。

このプランは「rdf-config + togopackage に準拠」というユーザ初期方針からは外れるが、依存検証の結果次第では合理的な選択になる。Phase 0.5 終了時にユーザに判断を仰ぐ。

### IRI 占有について

- `starrydata.org` のドメインは我々が所有していないため、`https://starrydata.org/...` を IRI として使ってはいけない。
- 永続化戦略は §4.0 にまとめた通り、**Phase 1 は GitHub Pages、Phase 2 で w3id.org の redirect を被せる**、という二段構成にする。
- w3id.org の `csv2rdf` パスは `perma-id/w3id.org` への PR ベース。Phase 1 を作りながら並行して PR を出すと良い。条件は OSS の README + ライセンス + 1〜2 名のメンテナ名義。

---

## 12. 直近のアクション（次セッションで着手する手順）

**Phase 0.5（依存検証）を最優先**で。Phase 1 の実装を急がない。

1. GitHub に `csv2rdf-mcp` リポジトリを新規作成（Apache-2.0）。
2. このプランを `docs/internal/design-plan.md` としてコミット。
3. **togopackage を 1 時間素振り**: docker pull → 起動 → README の sample データで `/sparql` を叩く → `config.yaml` で複数 source を扱えるか、reload API があるか確認。LICENSE 確認。
4. **Oxigraph を 30 分素振り**: docker pull → 起動 → SPARQL 1.1 update で部分追記が効くか確認。
5. **Morph-KGC を 1 時間素振り**: starrydata の papers.csv（小さい方）に YARRRML を書いて RDF 化、JSON 埋め込み（authors）の展開がどこまで宣言的にできるか確認。
6. 3〜5 の結果を `docs/internal/phase05-decisions.md` に書き、採用バックエンド / ingester 方式を確定。
7. その後はじめて Phase 1 の本実装に入る。

---

## 付録 A: 代表 SPARQL クエリ 5 本

### A1. 特定組成のサンプルが持つ Curve 一覧

```sparql
PREFIX sd: <https://starrydata.org/ontology#>
PREFIX schema: <https://schema.org/>
SELECT ?sample ?sampleName ?curve ?propY WHERE {
  ?sample a sd:Sample ;
          sd:compositionString ?comp ;
          schema:name ?sampleName .
  FILTER(CONTAINS(LCASE(?comp), "bi2te3"))
  ?curve sd:ofSample ?sample ;
         sd:propertyY ?propY .
}
LIMIT 50
```

### A2. Seebeck 係数の絶対値が 300 µV/K を超える curve

```sparql
PREFIX sd: <https://starrydata.org/ontology#>
SELECT ?curve ?sampleName ?yMax WHERE {
  ?curve sd:propertyY "Seebeck coefficient" ;
         sd:yMax ?yMax ;
         sd:ofSample/schema:name ?sampleName .
  FILTER(?yMax > 0.0003)
}
ORDER BY DESC(?yMax) LIMIT 20
```

### A3. 著者名から papers を引く

```sparql
PREFIX sd: <https://starrydata.org/ontology#>
PREFIX schema: <https://schema.org/>
SELECT ?paper ?title ?doi WHERE {
  ?paper a sd:Paper ;
         schema:author/schema:familyName "Snyder" ;
         schema:name ?title ;
         schema:identifier ?doi .
}
LIMIT 50
```

### A4. 任意の DOI から関連する curve すべて

```sparql
PREFIX sd: <https://starrydata.org/ontology#>
PREFIX schema: <https://schema.org/>
SELECT ?curve ?propX ?propY WHERE {
  ?paper schema:identifier "10.1021/ar400290f" .
  ?sample sd:fromPaper ?paper .
  ?curve sd:ofSample ?sample ;
         sd:propertyX ?propX ;
         sd:propertyY ?propY .
}
```

### A5. 熱電 ZT > 1.5 の curve（PROV 経由で引用元論文付き）

```sparql
PREFIX sd: <https://starrydata.org/ontology#>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX schema: <https://schema.org/>
SELECT ?paperTitle ?sampleName ?ztMax WHERE {
  ?curve sd:propertyY "ZT" ;
         sd:yMax ?ztMax ;
         sd:ofSample ?sample .
  ?sample schema:name ?sampleName ;
          sd:fromPaper/schema:name ?paperTitle .
  FILTER(?ztMax > 1.5)
}
ORDER BY DESC(?ztMax) LIMIT 30
```

---

## 13. Vault（仮称、別リポジトリ、self-hosted）

csv2rdf-mcp のスコープを超えるが、設計の連続性を確保するためにここに記す。実装は **別リポジトリ**（仮: `csv2rdf-vault`）として独立させる。

> **重要な前提変更（ソブリン制約反映）**: 旧設計では multi-tenant SaaS として描いていたが、Crucible/Graphium のソブリン哲学と矛盾するため、**Vault は self-hostable な csv2rdf-mcp の Web UI 層 + チーム管理機能**として再定義する。サービス提供者がユーザデータを抱える構造は採らない。

### 13.1 positioning（"private staging tier"、ただし self-hosted）

```
公開・永続・DOI 付き          [Zenodo / figshare / RDF Portal]
                                       ▲
                                       │ graduate / export（境界を跨ぐ唯一の出口）
                                       │
─────────────── ソブリン境界 ───────────────
                                       │
プライベート・WIP・チーム共有 [Vault（self-hosted Web UI）]
                                       ▲
                                       │ uses
                                       │
ローカル・コアエンジン        [csv2rdf-mcp]
```

Vault の刺さる場所:
- **同じ closed server 上の Web UI**: csv2rdf-mcp はエンジン、Vault はその使いやすい front。SPARQL を書かないメンバーでも dataset を一覧・検索できる
- **チーム共有**: 同じ Vault deployment 内で複数の "team space" を切れる（外部マルチテナントではなく、同じ組織内の論理分割）
- **graduate 経路**: Vault → Zenodo への export を 1 コマンドで（DOI も自動取得、IRI redirect 設定込み）
- **PROV で繋がる引用ネットワーク**: Graphium のノートが Vault の IRI を引用 → Zenodo 化されても redirect で生き続ける

キャッチ案: "Your private RDF lab notebook, hosted on your own server. Graduate to Zenodo when ready."

> **AI クライアントから見たアクセス経路**: Vault は MCP を csv2rdf-mcp の上に被せる形で公開する。Graphium は Vault の MCP に **直接接続**して dataset を検索する。Crucible は registry（カタログ）であり、Vault を発見させるだけで経路には入らない（§13.2 参照）。

### 13.2 Crucible との関係（重要）

**結論: Crucible を front door として通す必要はない。Vault は standalone で動き、Crucible には opt-in で登録できるようにする。**

なぜか:

- Crucible は **MCP サーバの registry（yellow pages）**であって、proxy（データの通り道）ではない。
- MCP クライアント（Claude Desktop, Cline 等）は複数 MCP を同時接続可能なので、Vault に直接繋ぐのが普通。
- データトラフィックは AI client → Vault で **直接**。Crucible は経路に入らない。

|  | Crucible 経由（opt-in） | Vault に直接 |
|---|---|---|
| 一覧性 | 他 MCP と横並びで発見可 | Vault 単独で完結 |
| ガバナンス | 「Crucible 登録のみ」ポリシーを敷ける | Vault 側で完結 |
| 障害ドメイン | Crucible 障害で発見が止まる（既知サーバへの接続は継続） | 完全に独立 |
| データプレーン | 直接接続（同じ） | 直接接続（同じ） |
| 必須度 | optional | **standalone で動く** |

**設計上の含意**:

- Vault は **Crucible が無くてもデプロイ・使用できる**こと。Vault 単体の README で完結する手順を提供する。
- Crucible には **登録 1 行**で乗せられる仕組み（Vault が出す MCP manifest / `.well-known/mcp.json` のような URL を Crucible に貼るだけ）。
- Vault は Crucible に依存するパッケージや SDK を持たない。

> Crucible の自己定義（"MCP 集約インフラ"）は変えずに済む。Vault は独立した MCP server の 1 つに過ぎず、Crucible はそれを発見可能にするだけ。

### 13.3 アーキテクチャ（self-hosted、csv2rdf-mcp の上に被せる）

```
┌─ closed server（Crucible deployment と同居）─────────────────┐
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │ Vault                                               │     │
│  │  ┌──────────────┐                                   │     │
│  │  │  Web UI      │ ── 一覧・検索（DCAT/VoID）        │     │
│  │  │  (Next.js)   │ ── upload・dataset 管理          │     │
│  │  └──────────────┘                                   │     │
│  │  ┌──────────────┐    ┌─────────────────┐            │     │
│  │  │  API         │ ─→ │ SQLite/Postgres │            │     │
│  │  │  (FastAPI)   │    │  - team space   │            │     │
│  │  │              │    │  - dataset meta │            │     │
│  │  │              │    │  - PROV index   │            │     │
│  │  └──────┬───────┘    └─────────────────┘            │     │
│  │         │ uses                                      │     │
│  │  ┌──────▼──────────────────────────────────┐        │     │
│  │  │ MCP gateway （csv2rdf-mcp に proxy）    │        │     │
│  │  └─────────────────────────────────────────┘        │     │
│  └────────────────────┬────────────────────────────────┘     │
│                       │ SPARQL / MCP                          │
│                       ▼                                       │
│  ┌─────────────────────────────────────────────────────┐     │
│  │ csv2rdf-mcp                                         │     │
│  │  - CSV → RDF 変換エンジン                           │     │
│  │  - Oxigraph SPARQL                                  │     │
│  │  - 自前の MCP server                                │     │
│  └─────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

**ポイント**:
- Vault は csv2rdf-mcp の "薄いラッパー"。データ実体は csv2rdf-mcp 側に持つ
- multi-tenant SaaS ではなく **single deployment / multi-team space**（同じ組織内の論理分割）
- 小さいチームは csv2rdf-mcp 単体で十分。チーム共有・複数 dataset 管理・GUI が欲しくなったら Vault を追加で立てる
- ストレージは SQLite で開始、規模が出たら Postgres に切り替え

> **代替案**: Vault を独立リポジトリにせず、csv2rdf-mcp の `web/` ディレクトリとして同梱する選択肢もある。シンプル化のメリットと、関心分離のデメリットのトレードオフ。Phase 0.5 のユーザヒアリング次第で決める。

### 13.4 MVP 機能（4 つ）

1. **データセット一覧・検索**
   - DCAT（Data Catalog Vocabulary）と VoID（Vocabulary of Interlinked Datasets）でメタデータを構造化
   - タグ・ドメイン・スキーマ・トリプル数で絞り込み
   - 自分のテナントのみ表示（プライベート前提）

2. **Publish フロー（csv2rdf-mcp 連携）**
   - csv2rdf-mcp に `vault publish` サブコマンドを追加
   - CSV → RDF（local）→ Vault API へ push → SPARQL endpoint 起動 → カタログ登録
   - Storage backend は publish 時にテナント設定で選択

3. **MCP gateway（Vault 自前、standalone）**
   - Vault 全体で 1 つの MCP endpoint を露出（例: `https://vault.example.com/mcp`）
   - ツール: `list_my_datasets`, `sparql_query(dataset_id, query)`, `nl_to_sparql(dataset_id, question)`, `describe_iri(iri)`
   - AI クライアントから **直接接続**して使える
   - Crucible Registry には opt-in で登録可（登録は単に URL を貼るだけ。Vault は Crucible に依存しない）

4. **Graduate to Zenodo**
   - 1 クリックで Vault → Zenodo に export（RDF dump + README + DCAT metadata）
   - DOI 取得 → Vault 側に元の IRI からの redirect ルールを記録（IRI 永続化と整合）

### 13.5 アクセス制御の設計

- **Team space**: 同じ Vault deployment 内の論理的なチーム単位（外部マルチテナントではない）
- **dataset 単位の可視性**: `private`（self）/ `team`（同じ space 内）/ `link`（URL を知っている人、Vault 内ユーザ限定）
- **public** は提供しない。public にしたい場合は Zenodo に graduate するワークフローへ誘導
- 認証: deployment ごとに選べる仕組み（GitHub OAuth / LDAP / Crucible と同じ認証 / single-user モード）。デフォルトは閉域ネットワーク前提の single-user / shared-secret

### 13.6 PROV をプラットフォーム機能として活かす

Vault は PROV-O を **first-class** で扱う。これは Zenodo / figshare に対する明確な差別化:

- すべての dataset は upload 時に `prov:Bundle` でラップされ、`prov:generatedAtTime` `prov:wasAttributedTo` を必ず持つ
- dataset 同士のリネージ（A から派生した B、A と B を統合した C）を `prov:wasDerivedFrom` で繋ぐ
- Graphium のノート → Vault dataset への `prov:wasDerivedFrom` を逆引きできる API を提供
  - 「この dataset を引用しているノートは何件あるか」
  - 「この curve は何回引用されたか」
- 撤回トレース: dataset を invalidate すると、引用しているノートに通知（Graphium 側で警告表示）

これは PROV を真面目にやらないと出来ない機能群で、既存プラットフォームでは難しい。

### 13.7 ロードマップ（Vault 単独）

| Phase | スコープ | 目安 |
|---|---|---|
| V0 | Catalog + 最小 publish フロー（自分の Oxigraph に対する薄い proxy のみ） | 1 週間 |
| V1 | MCP gateway + Crucible 登録 + Graphium との PROV 逆引き | 2 週間 |
| V2 | テナント / アクセス制御 / GitHub OAuth | 1 週間 |
| V3 | Storage backend のプラグイン化（Git-native, hosted） | 2 週間 |
| V4 | Graduate to Zenodo（DOI 取得 + redirect 設定） | 1 週間 |

**着手タイミング**: csv2rdf-mcp の Phase 3（汎用 CSV 対応）が終わった後。csv2rdf-mcp が安定してから Vault を被せるのが順序として正しい。

### 13.8 既存類似サービスとの境界

| サービス | 役割 | Vault との関係 |
|---|---|---|
| Zenodo / figshare | 公開・永続・DOI | Vault からの graduate 先（補完関係） |
| Hugging Face Datasets | ML 公開データ | スキーマが parquet 中心、RDF/PROV が二次的（補完関係） |
| RDF Portal (DBCLS) | ライフサイエンス公開 | ドメイン限定（補完関係） |
| LOD Cloud | 公開 RDF カタログ | 公開のみ（補完関係） |
| Crucible | MCP Registry | Vault は Crucible registry に opt-in 登録（疎結合） |
| Dataverse | 学術データリポジトリ | 公開・永続寄り（補完関係） |

Vault が直接競合するサービスは事実上存在しない。これは差別化の根拠であると同時に「需要が無い」リスクの裏返しでもあるので、Phase 0.5 並列でユーザヒアリングを開始すること（同じ研究室の他メンバー、Graphium ユーザ、AI for Science コミュニティ）。

**ソブリン重視の文脈で近いもの**: Solid pods, Nextcloud, JupyterHub（self-hosted 系）。これらは "self-hosted な作業空間" という哲学を共有するが、RDF/PROV を first-class に扱う点で Vault は独自。

### 13.9 Vault リポジトリ初期構成（参考）

```
csv2rdf-vault/
├── README.md
├── docs/
│   ├── architecture.md
│   ├── access-control.md
│   └── prov-integration.md
├── apps/
│   ├── web/                # Next.js (or SvelteKit)
│   ├── api/                # FastAPI
│   └── mcp-gateway/        # 自作 MCP
├── packages/
│   ├── storage-oxigraph/
│   ├── storage-git/
│   ├── storage-managed/
│   └── prov-tools/
├── compose.yaml
└── infrastructure/
    └── terraform/          # 将来 managed tier をクラウドに置く時用
```

### 13.10 命名（仮）

Vault は仮称。最終候補:
- **Vault** — 比喩が直接的、Crucible との対比が綺麗（るつぼ／金庫）
- **Crucible Vault** — Crucible との関係を name で示す（が独立性は損なう）
- **Datasmith** — Crucible 〜 craftsmanship つながり
- **Stardust** — starrydata 由来、AI for Science の比喩としても良い
- **Shoko**（書庫） — 日本語、Crucible（るつぼ）と同じく日本語 metaphor 統一

→ 命名は Vault の MVP（V0）に着手する前に確定する。

---

## 付録 B: レビュー反映ログ

このプランは初版を独立レビューに通し、以下の指摘を反映してある（2026-05-27）。

| 指摘 | 反映 |
|---|---|
| QLever は静的インデックス前提、CSV 追加でフルリビルドの可能性 | §11 致命的リスク 1 に追記、Phase 0.5 で Oxigraph / Fuseki と比較する手順を §10 に追加 |
| togopackage / togomcp の制約検証が後ろ倒し | §10 に Phase 0.5「依存技術の素振り」を追加 |
| `starrydata.org` を勝手に IRI 名前空間に使うのは NG | §4 で `https://w3id.org/csv2rdf/starrydata/...` に変更 |
| MCP に LLM (`nl_to_sparql`) を内蔵するのは二重コスト | §10 Phase 2 を最小ツール構成に縮小、`nl_to_sparql` は廃止 |
| Phase 1 が 1〜2 日は楽観的 | §10 Phase 1 を 2〜3 日に修正、Phase 0.5 を別途確保 |
| Phase 3（UI）と Phase 4（汎用化）の順序が逆 | §10 で順序入れ替え、汎用化を先に |
| JSON literal 方式（C）は 2 次元範囲クエリに弱い | §4 に既知の限界として明文化 |
| QUDT URI は 2.1 系が最新 | §4 で `http://qudt.org/2.1/vocab/unit/` に修正 |
| R2RML / RML を代替案として明示すべき | §11 「代替アーキテクチャ（撤退路）」を新設 |

未反映（議論余地）:
- ShEx を CI に組むかどうか — Phase 2 の品質指標として後で議論。
- `sparqlist` / `grasp` を MVP で使うか — Phase 0.5 で togopackage が採用継続か決まってから判断。

### 追加改訂（2026-05-27 ユーザフィードバック反映）

| 指摘 | 反映 |
|---|---|
| w3id.org を勝手に取れるのか？GitHub Pages でも良いのでは | §4.0「IRI 永続化戦略」を新設し、Phase 1=GH Pages → Phase 2=w3id.org redirect の段階導入に変更。インスタンス URI 文字列は段階間で変えない方針を明記 |
| PROV と RDF の親和性、Graphium との連携にもっと寄せて欲しい | §4「PROV-O との接続」を大幅拡張（クラス対応表・典型 PROV パターン・Federation 3 選択肢）。§7「Graphium 連携」を全面書き直し、PROV-O を中心線に据え、citation ブロック型・共通プロパティ規約・撤回検知シナリオまで含めた |
| RDF を公開するプラットフォームを作っても面白いのでは | §13 を新設。"private staging tier" として positioning（Zenodo へ graduate する経路を持つ）、Crucible には統合せず Crucible Registry の 1 source として登録する設計を採用 |
| Crucible を front door として通す必要はあるのか | §13.2 を修正。Crucible は registry（カタログ）であって proxy ではない。Vault は standalone で動き、Crucible 登録は opt-in（URL を貼るだけ）に変更 |
| Crucible / Graphium はソブリン重視（closed server / desktop） | §0 を新設して 3 層アーキテクチャ図を冒頭に配置。Vault を multi-tenant SaaS から self-hosted な csv2rdf-mcp Web UI 層に再定義。§13 全面修正、アクセス制御もマルチテナントから「同じ deployment 内の team space」に変更 |
| 同一組織でも組織全体 / 個人 / ハイブリッドの 3 パターンの Crucible 利用があり得る | §0.1「マルチスコープ運用」を新設。現状の設計（Crucible=registry / MCP クライアント multi-connect / self-hostable）でそのまま対応可能。`Personal → Lab → Org → Zenodo` の段階的 graduate と `sd:GraduationActivity` の PROV 記録を追加。IRI スコープ命名規約・scope manifest・multi-Crucible UI・名前衝突回避を明文化 |

---

## 付録 C: 参考リンク

- rdf-config: <https://github.com/dbcls/rdf-config>
- togopackage: <https://github.com/dbcls/togopackage>
- togomcp: <https://github.com/dbcls/togomcp>（hosted: <https://togomcp.rdfportal.org/>）
- QLever: <https://github.com/ad-freiburg/qlever>
- PROV-O: <https://www.w3.org/TR/prov-o/>
- QUDT: <https://qudt.org>
