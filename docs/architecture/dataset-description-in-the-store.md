# データセットの説明も三つ組にする — `mie.yaml` を正本から投影に降格する

Status: proposed (2026-09-07)
owner: kumagallium

関連 ADR:
[`togomcp-auto-publish.md`](togomcp-auto-publish.md)（MIE の togomcp 配信）/
[`ontology-canonical-lifecycle.md`](ontology-canonical-lifecycle.md)（TBox グラフ・2 軸）/
[`id-move-after-publish.md`](id-move-after-publish.md)（version graph の外に置く理由）/
[`data-shape-checks.md`](data-shape-checks.md)（SHACL の出どころ）/
[`mapping-ir-compiler.md`](mapping-ir-compiler.md)（§9 に与えた扱いを §7 にも与える）/
[`deterministic-design-assembly.md`](deterministic-design-assembly.md)（D2: MIE の言葉は機械文が既定）

## 1. 問題 — 説明だけが古くなる

データセットの「説明」（題・説明文・検索語・例クエリ・落とし穴）は
`sources/registry/{id}/mie.yaml` という **1 枚の YAML ファイルが正本**である。
一方、そのデータセットが実際に何を持っているかは、ストアの中の三つ組が持っている。

正本が 2 つに割れているので、両者は静かにずれる:

| 起きること | 結果 |
|---|---|
| 設計を見直して種類を分ける | 例クエリが実在しないクラスを指したまま残る |
| 見直しを保存する（公開はまだ） | `update_dataset_artifacts` が**公開中の `mie.yaml` を即座に上書きする**。街に出ている説明が、まだ公開していない設計の説明に化ける |
| 別インスタンスへ持ち出す | 説明はファイルとして運べるが、ストアに問い合わせても出てこない |

`schema_summary`（`mcp/src/asterism_mcp/tools.py:635`）は実測なので古くならない。
古くなるのは**ファイルに書いた側だけ**である。そして古くなった側が、
togomcp への配信（`api/src/asterism_api/togomcp_sync.py:76` `project_mie`）と
データセット発見（`ingest/src/asterism/catalog.py:126` `_mie_description`）という
**外向きの 2 経路**を握っている。

これは本プロダクトの主軸（引用できる事実）と整合しない。説明はデータについての
事実であって、データの隣に置いた付箋ではない。

## 2. 決定 — 説明もストアに入れる

**公開されたデータセットの説明の正本はストアの三つ組。`mie.yaml` はそこからの
決定論投影（生成物）に降格する。**

これは §9 に対して [`mapping-ir-compiler.md`](mapping-ir-compiler.md) が下した判断と
同じ形である。LLM の出力を最終形として受け取るのをやめ、**構造（IR / 三つ組）に
落としてから決定論でシリアライズする**。§7 にも同じ扱いを与える。

```
LLM が書く §7（散文・キーワード・落とし穴）
        ↓ 決定論パース
   三つ組  ──設計中──→ registry の metadata.ttl   （まだストアには入れない）
          └─公開時──→ meta グラフ  ← 正本。街に出ている説明はこれだけ
        ↓ 決定論投影（LLM 0 回）
   mie.yaml / togomcp 配信 / find_datasets の説明文
```

**設計中はストアが要らない**、という点が重要である。materialize は一時ディレクトリで
走り、検査（T1-T10）は `OxigraphClient` を 1 度も触らない。設計段の三つ組は
`metadata.ttl` に落ち、そこから投影した `mie.yaml` を検査が読む。
「正本はストア」は**公開されたものについての言明**であり、設計中のものについては
「正本は三つ組（置き場はまだファイル）」である。この区別を §4 の表が担う。

## 3. 何を標準語彙に載せ、何を独自にするか

主語はデータセット IRI `https://kumagallium.github.io/asterism/dataset/{id}`。
グラフ IRI（`…/graph/canonical/{id}`）は入れ物であって同一性ではない
（`substrate.py` の既存コメント）ので、**説明の主語には使わない**。`id` は
不変（`rename` は表示名しか変えない）なので、この IRI も不変である。

