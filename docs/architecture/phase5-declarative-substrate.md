# Phase 5 土台 — ソース非依存の宣言的 substrate（option 2 の設計）

決定: 2026-06-01 / 設計セッション（core）
status: 設計確定（feasibility・cross-dataset join・関数ライブラリ v0・設計→Ask 連結 すべて実証ずみ。core への commit は CC 待ち）

本書は [`ingestion-execution-safety.md`](ingestion-execution-safety.md) の **option 2**（生成コードなしで安全に多数ソースを RDF 化する）の設計を確定する。2 本のスレッド（Morph-KGC スパイク・MP 連結 PoC）が収束し、実データの cross-dataset クエリで妥当性を確認した。

## TL;DR

### 結論

1. **substrate は「CSV → RDF」ではなく「構造化ソース → RDF」。** CSV（starrydata）と JSON/API（Materials Project）は等しく入力型の一つ。RML / Morph-KGC は両方を native に扱う。**CSV に縛らない。**
2. **3 部構成**: (a) 宣言的マッピング（RML, Morph-KGC 実行）、(b) 閉じた検証済み関数ライブラリ（FnO）、(c) すべての変換・リンクに PROV。
3. **生成コードを実行しない**を維持（option 2 / RCE なし）。新しい変換は人間が一度だけライブラリに足す。
4. **実証済み**: starrydata(CSV) × MP(API) を同じ sample IRI で結合し、「母相構造 × 測定物性」が 1 つの SPARQL で引けた（Bi2Te3→ZT 0.91 等、実データ）。

### 名前と IRI（汎化との関係）

- **engine は汎化する**（ソース非依存）。MP が API 由来で既に CSV 前提を破っている。
- **IRI namespace（`.../asterism/...`）と repo 名は当面据え置く。** IRI はデータの同一性で、変えると既存 RDF が壊れる（破壊的変更）。`asterism` という文字列は「CSV だけ」を主張するものではなく、安定した namespace に過ぎない。**汎化に IRI 変更は不要。**
- リネームは将来の positioning 判断（移行計画つき）として分離する。今やる必要はない。

## 1. 収束した 2 スレッド

| スレッド | 担う | 状態 |
|---|---|---|
| 宣言的マッピング（Morph-KGC スパイク） | 構造化ソース → RDF の「易しい 8 割」 | papers を純 RML で 40→120 triples、コードゼロで実証 |
| 検証済み関数 + PROV（MP 連結 PoC） | 科学的ロジック（母相正規化・欠陥・一致度） | `StructureMatchActivity`（method/confidence、unresolved も記録）で実証 |

合わせて = option 2 の substrate。

## 2. substrate の 3 部

### (a) 宣言的マッピング（RML / Morph-KGC）

- ソース → RDF を宣言で書く。RML の logical source は CSV・JSON・DB・API を扱う。
- **CSV（starrydata）**: テンプレートで複合 IRI も表現可（関数不要）。
- **JSON/API（MP）**: JSONPath で native に展開。CSV のセル内 JSON のような難所が無く、むしろ楽。
- 実行は Morph-KGC（既存依存）。**生成 Python を一切走らせない。**

### (b) 閉じた検証済み関数ライブラリ（FnO）

宣言で書けない少数の変換を、人間が一度書いて vet し、全ソースで再利用する:

- date 正規化 / QUDT 文字列→IRI / IRI sanitize（starrydata 由来）
- 母相正規化（ドープ剥がし）（MP PoC 由来）

新しい変換が要るソースは、人間が 1 回ライブラリに足す。**per-dataset の codegen が無い → RCE が経路から消える。**

### (c) すべての変換・リンクに PROV

- 取り込み = `IngestionActivity`、デジタル化 = `DigitizationActivity`、構造突き合わせ = `StructureMatchActivity`（method / confidence / 汎関数 / 日時）。
- 失敗・未解決も Activity として残す → 穴が queryable（捏造しない）。MP PoC が体現。

## 3. 妥当性 — cross-dataset クエリ（実データ）

starrydata（実験・CSV）と MP（計算・API）を同じ `sdr:sample/{SID}-{sample_id}` IRI で結合し、1 つの SPARQL で:

