# ADR: Tier 0 "enough" coverage gate

状態: **採択＋多値着地後 GATE PASS（2026-06-11・§5）** — Tier 0 関数ライブラリが「網羅」でなく
「**十分**」かを、多様コーパス上の計測ゲートで判定する。実装＋方法論＋スナップショットは
[`experiments/coverage-corpus/README.md`](../../experiments/coverage-corpus/README.md)
が生きた正。**検証レポート（マイルストーン記録）= [`../reports/tier0-coverage-sufficiency.md`](../reports/tier0-coverage-sufficiency.md)**（`…Raw` 63.6%→36.8%→**11.1% PASS**）。本 ADR は**決定（ゲートの定義と初期値）**のみを固定する。

関連: `phase5-declarative-substrate.md` §4/§5（関数ライブラリ・有界性・Tier0/1）/
`ingestion-execution-safety.md`（閉集合 no-codegen）/ `step0-rml-emission.md` §5.2（T9）/
[`../reports/tier0-coverage-sufficiency.md`](../reports/tier0-coverage-sufficiency.md)（検証レポート）。
出所: `handoff_to_claude_code_tier0_functions.md` Track C。

## 1. 文脈

宣言経路（RML→Morph-KGC）で「列→述語」に書けない難変換だけを、閉じた検証済み関数集合
（`asterism.functions` の Tier 0）が担う。長尾の網羅は**目標でない**（長尾はデータ＝表/
パターン/雛形へ逃がす）。では「頭」はどこまで埋めれば十分か——を意見でなく数値にする。

## 2. 決定

**ゲート**: あるデータセットの提案 RML で、**真の計算が要る列**（vetted 関数 **または**
`…Raw` フォールバックで処理された列）のうち、`…Raw` リテラルに落ちた割合
（`raw_rate = raw_fallbacks / (function_maps + raw_fallbacks)`）を、コーパス全体で
プールした値が **`RAW_RATE_GATE` 未満**であること。

**初期値 `RAW_RATE_GATE = 0.15`**（`coverage.DEFAULT_RAW_RATE_GATE`）。

- これは **A/B の受け入れ指標であって現状値ではない**。現 Tier0（starrydata 形の 8 関数）は
  本コーパスで **約 64%**＝意図的に FAIL。A/B が進むと自動で改善する数値。
- 15% は、真に**還元不能**なフォールバック（Crossref `author` 等の object 配列＝スカラ関数
  でなく RML の入れ子 TriplesMap/多値展開が要る）を許容しつつ、容易な勝ち筋（単一要素配列・
  カンマ区切り・date-parts）は Track B プリミティブで覆うことを要求する水準。
- **A/B 着地ごとに再較正**。コーパスが大きくなり分母が 2 ファイル支配でなくなったら 10% へ。

## 3. 計測の安定インタフェース（A/B 非依存）

`inspect`（列構造）・`materialize`（§9 RML 抽出）・`rml_check.load_registry_fn_iris`
（閉集合）のみに依存。`functions.py` 内部や `propose` システムプロンプトには触れない＝
A（REGISTRY 追記）・B（プリミティブ追記）の編集面と競合しない。需要シグナル
（T9 ミス＋関数別利用回数＋demand-by-category）は Tier1→Tier0 昇格と Track A の優先付けの
根拠にする。

## 4. 既知の限界（重要）

ゲートは **多値/入れ子**列の `…Raw` のみを見る。**スカラ**の未充足変換（epoch→dateTime・
裸 DOI・真偽/列挙・値+単位）は直接リテラルに落ちて `raw_rate` に**現れない**＝Track A の
頭関数が狙う需要。これは coverage の **demand-by-category**（heuristic・ゲート非投入）で別に
可視化する。**ゲート（Track B/展開の信号）と demand 表（Track A の信号）を併読する**こと。
A が関数を足すと該当列が direct→function に移り、計算分母が増えて `raw_rate` も下がる。

## 5. A/B 着地後の再較正（2026-06-10・PR #167/#170 後）

A（コア関数）・B（プリミティブ）merge 後、同一コーパス・同一 inspection で全 12 proposal を
**新プロンプト（24 関数）で再生成**し `report` 再実行（生成は Claude が `propose.SYSTEM_PROMPT`
を演じる Track C の手順どおり・各列は実サンプル形に忠実＝メトリクス稼ぎ無し）。

- **`…Raw` 率 63.6% → 36.8%**（−27pt・相対 −42%）。計算列 11→19（function 4→12・raw 7 据置）。
- **稼働関数**（§4 の「earn their place」）: `date_iso`×3・`lookup`×2・`datetime_iso`×2・
  `url_canonical`×2・`doi_norm`・`year_only`・`bool_norm`。すべて A/B 由来の需要を実コーパスで充足。
- **demand-by-category が決定的**: スカラ需要（`doi`/`epoch_millis`/`messy_date`/`url`/`boolean`）は
  **すべて function 列 >0・raw/direct/unmapped =0** ＝ **スカラ被覆は本コーパスで実質完了**。
  `value_with_unit_name` の 4 direct は単位が**列名**にありセル値はクリーン＝direct が正しい（関数不要）。
- **残 `…Raw`（7）は 100% が `multivalue_or_json`**: crossref の `title`/`author`/`container-title`
  （JSON 配列・object 配列）と earthquakes の `ids`/`sources`/`types`（カンマ列）・`coordinates`
  （座標配列）。これは **多値展開（exploder）＝ RML iterator/入れ子 TriplesMap 意味論**が要る別
  ワークストリームで、Track A/B のスコープ外（§2 が既に「還元不能」として 15% 許容に織り込んだ層）。

**含意・再較正**: 36.8% の FAIL は **スカラ関数の不足でなく、保留中の多値 exploder のみ**を測っている。
よって `RAW_RATE_GATE=0.15` を機械的に下げるのは誤り。次のいずれか:
(a) **多値 exploder を別 KPI に分離**し、ゲートは「スカラ未充足（demand 表の raw/unmapped）= 0」を
判定する形へ精緻化（今回スカラは 0 ＝ PASS 相当）。(b) コーパスを多値の薄い多ドメインで拡張し
分母を平準化してから 10–15% を再評価。**当面の結論: スカラ Tier0 は「十分」に到達。残課題は多値展開**。
