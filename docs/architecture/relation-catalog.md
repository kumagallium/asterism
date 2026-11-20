# 関係の目録 — 関係の定義を置く、コードの中の 1 か所

状態: 実装（R0a・R0b・R0c は実装済み、R0d はこの PR）。設計は AI for Science 側 `ui-redesign-2026-10-19/design.md` §23.13。
前提 = [`relations-for-experiments.md`](relations-for-experiments.md)（12 文・15 述語）。

## 背景

関係（一種・同じ・…・物性）の定義が、ingest・step0・api・ui・i18n に写しとして散っていた。
関係を 1 つ足すと十数か所を直す。目録を 1 か所に置き、正の定義はそこから導き、写しは
同期テストで見張る。

## 目録の形（`ingest/src/asterism/relation_catalog.py`）

| 表 | 1 件 | 欄 |
|---|---|---|
| `RELATIONS`（12） | 下書き側の短い名前（`sub` `eq` … `has_property`） | `name`・`label` ja/en・`sentence` ja/en・`predicates`（`PredicateRule` = 確定側の名前・IRI・`when`）・`symmetric_draft`・`symmetric_pair`・`experiment`・`builtin`。R0b で足した欄: `to_property_only`・`same_kind`・`acyclic`・`factor_directions`・`question_op`・`reserve_slug`・`paper` |
| `PREDICATES`（15） | 確定側の述語（`subClassOf` … `hasProperty`） | `name`・`iri`・`shapes`（from, to）・`same_shape`・`roles`（from, to）または None・`no_self`・`subsumption`・`symmetric`・`knowledge`・`own`（自前の述語の記録）。R0b で足した欄: `cycle_rels` |

R0b で足した欄の意味（`GET /api/relations` の返りには足しただけで、R0a の欄は変えていない）:

| 欄 | 意味 | 値が真の関係 |
|---|---|---|
| `to_property_only` | 相手は項目だけ（`target_not_property`） | lowers・raises・affects |
| `same_kind` | 両端が同じ種別（`mixed`） | sub・eq・part |
| `acyclic` | 下書き・台帳の上で輪を拒む（`cycle`） | sub・part |
| `factor_directions` | その線を数える `factors` の向き（`affects` の問いは 3 つ全部） | lowers=(lowers, affects)・raises=(raises, affects)・affects=(affects) |
| `question_op` | その線を数える問いの型 | part=parts・sub=kinds・lowers/raises/affects=factors・makes=makers・measures=measurers・becomes=sources・competes=competitors・has_property=bearers |
| `reserve_slug` | 関係の名前を `sv:` の slug に使わせない | lowers・raises・affects・makes・measures・becomes・competes・has_property |
| `paper` | 論文から読む関係（`paper_propose.PAPER_RELS`）。偽は eq・quantity | 上記以外 |
| `Predicate.cycle_rels` | 輪を調べるとき辿る辺の述語 | subClassOf=(subClassOf, equivalentClass)・subPropertyOf=(subPropertyOf, equivalentProperty)・isPartOf=(isPartOf) |

読む関数は `relation_catalog` にある（`to_property_only_relations`・`same_kind_relations`・`acyclic_relations`・
`factor_relations`・`direction_relations`・`question_ops`・`reserved_slugs`・`cycle_rels`・`class_both_relations`・
`from_class_relations`・`symmetric_pair_predicates`・`from_class_predicates`・`predicates_of` ほか）。

目録は葉のモジュール（ほかの `asterism` モジュールを import しない）。

## 正と写し

| 正（目録から導く・R0a） | 公開名は残す |
|---|---|
| `themes.RELATION_KINDS`・`EXPERIMENT_RELATIONS`・`SYMMETRIC_DRAFT_RELATIONS`・`relation_predicate` | `themes` |
| `graph_diff.PRED_TO_REL`・`REL_SENTENCE` | `graph_diff` |
| `crosswalk_runtime` の `ALIGN_RELATIONS`・`KNOWLEDGE_RELATIONS`・`SYMMETRIC_RELATIONS`・`NO_SELF_RELATIONS`・`ROLE_RULES`・`OWN_RELATIONS`・`_SUBSUMPTION`・`RELATION_SHAPES`・`SAME_SHAPE_RELATIONS` | `crosswalk_runtime` |
| `pair_relations.SYMMETRIC_PAIR_RELATIONS`（`PAIR_RELATIONS` は `RELATION_KINDS` から元々導出） | `pair_relations` |

