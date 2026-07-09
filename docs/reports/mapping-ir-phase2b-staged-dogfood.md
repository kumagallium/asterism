# Report: Mapping IR Phase 2b staged round-0 — weak-model dogfood

Status: 2026-07-08 (Qwen3.6-35B-A3B / gpt-oss-120b 両モデル完了)
関連: [ADR mapping-ir-phase2b-skeleton-wizard](../architecture/mapping-ir-phase2b-skeleton-wizard.md) §10.4/§10.5,
[Phase 1 equivalence report](mapping-ir-compiler-equivalence.md)

## Q（問い）

Phase 2b は round-0 を「骨格→人間ゲート→per-map→文書」に段階化した。実機の弱いモデルで:

1. **骨格の subject 主キーは、人間ゲートが要るほど外れるのか?**（ADR の中核仮説＝弱モデルは
   「curve を単一の非一意キーで立てる」級の高コスト誤りを骨格段で作り込む）
2. **段階生成（per-map + 文書 + 自己修正）は実機で完走・収束するか?**
3. guided JSON（Phase 2a）は実 Sakura backend で本当に効くか?

## Method（方法）

`experiments/mapping-ir-weakmodel-dogfood/run_staged_dogfood.py`（api server 不要・純 step0+api）:
`propose_skeleton`（job1）→ 各 map の subject key を記録 → `run_design_loop(skeleton=…)`（job2＝
per-map + 文書 + 自己修正）→ compiled RML への既存ゲート。

- モデル: `preview/Qwen3.6-35B-A3B` / `gpt-oss-120b`（Sakura AI Engine・OpenAI 互換）。
- データ: Phase 1 と同じ Starrydata `papers/samples/curves` を各 **40 行**（CSV-safe subset）。
- domain hint は Phase 1 dogfood と同一。`max_rounds=3`。

## Result（結果）

### 実機で 2 件の robustness バグを検出→修正（dogfood が狙い通り機能）

| # | 症状 | 原因 | 修正 |
|---|---|---|---|
| 1 | 骨格生成が即クラッシュ `Grammar error: Unimplemented keys: [propertyNames]` | Sakura vLLM の guided-decoding が JSON Schema の `propertyNames` 未実装。`_prefixes_schema` が使用（**Phase 2a の full schema も同じ＝潜在バグ**） | `propertyNames` を撤去（prefix 名は strict パーサが検証＝ゲート不変）＋LLM フォールバック正規表現に `grammar\|unimplemented` を追加（未対応キーワードは json_object へ降格） |
| 2 | per-map が truncated JSON を返し**ジョブ全体がクラッシュ**（gpt-oss） | `generate_map_properties` の parse 失敗（`ValueError`）が `propose_from_skeleton` を貫通 | per-map を try/except で囲み、失敗した map は**プロパティ無しで継続**（単一ショット round-0 と同じ耐性＝gap は検証で surface） |

いずれも汎用（材料非依存）。単体テストでピン（step0 366 / api 219 緑）。

### Q1: 骨格の主キー品質 — **人間ゲートの必要性が実証された**

**Qwen3.6-35B-A3B**（骨格 22.8s）:

| map | 生成された subject key | 複合? | 評価 |
|---|---|---|---|
| paper | `sdr:paper/{SID}` | 単一 | 妥当（SID は論文 ID） |
| sample | `sdr:sample/{sample_id}` | 単一 | ❌ `sample_id` は論文内でのみ一意 → `{SID}-{sample_id}` が正 |
| curve | `sdr:curve/{composition}` | 単一 | ❌ **composition は曲線に非一意**（多数の曲線が同組成）→ 全曲線が数個の IRI に潰れる |

`skeleton_all_keys_composite = false`。**弱モデルは ADR が予言した高コストの主キー誤りを、
まさに骨格段で作った**。骨格は 22.8 秒で出るので、人間が「curve が composition キー」を見て
複合キーへ直すのは、その後の **249.8 秒の per-map+文書生成が誤った土台に載る前**にできる。
これが早期ゲートの価値の直接的な証拠。

### Q2: 段階生成の完走・収束（Qwen3.6）

per-map+文書+自己修正で **249.8s**。自己修正の推移:

| round | issues | 備考 |
|---|---|---|
| 0（per-map 初回） | 76 | 3 map のプロパティ生成直後 |
| 1 | **1** | §9 外科的修復が 75 件を 1 ラウンドで解消 |
| 2-3 | 1 | 膠着（`split` の `delimiter` 定数引数欠落）→ max_rounds |

最終 issue = `map 'paper' property schema:keywords: split requires the constant arg 'delimiter'`。
弱モデルが `function: split` に `args:{delimiter}` を付け忘れ、3 ラウンドで直せず未収束
（compiled=false＝**422 ゲートが正しく捕捉する**）。§9 外科的修復の効き（76→1）は明確。

