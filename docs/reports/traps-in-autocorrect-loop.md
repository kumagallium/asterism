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