| `mie.yaml` のキー | 三つ組 | 型 | 出どころ |
|---|---|---|---|
| （主語の型） | `a dcat:Dataset, void:Dataset` | — | DCAT / VoID |
| `schema_info.title` | `dcterms:title` | リテラル | DCAT |
| `schema_info.description` | `dcterms:description` | リテラル | DCAT |
| `schema_info.keywords[]` | `dcat:keyword` | リテラル（複数） | DCAT |
| `schema_info.categories[]` | `dcat:theme <…/theme/{slug}>` ＋ 行き先に `a skos:Concept ; skos:prefLabel` | **IRI**（`dcat:theme` の値域が `skos:Concept` なので概念を鋳造する） | DCAT |
| `schema_info.base_uri` | `void:uriSpace` | **リテラル**（`xsd:string`。IRI 型で書くのは VoID の誤用） | VoID |
| `schema_info.endpoint` | `void:sparqlEndpoint` | IRI | VoID |
| `schema_info.graphs[]` | `ast:namedGraph` | IRI（複数） | **独自**（`sd:namedGraph` は空ノードの入れ物を要求する。本リポジトリの RDF は空ノードを作らない — TBox は T3 が禁じ、実体はすべてテンプレート鋳造の IRI） |
| （RML から導いた shape） | `ast:hasShape <shape IRI>` ＋ 行き先に `sh:NodeShape` 一式 | IRI | リンク述語は**独自**、shape 本体は SHACL（`ingest/src/asterism/shapes.py:523` `shapes_to_shacl` の出力）。`base` は `…/dataset/{id}/shape/` にする — 既定の `urn:asterism:shape:` はクラスのローカル名だけで IRI を作るので、2 つのデータセットの `Sample` が同じ IRI を持ってしまう |
| `shape_expressions` | `ast:shapeExpressions` | リテラル | **独自**。手書き ShEx の保存用（§7.2） |
| `sparql_query_examples[]` | `ast:hasQueryExample <…/dataset/{id}/query/{n}>`、行き先に `a sh:SPARQLSelectExecutable ; sh:select "…" ; dcterms:title ; rdfs:comment` | IRI | リンク述語は**独自**、行き先は **SHACL の実行可能クエリ語彙**。DBCLS/SIB の sparql-examples が同じ形なので、外の資産と同じ棚に載る |
| `anti_patterns` | `ast:antiPattern` | リテラル | **独自**（標準が無い） |
| `architectural_notes` | `ast:architecturalNote` | リテラル | **独自**（同上） |
| `sample_rdf_entries[]` | `ast:hasSampleEntry <…/dataset/{id}/sample/{n}>`、行き先に `dcterms:identifier` ＋ `ast:turtle` | IRI | **独自** |
| 上記以外の未知キー | `ast:hasExtraSection <…/dataset/{id}/section/{key}>`、行き先に `ast:sectionName` ＋ `ast:sectionValue`（YAML リテラル） | IRI | **独自**。往復を無損失にするための逃がし口（§9） |

持ち出し（exchange）は `.ttl` / `.yaml` の中の実体 IRI base を置換する
（`exchange.py:53` `_TEXT_SUFFIXES`）。`metadata.ttl` と `mie.yaml` は同じ規則で
揃って置換されるので、例クエリの中の IRI と `void:uriSpace` がずれることはない。
データセット IRI（`…/asterism/dataset/{id}`）は実体 IRI base ではないので置換されず、
インスタンス間で同じ ID は同じ主語を指す。

**件数・統計は三つ組にしない。** 「何件あるか」「どのクラスがあるか」は
`schema_summary` が実測で答える。説明の側に写しを持つと、それが古くなる —
本 ADR が直そうとしている故障そのものである。この線引きの帰結として、
データを足すだけの `append`（`substrate.py:871` `run_append_ingest`）は
**説明を陳腐化させない**（件数は説明に書かれていないから）。

独自語彙は既存の `ASTERISM_NS`（`https://kumagallium.github.io/asterism/vocab#`）に
足す。ライフサイクル制御語彙（`ast:status` / `ast:liveGraph`）と同じ名前空間で、
**同じ製品の語彙が 2 つに割れない**ことを優先する。

独自述語は `rdf:Property` ＋ `rdfs:label` として **各データセットのメタグラフの中に
同梱する**。共有の語彙グラフを 1 枚立てて参照させるほうが重複は無いが、そのぶん
「書かれる順番」に依存する（まだ書かれていない語彙グラフを参照するメタグラフが
一瞬できる）。メタグラフは件数の勘定に入らない（§6）ので重複の実害が無く、
**1 データセット = 1 枚で自己記述的**なほうが持ち出し（exchange）で壊れない。

## 4. 置き場と、書く時機

`META_GRAPH_BASE = LIFECYCLE_GRAPH_BASE + "meta/"`（`meta_graph_iri(id)`）。TBox の
`ontology/{id}` と同じ層に、同じ理由で置く。

