# step0 が宣言 RML を出力する（#14 仕様メモ）

決定母体: [`phase5-declarative-substrate.md`](phase5-declarative-substrate.md)（§4 関数ライブラリ, §5 統治）
status: 仕様（Cowork 前さばき）→ 実装は CC（step0 ロジック）

## 0. 目的と人間ゲート

step0 の propose が、手続き型 `ingester.py`（任意 Python）に代えて**宣言的 RML マッピング**を出力する。人間が承認するのは *列→述語の対応＋ Tier 0 関数への参照* であって、コードではない。承認された RML を substrate（Morph-KGC ＋ `asterism.functions`）が実行する。

- レビュー面 = 「どの列がどの述語に／呼ぶ関数は適切か」。`e2e/mappings.rml.ttl` が雛形（全体 ~40 行）。
- RML が参照してよい関数は **Tier 0（`asterism.functions` の REGISTRY）だけ**。新コードは混ざらない。
- 当てる関数が無い列は **生文字列で素通り＋フラグ**（ADR §5.4）。1 列の不明が取り込み全体を止めない。

## 1. RML の形（雛形）

語彙: `rr:`(R2RML) / `rml:`(logicalSource, reference) / `ql:CSV` / `rmlf:`(`http://w3id.org/rml/`, FnO 実行) / `fn:`(`https://kumagallium.github.io/asterism/fn/`)。1 TriplesMap = 1 行種別（paper/sample/curve）。複合 IRI は `rr:template`、関数列は `rmlf:functionExecution`。具体は `experiments/phase5-morph-kgc-spike/e2e/mappings.rml.ttl`。

### 1.1 パラメータ化プリミティブの定数引数（lookup / regex_extract / template）

長尾の固有変換は **パラメータ化プリミティブ**（`fn:lookup` / `fn:regex_extract` / `fn:template`、§4・phase5 §5.1）で吸収する。これらは列値（`value`）に加えて**表名・正規表現・雛形**という *定数* 引数を取る。定数は列参照と違い `rml:reference` でなく **`rmlf:constant`** で渡す:

```turtle
# lookup(value, table): value は列参照、table は定数。
rr:objectMap [ rmlf:functionExecution [ rmlf:function fn:lookup ;
  rmlf:input [ rmlf:parameter fn:p_value ; rmlf:inputValueMap [ rml:reference "country" ] ] ;
  rmlf:input [ rmlf:parameter fn:p_table ; rmlf:inputValueMap [ rmlf:constant "country_iso3166" ] ] ] ]
```

罠: 定数は **必ず新名前空間 `rmlf:constant`**（= `http://w3id.org/rml/constant`）。レガシー `rml:`（`http://semweb.mmlab.be/ns/rml#`）には `constant` が無く、Morph-KGC が黙って無視する。プリミティブの定数パラメータ IRI は `fn:p_table` / `fn:p_pattern` / `fn:p_template` / `fn:p_field1`…`fn:p_field4`。`regex_extract` は **re2 互換**パターンのみ（後方参照・先読み不可）。利用可能な seed 表は `ingest/src/asterism/tables/`（`bool` / `country_iso3166` / `unit_alias`）。定数は関数 IRI ではないので T9 閉集合チェックには影響しない（`rmlf:function` のみ照合）。多値の定数パラメータ IRI は `fn:p_index`（`array_at`）/ `fn:p_delimiter`（`split`）。**`split` は `list[str]` を返し Morph-KGC が各要素を 1 トリプルへ explode する**（区切り多値の宣言的展開・入れ子 TriplesMap 不要）＝ `str -> str` 不変の唯一の例外。1 要素配列は `json_array_single`、定位置配列は `array_at`、object 配列のみ `…Raw`／入れ子 TriplesMap。

## 2. 既存 ingester 出力 ↔ RML 対応表

種別: **直接**=`rml:reference` / **関数**=Tier 0 を `rmlf:functionExecution`（空文字→トリプル無し） / **テンプレ**=`rr:template` IRI / **来歴配線**=substrate 定数（step0 が出すデータマッピングとは別。§4 末尾） / **fallback**=生文字列で素通り＋フラグ / **Tier0要追加**=parity に関数追加が要る。