### Q2/Q3: gpt-oss-120b（robustness 修正の前後）

- **修正前**（バグ #2 発見時・骨格 3 map）: 骨格は出た（全 map で `classes:[]` 空・主キーは
  paper=SID/sample=sample_id/curve=**sample_id**＝やはり非複合）が、per-map が truncated JSON
  を返し **ジョブ全体がクラッシュ**（→ バグ #2 の修正）。
- **修正後**（骨格 6.0s / continue 45.0s）: **クラッシュせず完走**（耐性修正が機能）。ただし
  設計品質は粗い＝この run の骨格は **paper 1 map のみ**（sample/curve を脱落・classes も空）、
  per-map は paper に 14 プロパティを **object form 無し**（column/columns/object_template/
  constant のどれも付けず `transform` だけ）で生成 → 28 issues が round 1 で不変→
  **no_progress で停止**（compiled=false）。gpt-oss は本タスクで Qwen より不安定
  （骨格の非決定性が大きい）。**骨格段でエンティティ脱落を人間が即座に気づける**点でも
  ゲートの価値を裏づける（「sample/curve はどこ?」を per-map 前に直せる）。

## Conclusion（結論）

1. **早期の人間ゲートは正当化された**（Q1）。両弱モデルとも subject 主キーを非複合で立て、
   ADR が予言した「curve を非一意キーで潰す」誤りを骨格段で作った。骨格は ~20 秒・数百トークン
   で出るので、人間が最重要判断（主キー）を最安タイミングで直せる。
2. **段階生成は実機で完走する**（Q2・耐性修正後は両モデルともクラッシュせず）。ただし**収束は
   モデル依存**: Qwen は per-map 初回 76 issues を §9 外科的修復が 1 ラウンドで 1 まで解消
   （膠着＝split 定数引数）、gpt-oss は object form 無しの粗い per-map で no_progress 停止。
   いずれも `compiled=false` を **422 ゲートが最終防衛線**として捕捉。per-map 初回品質と収束は
   Phase 2b の主眼ではない（骨格ゲートが主眼）。
3. **guided JSON は実 Sakura で機能する**（Q3・propertyNames 撤去後）。未対応キーワードは
   json_object へ降格する防御も入った。
4. dogfood は**実機でしか出ない 2 件の robustness バグ**（guided 非互換・per-map truncation
   クラッシュ）を捕捉した＝この検証自体が高い価値を持った。

## Limitations（限界・正直に）

- **per-map 初回品質は「単一ショットより良い」わけではない**（Qwen 76 issues）。Phase 2b の
  価値は per-map 品質でなく**骨格の早期ゲート**にある。per-map の初回粗さは外科的修復頼み。
- **収束は保証されない・モデル依存**（Qwen=split-arg で未収束／gpt-oss=object form 無しで
  no_progress 停止）。弱モデルの定数引数欠落・object form 欠落は膠着し、targeted 誘導の追加
  余地（follow-up）。gpt-oss はこの run で**骨格から sample/curve を脱落**＝骨格の完全性も
  モデル依存（人間ゲートで気づける・骨格の非決定性は複数サンプルで要評価）。
- **骨格 `note` が空**（両モデルとも主キー根拠メモを埋めなかった）＝人間ゲートの補助情報が
  出ていない。プロンプト強化 or UI で inspection の一意性統計を直接見せる方が確実。
- サンプルは **40 行 × 1 データセット（starrydata）× 2 モデル**。汎化には多コーパスが必要。
- per-map truncation は「プロパティ無しで継続」に degrade するが、その map は空になる。
  **per-map の 1 回リトライ**は未実装（follow-up）。

## Reproduce（再現）

```bash
export SAKURA_API_KEY=$(tr -d '[:space:]' < ~/.config/opencode/apikey-sakura-aiengine)
export ASTERISM_LLM_TIMEOUT_SECONDS=1800
api/.venv/bin/python experiments/mapping-ir-weakmodel-dogfood/run_staged_dogfood.py \
    --model preview/Qwen3.6-35B-A3B --api-base https://api.ai.sakura.ad.jp/v1 \
    --papers ../starrydata_dataset/starrydata_papers.csv \
    --samples ../starrydata_dataset/starrydata_samples.csv \
    --curves ../starrydata_dataset/starrydata_curves.csv \
    --rows 40 --out experiments/mapping-ir-weakmodel-dogfood/results/staged-qwen.json
```

生成物（証跡）: `experiments/mapping-ir-weakmodel-dogfood/results/staged-{qwen,gptoss}.{json,log}`。
