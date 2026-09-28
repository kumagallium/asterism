# ADR: 前面 UI を「1 件／絞り込み × カード」に作り替える（object cards）

- **状態**: 実装中（branch `feat/object-cards-ui`、PR A = ADR＋契約層、2026-09-23）
- **決定者**: kumagallium + Claude Code
- **背景**:
  - 津田先生の指摘: チャットだと成果物がわかりにくい。持ち出せて単体で動くもの
    （standalone）を出すべき。「これからは one software per object の時代」。後に
    「python である必要はない。ローカル LLM ベースのエージェントを生成できればよい」。
  - 熊谷さんの現状認識: 作る UI が本当は大事だが、多くのユーザーは使う UI しか見ない。
    使う UI で既存チャット（Claude 等）に体験で勝てないので、**勝負する場所を会話から
    成果物に移す**。
  - Asterism にしか出せない成果物 = **出典と版が付いたまま梱包されたもの**
    （カード・エージェント）。他の AI が吐く使い捨てスクリプトとの差は中身のデータ。
  - **Asterism は汎用基盤**（材料科学前提ではない）。Starrydata は上に載る 1 データ
    セット。論文（文書層）も装置ファイルも同じ仕組みで載る。設計に分野固有の名詞を
    書かない（引き継ぎ書 §7）。

## 問題

引き継ぎ書の設計を見直した際に見つかった穴。反映先は本 ADR および実装ステップ。

| # | 穴 | 反映先 |
|---|---|---|
| 1 | 「配れる」判定にライセンスが要る（自分のデータ不在だけでは不十分） | Step 6 |
| 2 | 現行ツールの出口は「列名の集合」で意味型ではない | Step 2 |
| 3 | staging（TTL 7 日）では「自分のデータ」が消える → 私の棚に永続化 | Step 4 |
| 4 | 運営者の「作る UI」は残る（同居） | Step 5 |
| 5 | 「角度を足す」の中身は LLM が書く = propose 品質のボトルネック再来 → 既定カードは決定論、LLM は Phase 2 | Step 5 / §9 |
| 6 | 試作の設計に材料科学が混ざっていた（既定カードの名前・絞り込みの項目・近さの尺度） | 決定 7・8・16 |
| 7 | 出口の意味型が数値系だけで、手順・階層・網目（構造）を見ていない | 決定 7・15、§5.1 |
| 8 | Mermaid の見た目が製品品質でない | 決定 10、Step 3 |
| 9 | 表の出力に仕様が無かった（Vega-Lite は表を描かない） | §5.2、Step 3 |

## 決定

### O1. 三層

**固い核**（事実・出典・版・意味空間・1 件の同一性 = 全員共通、作らせない）／**契約**
（ツールの入口と出口の型を意味空間の語彙で書く = 機械が検査）／**柔らかい殻**（計算の
中身と見せ方 = その場で作る・人がいじれる）

### O2. 蓄積と使い捨て

棚（事実・ツール・意味の対応づけ）は蓄積。タスク記述・組み合わせ・生成物（カード・
エージェント）は使い捨て = **作り直せる**。生成物を保管する設計にしない

### O3. ページの単位

**1 件**（個体。IRI を持つ）と **絞り込み**（条件で決まる一覧。IRI を持たない。既定の
条件は決定論の問い合わせ）。「ある物性が高い材料の一覧」「自分のファイルの全件」は
絞り込み

### O4. 左の一覧

2 セクション: **自分のデータ**（置いたファイルに出てきた 1 件と、そのファイル全体の
絞り込み。全部並ぶ）／**オープンデータ**（さがして開いた 1 件・絞り込みが残る。一覧
化しない）。= 私の棚／公開の棚（既存 local-first 3 層に名前を付けたもの）

### O5. 型の一致で分かれる

置いたファイルの**形**が既存の型と一致 → 設計なしで置ける（1 件の照合だけ）。部分一致
→ 一致した列だけで置き、残りは後で「定義を直す」。**一致なし → 「棚を作る」へ渡し、
型ができたら戻る**。判定は列名・単位・方言の検出で決定論