R0b で導出に置き換えた物（公開名・private な名前は残し、値を目録から作る。値・型・並び・検査の結果は不変）:

| 場所 | 導出にした物 | 目録の欄 |
|---|---|---|
| `pair_relations` | `_DIRECTED_TO_PROPERTY`・`_SAME_KIND`・`_CYCLE_RELS`・`_FACTOR_DIRECTIONS`・`questions` の `sub`/`part`/`has_property` の直書き（問いの型で分岐） | `to_property_only`・`same_kind`・`acyclic`・`factor_directions`・`question_op` |
| `ledger_answer` | `_FACTOR_DIRECTIONS`・`sub`/`part`/`has_property` の直書き・`missing` の既定の関係 | 同上＋`relation_for_op("bearers")` |
| `themes` | `KNOWLEDGE_DIRECTIONS`・`rel_trip` の `competes`（`SYMMETRIC_DRAFT_RELATIONS`）・輪の辺（`sub`/`part`）・項目だけの相手・同種別・確定側の `lowers` 系のエラー文の条件 | 同上 |
| `graph_diff` | `check_relation` の `lowers` 系・`has_relation` の `("eq", "competes")` | `to_property_only`・`symmetric_pair` |
| `shared_vocab` | `RESERVED_SLUGS`（目録の名前の分。`part_of`・`regulates`・`competes_with` は目録に無い書き方なので残す）・`_SUBSUMPTION_RELS`・`_CLASS_RELS`・`_PROP_RELS`・`cycle_rels`（`_PART_RELS` は消した） | `reserve_slug`・`cycle_rels` |
| `sky_view` | `_LINE_RELATIONS`・`_SUBSUMPTION`（`isPartOf` を含まない別の意味）・`_STANDARD_MATCH_RELATIONS`・`_DECISION_RELATIONS` | `predicates_of(関係名…)` |
| `knowledge` | `DIRECTIONS`・`_DIRECTION_RELATIONS`（`factor_directions` の逆引き） | `factor_directions` |
| api `chat_paper` | `_FACTOR_DIR`・`_OP_KIND`・`_CLASS_BOTH`・`build_ops` の `part`/`sub`/`has_property`/`measures`/`makes` の直書き | `question_op`・`same_kind`＋`paper`・述語の `shapes` |
| api `chat_turns` | 行の範囲の `eq` の対称（`symmetric_pair`）・`factors` の向きの検査（`KNOWLEDGE_DIRECTIONS`） | `symmetric_pair`・`factor_directions` |
| api `consult_graph_routes` | `_SYMMETRIC`・`_SUBSUMPTION_RELATIONS`・当てはめの `eq`/`sub` の振り分け（`startswith("equivalent")` をやめて述語 → 関係の引き） | `symmetric_pair`・`PredicateRule.when` |

置き換え前の結果は `ingest/tests/test_relation_copies_snapshot.py`・`api/tests/test_relation_copies_snapshot.py` が固定している
（関係 12 + 未知 × 端の種別 × 役割の全組み合わせでの検査の結果・エラーの文・検査の順・台帳の乱数の試行の摘要）。
R0a の見張りのうち、api の `chat_paper._CLASS_BOTH`・`consult_graph_routes._SYMMETRIC`／`_SUBSUMPTION_RELATIONS` は導出になったので外した。

まだ写しのままの物（同期テストが目録と突き合わせる。ずれたら落ちる）:

| 写し | テスト |
|---|---|
| step0 `theme_propose.RELATIONS`・`pair_propose.PAIR_RELS`／`SYMMETRIC_RELS`・`paper_propose.PAPER_RELS` | `step0/tests/test_relation_catalog_sync.py`・`api/tests/test_relations_api.py` |
| i18n `themes.json`／`decide.json`（R0c で ui のコードの写しは目録から導く形になった。文言の json は残す） | `ingest/tests/test_relation_catalog_sync.py`（ファイルを読んで突き合わせる） |
| ui の組み込みの目録 `ui/src/relations/builtinCatalog.ts`（生成物 1 か所） | `python scripts/gen_relation_catalog_ts.py --check` と同じ一致を ingest のテストが見る（ずれたら落ちる）。作り直しは `python scripts/gen_relation_catalog_ts.py` |

置き換え前の値は `ingest/tests/test_relation_catalog_snapshot.py` が固定している（同じ値・型・並び。
`relation_predicate` は 12 関係 + 未知 × 種別 4 × 相手の種別 4 の全組み合わせ）。

## R0 の分け方

- R0a（実装済み）: 目録・ingest の正の定義を導出に・`GET /api/relations`・同期テスト
- R0b（実装済み・#845）: ingest・api の写しを目録の導出に。`step0/`・`mcp/`・`demo-agent/` は AI への指示と道具の説明で
  文面を変えないため写しのまま（同期テストで縛る）。同期テストは step0 と ui・i18n の分が残る
- R0c（実装済み・#844）: ui と i18n を API の目録から
- R1（別の PR・`pair-relations.md`「言えない」の種類）: 目録の集合は変えない。`none` のとき、集合に無い関係の種類を
  短い名詞で台帳に残す（R2 以降で並べる・目録へ足す入口にする）。`PAIR_RELS` などの一覧は不変
- R2（別の PR・`pair-relations.md`「束ねる」）: R1 の種類を `merge_key` で束ね、種類ごとの組の数と例を返す。目録と同じ鍵の
  群には `in_catalog`（R3 で「既にある関係」と見せる）。目録は変えない
- R0d（この PR）: 目録をストアに置く（下の「ストアでの表し方」。利用者が案 A を選んだ。人が足す口は未）

## 向きは 3 層で中身が違う（揃えない）

| 層 | 向きの無い関係 | 欄 |
|---|---|---|
| 下書き（`themes`） | `competes` だけ | `symmetric_draft` |
| 台帳・提案（`pair_relations`・step0） | `eq` と `competes` | `symmetric_pair` |
| 確定（`crosswalk_runtime`） | `competesWith` だけ | `Predicate.symmetric` |

`eq` は下書きと確定では向きありのまま扱い（A→B と B→A は別の行）、台帳・提案では語の id 順に並べて 1 本にする。
理由は各層が別々に育ったため。揃えると振る舞いが変わる（既存の台帳・下書きの鍵が変わる）ので R0a では
欄を分けて今の値を保った。揃えるなら別の PR で、データの移し替えとテストの更新を伴う。

## 目録で表しにくい物（コードに残す）

- `measures` の割れ方: 述語は 2 つ（`measuresProperty`・`measuresMaterial`）で、相手の種別
  （`to_kind`）で決まる。目録は `PredicateRule.when` で持ち、`resolve_predicate` が引く。相手の種別が
  無ければ決まらない（`themes.relation_predicate` が `ThemeError`）。`sub`・`eq` は主語の種別（`from_kind`）で割れる。
- `factors` の閉包・`missing` の定義などの問いの型（13 の op）は目録に入れない（`knowledge.OPS`・`LINE_OPS`）。
- 関係ごとの意味づけのうち「同じ提案の中で相手の種別を決める」（`chat_paper.build_ops` の、項目と決まった語は項目・
  `measures` の相手は提案の中の他の関係から決める・既存の同名の語の選び方）。目録は関係ごとの規則（`to_property_only` など）
  を持つだけで、提案ごとの推論は持たない。
- `factors` の 1 段の閉包（X の一部への線も数える）・`missing` の定義（`MISSING_MIN_OTHERS`・持たない物性の数え方）・
  `quantity` の特例（相手は標準の量の種類だけ＝`quantity_target`）。これらの分岐の入口の判定は目録から引くが、中身はコード。
