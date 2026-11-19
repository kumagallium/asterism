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
- R0d: 目録をストアに置く（下の「ストアでの表し方」。人が足す口は未）

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
- 失敗しても起動を止めない（ログだけ）。ストアに繋がらない起動・テストの偽ストアでも壊れない。

### 読み戻しの口

- `GET /api/relations` — 従来どおりコードの目録（既定 `source=code`）。
- `GET /api/relations?source=store` — ストアから読んだ同じ形の目録（並びも同じ）。テストでコードの目録と一致を見る。

### 外に紛れ込まない

グラフは `canonical/`・`ontology/`・`meta/`・`moved/` のどの接頭辞にも入らず、データセットの一覧（registry 由来）・
`/api/sky`・`find_datasets`・名前の索引の列挙（接頭辞つき）には現れない。`readable_graph_iris` にも入れていないので、
データセットの範囲（`FROM`）にも混ざらない。

### MCP から引けるか（未対応・要判断）

**今は引けない**。`sparql_query` の `FROM` / `FROM NAMED` は `readable_graph_iris`（canonical・ontology・共有の言葉）に
限られ、`vocab/relations` は拒否される（実機で確認）。引けるようにするには `readable_graph_iris` に足す必要があるが、
それは `/api/cards` など `readable_graph_iris` を使う他の読み手の範囲にも混ざる。別の判断（専用の許可リストにするか）を要するので、
この PR では触っていない。