### O6. 2 つの「型」

**入力の形**（ファイルの型: 設計が要るかを決める）と **1 件の種類**（クラス: 既定
カード・絞り込みフォーム・並びの共通化の単位）。新しい形でも既存の種類に落ちれば
カードは最初から出る

### O7. 既定カードの決めかた

**分野で決めず、データの形で決める。** `facts`（棚にあること）と `breakdown`（出典の
内訳）は必ず。`series`（条件→量）は条件つきの量があるときだけ。`ranked`（近いもの＝
共有するキーやつながりで測る）はつながりがあるときだけ。`flow`（作られた手順）は
provenance があるときだけ。**名前はスキーマから来る**

### O8. 絞り込みのフォーム

「1 件の種類」のスキーマから**自動生成**（どの量・どの分類・どの条件・どの並べ方が
あるか）。LLM ゼロ。「1 行書く→条件に直す」は Phase 2

### O9. 見せ方の 3 段

**選ぶ**（型から決まった既定ビューを別のビューに差し替える）／**組む**（軸・色分け・
しきい値・並び）／**書く**（LLM が仕様を書く = Phase 2、印が付く）。書く対象は Python
ではなく **Vega-Lite／Mermaid（flowchart 部分集合）／表仕様**

### O10. 描画は house style

3 つの仕様は Asterism の見た目で描く。Vega-Lite は config テーマ（色・フォント・軸）、
グラフは **Mermaid を解析して React Flow で描く**（Mermaid 自身の SVG は使わない。
見た目が合わない）、表は自前の描画器

### O11. 生成コードを実行しない

仕様（JSON／Mermaid テキスト）はデータ。描画器は固定。`CLAUDE.md` の不変条件と両立
させる唯一の形

### O12. 持ち帰る単位

**ページのエージェント**（`facts/` `tools/` `cards/` `AGENT.md` `mcp.json`
`materials.json`）。エージェントは**答えを作らず、ツールを選ぶだけ**（既存 Ask と
同じ規律）→ 数字に出典が付いたまま。範囲が狭いのでローカル LLM で足りる。カード単位
の `.py` は副産物で Phase 1 では作らない

### O13. 配れる判定

`配れる = 自分のデータを材料に含まない ∧ 全材料が再配布可`。材料表から機械が決める。
判定不能は「手元限り」に倒す。エージェント書き出しは「配れる版（自分のデータを抜く）」
を選べる

### O14. 2 つのロールの同居

右上の切り替え「使う／棚を作る」で**同じ画面に同居**（別アプリにしない）。「データを
置く」は**同じかんたんウィザード**（聞く数を最小にし、ページで終わる）。「棚を作る」
は新しい形のときと、公開品質まで詰めるときの入口。利用者は材料表の「定義を直す」から
だけ厚い版に入る

### O15. flow の入れかた

Phase 1 は**既存の取り込み由来（provenance）だけで flow を描く**。MatPROV 等の
実験手順データは後で同じ描画に載せる

### O16. 汎用性

画面に出る名詞（1 件の種類・量・条件）は**すべてスキーマから来る**。コード・設計・
テスト名に分野固有の名詞（例: 材料・組成・温度・ZT）を書かない。テストデータは
2 分野以上で持つ

### O17. 出口の意味型（`OutputKind`）

| 値 | 意味 | 既定ビュー | Phase 1 |
|---|---|---|---|
| `quantity` | 量 1 つ＋単位（＋条件） | 数値カード | ○ |
| `series` | 条件 → 量の系列 | 折れ線（Vega-Lite） | ○ |
| `pairs` | 量 vs 量 | 散布図（Vega-Lite） | ○ |
| `ranked` | 1 件の集合＋量 | 順位表（表仕様） | ○ |
| `breakdown` | 分類ラベル＋件数 | 内訳バー（Vega-Lite） | ○ |
| `facts` | 事実の集合そのもの | 出典つき表（表仕様） | ○ |
| `flow` | 工程の連なり（PROV の activity/entity の有向グラフ） | グラフ（React Flow） | ○（provenance からのみ） |
| `timeline` / `tree` / `network` / `statement` | 時間順／部分と全体／網目／出典つきの文 | — | 後（逃げ道の印が溜まった形から） |

