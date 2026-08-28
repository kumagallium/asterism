# ADR: Tier 0 — 単一 object のドットパス取得と Python リテラル repr の受理

決定: 2026-08-27 / status: **採択**（実装 = `feat/tier0-json-get`）

関連: [`phase5-declarative-substrate.md`](phase5-declarative-substrate.md) §4/§5（関数ライブラリ・有界性）/
[`ingestion-execution-safety.md`](ingestion-execution-safety.md)（閉集合 no-codegen の安全境界）/
[`tier0-coverage-gate.md`](tier0-coverage-gate.md)（「十分」の判定）/
[`non-csv-sources.md`](non-csv-sources.md)（JSON 経路と配列 leaf の制約）

## 1. 文脈 — 実データで同時に開いた 2 つの穴

Materials Project の全材料 CSV（84k 行・`material_id` / `pretty_formula` / `band_gap` /
`e_above_hull` / 弾性率 / `structure` …）を取り込もうとして、Tier 0 の穴が 2 つ同時に露呈した。
`structure` 列の中身は次の形をしている:

```
{'@module': 'pymatgen.core.structure', '@class': 'Structure',
 'lattice': {'matrix': [[…]], 'a': 3.339, 'b': 3.339, 'c': 3.418,
             'alpha': 119.24, 'beta': 119.24, 'gamma': 90.0, 'volume': 27.56},
 'sites': [{'species': [{'element': 'In', 'occu': 1}], 'abc': […], 'label': 'In'}]}
```

### 穴 1: 単一 object から値を取る関数が無い

| 既存関数 | 入力の形 | 出力 |
|---|---|---|
| `json_array_single` | 1 要素**配列** | スカラ |
| `array_at(index)` | **配列** | スカラ |
| `json_array` | 配列 of スカラ | list（explode） |
| `json_pluck(field)` | **配列 of object** | list（explode） |

すべて**配列**が入口で、`{…}` という単一 object から `lattice.a` を取る手段が無い。しかも
`structure.lattice.a` は 2 段ネストなので、仮に単段の field 取得があっても足りない。

### 穴 2: Python の dict repr は JSON ではない

上のセルはシングルクォート＝**Python リテラルの repr であって JSON ではない**。pandas で
dict / list を持つ列を `to_csv()` すればこの形になる。pymatgen 固有ではなく、Python 系の
データ処理から出てきた CSV に広く現れる。

既存の JSON 系関数は例外なく `json.loads()` で実装されているため、`JSONDecodeError` を握って
静かに `""` / `None` を返す。**症状は「何も出てこない」で、原因が利用者からは見えない。**

実測（当該 CSV の先頭 3,000 行）: `json.loads` **0 / 3000 成功**、`ast.literal_eval`
**3000 / 3000 成功**。

### 穴 2 の帰結 — 設計フローが列を「見る」ことすらできない

穴 2 は取り込み時だけの問題ではない。step0 の `inspect` も `json.loads` で型を推論するため、
この列は `json-object` ではなく **`xsd:string`** に落ち、inspection Markdown の「JSON columns」
セクションに載らない＝**キー一覧が出ない**。サンプル値は 40 字で切られるので
`` `{'@module': 'pymatgen.core.structure', '@` `` までしか見えず、`lattice` というキーの存在すら
AI に伝わらない。**関数を足すだけでは `path` を書きようが無い。**

## 2. 決定

| # | 論点 | 決定 |
|---|---|---|
| D1 | 単一 object の値取得 | **`json_get(value, path)` を Tier 0 に追加**。`json_pluck`（配列 of object）と対になる単一 object 側。`path` はドット区切りの**定数引数**（`fn:p_path`・`CONSTANT_PARAM_IRIS`） |
| D2 | Python リテラルの受理 | `json.loads` → 失敗時のみ `ast.literal_eval` のフォールバックを、**JSON 系 5 関数すべて**に適用（`json_get` / `json_array_single` / `json_array` / `array_at` / `json_pluck`）。列単位でなくファイル単位で発生する形なので、片方だけ対応すると「なぜこの列だけ取れないのか」という不可解な挙動になる |
| D3 | 受理の実装場所 | **新モジュール `asterism._jsonio` に集約**。Python リテラルを受理する経路をここ 1 箇所に閉じ、DoS ガードと監査対象を明確にする |
| D4 | 置換方式は棄却 | 「シングルクォート → ダブルクォート」の機械的置換は、値に `'` を含むデータ（CIF の `'P 1'` 等）で**静かに壊れた値**を生む。`literal_eval` が正しい |
| D5 | スカラのみ | `json_get` の最終値が list / dict なら `""`。既存 `array_at` / `json_pluck` と同じ方針 |
| D5b | index セグメントは素の整数のみ | `int()` は `" 1"` / `"+1"` / Python の桁区切り `"1_0"`（= 10）も受ける。`path` は人間/AI が書く定数なので実害は小さいが驚きの元＝**`-?[0-9]+` に限定**する |
| D6 | 多値扱いしない | `json_get` は `MULTIVALUED_FUNCTIONS` に入れない（スカラ 1 値） |
| D7 | 設計時の型推論も同じ受理をする | step0 の `inspect`（`_detect_json_kind` / `_json_first_keys`）も Python リテラルを受理する。**step0 は「No hard runtime deps」を意図的に守っている**（`step0/pyproject.toml`）ため ingest を import せず、**同じ上限値の複製**を持つ。役割が違う＝ ingest 側は取り込み時に値を*読む*（安全境界の対象）、step0 側は設計時に列の形を*見る*。**両者は対で保守する**（双方の docstring で相互参照） |
| D8 | キー一覧をネスト対応にする | `json_get` の `path` に必要なのは**ネストしたキー**なので、inspection の `json_keys` を**ドットパス 2 段**まで出す（object のみ降り、配列は既存の多値経路に委ねる）。`max_keys` 12 → 24 |

