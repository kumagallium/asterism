# 設計の続きも判断から導く — かんたん経路の §9 決定論化（D5 の完成形）

Status: accepted (2026-09-02)
owner: kumagallium

前提 ADR: [`skeleton-from-easy-judgments.md`](skeleton-from-easy-judgments.md)（D5:
round-0 の骨格 LLM 廃止）、[`meaning-before-identity.md`](meaning-before-identity.md)、
[`column-ownership-and-growth.md`](column-ownership-and-growth.md)（G6）、
[`ingestion-execution-safety.md`](ingestion-execution-safety.md)

## 1. きっかけ — 元素表 JSON の一日（2026-09-01〜02）

かんたんモードで JSON を初めて決定論組み立てに通したところ、設計の続き（⑤→⑥の
§9 生成）で**修理ループが 4 連続で空転**した。原因は毎回、LLM の「発明」だった:

| ラウンドの敵 | LLM が発明したもの | 塞いだ決定論 |
|---|---|---|
| v0.31.7 | ソースのファイル名 | 参照名規約の全経路統一 |
| v0.31.8 | `dialects:` のフラット構造 | 権威 overlay の双方向化（掃除） |
| v0.31.9 | camelCase の幻の列名 | 表化ヘッダの設計時導出＋閉メニュー |
| v0.31.10 | 値の IRI 化（空の入れ物） | 受け口ラベルの保証パス |

利用者の問いが本質を突いた:「**項目の意味も外とのつながりも人間が決めていて、
グラフもできているのに、何がそこまで難しいのか**」。答え: 難しさは残っていない。
④⑤を経た時点で、設計に必要な判断は**全部確定している** — 種類と ID（組み立て）、
列の帰属（owns）、意味と単位（人間・③）、型（検査）、参照（④）。それでも §9 の
性質規則だけ旧来の LLM 生成が残っており、**自由度がそのまま故障面**になっていた。

## 2. 決定

### D1 かんたん経路の §9 は決定論で組む

`propose_from_skeleton(deterministic=True)`（step0）: per-map の性質表を
`default_property_table`（生成失敗時のフォールバックとして実戦済みの機械組み）で
**常に**組む。材料は確定済みの判断だけ:

- 列 → 述語: `_identifier`（lowerCamel・重複は連番）を ontology prefix に接ぐ
- 型: 検査の `column_types`（数値に `datatype:` — SPARQL の辞書順比較事故の予防）
- 意味・単位: ③の確定が `apply_column_meanings` で**上書き**（従来どおり）
- 帰属: `column_owners`（owns + キーの持ち主）で他人の持ち物を書かない
- 除外: ③の「取り込まない」列は表に載せない
- 受け口ラベル・同一ソースのリンク・dialects: 既存の決定論パスがそのまま効く

文書（§1-8）も `_synthesize_document`（doc_synth — LLM 落ち時のフォールバックと
同一経路）で機械合成する。**この段の LLM 呼び出しはゼロ**。

### D2 LLM に残る仕事

③の意味の下書き・相談（consult）・**refine**（検証がエラーを出したときの修理
ラウンド — 決定論組みでは構造上ほぼ発火しない）。MIE の説明文・キーワードは
doc_synth の機械文が既定で、磨きたい人は refine/相談で頼める。

### D3 適用範囲

API は `POST /api/propose/continue` の `deterministic_rules`（form・既定 off）。
かんたんモードのウィザードだけが on を送る。**詳細モード（workbench）は従来どおり
LLM 生成** — 骨格を手で書く人は述語や関数の選択に LLM の提案価値が残るため。

## 3. 非目標

- 詳細モードの決定論化（現行維持）
- 入れ子配列の質的改善（`json-array` は JSON 文字列セルのまま —
  `native-json-denormalization.md` の実装で扱う）
- MIE の言葉の質（機械文が既定。言葉の仕事は LLM と人に残る）

## 4. 検証

- step0 テスト: LLM センチネル（呼ばれたら fail）で §9 全要素
  （述語・型・意味の上書き・除外・帰属・受け口ラベル・文書合成）を確認
- 実データ受け入れ: 元素表 JSON（本 ADR のきっかけ）が④→⑤→⑥を
  修理ラウンド 0 で通ること