ツール宣言に `output_kind` と、`quantity`/`series`/`pairs` は `quantity_kind`
（QUDT）・`unit` を必須で付ける。無いツールは `facts`（後方互換）。
（本実装での扱いは O25 参照 — `quantity_kind`/`unit` は任意に緩めた。）

### O18. ビュー仕様（`ViewSpec`）— 3 言語、いずれも**データであって実行しない**

```yaml
lang: vega-lite | graph | table
spec: {…}            # 下の 3 つのどれか
custom: false        # true = 「書く」で LLM が書いた（印が付く・Phase 2）
```

| lang | 中身 | 描画器 |
|---|---|---|
| `vega-lite` | Vega-Lite の JSON。Asterism の config テーマ（`ui-guidelines` の色・フォント・軸）を必ず合成 | vega-embed |
| `graph` | `{nodes:[{id,label,kind: entity\|activity\|other, props}], edges:[{from,to,label?}]}`。**Mermaid flowchart（`graph LR/TD` の部分集合）から決定論で変換**して得る。逆変換（graph → Mermaid）も持ち、持ち帰り束では Mermaid テキストとして残す | React Flow（house style・決定論の並べかた） |
| `table` | `{columns:[{field,label,unit?,format?}], sort?, group_by?, limit?, highlight?:[{when,style}]}` | 自前（既存の表の部品） |

既定カードも同じ 3 言語の仕様を**決定論で生成**する（= 描画器は 1 系統。「書く」で
LLM が書くものと同じ形）。

### O19. カード仕様（`CardSpec`・私の棚に保存）

```yaml
id: card-…                  # 決定論（subject + tool + params のハッシュ）
subject: {kind: individual, iri: …} | {kind: set, set_id: …}
tool: <registry tool id>
params: {…}
view: <ViewSpec>
materials:                  # Step 6 で機械が埋める
  - kind: own | open
    dataset: <dataset id>
    snapshot: <version>
    license: <SPDX or "unknown">
    count: <int>
shareable: <bool>           # 派生。own が無い ∧ 全 license が再配布可
```

### O20. 左の一覧の項目（`SubjectItem`）

```json
{ "kind": "individual" | "set", "id": "<IRI or set_id>", "label": "…", "class_label": "…",
  "source": "own" | "open", "card_count": 4, "match": "linked" | "ambiguous" | "own_only" | null }
```

`source: own` は「データを置く」から、`open` は「さがす」から生まれる。ラベルは
K4（生の識別子を見せない）。

### O21. 絞り込み仕様（`SetSpec`・IRI を持たない）

```yaml
set_id: set-…                    # 決定論（class + where + order のハッシュ）
class: <1 件の種類の IRI>
where:
  - {property: <IRI>, op: gt|lt|eq|between|in, value: …, at: {property: <条件の IRI>, value: …, tolerance: …}?}
order_by: {property: <IRI>, dir: desc|asc, at: {…}?}
limit: 20
source_scope: all | own | open   # 自分のファイル全件 = own
```

フォームの項目は `class` のスキーマから生成する（量の種類・分類・条件になりうる量・
並べ方）。**分野の項目を固定で書かない**。

### O22. 形の一致（`ShapeMatch`）

```json
{ "type_id": "<既存の型 or null>", "dialect": "…", "matched_columns": [...], "unmatched_columns": [...], "confidence": 0.0 }
```

`type_id` あり → 置ける（部分一致は `unmatched_columns` を後回し）。null → 「棚を作る」
へファイルごと渡し、型ができたら「置く」に戻る。

### O23. エージェント束（standalone）