| 決めたこと | なぜ |
|---|---|
| **version graph の中に置かない** | 次の promote が旧 version を落とす。説明を道連れにするのは、`id-move-after-publish.md` §6 が台帳について避けたのと同じ故障 |
| **canonical スコープ（`_from_merge`）に入れない** | 説明は「誰かが尋ねた事実」ではない。ontology グラフと同じ立場 |
| **追記ではなく置き換え**（DROP → load） | 引っ越し台帳と違い、説明は「いまの言い分」であって履歴ではない。再公開で古い説明が残らない |
| **書くのは best-effort** | 投影の失敗が promote を落としてはならない（ontology 投影・crosswalk 再構築の先例） |

書き手は **3 つ**ある。既存の `mie.yaml` の書き手が
`registry.save_dataset` / `update_dataset_artifacts`（`api/src/asterism_api/registry.py:31`
の `_ARTIFACT_FILES`）＝**設計を保存した瞬間**であることを見落とさない:

| 時機 | registry の `metadata.ttl` | ストアの `meta/{id}` | 理由 |
|---|---|---|---|
| 設計の保存（`save_dataset` / `update_dataset_artifacts`） | **書く** | **書かない** | ここが §1 の 2 行目の事故の発生源。設計の保存はストアに触らない、と決めれば「公開中の説明が見直しの説明に化ける」が構造的に起きない |
| 取り込み（ingest）・**未公開** | 書く | 書く | 公開前でもカタログに説明が要る |
| 取り込み（ingest）・**公開済みの見直し** | 書く | 書かない | 上と同じ理由。街の説明は公開の瞬間に切り替わる |
| 追記（append） | 触らない | 触らない | 説明に件数を持たないので、足しても古くならない（§3） |
| 公開（promote） | 触らない | **必ず書き直す** | 街に出た説明はここで確定する（`published_subjects` と同じ流儀） |
| 撤回（retract） | 触らない | 触らない | 説明は残す。スコープから外れるのは `canonical_graphs` の側 |
| 再開（reinstate） | 触らない | 触らない | 同上（`project_mie` の再配信は現行どおり） |
| 削除（delete） | ディレクトリごと消える | **落とす** | `to_drop` に `meta_graph_iri` を明示的に加える。現行の `to_drop`（`main.py:8877`）は live / staged / 全 version だけを列挙しており、**`ontology_graph_iri` も落としていない**。同じ穴なので同時に塞ぐ |
| 持ち出し（exchange）の import | `metadata.ttl` を運ぶ | import 先の promote で書かれる | ストアが空の環境でも説明が復元できる |
| 表示名の変更（rename） | 触らない | 触らない | 表示名（`meta.name`）と説明の題（`dcterms:title`）は別物。前者の正本は `meta.json` のまま。現行も `mie.yaml` に触れていない |
| 文書データセット（`POST /api/documents`） | 書かない | 書かない | materialize を通らず `mie.yaml` を持たない（`main.py:8092`）。説明が無いものに説明を発明しない |

`metadata.ttl` を組むのは **`registry.save_dataset` / `update_dataset_artifacts` の中**である。
新規データセットの `id` は `save_dataset` が採番する（`{slug}-{uuid8}`）ので、主語 IRI が
決まるのはそこが最初であり、呼び出し側（`main.py` の 3 箇所の artifacts 組み立て）は
触らない。同じ場所で `mie.yaml` も投影結果に差し替える。
`metadata.ttl` は「メタグラフのシリアライズ」であって正本ではない。`mie.yaml` も
従来どおり同じディレクトリに置くが、**投影結果を置くだけ**になる（人が手で直す
場所ではなくなる）。

## 5. LLM をどこに残すか

`deterministic-design-assembly.md` の D2 が「MIE の説明文・キーワードは
doc_synth の機械文が既定。言葉の仕事は LLM と人に残る」と決めている。本 ADR は
その線を動かさず、**LLM の出力の行き先だけを変える**:

- 詳細モード: LLM が §7 を書く。その YAML を**直接ファイルにしない**。決定論で
  パースして三つ組にし、そこから投影する。
- かんたんモード: `synthesize_mie_yaml`（`doc_synth.py:257`）が機械文を作る。
  こちらも同じく三つ組を経由する。

つまり `synthesize_mie_yaml` の役割は「§7 の YAML を作る」から
「**三つ組の材料を作る**」に移り、YAML 化は投影側 1 箇所に集まる。
投影経路の LLM 呼び出しは 0 回である。

## 6. `schema_summary` との境界

