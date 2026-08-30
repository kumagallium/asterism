# 分野特化の境界 — 機構は汎用、内容の特化は台帳で見えるようにする

Status: accepted (2026-08-31)
owner: kumagallium

前提 ADR: [`ask-quality-and-generality.md`](ask-quality-and-generality.md)
（「材料科学への言及を機構に入れない」）

## 1. きっかけ

利用者指摘（2026-08-31）:「全体的に、材料科学に特化したハードコーディングの
説明にならないようにしてほしい」。

全数調査（ja ロケール + 相談プロンプト）の結果、分野語を含む箇所は **43 件**で、
性質が 3 層に分かれた。**層によって直し方が違う** — 文言だけ直しても済まない
ものが混ざっており、それを区別せずに「掃除」すると、直したつもりで実体が残る。

| 層 | 中身 | 直し方 |
|---|---|---|
| A 例示 | 「例: 組成でつなぐ」「例: Crystal」など 17 件 | 分野を跨ぐ例に差し替え（済） |
| B 説明文 | 本文に埋まった分野語 約 20 件 | 形で書き直す（済） |
| C 機能・構造 | 実在の関数・固定 ID・同梱ツール・デモの中身 | **消さない。本 ADR の台帳で管理** |

## 2. 決定

### D1 境界線 —「機構は汎用、内容は特化してよい」

`ask-quality-and-generality.md` の線を一般則として明文化する:

- **機構**（判定・ルーティング・スキーマ・プロンプトの地の文）は分野の語を
  持たない。例示も含む — 地の文の例は全分野の利用者に届く
- **内容**（利用者のデータ・デモ fixture・opt-in の関数やツール・外部語彙の
  事実の説明）は分野に特化してよい。化学式の正規化のように、**その分野で
  しか正しくない有用な機能**を汎用化の名目で失わない

### D2 A+B は分野を跨ぐ例・形の記述に置き換える（実施済み）

- 例は分野を跨ぐもの（試料名・著者・測定装置・DOI・カタログ番号…）か、
  「1 枚の記録と、その記録が説明している対象」のような**形**の記述にする
- 「試料」「測定」「観測点」は実験科学全般の語として使ってよい
- 相談チャットのプロンプトの雛形は `<列名>` 形式の**形だけ**にする（分野の
  実例をここに置くと、全分野の相談に同じ偏りが混ざる）

### D3 C は消さずに、この台帳で見えるようにする

| # | 何 | 場所 | なぜ残す |
|---|---|---|---|
| 1 | crosswalk 正規化レシピ `composition` / `element_canonical` | `ingest/src/asterism/crosswalk.py`・UI `crosswalk.builder.norm.*` | vetted な opt-in 関数。選ばなければ効かない。化学式でしか正しくない処理そのもの |
| 2 | crosswalk 既定視点の固定 ID `composition` | `ui/src/CrosswalkBuilder.tsx` `STANDARD_ID`・api の back-compat 経路 | 視点 ID はハブ語彙の IRI に入る。動かすと公開済みのつながりが壊れる（`id-move-after-publish.md` と同じ性質） |
| 3 | watcher 取り込み口 `POST /upload/{kind}` = `papers\|samples\|curves` | `api/src/asterism_api/main.py`・ラベル `shared.step.*` / `jobs.kind.*` | Phase 2 の固定構造。かんたんモード経路は kind に依存しない。汎用化は watcher の設計変更（別課題） |
| 4 | 同梱ツール `measurement_provenance`（composition 引数） | `tools.bundled.*` | starrydata デモ向けの実ツール。ラベルは実引数の名前 |
| 5 | デモ fixture（ZT / Seebeck / SnSe） | `shared.demo.*` | デモデータが熱電なので、その回答例も熱電。デモの中身は「内容」 |
| 6 | EMMO / CMSO の説明 | `vocab.known.*` | 「材料科学の標準のことば」は**事実**。事実の記述は特化ではない |

新しく C に該当するもの（分野固有の関数・固定 ID・同梱ツール）を足すときは、
opt-in にしたうえで**この表に追記**する。

## 3. 非目標

- C の汎用化リファクタ（watcher kind の一般化・既定視点 ID の抽象化）は
  しない。どちらも別の設計判断で、公開済み ID の互換に触る
- 分野語の機械リンター（ロケール全文の語彙検査）は入れない — C のラベルや
  デモ文言が恒常的な偽陽性になり、allowlist の保守が本体の直しより重くなる

## 4. 検証

- `api/tests/test_design_consult.py::test_consult_prompt_template_is_domain_free`
  — 相談プロンプトの**地の文**（マニュアル注入部を除く）に分野語が無い
- 同 `test_consult_structural_example_is_domain_free` — D3 提案ブロックの
  雛形が `<列名>` 形式の形だけであること