```
<subject-slug>/
  AGENT.md         # 何のエージェントか・答えられること・答えられないこと・出典の出しかた（答えを作らずツールを選ぶ）
  mcp.json         # ローカル LLM や AI クライアントに貼る設定（同梱 stdio を facts/ と tools/ で起動）
  facts/           # 使った事実の切り出し（Turtle or Parquet）。値ごとに IRI・出典・snapshot。全 DB ではない
  tools/           # このページに効くツールの宣言（registry の部分集合）
  cards/           # いま並んでいるカード（CardSpec + ViewSpec。graph は Mermaid テキストで）
  materials.json   # 材料表と shareable
```

**Asterism への HTTP 依存を持たない。** 実行系は既存の stdio MCP をそのまま束に向ける
（`asterism-agent serve <dir>` のような 1 コマンド）。

### O24. YAML の形と役割の規則

`query_tools.yaml` の各ツールに `output_kind`（省略時 `facts`）と、`result.item` の
各列に `role`（および任意で `quantity_kind`/`unit`）を**任意で**足せるようにする。
`output_kind` の語彙は定数 `OUTPUT_KINDS`（`quantity` `series` `pairs` `ranked`
`breakdown` `facts` `flow`）。`flow` は宣言できない（O28）。`role` の語彙は定数
`ITEM_ROLES`（`subject` `label` `value` `x` `y` `series` `category` `count` `at`）。
`output_kind` ごとに必須・任意の role を定数 `ROLE_RULES` で定める:

| output_kind | required（各ちょうど 1 つ） | optional |
|---|---|---|
| quantity | value | subject, label, at |
| series | x, y | series, subject, label |
| pairs | x, y | subject, label |
| ranked | subject, value | label, at |
| breakdown | category, count | — |
| facts | — | どれでも |

`output_kind` を**明示した**ツールだけこの規則を検査する（違反は保存/parse 時に
error）。省略（= facts）は検査しない。同じ role が 2 列に付くのも error（facts の
optional は除く）。

### O25. `quantity_kind`/`unit` を任意にした（引き継ぎ書からの逸脱）

引き継ぎ書 §5.1 は「`quantity`/`series`/`pairs` では `quantity_kind`/`unit` を必須」
としているが、単位がパラメータで決まるツール（例: 同梱の `property_ranking` は
`property_y` 引数で量が変わる）が実在するため、本実装では**任意**とし、lint が
warning を出すに留める。値の検証（QUDT カタログに存在するか）も Phase 1 ではしない
（文字列を素通しする）。

### O26. 推定規則（`infer_output_kind`）

`output_kind` の無い宣言から `(kind, {item_key: role})` を推定する、純粋・決定論・
store アクセスなしの関数を設ける。**列名の意味は見ない**（分野語を持ち込まない）。
`number: true` の指定・query の構文・`_iri` 接尾だけで決める。この順で最初に当たった
ものを採る:

1. 数値列がちょうど 1 つ ∧ その var が query 内で `(COUNT(` … `AS ?var)` の AS 先
   ∧ 数値でない列がちょうど 1 つ → `breakdown`（category = その列, count = 数値列）
2. 数値列がちょうど 1 つ ∧ query が `LIMIT 1` で終わる（末尾の空白・改行は無視、
   大文字小文字無視） → `quantity`（value = 数値列, subject = 最初の IRI 列があれば）
3. 数値列がちょうど 1 つ ∧ query に `ORDER BY DESC(?var)` か `ORDER BY ASC(?var)`
   があり var がその数値列の var ∧ IRI 列が 1 つ以上 → `ranked`（subject = 最初の
   IRI 列, value = 数値列）
4. それ以外 → `facts`（roles 空）

規則 3 は `ORDER BY ?var`（括弧なし）には当てない。**数値列が 2 つ以上は必ず
facts**（判断が要るものは保守側に倒す）。

### O27. 推定は読み出し時に付け、YAML は書き換えない

