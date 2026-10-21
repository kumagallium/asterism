# 問いの提案の金標準（CQ gold）

「問いを提案してもらう」（`POST /api/consult/suggest-questions`・ADR `../sky-chart-two-entries.md` §2.4f）が
出す問いが、研究者が実際に聞きたい問いにどれだけ届くかを測る物差し。形は Bench4KE
（Bench4KE: Benchmarking Automated Competency Question Generation, arXiv 2505.24554）の
`ground-truth` に準じる: 事例（case）ごとに `{source, gold_cqs[], expected_answers[]}`。
Bench4KE は CQ と gold の類似（完全一致・正規化一致・意味の近さ）を指標にするが、ここでは
意味の近さ（埋め込み）は使わず、決定論の 2 つ（`normalize_label` の完全一致・語の重なり）と
「閉じた形に落ちた率」を測る。Bench4KE への投稿は考えていない。

| ファイル | 中身 | 事例 | gold の問い | うち閉じた形に落ちる想定 |
|---|---|---|---|---|
| `starrydata.yaml` | ADR §2.4e の「Starrydata の問い 10 本」。入力は tidy の列の表示名 | 3 | 10 | 8 |
| `samples.yaml` | 見本 4 データセット（価格表・店・レシピカード・世界の国）× 2 本。`query_tools.yaml` と設計の項目名から | 4 | 8 | 8 |
| `papers.yaml` | 熱電の論文 2 本分の**自分で書いた要約**と問い 3 本ずつ（原文は入れない） | 2 | 6 | 6 |

## 事例の形

```yaml
cases:
  - id: <事例の ID>
    source:                    # AI に渡すものそのまま（名前だけ・値は入れない）
      screen: dataset | paper  # term は金標準に無い（図の穴の問いは「正解」が決まらない）
      dataset_label: ...
      kinds: [行が何かの名前, ...]                       # dataset のみ
      columns: [{name, kind_hint, label?}, ...]          # dataset のみ。name=見出し・label=設計で付けた表示名
      text: 要旨と結論の要約                              # paper のみ
      terms: [{label, kind: class|property}, ...]        # 図に既にある用語（任意）
    gold_cqs: [問いの文, ...]
    expected_answers:          # gold_cqs と同じ添字で対になる
      - {op, mode, kind, property, property2, kind2, value, match, target, direction, closed}
```

- `closed: false`（`op: null`）は、データの形 7 つにも知識の形 3 つにも落ちない問い（op の合成）。
  評価の再現率の分母に**入れない**（機械が出さないのが正しい）。ADR §2.4e の #9・#10。
- 「答えが返る」ことは金標準では確かめない（`op に落ちる` までの物差し）。

## 評価

`scripts/eval_cq_suggest.py`（任意実行・CI では回さない）。使い方は
`uv run --project api python scripts/eval_cq_suggest.py --help`。結果は `results-<日付>.md`。
初回の数値は**未計測**（実 LLM を回した日に書き換える）。

- 一致の判定は 3 段。**完全一致** = `normalize_label`（NFKC・小文字化・空白畳み）が同じ。**重なり** =
  語の重なり Jaccard ≥ 0.5（ASCII の語は 1 語、かな・漢字は 2 文字の並びを 1 語として数える・記号は捨てる）。
  **答え方も一致** = 重なりで当たった問いの `op` と `kind`／`property`／`target` が `expected_answers` と同じ。
- **閉じた形に落ちた率** = 画面に出た問いの数 ÷ AI が返した問いの数（後処理が閉じた形に落ちない・
  名前が実在しない・既存と重なる問いを落とした残り）。
- 再現率は 2 つ: 画面に出た問い（利用者が見るもの）に対する gold の当たり率と、後処理の前（AI が返した
  もの）に対する当たり率。差が後処理の落とした分。

## 足し方

事例を足すときは、列の見出しと型のヒント・要旨の要約だけを入れる（データの値・論文の原文は入れない）。
gold の問いは、その入力から研究者が聞きたくなるものを人が書く（AI の出力を gold にしない）。