- 当てはめ（`upper.json`）の `consult_graph_routes._BIND_TO_UPPER`（`eq`→`equivalentProperty`・`sub`→`subPropertyOf`）と
  その `eq`／`sub` の振り分け。項目の列だけに置ける関係で、関係の定義ではなく当てはめの規則。
- 問いの文・表示の文言（`chat_paper._Q_TEXT`・`shared_vocab._KNOWLEDGE_SENTENCES` など）。
- AI への指示と道具の説明（`step0/`・`mcp/`・`demo-agent/`）。文面を変えないので写しのまま。

## R0c 後の ui（実装メモ）

- 実行時は `GET /api/relations` を起動後に読み（失敗したら 1 回だけ読み直す）、組み込み（生成物）に重ねる
  （`ui/src/relations/catalog.ts`）。形の検査を通らない返り・重ねた後に主な関数が例外を出す返りは捨てて組み込みのまま。
- 判定（「関係か」「知識の関係か」「向き」）は目録の読み関数（`relationFns.ts`）。`RELATION_ORDER`・`KNOWLEDGE_RELATIONS`・
  `REL_KINDS` の定数は型の元と既存の呼び手のための組み込みの並び。
- **R3 の課題**: `getCatalog()` を直に呼ぶ所は、目録が変わっても次の再描画まで古い結果を持つ（`useCatalog()` を呼ぶのは
  `HandForm`・`GraphPlaceSide` の 2 部品だけ）。API にだけある関係が来るときは、使う部品を広げる。i18n json に無い関係の文言も同時に要る。

### コードに残した物（ui）

目録に欄が無い・画面ごとの見せ方なので、直書きのまま残した。

- `lineChoice.ts` `relationSymbol` の記号（↓ ↑ ↕ ⇒ ↔ など）
- `sky/skyLayout.ts` の `isEq`（約 308 行）・札の幅 `PILL_W_PART` と `'isPartOf'` の判定（約 969 行）
- `themes/themeDraft.ts` の `'competes'`・`'has_property'` を見る数え方（約 761-767 行）・`lineEstimate`・`knowledgeEstimate`・`MISSING_MIN_OTHERS`
- `chat/handWays.ts` の `need`
- `decide/decide.ts` の `CHOICE_ORDER`（ひとこと決めるの並び）
- `startswith("equivalent")` が残る 3 か所（`ingest/src/asterism/suggested_lines.py` の `suggested_lines`・`api/src/asterism_api/chat_attach.py` の提案の文の選び分け・`ingest/src/asterism/shared_vocab.py` の `_down_set`）。述語の名前の接頭辞で「同じ」を見ており、目録は引いていない。
- `sky_view._LINE_RELATIONS` の並び（種類の側 → 項目の側）は、目録の述語の並びと `shapes` の from に依る。目録で述語の並びや種別を変えると並びが変わる。

## ストアでの表し方（R0d）

状態: 実装（R0d）。**振る舞いは変えない**: 今の読み手（`themes`・`graph_diff`・`crosswalk_runtime` など）は
コードの目録を読んだまま。ストアには「置く」と「読み戻す」だけを足した。実装は
`ingest/src/asterism/relation_store.py`。

### 置き場所と名前

| 物 | IRI |
|---|---|
| グラフ | `substrate.RELATION_CATALOG_GRAPH` = `…/graph/vocab/relations`（`SHARED_VOCAB_GRAPH` と並ぶ） |
| 項目の名前空間 | `https://kumagallium.github.io/asterism/vocab/relation-catalog#` |
| 関係 | `#<短い名前>`（`#sub`・`#has_property`） |
| 確定の述語の記述 | `#predicate-<camelCase>`（`#predicate-competesWith`） |
| 規則（関係にぶら下がる） | `#<関係>-rule-<n>`（n は 1 から） |
| 入れ子・並びの子の節 | `#<親>-<欄>`・`#<親>-<欄>-<n>`（例 `#predicate-competesWith-own-searched-3`） |