`annotate_output_kind` は生 dict のコピーを返す関数で、`output_kind` が既にあれば
そのまま + `output_kind_inferred=False` を付け、無ければ `infer_output_kind` の結果
を `output_kind` に入れ、`result.item` の各 spec に role を足し（短縮形 `str` は
`{var, number: False, role}` の dict に展開）、`output_kind_inferred=True` を付ける。
保存されている YAML 自体は変更しない。API の読み出し経路（一覧・単体）がこの関数を
通す。

### O28. `flow` は宣言できず provenance からのみ

`flow` は `DECLARABLE_OUTPUT_KINDS`（`OUTPUT_KINDS` から `flow` を除いた集合）の外
にあるため、ツール宣言の `output_kind` に書くと parse で error になる。`flow` の
材料は宣言ツールではなく `prov_graph`（O29）が provenance から都度組み立てる。

### O29. `prov_graph` の 7 述語と向きの正規化・kind・label・出どころ

汎用の有向グラフ探索を新設する（既存 `provenance_of` は特定データセットの述語を
直書きした線形 chain で汎用ではない。O30 のとおり据え置く）。辿る述語
（`prov:` = `http://www.w3.org/ns/prov#`）と**辺の向きの正規化**（from が先・to が
後 = データ／時間の流れ。描画器に PROV の知識を持ち込まないため、ここで揃える）:

| 三つ組 | edge |
|---|---|
| `?e prov:wasGeneratedBy ?a` | a → e, `generated` |
| `?a prov:used ?e` | e → a, `used` |
| `?e2 prov:wasDerivedFrom ?e1` | e1 → e2, `derived` |
| `?e prov:wasAttributedTo ?ag` | ag → e, `attributed` |
| `?a prov:wasAssociatedWith ?ag` | ag → a, `associated` |
| `?a2 prov:wasInformedBy ?a1` | a1 → a2, `informed` |
| `?e prov:wasQuotedFrom ?src` | src → e, `quoted` |

起点 IRI から上の 7 述語を**両方向**（主語としても目的語としても）に BFS。
`max_depth` 段まで、`max_nodes` 個まで（超えたら `graph.truncated: true`）。1 段
ごとに 1 回の SPARQL（`VALUES ?n { ... }` で前段の節をまとめて問う）。

**kind** の決めかた（型 IRI から。分野語なし）: `a prov:Activity` があるか、型 IRI
のローカル名が `Activity` で終わる → `activity`。`a prov:Agent`（または
`prov:Person`/`prov:Organization`/`prov:SoftwareAgent`）→ `other`。それ以外
（`prov:Entity` 含む・型不明含む）→ `entity`。

**label** の決めかた: `rdfs:label` → `schema:name`（`http://schema.org/name` と
`https://schema.org/name` の両方）→ `dcterms:title` → 型 IRI のローカル名 → IRI の
ローカル名（`#` か `/` の後ろ）。**生の IRI を label にしない**（K4）。言語タグ付き
が複数あれば `ja` → `en` → 最初。

**出どころ**: 版グラフの `GRAPH ?g` を束縛して `dataset_id_of_canonical_graph(g)` で
`dataset_id` を、`/v{n}` の末尾から `snapshot`（`"v3"` の形）を取る。版グラフの一覧
は `canonical_graphs(client)` から、`ast:liveGraph` で解決した版グラフだけに絞る
（FROM 束では `?g` が取れないので `FROM NAMED` + `GRAPH ?g { ... }` を使う）。
`materials` = `graph.nodes` に現れた `(dataset_id, snapshot)` の重複なしの一覧
（並びは dataset_id, snapshot の辞書順）。`found` = 起点 IRI が少なくとも 1 つの
三つ組の主語か目的語として存在する（辺が 0 でも `found: true` で nodes は起点 1
つ）。不正 IRI（`http(s)://` で始まらない・`<>` を含む）は `ValueError`。

### O30. 既存 `provenance_of` は据え置き

