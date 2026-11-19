# 関係の目録 — 関係の定義を置く、コードの中の 1 か所

状態: 実装（R0a・R0b）。設計は AI for Science 側 `ui-redesign-2026-10-19/design.md` §23.13。
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
| ui `sentenceRows.ts`・`mapModel.ts`・`lineChoice.ts`・`themeDraft.ts`・i18n `themes.json`／`decide.json` | `ingest/tests/test_relation_catalog_sync.py`（ファイルを読んで突き合わせる） |

置き換え前の値は `ingest/tests/test_relation_catalog_snapshot.py` が固定している（同じ値・型・並び。
`relation_predicate` は 12 関係 + 未知 × 種別 4 × 相手の種別 4 の全組み合わせ）。

## R0 の分け方

- R0a（これ）: 目録・ingest の正の定義を導出に・`GET /api/relations`・同期テスト
- R0b（実装済み）: ingest・api の写しを目録の導出に。`step0/`・`mcp/`・`demo-agent/` は AI への指示と道具の説明で
  文面を変えないため写しのまま（同期テストで縛る）。同期テストは step0 と ui・i18n の分が残る
- R0c: ui と i18n を API の目録から
- R0d: 目録をストアに置く（利用者が関係を足せる。形は利用者に案を示してから）

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