関係の名前と `predicate-` で始まる名前はぶつからない（関係の名前は `predicate-` で始まらない）。
標準の述語（RO・SOSA・OWL・RDFS・QUDT の IRI）と自前の述語 `…/vocab/relation#competesWith` は**主語として何も書かない**。
項目の `xw:predicate` が指すだけ。自前の記録（表示名・定義・探した語彙・日付）は述語の記述の子の節（`-own`）に書く。

### 語彙（`xw:` = `…/crosswalk/ontology#`。既存の用語と名前がぶつからないことを `git grep` で確認）

| 種別 | 用語 |
|---|---|
| クラス | `xw:RelationEntry`・`xw:PredicateEntry`・`xw:PredicateRule`・`xw:EntryPart`（入れ子・並びの子の節） |
| 項目の性質 | `rdfs:label`（`@ja`・`@en`）・`xw:name`・`xw:sentence`（言語タグつき）・`xw:order`（並び）・`xw:builtin`・`xw:addedBy`・`prov:generatedAtTime`・`xw:rule`（関係 → 規則）・`xw:predicate`（規則・述語の記述 → 述語の IRI） |
| 欄の性質 | 欄名の camelCase（`xw:symmetricDraft`・`xw:noSelf`・`xw:sameShape`・`xw:shapes`・`xw:roles`・`xw:own`・`xw:when` …） |
| 子の節 | `xw:ownerEntry`（どの項目のものか）・`xw:value`（文字列の並び）・`xw:from`・`xw:to`・`xw:date`・`xw:definition`・`xw:searched` |

組み込みの項目には `xw:builtin true`・`xw:addedBy "machine"`・`prov:generatedAtTime`（置いた時刻）。
人が足す段（R0d の後）は `xw:builtin` を付けず、`xw:addedBy "human"` とする想定。

### 名前の規則と予約の欄名

| 物 | 規則 | 今の件数 |
|---|---|---|
| 関係の名前 | `^[a-z][a-z0-9_]*$` | 12 件すべて合う |
| 述語の名前 | `^[a-z][A-Za-z0-9]*$` | 15 件すべて合う |

理由: 子の節の IRI（`<項目>-…`）は必ず `-` を含み、関係の名前には `-` が無いので、子の節が別の関係の IRI と
ぶつからない。述語の記述の IRI（`predicate-<名前>`）も、関係の名前に `-` が無いのでぶつからない
（述語の名前にも `-` が無いので、子の節ともぶつからない）。`catalog_triples` は規則に合わない名前を `ValueError` で断る
（人が足す口も同じ検査を通る）。

予約の欄名（camelCase・`RESERVED_FIELDS`）: `order`・`value`・`ownerEntry`・`addedBy`・`rule`・`predicate`。
規則用の性質と同じ名前になって壊れるので、目録の欄（入れ子の中も）がこの名前だと `ValueError`。
`name`・`builtin`・`label`・`iri`・`predicates` は目録の欄が同じ性質に写る約束なので予約の外。

`catalog_triples(catalog, *, builtin, at=None)` の `builtin` は呼び手が明示する（`seed_builtin` は `True`）。
項目に `builtin` の欄があればそれが優先。人の述語は `builtin=False` で通すと `xw:builtin true` が付かず、
種まきは触らない。

### 規則の表し方（「測る」「一種」「同じもの」）

両端の種別で述語が割れる関係は、規則の節を並べて関係にぶら下げる。並びは `xw:order`（意味を持つ: 1 つ目の条件に当たれば 1 つ目）。