`mcp/src/asterism_mcp/tools.py::provenance_of` は触らない。特定データセットの述語
直書きで汎用ではないが、既存の利用箇所（表示ラベル特化含む）を壊さないため今回は
改修しない。汎用化は `prov_graph`（O29）を新設することで別経路として満たす。

### O31. ライセンスの持ち場

正本はストア（`metadata.ttl` の `dcterms:license` — ADR
dataset-description-in-the-store.md §3。SPDX 識別子はリテラル、URL は IRI）。
`mie.yaml` の `schema_info.license` はそこからの投影で、`PUT
/api/datasets/{id}/license` が書き手（公開済みなら既存 `_project_meta_graph` で
ストアも書き直す）。読み手 `asterism.licenses` は決定論・LLM ゼロの純粋関数
（`normalize_license`/`redistributable`/`dataset_license`）: 許可リスト
`KNOWN_LICENSES` に無い値・空・不明はすべて `None`（再配布可否「不明」）— O13
「配れる判定は保守側」の実装そのもの。NC/ND 系（`CC-BY-NC-4.0` 等）は明示的に
`False`（配る相手の用途が分からないので、たとえ緩いライセンスの派生でも保守側に
倒す）。

### O32. `meta.origin`

データセットが「自分のデータ」か「よそから来た材料」かを machine-readable に持つ
1 フィールド（`own` | `open` | それ以外は全部 `unknown`）。書くのは 2 箇所だけ:
「データを置く」の `place/commit`（`own`）と snapshot の取り込み
`exchange.import_snapshot`（`open`）。読み手 `registry.dataset_origin(root, id)`
は id が不正・データセット不在・値が上の 2 つ以外のときも `unknown` を返す（O13
と同じ保守側則 — 「置いた」でも「取り込んだ」でもないデータセットの出どころを
機械が勝手に推測しない）。材料表（`materials.py`、契約 PR D §2）はこの `kind` を
`shareable` 判定の一部にする。

### O33. 束の中身と `_builtin` の焼き込み

持ち帰るエージェント束（O12・O23）は `facts/`（版グラフごとの GRAPH ブロックで
TriG）・`tools/<dataset_id>/`（このページのカードが使った宣言ツールだけの部分集
合）・`tools/_builtin/`（組み込み 5 ツール `subject_facts` などを、subject を
パラメータではなく**焼き込んだ**パラメータ無し宣言ツールに落としたもの — 束の
中では「このページの」ツールとして振る舞う）・`cards/`（CardSpec + view。graph は
Mermaid テキスト）・`AGENT.md`（答えを作らずツールを選ぶ規律・答えられないこと・
出典の出しかた）・`mcp.json`・`materials.json`（材料表 + `shareable` + 生成時
刻・版）。`share: 'shareable'` を選ぶと own のデータセットの facts グラフ・
own 依存カードが束から落ちる（全部 own なら 409）。

### O34. `asterism-agent serve` の起動順とポート

1 コマンド（`asterism-agent serve <束のフォルダ>`）が: ①束の最低限の形（
`materials.json`・`facts/facts.trig`・`tools/`）を確認 → ②oxigraph バイナリを
探す（`PATH` → `ASTERISM_OXIGRAPH_BIN` → 同梱パス）→ ③`oxigraph serve
--location <束>/.store --bind 127.0.0.1:<空きポート>` を子プロセスで起動し ready
を待つ → ④初回だけ `facts/*.trig` を Graph Store Protocol で投入（`.loaded`
マーカー）→ ⑤`CSV2RDF_OXIGRAPH_URL`・`CSV2RDF_REGISTRY_ROOT=<束>/tools` などを
env に置いて同一プロセスで既存 `asterism_mcp.server._main` を stdio で呼ぶ。
**api パッケージを import しない**（O23 の「Asterism への HTTP 依存を持たない」
の実装 — 自分で立てた oxigraph 以外どこにも httpx で問い合わせない）。終了時は
oxigraph を SIGTERM → 5 秒で SIGKILL。