### Paper（`papers.csv` → `sdr:paper/{SID}`、`rr:class sd:Paper`＋`schema:ScholarlyArticle`＋`prov:Entity`）

| 述語 | 列 | 種別 |
|---|---|---|
| `dcterms:identifier` | SID | 直接 |
| `schema:identifier` | DOI | 直接 |
| `schema:name` | title | 直接（`strip_quoted` は §3 参照） |
| `schema:url` | URL | 関数 `iri_safe`（IRI termType） |
| `schema:datePublished` | issued | 関数 `date_iso`（`xsd:date`） |
| `bibo:volume`/`issue`/`pages` | volume/issue/page | 直接 |
| `schema:publisher` | publisher | 直接 |
| `dcterms:created` | created_at | 直接 |
| `schema:isPartOf` → periodical | container_title | テンプレ `sdr:periodical/{slug(...)}` ＋ 副 TriplesMap（`slug` は Tier 0 にあり。§3.1） |
| `sd:projectName` | project_names | **fallback**（多値・セル内 JSON。§3.2） |
| `schema:author` → person | author | **fallback**（多値・入れ子。§3.2） |

### Sample（`samples.csv` → `sdr:sample/{SID}-{sample_id}`、`sd:Sample`＋`prov:Entity`）

| 述語 | 列 | 種別 |
|---|---|---|
| `dcterms:identifier` | {SID}-{sample_id} | テンプレ（リテラル） |
| `sd:rawSampleId` | sample_id | 直接 |
| `schema:name` | sample_name | 直接 |
| `sd:compositionString` | composition | 直接 |
| `sd:compositionDetails` | composition_details | 直接 |
| `sd:fromPaper` | SID | テンプレ `sdr:paper/{SID}` |
| `dcterms:created`/`modified` | created_at/updated_at | 直接 |
| `sd:hasDescriptor` → descriptor | sample_info | **fallback**（多値・入れ子。§3.2） |

### Curve（`curves.csv` → `sdr:curve/{SID}-{figure_id}-{sample_id}`、`sd:Curve`＋`prov:Entity`）

| 述語 | 列 | 種別 |
|---|---|---|
| `dcterms:identifier` | 複合 | テンプレ（リテラル） |
| `sd:rawFigureId` | figure_id | 直接 |
| `sd:figureName` | figure_name | 直接 |
| `sd:ofSample` | SID, sample_id | テンプレ `sdr:sample/{SID}-{sample_id}` |
| `sd:propertyX`/`propertyY` | prop_x/prop_y | 直接 |
| `sd:unitXString`/`unitYString` | unit_x/unit_y | 直接 |
| `sd:comments` | comments | 直接 |
| `sd:propertyXQuantity`/`YQuantity` | prop_x/prop_y | 関数 `qudt_quantity`（IRI・空→skip。条件付きトリプルを空文字で再現） |
| `sd:unitX`/`unitY` | unit_x/unit_y | 関数 `qudt_unit`（IRI・空→skip） |
| `sd:xValuesJSON`/`yValuesJSON` | x/y | 直接（リテラル） |
| `sd:xMin`/`xMax` | x | 関数 `float_array_min`/`float_array_max`（`xsd:double`） |
| `sd:yMin`/`yMax` | y | 関数 `float_array_min`/`float_array_max`（`xsd:double`） |
| `sd:pointCount` | x, y | **Tier0要追加** `float_array_count`（min(len(x),len(y))。2 入力 → §3.3） |
| `sd:projectName` | project_names | **fallback**（§3.2） |
| `dcterms:created`/`modified` | created_at/updated_at | 直接 |

## 3. 難所と扱い

### 3.1 periodical（共有ノード）
`slug` は Tier 0 にあるので IRI セグメント `sdr:periodical/{slug(container_title)}` を作れる。periodical 自身の `rdf:type`/`schema:name`/`alternateName` は `container_title` をキーにした**副 TriplesMap**で出す（Morph-KGC の set 意味で重複排除）。step0 v1 で実装可。