```
rc:measures      a xw:RelationEntry ; rdfs:label "測る"@ja , "measures"@en ; xw:rule rc:measures-rule-1 , rc:measures-rule-2 .
rc:measures-rule-1  a xw:PredicateRule ; xw:order 1 ; xw:name "measuresProperty" ; xw:predicate obo:RO_0009006 ; xw:when rc:measures-rule-1-when-1 , rc:measures-rule-1-when-2 .
rc:measures-rule-1-when-1  xw:order 1 ; xw:value "to_kind" .   # -when-2 は "property"
rc:predicate-competesWith  a xw:PredicateEntry ; xw:predicate <…/vocab/relation#competesWith> ; xw:own rc:predicate-competesWith-own .
rc:predicate-competesWith-own  rdfs:label "競合する"@ja ; xw:date "2026-11-17" ; xw:searched rc:predicate-competesWith-own-searched-1 , … .
```

### 目録の欄を一般的に写す（R0b などで欄が足されても動く）

`catalog_triples(catalog_to_json())` は、JSON の**値の型**だけで写す（モジュールの docstring の表）。
文字列・真偽・整数・言語別の文字列・入れ子の辞書・文字列の並び・辞書の並び・`None`（何も書かない）。
読み戻し（`read_catalog`）は、今のコードの目録の形（欄の名前と型）を雛形にするので、欄が足されれば
書くのも読むのも自動で追従する。**無視するもの**: 上の型以外の値（リストのリスト・浮動小数点など）、
ストアにあってコードの目録に無い欄（読み戻さない）。欄が `None` の項目しか無い欄は、型が分からないので
読み戻しても `None`。

### 種まき（`seed_builtin(client, *, at)`）

- 起動時（`main.py` の `lifespan`）に 1 回。単一ユーザー（`local.py`）も本番（`compose.prod.yaml`）も同じ `lifespan` を通る。
- 冪等: ストアの `xw:builtin true` の項目を読み戻し、今の目録（`catalog_to_json()`）と**完全に同じ**なら何も書かない（時刻も動かさない）。
- 違えば、組み込みの項目（と `xw:ownerEntry` でぶら下がる子の節）だけを消して入れ直す（1 回の更新）。
  `xw:builtin true` でない項目＝将来人が足した関係には触らない。時刻は入れ直したときだけ新しくなる。
- **組み込みの項目の主語は機械のもの**。組み込みの項目（と子の節）の主語に付いた 3 つ組は、再種まきで消える。
  人の確認・注釈はここに直接書かない（人の確認は別の節に書き、組み込みの項目を指す形にする。R3 で作る）。
- 失敗しても起動を止めない（ログだけ）。ストアに繋がらない起動・テストの偽ストアでも壊れない。

### 読み戻しの口

- `GET /api/relations` — 従来どおりコードの目録（既定 `source=code`）。
- `GET /api/relations?source=store` — ストアから読んだ同じ形の目録（並びも同じ）。テストでコードの目録と一致を見る。

### 外に紛れ込まない

グラフは `canonical/`・`ontology/`・`meta/`・`moved/` のどの接頭辞にも入らず、データセットの一覧（registry 由来）・
`/api/sky`・`find_datasets`・名前の索引の列挙（接頭辞つき）には現れない。`readable_graph_iris` にも入れていないので、
データセットの範囲（`FROM`）にも混ざらない。

### MCP から引けるか（利用者の裁定 2026-11-19: 許可リストに目録のグラフだけを足す）

**引ける**。生の SPARQL の検査（`substrate.canonical_merge_query`。`/api/sparql` と MCP の `sparql_query` が通る）が、
`FROM <…/graph/vocab/relations>`・`FROM NAMED <…>` で名前を挙げたときだけ目録のグラフを読ませる。
`readable_graph_iris` には入れない（データセットの範囲・`/api/cards` の範囲・既定の FROM に混ざらない）。
目録は確認済みの関係の定義だけなので、外の AI に見せても未確認は漏れない。目録を構造のまま返す道具 `list_relations`（引数なし・`{relations, predicates, source}`・
`source` はストア `store`／読めない・空・形の検査に通らないときのコードの目録 `code`）を MCP に足した。**これが主役の道**で、AI は SPARQL・IRI を書かずに引ける。`sparql_query` で目録のグラフを名指す道は逃げ道。アプリ内の `/mcp` も同じ道具を出す。