### O35. `default_view`/`mermaid_flow` は ui と Python で二重実装するが、共有
フィクスチャで同一に保つ

持ち帰った束は Python 単独（ui を持たない）で `cards/<id>.json` の
`view`（既定ビュー）と `cards/<id>.mmd`（Mermaid）を書く必要があるため、
`ui/src/cards/defaultView.ts`/`toMermaidFlowchart` と同じロジックを
`ingest/src/asterism/default_view.py`/`mermaid_flow.py` に**決定論で**再実装
する（実行系を共有できない ui/Python 間の唯一の選択）。2 つの実装が黙って食い
違わないよう、テストケース（入力 → 期待 JSON/Mermaid テキスト）を
`ui/src/cards/fixtures/default_view_cases.json`・`mermaid_cases.json` に 1 箇所
だけ持ち、ui 側テストと Python 側テストが同じフィクスチャファイルを読んで両方
とも固定する（どちらかを直し忘れたら、直していない側のテストが赤くなる）。

## 却下した代替案

- **チャットを主役のまま** — 既存チャット（Claude 等）に体験で勝てない。
- **Python スクリプト書き出し** — 出典が剥がれる・実行できない。
- **Mermaid 自身の SVG で描く** — house style に合わない。
- **既定カードを分野で決める** — 汎用基盤に反する。
- **`output_kind` を result.item の列ごとに持つ** — 1 つの答えの形はツールに 1 つ。
- **既存 `provenance_of` の改修** — 特定データセットの述語直書きで汎用化しにくい。

## 実証

### PR A: 実 registry の宣言ツールに推定を流した結果（2026-09-23）

手元の実 registry（`~/Documents/Asterism-review` の 3 データセット。かんたんウィザードが自動生成した宣言ツール 9 本）に `annotate_output_kind` を流した。全部 `output_kind_inferred: true`（YAML は無改変）。

| データセット | ツール | 推定 | 役割 | 数値列 |
|---|---|---|---|---|
| periodictablecsv-381c132b | counts_by_kind | breakdown | class_iri=category, count=count | count |
| periodictablecsv-381c132b | value_range | facts | — | count, min, max |
| periodictablecsv-381c132b | top_value | quantity | subject_iri=subject, value=value | value |
| xrd-346deb24 | counts_by_kind / value_range / top_value | breakdown / facts / quantity | 同上 | 同上 |
| xrd-47441557 | counts_by_kind / value_range / top_value | breakdown / facts / quantity | 同上 | 同上 |

- `value_range` は数値列が 3 つ（count/min/max）なので O26 の保守則で `facts` に落ちる。範囲を「量」として見せたければ、生成側（`synthesize_query_tools_from_trial_queries`）が明示で書くのが筋（現状は facts のまま）。
- 同梱 3 データセットの 10 ツールは `ingest/tests/test_output_kind.py` で推定結果を固定（ranked 1・facts 9。人が vet した明示は ranked 2）。
- `prov_graph` は pyoxigraph 実 store の架空 2 分野（気象観測ログ／原稿解析）で 23 件のテストが緑。公開済み版グラフが 0 件のときは SPARQL を発行せず `found: false`（draft 隔離の不変条件）。


### PR C: 実データで「置く→照合→並べる→ページ」を一周（2026-09-23）

手元の実 registry（周期表データセットを公開済み）を隔離 HOME にコピーし、その元 CSV から 7 列・4 行（うち 1 行は棚に無い架空の行）を切り出したファイルを「データを置く」に通した。

