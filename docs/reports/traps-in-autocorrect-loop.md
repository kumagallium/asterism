# 「確かめる」で人間が押していたクリックは、機械が回せていなかったラウンドだった

- 日付: 2026-08-16
- 対象: `api/src/asterism_api/design_loop.py`（propose 自己修正ループ）/ `ui/src/kantan/KantanWizard.tsx` S5

## Q（問い）

かんたんモードで小さなデータ（70 行の XRD 参考文献ファイル）を取り込むと、「確かめる」で
**AI のやり直しが 5 回程度**発生し、人間の体験は「AI に直してもらう」ボタンを押すだけになる。
このやり直しは自動で回せないのか。エラーは毎回見せる必要があるのか。

## Method（方法）

1. コード読解で検証器の配置を確認（`design_loop.collect_issues` と `/api/materialize` の
   `validate_schema`）。
2. リポジトリに残る**実際の弱モデル出力** 7 本
   (`experiments/mapping-ir-weakmodel-dogfood/results/*.proposal.md`) を materialize し、
   トラップ検証 T1–T10 を実行して不合格 ID を数える。ソース CSV は渡さない＝`/api/materialize`
   と同一のバンドル構成（T1/T6 は両方で skip）。

## Result（結果）

**検証器が二重化していた。**

| | オラクル | ループ |
|---|---|---|
| `design_loop` | Mapping IR parse/validate/compile ＋ RML safety/design ＋ advisories | あり（既定 3 ラウンド・循環検出・best-so-far） |
| `/api/materialize` | **トラップ検証 T1–T10** ＋ warnings ＋ validation_issues | **なし**（人間のクリック待ち） |

ループは自分が一度も回していない検証の手前で「収束した」と宣言し、その後 materialize で
初めて落ちる。UI の「AI に直してもらう」は `startRefineChain` を呼ぶだけ＝**機械のラウンドと等価**。

実測（実際の弱モデル出力 7 本、トラップ不合格 ID）:

| 出力 | 不合格トラップ |
|---|---|
| dogfood-gptoss-run2 | T5 |
| dogfood-gptoss-run3 | T10 |
| dogfood-gptoss | T10 |
| dogfood-qwen36-run2 | T10 |
| dogfood-qwen36 | T2, T4, T7, T10 |
| staged-gptoss | T4, T10 |
| staged-qwen | T4, T10 |

**7/7 が最低 1 つのトラップに不合格。** 頻度は T10（6/7・§7 の SPARQL 例が構文エラー）、
T4（3/7・MIE キーワード不足）が支配的。いずれも **§7 MIE** に在る＝§9 だけを再生成する
surgical repair では原理的に届かない。

## Conclusion（結論）

トラップ検証をループのオラクルに合流させた（本 PR）。

- `_evaluate()` が 1 回の materialize で「§9 抽出 ＋ IR/RML 検証 ＋ トラップ検証」をまとめて回す。
- トラップの `fail` を issue 化。message は wizard の `runAiFix` と同形＝**症状＋決定論修正レシピ**
  （症状だけでは弱モデルは無限ループする＝2026-07-14 の T4 事故）。キーは trap ID
  （レシピ本文は導出語を含み揺れるため、停滞検出をすり抜ける）。
- trap issue があるラウンドは**全文 refine を強制**。surgical は §9 しか触れない。
- `ASTERISM_AUTOCORRECT_ROUNDS` 既定 3 → 5。人間が手で回していた実測回数より機械の上限が
  少ないと、結局尻尾を人間に返すことになる。

**エラーの見せ方は変えていない。** かんたんモードは既に正しい形になっていた＝ループ中は進捗のみ、
収束したら「品質チェックも自動で通しました」の 1 行、収束しなかったときだけ停止カード。
本 PR が変えたのは「収束しなかった」の頻度であって、見せ方ではない。

**決定論的に貼るところまではやらない**という判断も記録しておく。T4 のレシピは完全に機械生成
（貼るだけの `schema_info` YAML）なので自動適用できるが、T10 が 6/7 で同じラウンドを要求する以上、
T4 はそのラウンドに相乗りするだけで**節約はゼロ**。逆に T10 を決定論適用すると、モデルが書いた
意味のある例クエリが汎用プローブ (`SELECT ?s ?type WHERE { ?s a ?type } LIMIT 20`) に置き換わり
品質が落ちる。LLM を先に、決定論をフォールバックに、という現在の順序が正しい。

## Limitations（限界）