```sparql
?sample sd:hasHostStructure ?struct .                              # MP（計算）
?curve  sd:ofSample ?sample ; sd:propertyY ?py ; sd:yMax ?ymax .   # starrydata（実験）
```

結果（実データ・抜粋）: **Bi2Te3 母相 → ZT 0.91（3 sample）/ PbTe 母相 → Seebeck 12 sample**。どちらのソース単独でも答えられない問いが、共有 IRI 経由で通る。これが「成長 ＝ 既存語彙で繋がる」の実体。

## 4. 関数ライブラリ v0（実装・検証ずみ）

`asterism/functions.py`（Cowork 準備＋検証ずみ・CC commit 待ち）。宣言経路が参照してよい**閉じた集合**。各々 `starrydata` / `qudt` の既存実装へ薄く委譲（単一の真実源）。FnO は文字列を渡すので全部 `str -> str`、該当なしは `""`。`register(udf)` 1 行で Morph-KGC に全登録。

| 関数 | 由来 | 役割 |
|---|---|---|
| `date_iso` | starrydata | 独自日付 → xsd:date |
| `float_array_max` / `float_array_min` | starrydata | セル内 JSON 数値配列 → 最大 / 最小 |
| `iri_safe` | starrydata | IRI 不正文字の処理・非絶対 IRI の skip |
| `slug` | starrydata | IRI セグメント用 slug |
| `qudt_quantity` / `qudt_unit` | starrydata | 物性 / 単位の文字列 → QUDT IRI |

コア関数拡充（Track A、2026-06-10 着地）— 高頻度の「頭」の format 級変換 14 個。ロジックは `asterism/transforms.py`（ドメイン中立・単一の真実源。`bool_norm` のみ `lookup` の `bool` 表に委譲）。全 `str -> str`・該当なし `""`・単一入力（`p_value`）。Track C coverage の需要カテゴリ（epoch/値+単位/真偽/DOI/数値クレンジング）に対応:

| 関数 | 役割 |
|---|---|
| `number_clean` | 桁区切り/通貨/会計括弧 → xsd:double（`"$1,234.50"` → `1234.50`） |
| `percent_to_ratio` | `"12%"` → `0.12` |
| `range_min` / `range_max` | 数値レンジ `"10-20"` → 下端 / 上端 |
| `datetime_iso` | 雑多な日時 OR epoch(ms/s) → ISO 8601 dateTime |
| `year_only` | 4 桁年の抽出（epoch 誤爆ガード付き） |
| `nfkc_norm` / `trim_collapse` / `strip_footnote` | 文字列正規化（NFKC / 空白畳み / 脚注除去） |
| `bool_norm` | 真偽語彙 → `true`/`false`（`bool` 表に委譲） |
| `doi_norm` / `url_canonical` | ID 正規化（DOI 素形 / URL 正準化） |
| `value_of` / `unit_of` | 値+単位セルの分離（`"300 K"` → `300` / `K`） |

パラメータ化プリミティブ（§5.1、2026-06-09 着地）— value（列参照）に加え **定数引数**（表/正規表現/雛形）を取り、可変性をコードでなくデータへ逃がす。定数は RML で `rml:reference` でなく `rmlf:constant` で渡す（規約・罠は `step0-rml-emission.md` §1.1）。エンジンは `asterism/primitives.py`（ドメイン中立・単一の真実源）:

| プリミティブ | 定数引数 | 役割 |
|---|---|---|
| `lookup` | `table`（seed 表名） | 値を語彙表で引く（`bool` / `country_iso3166` / `unit_alias`、`ingest/src/asterism/tables/`・パッケージ同梱）。ミス → "" |
| `regex_extract` | `pattern`（re2 互換） | 部分文字列抽出（named group `v` → group 1 → 全体）。**google-re2 で線形時間＝ReDoS 免疫**・入力長上限。ミス → "" |
| `template` | `template`（`{1}`…`{4}`） | field1…4 を安全補間（`str.format`/eval 不可の単一パス置換）。欠損 → "" |