- 形の一致: 既存の型（29 列）に対し 6/7 列一致（confidence 0.86）。ID 列（subject template の列）が一致したので `type_id` あり＝設計なしで置ける。
- 1 件の照合: 3 件が `linked`（棚の IRI に同定）、1 件が `own_only`。
- 並べる: 設計を一致列だけに刈り込み（落とした 28 列は `pruned_columns` として返す）、materialize→ingest→promote が LLM ゼロで完走。own データセット（92 三つ組・`meta.origin = own`）と、ファイル全件の絞り込み（`source_scope: own`）ができた。
- ページ: 既定カード（事実・出典・（辺があれば）手順）が決定論で並び、絞り込みのページは一覧・内訳（distinct が最小の分類プロパティ）・件数。クラススキーマの kind はストアの実データで決めた（Mapping IR 上は定数 IRI の行でも、実データが IRI なら link）。
- 見つけて直した穴: ingest→step0 の逆依存／IRIREF 禁止文字の素通し／commit が CURIE テンプレートを展開せず再特定に失敗／設計の刈り込み漏れ／facts の重複（own と open の両方にある 1 件）。

### PR D: 束を書き出して Asterism 本体と独立に動かす（2026-09-23）

PR C の隔離 HOME（周期表を公開済み・置いた own データセットあり）で、周期表の 1 件のページから「エージェントを持ち帰る」を API で実行した。

- 材料表: 事実カードの材料は own（my-elements v1・ライセンス不明・23 件）と open（periodic-table v3・`PUT /api/datasets/{id}/license` で CC-BY-4.0・51 件）。判定は「手元限り」（理由 `own_data` と `unknown_license`）。手順（flow）カードの材料も同じ形で返る。
- 配れる版: own を抜いた**あと**の材料で判定し 200（shareable true・材料は open 1 つ）。全部版は shareable false と理由つき。own しか無い 1 件は 409（`no_materials`）。
- 束の中身: `AGENT.md`（7 節・ja）・`README.md`・`mcp.json`・`materials.json`・`facts/facts.trig`（版グラフごとの GRAPH）・`facts/control.trig`（`ast:liveGraph`）・`tools/_builtin/query_tools.yaml`（`page_facts`／`page_sources`。**FROM 句を持たない素の SPARQL** と、射影変数に合わせた `result.item`）・`cards/*.json`。`asterism-agent serve <dir> --check` は ok。
- 実行: `asterism-agent serve <dir>` が `.app` 同梱の oxigraph を自前で起動して TriG を投入し、同一プロセスで stdio MCP を出す。MCP クライアント（Python SDK）から `page_facts` を呼ぶと **51 件（カードと同じ数）が IRI 付き**で、`page_sources` が出どころ 1 件（periodic-table v3・51）を返した。Asterism の api には一切アクセスしない。
- 見つけて直した穴: 束の組み込みツールに書き出し時の FROM 句（束に無い他データセットの版グラフ）が残り、実行時の許可リスト検査で拒否されて 0 件になった／`result.item` が加工後のキー名で射影変数と合わず全列 null になった／配れる版の判定を own を抜く前にしていた／手順カードの材料が別形のままだった。
- 残: `page_sources` の `category` は束では版グラフの生 IRI（ラベルは射影に無い）。Claude Desktop／ローカル LLM からの対話は本体の手元確認（MCP クライアントでの機械確認まで）。
＋ D2-ui の並列段の検査結果はそれぞれの PR の `notes` を参照）

## 残課題

- 見せ方の「書く」（LLM が Vega-Lite／Mermaid／表仕様を書く）と「角度を足す」（1 行
  書いてカードが生える）— 描画器と `custom` の印まで
- 「1 行書く → 絞り込みの条件に直す」（LLM）
- MatPROV 等の実験手順データの取り込み（flow の描画は Phase 1 で用意し、載せるのは後）
- `timeline` / `tree` / `network` / `statement` の描画
- カード単位の `.py` 書き出し
- 並びの継承（「この並びを共通にする」= 種類ごとのテンプレート）・昇格ループ
  （「棚に残す」）
- 配る基盤（束を他人に渡す・受け取る・togomcp/Crucible 連携）。今回は「配れる」印と
  束の生成まで
- 既存画面の撤去・タブ順の入れ替え（Home/Gallery/Ask/Crosswalk/Workbench は残す。
  前面化はユーザ確認後）
- 運営者向け「棚を作る」の改修
- 予測モデル（学習）を含むカード
