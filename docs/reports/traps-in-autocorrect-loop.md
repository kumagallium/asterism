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

---

## Addendum 2026-08-18 — v0.17.0 でまだ 5 クリック：3 段目（round 0 の崩壊＋4 つの構造欠陥）

### Q

#364/#367 を含む v0.17.0 で同じ XRD ファイルを取り込んでも「AI に直してもらう」が 5 回以上走った。仕方ないのか。

### Method

usage JSONL（`registry/_usage/events-2026-08.jsonl`）で feature 別コール数を数え、
`registry/dataset-7146fe8d/history/*/mapping.yaml`（手動 refine 前スナップショット 5 個）と最終
`proposal.md` を、`/api/materialize` 相当（source 無し）と `design_loop._verdict`（staged source 有り）の
両方でリプレイした。

### Result

```
propose ×7 → propose.autocorrect ×2（自動ループここで停止）→ refine ×5（手動クリック）
```

- **round 0 = モデル（gpt-oss-120b）の degeneration**。§9 の全 17 プロパティに `column:` が無く、`unit:` に
  日本語の言い訳文、最後は zero-width 文字の反復ループ。当たり外れ（8/16 は正しく書けていた）。
- **自動ループは 2 ラウンドで停止**（surgical guided JSON の出力が破棄 ×2 → no_progress）。一方、手動の
  全文 refine は 38 → 2 issue まで進んだ＝機械が使えたラウンド。
- **終盤 3 クリックは transform 行が 1 バイトも変わっていない**（履歴 diff）。残 2 issue は
  `transform['False'] must be a non-empty string (got False)`＝モデルが書いた `transform: {No: No}` を
  PyYAML 1.1 が boolean と読んだ。モデルは「False」を一度も書いていないので解読不能。
- 手動経路（`/api/materialize` → `/api/refine`）は attach 前にソースを見られず（staging は別ディレクトリ）、
  did-you-mean も oracle も一度も届いていない → でっち上げ列名 17 個が「問題なし」で通る。
- per-map の guided スキーマは `required: ['predicate']` のみ・`unit/label` に上限なし＝round 0 の壊れ方を
  文法が許していた。

### 直したもの（PR #378）

| # | 欠陥 | 修正 | 検証 |
|---|---|---|---|
| ④ | 列名 `No` が YAML 1.1 で `False` | `spec_yaml.load_spec_yaml`（YAML 1.2 boolean のみ）を IR を読む 9 箇所すべてに。非文字列スカラーには「引用符で書け」の hint | 最終 proposal のリプレイ: 解読不能 2 件 → 実列一覧付き did-you-mean 17 件 |
| ⑤ | surgical 空振りで即停止 | no-progress をモード別に持ち、surgical が動かせない keyset は全文 refine を 1 回試してから諦める | mock で surgical 破棄→全文で収束 (3 コール)、両方失敗→no_progress (3 コール・max 未消化) |
| ⑥ | 文法が `column` 欠落と string 内暴走を許容 | property row を「object form のいずれか必須」の `anyOf`（完全な row スキーマの union）に、全 free-text に `maxLength` | jsonschema で column 無し行が拒否・unit 400 字が拒否・収束済み実 dogfood spec 全件は通過。**vLLM 側の受理はライブ未確認** — 未実装キーは client が名指し分だけ剥がして json_schema で再試行（新規） |
| ⑦ | attach 前の手動経路が盲目 | `/api/materialize` と `/api/refine` に `staging_id`（＋refine は `dataset_id`）。materialize は staged source で did-you-mean・方言再ピン、refine は自動ループと同じ closed-menu oracle を付与 | api テスト: 同じ typo 設計が staging_id 無しでは素通り、有りでは実列名付きで検出。refine の user message に oracle |
| E | `transform: {No: No}` を 5 回書き続ける | 値が Tier-0 関数でなく key と同一の transform を決定論で除去（既存の「厳密に減った時だけ採用」ガード） | 実データ: transform 3 件が LLM 0 コールで消える |

### 何が残るか（正直に）

round 0 の崩壊そのものはモデル品質。⑥で「column 無し」は生成不能になるが、gpt-oss が別の形で崩れる可能性は残る。
その場合でも ⑤ が全文 refine に格上げし、④⑦E により全ラウンドが**解読可能・実列付き**になるので、
5 回押しても 1 バイトも変わらない状態は原理的に起きない。

### 再現

```bash
# 最終 proposal（5 クリック後）を新ローダで
PYTHONPATH=api/src:step0/src:ingest/src python - <<'PY'
from pathlib import Path
from asterism_api.design_loop import _verdict, _evaluate
reg = Path.home()/"Library/Application Support/Asterism/sources/registry"
md = (reg/"dataset-7146fe8d/proposal.md").read_text()
src = reg/"_staging/4cae09fc-9ec2-4e6e-8e7d-74c08338a757"
print(len(_verdict(md, src)[1]))          # 17（旧: 解読不能 2）
print(len(_evaluate(md, src)[2]))         # 14（transform 3 件は決定論で消える）
PY
```