## 人が足す関係（R3a）

設計: design.md §23.13（③）・契約 `contracts/prR3a-contract.md`。**API まで**（画面・AI への選択肢・問い・星図は後の段）。

### 形

1 件 = `Relation`（`builtin=False`）＋ 確定側の `Predicate` 1 つ。**名前と述語は機械が作る**。人も AI も書かない。

| 項目 | 値 |
|---|---|
| 関係の名前 | `u_<8 桁の 16 進>`（`secrets.token_hex(4)`・既存とぶつかれば引き直す）。**組み込みは `u_` で始めない**（テストで固定） |
| 述語の名前・IRI | `u<同じ 8 桁>`・`REL_NS + 述語名`（自前の名前空間の下。標準の述語は任意の `exact_match` で「同じ意味」と指すだけ） |
| 項目の IRI | 組み込みと同じ（`CATALOG_NS#<名前>`・`#predicate-<述語名>`） |
| 人が決める性質 | **向き（対称か）**と**終わりの種類（B は項目に限るか）**の 2 つだけ。`symmetric_draft = symmetric_pair = 向きが無い`・`to_property_only = 項目に限る`。循環・同じ種類・実験・論文は付けない（`False`） |
| 述語の形 | `knowledge=True`・`shapes=(両方, 項目か両方)`・`own`（表示名・説明・探した範囲は空・足した日） |
| 足す欄 | `definition`（AI への 1 文・必須）・`exact_match`・`retired`・`retired_at`・`unstated_key`。来歴は `xw:addedBy "human"`＋`prov:generatedAtTime`（読み戻すと `added_by`・`added_at`）。JSON には**人の項目にだけ**出る |
| 入力の検査 | 表示名 1〜40 文字・説明 1〜200 文字（NFKC・制御／書式の文字なし・URL・`<>{}`・バッククォート・バックスラッシュを断る）。表示名が組み込み・足した関係の名前・表示名と `merge_key` で同じなら 409 |

文の型が無いときは表示名から機械が作る既定の文（`{s} → {o}（表示名）`・向きが無ければ `↔`）。

### 書く・読む（`relation_store`）

- `add_relation`・`update_relation`（表示名・文・説明・`exact_match` だけ）・`retire_relation`・`delete_relation`・`read_human`・`usage`・`refresh_human`。書くのは目録のグラフだけで、**1 回の更新**（読み手が半端な項目を見ない）。組み込みの名前を渡されたら断る。
- `xw:order` は組み込みの後ろ（10000〜）。置き換え（直す・やめる）でも並びは変わらない。
- `seed_builtin` は今のまま、人の項目（`xw:builtin true` でない）に触らない。

### 目録への重ね方（`relation_catalog`）