多値/ネストの「容易な勝ち筋」（2026-06-10 着地・`tier0-coverage-gate.md` §5）— 単一要素配列の展開・定位置配列の取り出し・**flat 区切りの多値展開**。`split` は唯一 **`list[str]` を返し Morph-KGC が各要素を 1 トリプルへ explode**（宣言的多値経路・入れ子 TriplesMap 不要・実機確認済）:

| 関数 | 定数引数 | 役割 |
|---|---|---|
| `json_array_single` | — | `["X"]`（1 要素）→ `X`。複数要素/非配列 → ""（データを黙って落とさない） |
| `array_at` | `index` | JSON 配列の定位置要素（`[lon,lat,depth]` index 1 → lat）。負 index 可。範囲外/非配列 → "" |
| `split` | `delimiter` | 区切り多値 → `list[str]`（Morph-KGC explode で複数トリプル）。object 配列は対象外（入れ子 TriplesMap へ） |

入れ子 object 配列（author 等）の展開＝`rml:iterator`＋副 TriplesMap は別ワークストリーム（残課題）。

検証: 単体 6/6（プリミティブは別途 `test_primitives.py`）＋ **設計→Ask の e2e**（このライブラリを参照する RML → Morph-KGC → 6686 triples → Ask が根拠+来歴付き回答。手続き型 ingester と同形・値一致）。検証コード `../../experiments/phase5-morph-kgc-spike/e2e/`。プリミティブの定数入力 e2e は `ingest/tests/test_substrate.py::test_materialize_with_parameterized_primitives`。

PROV について: セル変換（Tier 0）は粒度が細かすぎるので個別 PROV は持たない。PROV は取り込み / 突き合わせ Activity 級（§2c）で記録する。意味的に重い *linker*（次項 `normalize_host` 等）は別途 PROV（`StructureMatchActivity` 流）を持つ。

**`normalize_host`（MP PoC）は v0 外** — 2 入力・構造化返り値で「セル変換」と毛色が違うため、MP 連結昇格時に *linker 関数*（Tier 1 的、PROV つき）として別途入れる。

## 5. 関数ライブラリの統治とスケール

関数ライブラリ（§2b, §4）を「無限に膨らむ保守負債」にしない統治。原則は *関数は有界に保ち、可変性はデータへ逃がす*。

### 5.1 何が有界で、何が膨らむか

- **関数 = フォーマット級プリミティブ**（日付・数値・配列・IRI・単位…の *表現* を解釈/変換）。種類はデータセット数でなく *フォーマット数* で決まり、大きいが有界・低成長（FnO/GREL コアが数十関数で足りるのと同じ）。データセットが 100 増えてもフォーマットの種類はほぼ増えない。
- **膨らむものは関数の外へ**: 列の意味 → 各データセットの **RML マッピング**（その都度レビュー、共有ライブラリに入れない）。語彙の同義 → **語彙テーブル**（`qudt_map.yaml` 等。*コードでなくデータ*）。長い裾 → **パラメータ化プリミティブ**（`lookup(table)` / `regex_extract(pattern)` / `template`）で固有性を *データ/設定* に吸収する（2026-06-09 実装: §4 表・`asterism/primitives.py`。regex は google-re2 の線形時間マッチャで ReDoS を構造的に根絶）。

### 5.2 二層モデル

| | Tier 0（コア） | Tier 1（デプロイ局所） |
|---|---|---|
| 性質 | 汎用・全デプロイ信頼・curated | データセット / 組織固有 |
| 所有 | メンテナ | デプロイのスチュワード（技術担当） |
| 影響範囲 | グローバル | そのデプロイのマッピング内に閉じる |
| AI 参照 | 自由 | スチュワードがレビューした範囲のみ |

Tier 1 → Tier 0 の昇格は「複数デプロイが同じものを求めた（観測された汎用性）」ときだけ。増加は需要駆動で漸近する。precedent: FnO/GREL ＋ UDF、dbt core macro ＋ project macro、SQL 組込み ＋ UDF。

### 5.3 WASM の位置づけ（保留）