メタグラフは canonical スコープの外にあるので、`?s a ?cls` / `?s ?p ?o` の
**件数集計には構造上 1 件も混ざらない**（ontology グラフと同じ）。件数を守るために
新しい除外フィルタを足すのではなく、**入れないから混ざらない**を維持する。
`graph=<iri>` の許可リスト（現在は promoted canonical ＋ ontology）にも
メタグラフを足さない。

そのうえで、`dcterms:title` / `dcterms:description` は答えの先頭に置く価値がある。
返り値に別フィールドとして添える:

```
{graph, classes, predicates, class_shapes, datasets: [{iri, title, description}]}
```

**`_ontology_labels`（`tools.py:750`）の prefix 読みをそのまま真似てはならない。**
あれが安全なのは ontology グラフが promote でしか書かれないからで、メタグラフは
未公開データセットのぶんも同じ base の下に存在しうる（§4）。`STRSTARTS` で拾うと
**未公開の題・説明が公開の応答に漏れる**。読み方は `canonical_graphs()` が返す
公開済みデータセットから meta グラフ IRI を組み、`VALUES ?g { … }` で明示的に
限定する（`graph=<iri>` の許可リストと同じ「列挙して許す」流儀）。

メタグラフが 1 つも無ければ `datasets` は空。**説明が無くても schema_summary は
これまで通り動く**（ontology グラフに対する不変条件と同じ）。

## 7. 読み手がどう変わるか

### 7.1 `catalog._mie_description`（`find_datasets` の検索材料）

ファイルを読むのをやめ、ストアの `dcterms:description` を使う。ただし
**`catalog.py` を非同期化しない**: `find_datasets` は同期関数で、MCP 側は
`await` せずに呼んでいる（`mcp/src/asterism_mcp/server.py:222`）。一方
`OxigraphClient` のメソッドはすべて `async def` なので、`catalog.py` の中から
呼ぶと「走っているループの中で同期ブロック」になる。

取り込む前の下書きにはメタグラフが無い（§4）。その説明は registry の `metadata.ttl`
（三つ組のファイル形）から読む。`mie.yaml` は読まない。

したがって**解決は呼び出し側に置く**: MCP のツール本体（`async`）が
1 往復で `{dataset_id: description}` を引き、それを
`find_datasets(..., descriptions=mapping)` に渡す。`catalog.py` は同期のまま、
ストア依存を 1 つも持たない。辞書が空／該当なしのときは、これまで通り空文字に
落ちる（1 つの不良成果物が全データセットを隠さない、という既存の契約を維持）。

### 7.2 `validate._load_mie_yaml`（T1/T4/T6/T7/T10）

**変えない。** 検査は materialize が書いた §7 を今までどおり読み、
`OxigraphClient` を 1 度も触らない。step0 は ingest（`asterism`）に依存しないので、
三つ組への変換は step0 の中ではなく**保存の瞬間**（§4）に起きる。検査が読んだ
文書と保存された投影は意味的に等価（§9）であり、保存後の `mie.yaml` が
パースできることは api 側のテストで守る。

`shape_expressions` は T1 が複合 IRI テンプレートを拾う材料でもある。手書きの
ShEx（`data/togomcp/mie/starrydata.yaml` の約 90 行）は `ast:shapeExpressions` として
**逐語で保存し、逐語で投影し返す**ので T1 の材料は減らない。SHACL から ShEx を
機械変換して新規データセットにも出す案は採らない — 新しい変換器は新しい故障面で、
いま ShEx を必要としているのは手書き資産だけである（§10）。

### 7.3 `togomcp_sync.project_mie`（配信）

**シグネチャも出力形式も変えない。** 変えるのは呼び出し側で、
`artifacts["mie.yaml"]` の代わりに**ストアから投影した MIE 文字列**を渡す。
`endpoint` / `graphs` の固定と `FROM <live-graph>` の注入は今までどおり
`project_mie` の中で起きる（配信時の値であって、保存する事実ではない）。

### 7.4 触らない読み手

`registry.py` の `has_mie`、`exchange.py` の `has_mie`、`galleryApi.ts` の
成果物一覧、`tool_propose.py` の `mie_yaml` 引数、CI の `asterism-validate` は
**いずれも `mie.yaml` が存在すること・パースできることしか要求しない**。投影で
書かれ続けるので変更不要。UI も増やさない。

## 8. 既存データの移行

一度きりの決定論変換。`asterism-metadata migrate --registry <dir>` の 1 コマンド。

1. 各 `{id}/mie.yaml` をパースして三つ組を組む
2. 原本を `{id}/mie.yaml.authored` として残す（§9 のコメント喪失への備え）
3. `{id}/metadata.ttl` を書く
4. ストアが届くなら `meta/{id}` に load（**公開済みだけ**）
5. `{id}/mie.yaml` を投影結果で上書きする（以降ファイルは生成物）