- `set_human(relations, predicates)` で入れ物に入れる。読み出し関数は**組み込み＋足した関係**を返す（組み込みが先・足した順）。`catalog_to_json()` は**組み込みだけ**のまま（種まきと `gen_relation_catalog_ts` の正）。`overlay_catalog_to_json()` が重ねた目録。**足した関係 0 件なら全部の出力が組み込みだけのときと同じ**。
- **やめた関係（`retired`）**: 「選べる」集合（`assignable_relation_kinds`・`assignable_predicates`）からは外す。新しく選ぶ道はすべてこれを使って断る＝`pair_relations.assign`・AI の答えの検査（`check_answer`）・`crosswalk_runtime.assert_alignment`（`POST /api/crosswalk/alignments` も同じ関数）・話の下書きの確定（`themes._plan_commit`）・差分の検査（`graph_diff.check_relation`＝`retired`）。既存の線の読み戻し・取り消し（`remove_alignment`）・根拠の付け直し（`add_evidence`）は断らない。「読める」集合（`relation_kinds`・`pred_to_rel`・`rel_sentence`・確定の線の読み戻し）には残す。付いた組と根拠は残り、読み戻せる。名前と述語は後から変えない・戻す口は作らない。
- 起動時（種まきの後）に `refresh_human(startup=True)`（失敗しても起動は止めない＝足した関係 0 件として動く）。口で書いた後も同じだが、**書き込み後の読み直しに失敗したら前の状態を保つ**（0 件にしない）。
- **壊れた項目は捨てる**: `read_human`／`set_human` の手前の `sanitize_human` が、述語の記述が無い・規則の述語が見つからない・名前の規則違反・表示名が空の項目を捨てて警告のログを出す（目録全体は落とさない）。文の言語が欠けていれば、もう片方の言語か既定の文で埋める。壊れた項目でも `usage`・`DELETE` は検査なしの読み出しで通る。
- 書き込み（add / update / retire / delete）はイベントループごとの `asyncio.Lock` を 1 つかける（重複の検査と書き込み・使われ方の集計と削除の間を閉じる）。人が足せる関係は 500 件まで（超えたら 409）。
- 入力の文字の規則は `relation_store._clean_text` の 1 か所。表示名と `unstated_key` は R1 の `_unstated_kind` と同じ系統（スキーム・`//`・`www.`・Markdown のリンク・`[]`・`<>{}`・バッククォート・SQL 語・「無い」の言い換え・記号だけ／数字だけ・組み込みの関係名／述語名と同じ表示名を断る）。説明・文は自然文なので SQL 語と none は検査しない。
- **import 時に固定していた定数のうち B・C・D の道の物は、呼ぶたびに目録から引く**。`ALIGN_RELATIONS`（実体の dict を持ち続ける読み手のため、変わったときにキーの差分で足す・消す）・`graph_diff.PRED_TO_REL`／`REL_SENTENCE`・`pair_relations._CONFIRMED_NAMES`（`LiveMap`＝呼ぶたびに引く読み取り専用の辞書）。組み込みだけで足りる所（問いの型・論文・話の下書きの AI・step0）は触らない。

### 使われているか・削除

使われている = (a) 確定の線（足した関係の述語の 3 つ組がどこかのグラフにある）・(b) 取り込み元の台帳に `rel == 名前` の件・(c) 話の下書き・却下の記録に名前（関係の名前・述語の名前）が出る。`{lines, ledger, drafts}` を数える。
`DELETE` は全部 0 のときだけ（1 つでもあれば 409 と数。画面は「使うのをやめる」を勧める）。消すと目録の項目だけが消える。

### 口（`relations_routes.py`）

`POST /api/relations`・`PATCH /api/relations/{name}`・`POST /api/relations/{name}/retire`・`DELETE /api/relations/{name}`（すべて `write_auth`。組み込みは 403・無ければ 404）。`GET /api/relations` は既定で重ねた目録（`?usage=1` で足した関係に使われ方）。語の組に付ける口（`assign`）はやめた関係を 400（`reason: retired`）で断る。

### 残した穴

- **単一のプロセスの前提**（デスクトップと今の本番）。複数のワーカーで動くときの、足した関係の同期は未対応（書いたワーカーだけが重ねた目録を持つ。起動時に読む）。
- 足した関係を AI に選ばせる（R3b）・画面（R3c）・問い・星図・判定し直し（R4）は後の段。星図（`sky_view`）は星図の外なので線の集合は組み込みのまま（足した関係の線は読み戻しで落ちない・星図には描かれない）。
- `consult_graph_routes._SYMMETRIC`・`shared_vocab._REL_BY_IRI` は本体では使わなくなったが、既存のテストが固定しているので残してある。
- スタンドアロンの MCP（`asterism_mcp` が別のプロセスで動くとき）は、起動時に足した関係を読まない。`list_relations` はストアを読むので足した関係が出るが、線の読み戻しの述語の集合は組み込みのまま。

## R3b — 足した関係を組ごとに関係を選ぶ AI に渡す

R3a で足した関係は、R3b で AI の選べる集合に入る（別名 `x1`… で答え、機械が名前に戻す・上限 40・聞き直さない）。詳細は [`pair-relations.md`](pair-relations.md) の「足した関係を AI に渡す」。画面は R3c。