### 3.2 多値・入れ子（author / descriptor / project_names）— fallback
starrydata はこれらを**セル内 JSON**（1 セルに複数）で持つ。RML の CSV 反復は行単位で、セル内 JSON を複数トリプルに展開しない。素直な宣言化には「セルを複数行に分解する関数 or 副ソース」が要り、cell 変換の枠を超える。

→ step0 v1 は **生文字列フォールバック**: 例 `sd:authorsRaw`/`sd:projectNamesRaw`/`sd:sampleInfoRaw` に原文字列をそのまま載せ、列に「未展開」フラグを立てる。Ask の主要 intent（組成検索・物性ランキング・来歴）はこれらに依存しないので**デモ・通常運用は無傷**。将来、これらは Tier 1 的な *exploder*（多値展開）として別途設計（ADR §5.2/§5.5 のローカル拡張の典型例）。

### 3.3 `pointCount`（Tier 0 要追加）
`min(len(xs), len(ys))`。2 入力の集約。選択肢: (a) Tier 0 に `array_len(value)` を足し RML 側で 2 列ぶん出して min は別途、(b) `point_count(x, y)` を 2 入力関数として足す（FnO は多入力可。`register` の params を 2 つに）。**推奨: (b)**。`functions.py` の `_single` に加えて `_pair` ヘルパを 1 つ。未追加の間は pointCount を fallback（出さない）でも Ask は無傷。

### 来歴配線は substrate 定数（step0 の範囲外）
`prov:wasGeneratedBy`（ingestion/digitization）、`IngestionActivity`、`DigitizationActivity`＋`atTime`（`parse_curator_timestamp`）、WebPlotDigitizer agent は**列マッピングでなく取り込み 1 回ごとの定数配線**。step0 の RML はデータ・トリプルに集中し、来歴配線は substrate ランナーが付与する（§4 PROV 方針）。`atTime` 用に `datetime_utc` を Tier 0 に足すかは来歴配線実装時に判断。

## 4. 制約（step0 が守る）

1. 参照は **Tier 0（REGISTRY）の関数 IRI のみ**。範囲外を書いたら検証で弾く。
2. 当てる関数が無い／難所は **生文字列フォールバック＋フラグ**。取り込みを止めない。
3. **コード生成しない**。step0 の出力は RML（宣言）と、必要なら Tier 0 への*追加要望*（人間が一度 vet）だけ。

## 5. 検証ハーネス（step0 出力の RML に対し）

1. **構文**: RML が Turtle として parse できる。
2. **閉集合**: `rmlf:function` の IRI が全て `asterism.functions.REGISTRY` 内（範囲外参照を CI で落とす）。
3. **実行**: サンプル数行で Morph-KGC materialize がエラー無く通る。
4. **parity**: 同じ入力に対し、生成 RML の述語集合が手続き型 ingester の述語集合と一致（fallback 列・来歴配線・多値を除外した上で diff = 0）。`e2e/` の比較に倣う。

## 6. step0 の生成方針（実装ヒント）

step0 は既に「列→述語＋型」を推論する。型 → Tier 0 関数の小さな対応表で関数を選ぶ:

| 推論した列の性質 | 関数 |
|---|---|
| 日付（JSON date_parts 等） | `date_iso`（xsd:date） |
| セル内数値配列＋集約（最大/最小） | `float_array_max` / `float_array_min`（xsd:double） |
| 物性名 | `qudt_quantity`（IRI・条件付き） |
| 単位文字列 | `qudt_unit`（IRI・条件付き） |
| URL | `iri_safe`（IRI termType） |
| IRI セグメント化したい文字列 | `slug` |
| 上記いずれでもない | 直接リテラル、当てられなければ **生文字列フォールバック** |

複合キー（subject / fromPaper / ofSample）は `rr:template`。多値・入れ子は §3.2 の通り fallback。
