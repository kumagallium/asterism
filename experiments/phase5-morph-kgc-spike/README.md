# Phase 5 spike — 宣言的取り込み（RML / Morph-KGC）

目的: [`../../docs/architecture/ingestion-execution-safety.md`](../../docs/architecture/ingestion-execution-safety.md) の **option 2**（CSV→RDF を**生成 Python なし**で行う）が、既存依存の Morph-KGC で成立するかを実証する。

## 結果（2026-06-01）: 基本の宣言的経路は実データで動く

- `papers.rml.ttl` が starrydata papers を純 RML でマッピング（`SID`→IRI、`title`→`schema:name`、`DOI`→`dcterms:identifier`）。**コードゼロ**。
- Morph-KGC が **40 論文 → 120 トリプル**（論文ごとに `rdf:type sd:Paper` / `schema:name` / `dcterms:identifier`）を生成。手続き的 ingester とこれら列について同形。
- → **「codegen なし」の取り込みは現実に動く。** RCE を経路から消せる。

## RML テンプレートは思ったより多くを吸収する

関数（FnO）が**不要**な部分:

- 複合 IRI（`sample/{SID}-{sample_id}`、`curve/{SID}-{figure_id}-{sample_id}`）= `rr:template` だけで表現可。関数不要。
- 1:1 の 列→リテラル / IRI = ネイティブ。

## 関数（閉じた検証済みライブラリ）が要る部分

一度だけ人間が書いて vet し、全 dataset で再利用する:

- `issued` → `xsd:date`（独自日付パース）
- QUDT 文字列→IRI ルックアップ（表）
- IRI sanitize（`safe_url` 相当）

**最も難しいのはセル内 JSON**（`author` 配列・`x`/`y` 配列が CSV の 1 セルに入る）。これは FnO + リスト/ネスト処理が要る。ただしこれは **starrydata の CSV 由来のアーティファクト**で、**本来ネストした JSON を返すソース（例: Materials Project の API）なら RML の JSONPath が native に展開でき、この難所が消える**。

## 含意

- **option 2 は feasible**（基本実証済み）。閉じた関数ライブラリは小さい（概ね 4–6 関数）。
- **Materials Project（2 個目 dataset・クリーンな JSON）は理想の相棒**: マッピングが楽で、どの関数が実際に再発するかが見える。閉じた関数集合は「starrydata ∩ MP」の再発から決める。

## 次の一手

- [ ] FnO 関数を 1 つ（日付パース）足して、関数経路を end-to-end で実証。
- [ ] Materials Project の 1 レコード（JSON）を宣言的にマッピングし、関数集合を starrydata と突き合わせる。
- [ ] 閉じた関数ライブラリ v0 を交差から定義。

## 結果(2): FnO 関数経路も実証（2026-06-01）

`papers_fn.rml.ttl` + `udfs.py`（関数ライブラリ v0）で、宣言マッピングでは書けない日付パースを **FnO 経由の検証済み関数**で処理できることを実証:

- `issued`（CrossRef JSON 例 `{"date_parts":[[2013,12,5]]}`）→ `2013-12-05`^^xsd:date。
- 変換は既存 vetted 関数 `parse_issued` を `udfs.py` が薄く露出し、Morph-KGC が `udfs:` 設定 + `@udf` デコレータ注入で呼ぶ。**生成 Python はゼロ**。
- これで option 2 の全経路（宣言マッピング + 閉じた関数ライブラリ + PROV）が、「易しい 8 割（宣言）」「難しい 2 割（vetted 関数）」とも実データで通った。

morph-kgc の RML-FNML 構文メモ: 関数述語は**新 RML 名前空間** `http://w3id.org/rml/`（`rml:functionExecution` / `rml:function` / `rml:input` / `rml:parameter` / `rml:inputValueMap`）。旧 `fnml:functionValue` は本バージョン未対応。基本部分（`rml:source` / `reference`）は classic 名前空間で動く。

## ファイル

- `papers.rml.ttl` — 基本マッピング（コミット対象）
- `papers_fn.rml.ttl` — FnO 付きマッピング（コミット対象）
- `udfs.py` — 関数ライブラリ v0（`parse_date` / `sanitize_iri` / `qudt_*` を vetted 実装から露出）
- `papers.csv` — 実サブセット（demo seed 由来・gitignore 対象）