## 3. 安全性 — なぜ `literal_eval` が閉集合原則を破らないか

[`ingestion-execution-safety.md`](ingestion-execution-safety.md) §2 の鉄則は
「**AI が生成した ingester を自動実行しない**」。本追加はこれを破らない。

| ADR §2 の懸念 | 本追加では |
|---|---|
| AI 生成コードの実行 | **該当しない**。AI が生成するのは「どの列にどの関数を、どの定数引数で当てるか」だけ。関数本体は人間が vet して `REGISTRY` に足す＝Tier 0 の正規手順 |
| 共有インスタンスで他者のアップロードに紐づくコードが走る | **該当しない**。走るのは vet 済み関数のみ |
| 悪意ある CSV に誘導された有害コード | ← 唯一の検討点。以下 |

### 3.1 `ast.literal_eval` は `eval` ではない

`compile(source, mode="eval", flags=PyCF_ONLY_AST)` で AST を作り、**リテラルのみ**（str /
bytes / 数値 / tuple / list / dict / set / bool / None / 単項 ± / 複素数の加減）を再帰的に構築する。
**Call・Name・Attribute・Subscript の各ノードは `ValueError` で拒否される。**
したがって `__import__('os').system(…)` は Call として、`[].__class__` は Attribute として弾かれる。
**任意コード実行は原理的に起きない**（`eval` との決定的な違い）。

### 3.2 残る攻撃面は DoS だけ。2 つの上限で塞ぐ

| リスク | ガード |
|---|---|
| 巨大入力によるパース時間・メモリ | **入力長 1 MiB 上限**。超過は `None` |
| 深いネスト（`[[[[…]]]]`）によるパーサのスタック / メモリ消費 | **どちらのパーサを呼ぶ前にも**深さを走査し、**64 超は `None`**。走査は**文字列リテラル内も含めて全ての括弧を数える**（下記） |
| 例外がプロセスまで漏れる | `ValueError` / `SyntaxError` / `MemoryError` / `RecursionError` / `TypeError` を捕捉 |

### 3.2.1 深さ走査はクォートを解釈しない（レビューで判明した罠）

初版は「文字列リテラル内の括弧はスキップする」クォート追跡付きの走査だった。**これは破れる。**
トリプルクォート中に単独のアポストロフィを置く（`{'cif': '''it's fine''', 'm': [[[…150 段…]]]}`）と
「クォートが来たらトグル」式の追跡が脱同期し、以降ずっと「文字列の中」に留まる。結果、後続の
**本物のネストが 1 段と誤判定**され、150 段が `ast.literal_eval` に渡る。**実測で再現**（走査は
depth 1 を報告、149 段のリストが実際に構築された）。より深い入力では CPython 自身の PEG パーサ上限
（`SyntaxError: too many nested parentheses`）が偶然救うが、それは設計した防御ではない。

**決定: クォート追跡を捨て、全ての括弧を数える。** Python の文字列字句（両クォート・トリプル
クォート・エスケープ・prefix）を忠実に再現しない限り、この種の脱同期は必ず残る。全部数える走査は
**過大評価にしか倒れない**＝誤りは常に「拒否する」側に出る。上限 64 に達するには文字列値の中に
対応の取れない開き括弧が数十個要り、実データが取る形ではない（対称な `(x, y, z)` は深さを積まない）。

### 3.3 path 引数からの属性アクセスを構造的に排除

`json_get` の path 解決は **`dict.get()` と `list[int]` のみ**で、**`getattr` を使わない**。
よって `__class__` / `__globals__` のような文字列が来ても**ただのキー名**として扱われ、
存在しなければ `""` を返す。

