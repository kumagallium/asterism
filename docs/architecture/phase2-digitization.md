# Phase 2 #6: DigitizationActivity(来歴 / PROV)

このプロジェクトの第一原則は「PROV-O is the lingua franca」。Phase 1 は
**取り込み来歴**(`sd:IngestionActivity` = 我々のパイプラインが CSV を RDF 化した
記録)を持っていたが、**科学的来歴**(その曲線データが**そもそもどこから来たか**)
が欠けていた。

starrydata の curve は、論文中の**図を [WebPlotDigitizer](https://automeris.io/WebPlotDigitizer)
でトレースして数値化**したもの。Phase 2 #6 はこの digitization を独立した PROV
Activity として記録し、「この数値は実測ではなく図からの読み取りである」という
信頼性情報をクエリ可能にする。

## モデル

```
                 prov:wasGeneratedBy
   sd:Curve ──────────────┬───────────────► sd:IngestionActivity   (パイプライン来歴, Phase 1)
                          │                    prov:atTime / used / wasAssociatedWith(csv2rdf-mcp)
                          │
                          └───────────────► sd:DigitizationActivity (科学的来歴, Phase 2 #6)
                                               prov:wasAssociatedWith ─► <WebPlotDigitizer> (prov:SoftwareAgent)
                                               prov:atTime "..."^^xsd:dateTime  (curator timestamp, パース可能時)
```

1 つの `sd:Curve` が **2 つの Activity に `prov:wasGeneratedBy`** される。
PROV-O 的に妥当(entity は複数 activity に生成されうる)。来歴を辿るときは
activity の型でフィルタして使い分ける。

### IRI / 述語

| 要素 | IRI / 値 |
|---|---|
| DigitizationActivity | `sdr:digitization/{SID}-{figure_id}-{sample_id}`(curve と同じ複合キー) |
| 紐付け | `curve prov:wasGeneratedBy sdr:digitization/{key}` |
| 型 | `sd:DigitizationActivity`, `prov:Activity` |
| ツール agent | `<https://automeris.io/WebPlotDigitizer>`(`prov:SoftwareAgent`, schema:name/url) |
| 関連付け | `digitization prov:wasAssociatedWith <WebPlotDigitizer>` |
| 時刻 | `digitization prov:atTime "..."^^xsd:dateTime`(パース成功時のみ) |

agent の記述は全 curve 共有(IRI keyed なので Oxigraph set-semantics で重複しない)。

## タイムスタンプの扱い

starrydata の `created_at` は JS `Date.toString()` 形式:

```
Fri Sep 01 2017 18:19:39 GMT+0900 (Japan Standard Time)
```

Phase 1 はこれをパース失敗の脆さを避けて**文字列のまま** `dcterms:created` に
保持していた。Phase 2 #6 では:

- **`dcterms:created` の生文字列はそのまま維持**(fidelity)
- `prov:atTime` 用に `parse_curator_timestamp()` で **best-effort で ISO 8601 に
  パース**。成功時だけ `xsd:dateTime` で emit、失敗時は単に付けない(graceful)。

パーサは末尾の `(Japan Standard Time)` を除去し `GMT+0900` → `+0900` に直して
`%a %b %d %Y %H:%M:%S %z`(tz 無し fallback 付き)で解釈する。

## クエリ例(MIE sparql_query_examples にも収録)

```sparql
PREFIX prov:   <http://www.w3.org/ns/prov#>
PREFIX sd:     <https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#>
PREFIX schema: <https://schema.org/>
SELECT ?curve ?digitizedAt ?toolName WHERE {
  ?curve a sd:Curve ; prov:wasGeneratedBy ?act .
  ?act a sd:DigitizationActivity ; prov:wasAssociatedWith ?tool .
  ?tool schema:name ?toolName .
  OPTIONAL { ?act prov:atTime ?digitizedAt }
} LIMIT 10
```

> 注意: curve は 2 activity に生成されるので、取り込み来歴が欲しいときは
> `?act a sd:IngestionActivity`、digitization が欲しいときは
> `?act a sd:DigitizationActivity` と**型で絞る**。

## 触ったファイル

| ファイル | 変更 |
|---|---|
| `ingest/src/csv2rdf/starrydata.py` | `parse_curator_timestamp` helper、`WEBPLOTDIGITIZER_IRI` 定数、`_emit_curve` で DigitizationActivity を additive emit |
| `ingest/tests/test_starrydata.py` | timestamp パーサのユニットテスト |
| `ingest/tests/test_samples_curves.py` | digitization emit + atTime テスト |
| `data/togomcp/mie/starrydata.yaml` | CurveShape / DigitizationActivityShape / sample RDF / provenance SPARQL 例 / architectural_notes |
| `docs/ontology/starrydata.ttl` | `sd:DigitizationActivity` クラス定義 |

> NOTE: `docs/starrydata/ontology/ontology.ttl`(GitHub Pages コピー)は未 sync。

## 意図的に外したもの

- **digitization の curator(人)**: starrydata CSV に digitizer 個人情報が無いので
  agent はツール(WebPlotDigitizer)のみ。人を足すなら `prov:wasAssociatedWith` を
  増やせばよい。
- **figure 単位の digitization セッション集約**: created_at は行(curve)単位なので
  per-curve で記録。同一 figure の複数 curve をまとめる最適化は将来。
- **WebPlotDigitizer の version / 設定**: CSV に無いので未記録。