脅威は 3 軸 — **ホスト隔離**（WASM ◎）/ **意味的正しさ**（WASM 無力。サンドボックス内でも誤値を吐く・列を取り違える）/ **監査可能性**（むしろ悪化。名前付き関数 vs 不透明 blob を来歴が指す）。来歴・科学的真正性のシステムでは後二者が支配的で、閉じたライブラリは *隔離* でなく *正しさ＋監査* のために在る。WASM は束縛していない変数を最適化してしまう。

→ **WASM は Tier 0 を置換しない。** 将来サードパーティ拡張の生態系が出来たときの *Tier 1 の隔離手段* として棚に置く（pure 変換なら WASM と vetted 関数の差は capability でなく「誰がどの言語で書いたか」のみ）。今は導入しない — ランタイムが信頼基盤に 1 つ増える・文字列 ABI marshalling・決定性の規律（time/random import 禁止＝結局 pure）・Morph-KGC 橋、の重さが今の規模に見合わない。

### 5.4 オンボーディングは UDF 著述を要求しない

解くべきは「UDF を書きやすく」でなく「オンボーディングが UDF を *要求しない*」こと。受動的エンドユーザーに関数を書かせず段階的に逃がす:

1. 通常路（8 割）— AI が「列→述語」マッピングを提案、ユーザーは *承認のみ*（Tier 0 で足りる、著述ゼロ）。
2. 語彙のばらつき — 表に書くだけ（コードでなくデータ入力）。
3. 真に固有な変換（稀）— AI が関数を *下書き* し「実サンプル N 行でこう出る」入出力表を提示。ユーザーは *出力の妥当性* を見る（コードを読まない・影響は 1 データセット 1 列に閉じる）。
4. それも無理 — **その列は生文字列で素通り＋フラグ。**

> 離脱は「UDF が面倒」より「1 列のせいで全部入らない」で起きる。**未対応変換 → 生文字列フォールバック** を不変条件にし、オンボーディングを絶対に止めない。

### 5.5 クローズド運用でのスケール

本 substrate はクローズド / private 運用（機密データ・オンプレ）を想定する。するとメンテナは「各デプロイが何の関数を欲しているか」を観測できず、public issue も機密起点だと記述自体が不可能。よって *メンテナの全知* でなく *デプロイの自律* でスケールする:

- **ローカル拡張名前空間（主機構）** — `fn-local:` 等、スチュワードが所有・レビュー・昇格する独自の関数空間。保守コストはデプロイ数に対して O(1)。
- **厚いベースライン＋パラメータ化プリミティブ**（§5.1）で未対応率を下げ、そもそも観測の必要を減らす。
- **ローカル "未対応変換" ログ** — フォールバックした事象をデプロイ内に記録。見るのはスチュワードで、メンテナではない。フィードバックループを既定でローカルに置く。
- **機密を漏らさない上流路** — 共有は *データでなく関数*（pure 変換は普通非機密。動機データを伏せたまま関数だけ寄贈できる）。機密度が高い相談は private/NDA 窓口、public issue は非機密のフォーマット欠落に限定。

人物像: これに当たって動けるのは *受動的な研究者* でなく *デプロイのスチュワード*（技術者）。末端は承認のみ、ローカル関数を育てるのは管理者ロール。スチュワードがレビューする限り AI 任意コード自動実行の懸念も戻らない。

トレードオフ: クローズド運用は構造的にメンテナへの信号を減らす（privacy の代償・完全には消せない）。設計目標はループを *必須にしない* こと — 信号欠如が痛むのを *ロードマップ把握* に留め、*顧客の使用可否* に及ばせない。

## 6. 境界・関連

- これは **core（substrate）の仕事**。UI は取り込みエンジンを作らない。
- [`ingestion-execution-safety.md`](ingestion-execution-safety.md) の option 2 の設計本体。
- [`ontology-mapping-boundary-and-provenance.md`](ontology-mapping-boundary-and-provenance.md) の engine-vs-content の engine 側。
- 検証コード: `../../experiments/phase5-morph-kgc-spike/`（宣言マッピング）, `../../experiments/mp-linking-poc/`（科学ロジック + PROV）。

## 7. 残課題

