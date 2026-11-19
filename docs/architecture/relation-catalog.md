# 関係の目録 — 関係の定義を置く、コードの中の 1 か所

状態: 実装（R0a）。設計は AI for Science 側 `ui-redesign-2026-10-19/design.md` §23.13。
前提 = [`relations-for-experiments.md`](relations-for-experiments.md)（12 文・15 述語）。

## 背景

関係（一種・同じ・…・物性）の定義が、ingest・step0・api・ui・i18n に写しとして散っていた。
関係を 1 つ足すと十数か所を直す。目録を 1 か所に置き、正の定義はそこから導き、写しは
同期テストで見張る。

## 目録の形（`ingest/src/asterism/relation_catalog.py`）

| 表 | 1 件 | 欄 |
|---|---|---|
| `RELATIONS`（12） | 下書き側の短い名前（`sub` `eq` … `has_property`） | `name`・`label` ja/en・`sentence` ja/en・`predicates`（`PredicateRule` = 確定側の名前・IRI・`when`）・`symmetric_draft`・`symmetric_pair`・`experiment`・`builtin` |
| `PREDICATES`（15） | 確定側の述語（`subClassOf` … `hasProperty`） | `name`・`iri`・`shapes`（from, to）・`same_shape`・`roles`（from, to）または None・`no_self`・`subsumption`・`symmetric`・`knowledge`・`own`（自前の述語の記録） |

目録は葉のモジュール（ほかの `asterism` モジュールを import しない）。

## 正と写し

| 正（目録から導く・R0a） | 公開名は残す |
|---|---|
| `themes.RELATION_KINDS`・`EXPERIMENT_RELATIONS`・`SYMMETRIC_DRAFT_RELATIONS`・`relation_predicate` | `themes` |
| `graph_diff.PRED_TO_REL`・`REL_SENTENCE` | `graph_diff` |
| `crosswalk_runtime` の `ALIGN_RELATIONS`・`KNOWLEDGE_RELATIONS`・`SYMMETRIC_RELATIONS`・`NO_SELF_RELATIONS`・`ROLE_RULES`・`OWN_RELATIONS`・`_SUBSUMPTION`・`RELATION_SHAPES`・`SAME_SHAPE_RELATIONS` | `crosswalk_runtime` |
| `pair_relations.SYMMETRIC_PAIR_RELATIONS`（`PAIR_RELATIONS` は `RELATION_KINDS` から元々導出） | `pair_relations` |

写し（同期テストが目録と突き合わせる。ずれたら落ちる）:

| 写し | テスト |
|---|---|
| step0 `theme_propose.RELATIONS`・`pair_propose.PAIR_RELS`／`SYMMETRIC_RELS`・`paper_propose.PAPER_RELS` | `step0/tests/test_relation_catalog_sync.py`・`api/tests/test_relations_api.py` |
| api `chat_paper._CLASS_BOTH`・`consult_graph_routes._SYMMETRIC`／`_SUBSUMPTION_RELATIONS` | `api/tests/test_relations_api.py` |
| i18n `themes.json`／`decide.json`（R0c で ui のコードの写しは目録から導く形になった。文言の json は残す） | `ingest/tests/test_relation_catalog_sync.py`（ファイルを読んで突き合わせる） |
| ui の組み込みの目録 `ui/src/relations/builtinCatalog.ts`（生成物 1 か所） | `python scripts/gen_relation_catalog_ts.py --check` と同じ一致を ingest のテストが見る（ずれたら落ちる）。作り直しは `python scripts/gen_relation_catalog_ts.py` |

置き換え前の値は `ingest/tests/test_relation_catalog_snapshot.py` が固定している（同じ値・型・並び。
`relation_predicate` は 12 関係 + 未知 × 種別 4 × 相手の種別 4 の全組み合わせ）。

## R0 の分け方

- R0a（これ）: 目録・ingest の正の定義を導出に・`GET /api/relations`・同期テスト
- R0b: ingest・api・mcp・demo-agent の写しを目録の導出に（同期テストを消す）
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