---

## Addendum 2026-08-18 (夕) — v0.17.1 でまだ 4 クリック：ループが「壊れた設計」を合格にしていた

### Q

v0.17.1（#379+#381 入り）で再取り込みしても手動修正が 4 回発生した。自動ループは 2→4 ラウンドに増えたのに、なぜまだ人間が押すのか。

### Method

usage JSONL で内訳、`registry/dataset-34ce6866/history/*`（手動 4 ラウンド分のスナップショット）を
`design_loop._verdict` にリプレイ。ループ出口＝最初のクリック時点の状態を特定。

### Result

```
propose ×5 → propose.autocorrect ×4（#379 ⑤ の格上げが効いて 2→4）→ refine ×4（手動）
```

**ループ出口の設計は `_verdict` で 0 issues＝「収束」と判定されていた。** その中身：

| | ループ出口 | 手動 4 回後 |
|---|---|---|
| `column:` | **0** | 22 |
| `object_template:` | **25** | 2 |
| `unit: xsd:*`（datatype の誤記） | 1 | 0 |

全 25 プロパティが `object_template: .../resource/{列名}` ＝**測定値がすべて不透明な IRI に変換され、
リテラルが 1 つも出ない設計**。体積も強度も SPARQL から取り出せない。それを全ゲートが通していた
（列は実在し、関数は vetted、T1-T10 緑、接続性も空の入れ物も沈黙）。ユーザーの 4 クリックは、
機械が「合格」と言った設計を人間が作り直す作業だった。

沈黙の理由は `_tm_own_value_columns` が **object_template を「その行が持つ値」として数える**こと。
全列 IRI 化した設計は「値を持っている」ように見え、空の入れ物検査(G14)をすり抜ける。

⚠ 正直な自己評価: これは **#379 ⑥（文法で object form を必須化）が失敗モードを移した**可能性が高い。
「object form 無し」を生成不能にしたら、今度は「間違った object form」に逃げた。壊れ方が検査の外へ移動した。

### 方針転換

静的検査を 9 個目に増やすのをやめ、**結果（outcome）を見る**検査にした。壊れ方の形ではなく
「値が取り出せるか」を問うので、次に別の形で壊れても同じ検査が捕まえる。

| # | 修正 | 検証 |
|---|---|---|
| ⑧ | `_no_literal_advisories`: 列を読んでいるのにリテラルを 1 つも出さないマップ／設計を報告。R2RML の既定に忠実（template は既定 IRI・reference と関数パイプラインと datatype/language はリテラル）。**データが無いときは沈黙**（「値が届かない」はデータについての主張なので） | 実データのループ出口が **0 → 1 issue** で捕まる。正しい設計・関数経由の値・純リンクマップ・最小フィクスチャは誤検知ゼロ |
| ⑨ | `unit: xsd:*` → `datatype:` へ決定論移送。`unit` は表示文字列で、`xsd:` が入るのは必ず誤記 | 単体テスト＋実データ |
| ⑩ | `repair_design()`: 手動経路（refine → materialize）にも決定論修復と data-fact 再主張を通す。**それまで `_overlay_data_facts` と `_REPAIRS` は `run_design_loop` の中にしか無く、クリックのたびに機械が確定させた型が消えていた** | 実データで datatype **1 → 7 本**が LLM 0 コールで復活 |
| ⑪ | `_column_datatypes` が keyvalue プリアンブルの broadcast 列（Volume/RIR(I/Ic)/Dcalc/Z value）を見落としていた。`ins.columns` ではなく実際の行のキーを使う＋`source_kind != csv` で切らない。**方言は spec に固定済みのものを使う**（再検出すると `preamble: drop` になり列ごと消える） | 上記 7 本のうち 4 本はこれが無いと拾えない |

### Limitations

- ⑧ は advisory（人間が判断）であって hard gate ではない。純クロスウォークのような正当なリンク専用設計があるため
- round 0 でモデルが壊れること自体は防げない。防ぐのは「壊れたまま合格と言われること」
- 「今度こそ大丈夫」とは言えない。⑧ は形に依存しない分これまでより広く効くが、保証ではない

### Reproduce

```bash
PYTHONPATH=api/src:step0/src:ingest/src python - <<'PY'
from pathlib import Path
from asterism_api.design_loop import _verdict, repair_design
reg = Path.home()/"Library/Application Support/Asterism/sources/registry"
src = reg/"_staging/c44c8acc-22df-465d-a301-486c773ed1a7"
md = (reg/"dataset-34ce6866/history/20260818T051028Z/proposal.md").read_text()
print(len(_verdict(md, src)[1]))   # 旧 0 → 新 1（リテラル皆無を検出）
PY
```