- 収束 ≠ ingest-ready。トラップは静的ゲートで、本ゲートは ingest の 422。
- ソース CSV を渡さない構成なので T1（ID 一意性）/T6（偽 IRI）はループでも skip。wizard の
  ゲートと一致させることを優先した（ループが通れば wizard も通る）。
- 実 LLM での end-to-end 再現（XRD ファイルでクリック 0 回を実測）は未実施。本レポートの根拠は
  「実際の弱モデル出力に対する決定論検証」であって、ライブ計測ではない。
- T8（幻覚テスト）は llm 未指定のため skip のまま。

## Reproduce（再現）

```bash
cd api && PYTHONPATH=../api/src:../step0/src:../ingest/src python -m pytest tests/test_design_loop_traps.py -q
```

不合格トラップの実測（上表）:

```python
from pathlib import Path
import tempfile
from asterism_step0.materialize import materialize_schema
from asterism_api.design_loop import trap_issues

for md in sorted(Path("experiments/mapping-ir-weakmodel-dogfood/results").glob("*.proposal.md")):
    with tempfile.TemporaryDirectory() as tmp:
        mat = materialize_schema(md.read_text(), tmp, "design", write=True)
        print(md.name, [i.subject for i in trap_issues(mat)])
```

---

## Addendum 2026-08-17 — ライブ再現で見つかった 2 段目の障害（splice 不能＋既知修正の LLM 依存）

v0.14.5（トラップ合流を含む）でユーザーが同じ XRD ファイルを再取り込みしたところ、
**依然として「AI に直してもらう」が繰り返し必要**だった。usage 台帳の実測:
propose 8・propose.autocorrect 2・refine 3（すべて gpt-oss-120b）。最終保存された設計には
**数値 4 列（Z value / Volume / RIR(I/Ic) / Dcalc）の untyped-numeric advisory が残ったまま**
だった。

### 真因は 2 つ

**① splice 不能 — `unit:` 自動補完が外科修復を全滅させていた。**
materialize は抽出した §9 を `enrich_units` / `apply_source_dialects` で決定論加工して
**再シリアライズ**する（PyYAML はリストのインデントと引用符を正規化する）。
`replace_mapping_spec_block` はこの加工後テキストを文書中から探すため、
`RIR(I/Ic)` のような括弧付き列名から `unit:` が 1 つ補完されただけで
**「could not locate the mapping-spec block」→ 全外科修復ラウンドが空振り**していた。
ループは `spec repair discarded` を記録して `no_progress` 停止（実測どおり
autocorrect 2 回で打ち切り）。dialect 再ピン（`_overlay_detected_dialects`）も同じ関数を
使うため、同様に黙って no-op していた。
→ **修正**: `MaterializeResult.mapping_ir_source`（文書に実在する原文ブロック）を追加し、
splice はそれをキーにする。

**② 既知の修正を LLM に「お願い」していた。**
untyped-numeric advisory が発火する時点で、機械は全行を読み、全セルが数値であることを
証明し、integer/double まで決定済み＝**厳密な編集内容が既知**。それでも修正は refine
コメント頼みで、gpt-oss-120b は 3 回の refine で 1 列も直せなかった。
→ **修正**: `_stamp_numeric_datatypes` — advisory が名指しした §9 の property 行に
`datatype:` を決定論で押印し、**LLM 0 コール**で解消する。修復後に再検証し、issue が
**厳密に減った場合のみ採用**（悪化しない安全性）。structural エラー・legacy raw-RML・
`function`/`datatype` 既設行は対象外＝LLM に残す。

### 実測（ユーザーの実データ dataset-13dea822 の proposal.md をリプレイ）

| | issues | LLM コール |
|---|---|---|
| 修正前（ライブで詰まっていた状態） | 4 | 5 ラウンド費やして解消せず |
| 修正後 | **0** | **0** |

前回 addendum の「T10 が同ラウンドを要求する以上、決定論適用の節約はゼロ」という判断は
**このデータには当てはまらなかった**: 実際に残っていたのはトラップではなく advisory 4 件
のみで、そのすべてが機械が答えを知っている類だった。「LLM 先・決定論フォールバック」の
一般則は維持しつつ、**機械が編集内容を厳密に知っている advisory は決定論が先**が正しい。

### 再現

```bash
cd api && PYTHONPATH=../api/src:../step0/src:../ingest/src \
  python -m pytest tests/test_design_loop_datatype_repair.py tests/test_design_loop_traps.py -q
cd ../step0 && python -m pytest tests/test_mapping_ir_schema.py -q  # splice 回帰
```