- [x] 関数ライブラリ v0 を実装・検証（`functions.py`, 単体 6/6 + 設計→Ask e2e）。CC commit 待ち。
- [ ] step0 が宣言 RML を出力（Tier 0 のみ参照可・未対応列は生文字列フォールバック）。
- [ ] MP の structure facts を宣言的 RML に乗せ替え（API JSON → RDF）＋ `normalize_host` を linker 関数として昇格（PROV つき）。
- [ ] ソース型アダプタ（CSV / JSON-API）の最小抽象を定義。
- [ ] cross-dataset クエリを Ask の intent（母相構造 × 物性）に追加。
- [ ] ローカル拡張名前空間（`fn-local:`）＋ 未対応変換ログ（§5.5）。クローズド運用の自律スケール用。

## 8. 更新ログ

- 2026-06-01: 初版。2 スレッド収束 + cross-dataset join 実証を受けて、ソース非依存の substrate として確定。CSV に縛らない方針を明記。
- 2026-06-02: 関数ライブラリ v0 を実装・検証（`functions.py`）＋ 設計→Ask の e2e 連結を実証（§4）。関数ライブラリの統治を §5 に追加 — 有界性（関数=フォーマット級・可変性はデータへ）、Tier 0/1、WASM は Tier 1 隔離手段として保留、オンボーディングは UDF 著述を要求しない（未対応は生文字列フォールバック）、クローズド運用はローカル自律で O(1) スケール。
- 2026-06-09: パラメータ化プリミティブ（`lookup` / `regex_extract` / `template`）＋ seed 表（`bool` / `country_iso3166` / `unit_alias`）を実装（§4・§5.1、Tier0 拡充 Track B）。基盤＝FnO 定数引数対応: Morph-KGC は `rmlf:constant` 入力をネイティブ対応（`FunctionSpec`/`register` は無改変、定数を `rmlf:inputValueMap [ rmlf:constant "…" ]` で渡す。規約は `step0-rml-emission.md` §1.1）。no-codegen 不変（表/パターン/雛形は宣言データ）。`regex_extract` は **google-re2 の線形時間マッチャ**で ReDoS を構造的に根絶＋入力長上限。エンジン＝`asterism/primitives.py`。定数入力の e2e materialize（T9 通過）＋単体（lookup ミス/traversal・ReDoS・template 欠損/注入安全）緑。propose §9 にプリミティブの定数入力 RML 書き方＋利用可能表を追記。
- 2026-06-10: コア関数拡充（Tier0 拡充 Track A）— 高頻度の「頭」14 関数を実装（§4 表）。数値（`number_clean`/`percent_to_ratio`/`range_min`/`range_max`）・日時（`datetime_iso`=雑多 OR epoch・`year_only`）・文字列（`nfkc_norm`/`trim_collapse`/`strip_footnote`）・真偽（`bool_norm`=Track B `bool` 表に委譲）・ID（`doi_norm`/`url_canonical`）・値+単位（`value_of`/`unit_of`）。ロジックは `asterism/transforms.py`（ドメイン中立・単一の真実源）、`functions.py`/`propose.py` は **append-only**。全 `str -> str`・該当なし `""`・単一入力。Track C coverage の需要カテゴリに対応（着地後に `asterism-coverage report` 再実行→`…Raw` 率の再較正が次の一手）。単体 26 件＋ ruff/`transforms.py` mypy strict-clean。
- 2026-06-10: 多値「容易な勝ち筋」3 関数を追加（§4・`tier0-coverage-gate.md` §5）。`json_array_single`（1 要素配列の展開）・`array_at(value, index)`（JSON 配列の定位置要素・定数 index）・`split(value, delimiter)`（区切り多値 → **`list[str]` を返し Morph-KGC が explode** で複数トリプル化＝宣言的多値経路。`str -> str` 不変の唯一の例外で `FunctionSpec.func` 型を `str | list[str]` に拡張）。実機 e2e（`test_materialize_with_multivalue_functions`）で split の explode・array_at・json_array_single を確認。これで coverage の残 `…Raw`（multivalue_or_json）のうち単一要素配列/定位置配列/flat 区切りが関数化可能に。**残＝object 配列（author 等）の入れ子 TriplesMap 展開**。Tier0=28。
