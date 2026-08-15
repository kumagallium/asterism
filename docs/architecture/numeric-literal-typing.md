# 数値リテラルの型付け — 「エラーを出さずに間違える」最も静かな欠陥

Status: accepted (2026-08-14)

Related: [`mapping-ir-compiler.md`](mapping-ir-compiler.md),
[`column-ownership-and-growth.md`](column-ownership-and-growth.md)（G6 と同型の決定論補完）,
[`ask-quality-and-generality.md`](ask-quality-and-generality.md),
[`data-shape-checks.md`](data-shape-checks.md)

## 0. 問題 — 根拠つきで、自信を持って、間違える

実話（2026-08-14、XRD 参考カードの dogfood）。Ask に「intensity が最大になるときの角度」を
聞いたところ **77.47 度**と答えた。正解は **40.07 度**（intensity = 100.0）。77.47 は
intensity が **9.4** の行である。

原因は 1 つ。取り込まれた値に**型が付いていない**:

```json
{"type":"literal","value":"9.4"}     ← datatype なし
```

型のないリテラルを `ORDER BY DESC(?intensity)` で並べると **SPARQL は文字列として比較する**。
辞書順では `"9.4" > "6.6" > … > "100.0"`（`1` で始まるので後ろ）。だから 9.4 が「最大」に
なった。

**この欠陥の性質が最悪である**:

- **例外もエラーも出ない。** 取り込みは成功し、T1-T9 も通り、`/api/sparql` も 200 を返す
- **答えは「それらしい」。** 実在する角度が、実在するピークから、来歴つきで返る
- **Ask の「AI 生成 SPARQL（未検証）」バッジも救わない。** あれはクエリが未検証という意味で、
  答えの正しさは何も主張していない
- max / min / ORDER BY / 範囲フィルタ / 平均 — **数値を扱う問いがすべて静かに壊れる**

「引用できる事実」を主軸に置くプロダクトで、**事実が黙って間違う**のは最も重い部類の欠陥である。

## 1. 決定事項

| # | 論点 | 決定 | 理由 |
|---|------|------|------|
| N1 | 誰が型を決めるか | **実データ**が決める。`numeric_column_types()` が**全行**を走査し、非空セルがすべて数値の列だけを `xsd:integer` / `xsd:double` として返す | 列名は型の証拠にならない（`No` = `03-065-2664` は数値ではない）。inspector の `inferred_type` は**サンプル**（`_SAMPLE_RING`）投票なので、全行に型を刻む根拠には弱い |
| N2 | 生成時に機械が付ける | per-map 結果に対し `apply_numeric_datatypes()` が `datatype` を補完し、付けた列を進捗メッセージで報告 | 検査トラップの教訓の再適用（G6 と同型）: 機械が知っていることを、モデルの記憶に賭けない |
| N3 | 触らない行 | `function:` / `transform:` を持つ行（出力型は関数の責務）、`object_type: iri`（そもそもリテラルでない）、**既に `datatype` がある行**（明示は人の/モデルの判断） | 保守的側に倒す。誤って型を刻むと**不正なリテラル**を生み、静かな誤りを別の静かな誤りに置き換える |
| N4 | 既存データの検出 | `_untyped_numeric_advisories()` が RML + 実ソースを突き合わせ、「数値なのに型なし」を **design advisory**（自己修正ループに乗る側）として出す | N2 が効くのは新規設計だけ。既に取り込まれた設計と、手書き IR / single-shot 経路の網が要る |
| N5 | 沈黙の条件 | ソースが読めない、1 セルでも非数値、型が既にある → **黙る** | 「数値かもしれない」で型を刻むのは N3 と同じ危険。証拠がなければ主張しない（advisory の既存原則） |

## 2. 実装

- **step0** `inspect.py`: `numeric_column_types(rows, columns)` — 全行走査。小数を含めば
  `xsd:double`、全部整数なら `xsd:integer`、空セルは無視。
- **step0** `staged_propose.py`: `apply_numeric_datatypes()` を per-map ゲートの最後（借り列の
  除去の直後）に。付けた列は必ず報告する。
- **api** `design_loop.py`: `_column_datatypes()` が確定済み骨格のソースを読み、
  `propose_from_skeleton(column_types=…)` へ渡す。
- **ingest** `rml_validate.py`: `_untyped_numeric_advisories()` を `design_advisories()` に合流。

## 3. 実データ検証（xrd_参考文献.txt / dataset-691325b8）

- 型判定: `2theta` `d` `I` `Volume` `RIR(I/Ic)` `Dcalc` = `xsd:double`、`Z value` = `xsd:integer`。
  `No`（`03-065-2664`）・`Cell`（空白区切り 6 値）・`(hkl)` は**非数値のまま**＝正しい。
- 既存 RML への advisory: `I` / `d` / `Dcalc` / `RIR(I/Ic)` / `Volume` / `Z value` … を実際に検出。
- 誤答の再現と是正: 同じクエリで `9.4 → 77.47`、数値キャストを挟むと `100.0 → 40.07`。

## 4. 非目標（今回やらない）

- **日付の型付け** — `xsd:date` は書式（ISO かどうか）に敏感で、Tier-0 の `date_iso` を通す
  設計が前提。数値のような無条件の安全性がないので触らない。
- **既存グラフの遡及修正** — advisory は設計を直す入口まで。取り込み済みの三つ組を書き換える
  のは再取り込みの領分。
- **Ask 側の防御**（型なしリテラルを掴んだ比較の警告）— N2/N4 の網から漏れた分の最後の砦
  として将来の検討。