`anti_patterns` / `architectural_notes` は自由文リテラルなので逐語で往復する。
既定は dry-run（差分の表示だけ）、`--apply` で書く。冪等。

## 9. 等価性をどう担保するか

| テスト | 何を確かめる |
|---|---|
| 往復 | 既存 `mie.yaml` → 三つ組 → 投影 `mie.yaml` が**意味的に一致**（YAML のキー順・引用符・行折りは問わない）。対象は `step0/tests/fixtures/starrydata_min/mie.yaml` と `data/togomcp/mie/starrydata.yaml`（手書きの実物・528 行） |
| 無損失 | 上の 2 本に加え、モデル化していないキーを持つ文書で往復が壊れないこと（`ast:hasExtraSection` の逃がし口） |
| 実エンジン | メタグラフに対するクエリを**実 pyoxigraph に流す**。`data-shape-checks.md` の教訓（best-effort な `except` が壊れたクエリを無言で無効化する）をここでも封じる |
| 配信互換 | `project_mie` の出力が移行前後で**バイト一致**（既存 `api/tests/test_togomcp_sync.py` の fixture で） |
| 件数不変 | メタグラフ投入の前後で `schema_summary` の `classes` / `predicates` の件数が変わらない |
| 漏れなし | 未公開データセットのメタグラフを置いた状態で `schema_summary().datasets` に出ないこと（§6 の `VALUES` 限定の回帰） |

**往復で失われるものが 1 つある: YAML の `#` コメント。** `yaml.safe_load` →
`yaml.safe_dump` を通す以上これは避けられず、現行の `project_mie` でも同じことが
起きている。ただし `data/togomcp/mie/starrydata.yaml` には togomcp の
`rdf_portal.py` の行番号まで指した実務的なコメントが約 10 行あり、これは失って
よい情報ではない。だから移行は原本を `mie.yaml.authored` として残す（§8）。
**今後この種の知見を書く場所は `anti_patterns` / `architectural_notes`** ——
三つ組になり、投影に載り、AI に届く場所である。コメントは投影に載らない。

## 10. 非目標

- **SHACL → ShEx の機械変換**（§7.2）。手書き ShEx は保存・投影するが、新規に
  生成はしない。
- **件数・統計を説明に持つこと**（§3）。
- **メタグラフを Ask / FROM-merge のスコープに入れること**。説明は事実の隣にあるが、
  事実ではない。
- **人が `mie.yaml` を手で直す導線**。正本が三つ組になった以上、直す場所は設計側
  （§7 の言葉）か、後続で置く編集 API である。本 ADR では UI を増やさない。
- **共有の語彙グラフ**（§3 で自己記述を選んだ）。`shared-vocab-graph.md` の
  横断地図はメタグラフとは別の関心事で、こちらを前提にしない。

## 11. 変えないもの（不変条件の確認）

- **決定論**: 投影経路の LLM 呼び出しは 0 回。
- **IRI の不変性**: 既存の実体 IRI を 1 つも書き換えない。増えるのは
  `…/dataset/{id}` とその配下だけ。
- **人間ゲート 3 つ**: 増やしていない。
- **件数の勘定**: canonical スコープは 1 三つ組も増えない。
- **配信の互換**: togomcp が受け取る YAML の形は変わらない。
- **設計段のオフライン性**: materialize と T1-T10 はストアに繋がない。

## 12. 実装の順番（stacked PR）

| # | 中身 | 主な触る場所 |
|---|---|---|
| 1 | 本 ADR | `docs/architecture/` |
| 2 | `asterism.metadata`（三つ組ビルダ ＋ 投影 ＋ 往復・無損失・実 pyoxigraph テスト）。既存経路には繋がない | `ingest/src/asterism/metadata.py` |
| 3 | 書き手の切り替え（設計保存で `metadata.ttl`、ingest / promote で meta グラフ、delete の `to_drop` に meta ＋ ontology） | `api/src/asterism_api/registry.py` / `main.py` / `ingest/.../substrate.py` |
| 4 | 読み手の切り替え（`project_mie` の呼び出し側 ／ `find_datasets` の説明文を MCP 側で解決 ／ `schema_summary.datasets`） | `api/.../main.py` / `mcp/.../server.py` / `tools.py` / `ingest/.../catalog.py` |
| 5 | 移行コマンド ＋ 実データ（コピー）一周の差分報告 | `ingest/.../metadata_migrate.py` / `docs/reports/` |
