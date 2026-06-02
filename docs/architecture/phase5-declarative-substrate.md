# Phase 5 土台 — ソース非依存の宣言的 substrate（option 2 の設計）

決定: 2026-06-01 / 設計セッション（core）
status: 設計確定中（feasibility 実証済み・cross-dataset join 実証済み・関数ライブラリ v0 は実装待ち）

本書は [`ingestion-execution-safety.md`](ingestion-execution-safety.md) の **option 2**（生成コードなしで安全に多数ソースを RDF 化する）の設計を確定する。2 本のスレッド（Morph-KGC スパイク・MP 連結 PoC）が収束し、実データの cross-dataset クエリで妥当性を確認した。

## TL;DR

### 結論

1. **substrate は「CSV → RDF」ではなく「構造化ソース → RDF」。** CSV（starrydata）と JSON/API（Materials Project）は等しく入力型の一つ。RML / Morph-KGC は両方を native に扱う。**CSV に縛らない。**
2. **3 部構成**: (a) 宣言的マッピング（RML, Morph-KGC 実行）、(b) 閉じた検証済み関数ライブラリ（FnO）、(c) すべての変換・リンクに PROV。
3. **生成コードを実行しない**を維持（option 2 / RCE なし）。新しい変換は人間が一度だけライブラリに足す。
4. **実証済み**: starrydata(CSV) × MP(API) を同じ sample IRI で結合し、「母相構造 × 測定物性」が 1 つの SPARQL で引けた（Bi2Te3→ZT 0.91 等、実データ）。

### 名前と IRI（汎化との関係）

- **engine は汎化する**（ソース非依存）。MP が API 由来で既に CSV 前提を破っている。
- **IRI namespace（`.../csv2rdf-mcp/...`）と repo 名は当面据え置く。** IRI はデータの同一性で、変えると既存 RDF が壊れる（破壊的変更）。`csv2rdf-mcp` という文字列は「CSV だけ」を主張するものではなく、安定した namespace に過ぎない。**汎化に IRI 変更は不要。**
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

## 4. 関数ライブラリ v0（次の実装 ＝ step 3）

| 関数 | 由来 | 役割 |
|---|---|---|
| `parse_date` | starrydata | 独自日付 → xsd:date / dateTime |
| `qudt_iri` | starrydata | 物性 / 単位の文字列 → QUDT IRI |
| `sanitize_iri` | starrydata | IRI 不正文字の処理・非絶対 IRI の skip |
| `normalize_host` | MP PoC | 組成 → 母相（ドープ・非化学量論を剥がす） |

各関数は呼び出しを PROV Activity として記録（`StructureMatchActivity` 流）。これが v0。新規ソースで不足が出たら人間が追加。

## 5. 境界・関連

- これは **core（substrate）の仕事**。UI は取り込みエンジンを作らない。
- [`ingestion-execution-safety.md`](ingestion-execution-safety.md) の option 2 の設計本体。
- [`ontology-mapping-boundary-and-provenance.md`](ontology-mapping-boundary-and-provenance.md) の engine-vs-content の engine 側。
- 検証コード: `../../experiments/phase5-morph-kgc-spike/`（宣言マッピング）, `../../experiments/mp-linking-poc/`（科学ロジック + PROV）。

## 6. 残課題

- [ ] 関数ライブラリ v0 を実装（上表）＋ 各関数の PROV 記録。
- [ ] MP の structure facts を宣言的 RML に乗せ替え（API JSON → RDF）。
- [ ] ソース型アダプタ（CSV / JSON-API）の最小抽象を定義。
- [ ] cross-dataset クエリを Ask の intent（母相構造 × 物性）に追加。

## 7. 更新ログ

- 2026-06-01: 初版。2 スレッド収束 + cross-dataset join 実証を受けて、ソース非依存の substrate として確定。CSV に縛らない方針を明記。