これは既存プリミティブと同じ姿勢である: `lookup` は path traversal を拒否、`regex_extract` は
re2 で ReDoS を排除（stdlib `re` へのフォールバックを意図的に拒否）、`template` は
`str.format` / `eval` を使わない単一パスのリテラル置換。

## 4. 不変条件（維持）

- **生成コード非実行・IRI 不変・Tier 0 閉集合。** `REGISTRY` は append-only、関数 IRI・
  パラメータ IRI は安定（`fn:json_get` / `fn:p_path`）。
- **既存 4 関数の型チェックの厳しさは不変**（`isinstance(data, list)` はそのまま＝タプルを
  list として受けたりしない）。
- **`json.loads` が成功する入力の挙動はバイト不変**（速いパスを先に通す）。変わるのは
  「これまで静かに `""` / `None` に落ちていた Python リテラル入力が値を返すようになる」ことだけ＝additive。

## 5. 検証

- **単体**: `ingest/tests/test_jsonio.py`（新規）＝ JSON / Python リテラル repr / 1 MiB 超 /
  深さ 64 超 / **文字列リテラル内の括弧が深さに数えられないこと** / コード実行の試み
  （`__import__('os').system(…)`・`[].__class__`）が `None` になること。
  `test_primitives.py` ＝ `json_get` の正常系・list インデックス（負数含む）・型不一致・
  スカラのみ・**`getattr` 不使用**（`"__class__"` が `""`）・実データ形。
  `test_transforms.py` / `test_functions.py` ＝ 既存 4 関数の repr 受理と REGISTRY 登録。
  **ingest 701 passed / step0 679 passed**、両パッケージ `ruff check` PASS。
- **敵対的レビューで 1 件の実欠陥を発見・修正**（§3.2.1）。深さ走査のクォート追跡が破れて
  「64 超は拒否」の不変条件が実際に破れていた。回帰テスト（トリプルクォート脱同期・境界 64/65）を
  `test_jsonio.py` / `test_inspect.py` の両方に追加。
- **実 Morph-KGC e2e**（本 ADR の要）: 手書き RML（`fn:json_get` を 3 箇所＝`lattice.a` /
  `lattice.volume` / 配列インデックスを挟む `sites.0.label`）で 200 行を materialize →
  **1,400 トリプル**、`json_get(lattice.a)` は **200/200 行で値を出した**。Python リテラル repr が
  「宣言的 RML → 実 Morph-KGC → RDF」まで一気通貫で流れることを確認（`rmlf:constant` での
  定数引数の受け渡しも実機で成立）。
- **実データ e2e**: Materials Project 全材料 CSV（84k 行）の先頭 200 行を `asterism-inspect` に
  かけ、`structure` / `initial_structure` が **`xsd:string` → `json-object`** に変わり、キー一覧に
  `lattice.a` / `lattice.volume` などの**ドットパス**が出ること、`fn:json_get` の案内が
  「JSON columns」に出ることを確認。
- **841 MB 全量を端から端まで**（ローカル実機・専用 Oxigraph）:

  | 段 | 時間 | ピーク RSS |
  |---|---|---|
  | `asterism-inspect`（83,989 行） | 35.2 秒 | 1.14 GB |
  | `materialize_to_nt_file` → 587,923 triples / 122 MB | 306.8 秒 | 2.58 GB |
  | `stream_nt_file_to_oxigraph`（5 万行チャンク） | 39.5 秒 | チャンクで有界 |

  投入後の SPARQL で **材料 83,989 件すべてが `mp:latticeA` を持つ**ことを確認＝
  `json_get` が **83,989 / 83,989 行**で値を出した（取りこぼしゼロ）。
  **前処理スクリプトなしで CSV がそのまま通る**ことの実証。

## 6. 残課題

- **`sites` のような object 配列の 2 段ネスト**（`sites[].species[].element`）は本 ADR の対象外。
  単段の sub-field は `json_pluck` で取れるが、それ以上は入れ子 TriplesMap の領域
  （`non-csv-sources.md` §7・`tier0-coverage-gate.md` §4 の多値展開ワークストリーム）。
- **bool が `"True"` / `"False"` になる**（`str()` 慣習）。xsd:boolean としては不正だが、既存
  `array_at` と一貫させた。正規化には `bool_norm` を当てる必要があるが、**関数チェーンは現状
  propose が生成しない**（1 列 = 1 関数）。チェーンを許すかは別途の決定。
- **coverage corpus への反映**（`raw_rate` の再測定・demand-by-category）は次の較正時。
  Python リテラル列は現行コーパスに無いため、コーパス拡張の候補でもある。

## 7. 更新ログ

- 2026-08-27: 初版。Materials Project 全材料 CSV（84k 行）の取り込みで露呈した 2 つの穴に対する決定。
