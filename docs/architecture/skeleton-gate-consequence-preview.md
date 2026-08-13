# 骨格ゲートの帰結プレビュー — エンティティカード・潰れ分類・参照リスク

Status: accepted (2026-08-13)

Related: [`kantan-mode-two-tier-ux.md`](kantan-mode-two-tier-ux.md)（K7/K14）,
[`mapping-ir-phase2b-skeleton-wizard.md`](mapping-ir-phase2b-skeleton-wizard.md),
[`instance-iri-base.md`](instance-iri-base.md)

## 0. 問題 — 証拠は機械、判断は人間丸投げ

実話（2026-08-13、XRD リファレンスカードの dogfood）。1 ファイル = 1 枚の参照カード
（No: 03-065-2664）+ 47 ピークという典型的な「メタデータ部 + データ部」構造で、
**開発者本人が骨格ゲートの判定に迷った**:

- `sample/{No}` の「⚠ 47 行のうち 46 行が潰れます」は**正解の挙動**（メタデータ部が
  1 エンティティに合流する）なのに、事故の見た目で提示された。
- `peak/{2theta}` の正解 `peak/{No}/{(hkl)}` は、候補チップに**存在すらしなかった**
  （{(hkl)} / {(hkl)}+{2theta} / {(hkl)}+{d} が並列提示され、どれも追記に不安全）。

このとき必要だった 3 つの判断 — ①全行→1 件の潰れは正常か事故か、②測定値キーの
将来リスク、③親スコープ列の前置 — は、**すべて既に機械が持っている情報から決定論で
導出可能**だった。ゲートは実データの証拠（PR #280）を見せていたが、その証拠は
「ID の URL 構文」の語彙で書かれていた。人間が持っている知識は世界の側
（「Al3V は 1 材料、ピークは 47 本」）であって ID 構文ではない。検査トラップの
教訓（`inspection-trap-fix-recipes.md`）の人間版: **機械的要件は、レシピまで機械が
用意しないと着地しない。**

## 1. 決定事項

| # | 論点 | 決定 | 理由 |
|---|------|------|------|
| C1 | 潰れの分類 | `collapse_kind` = `unique` / `singleton` / `partial` をサーバが判定。**singleton（全行→1 件）は緑の正常系**「N 行すべてが同じ 1 件に合流します」で提示し、⚠ と続行時 confirm は **partial のみ** | 全行が 1 つに潰れる = ファイルスコープのメタデータエンティティ（参照カード・実験ヘッダ）で、合流こそが目的。正解を事故に見せたのが混乱の直接原因 |
| C2 | エンティティカード | 「実際の ID の例」（URL 構文）を**カード（帰結）**で置換: 代表 1 エンティティの実プロパティ値を表形式で描く。partial は**最大衝突グループ**を選び、食い違う値を実値 + 行番号で表示（上書き合戦の可視化）。singleton では行ごとに変わる列を conflict ではなく「別の種類の領分」として名前列挙 | URL が 3 本同じでは事故にしか見えない。カード 1 枚なら「情報が集まる」が直感で読める。「この設計だと何が何件できるか」の件数見出し付き |
| C3 | 参照リスク | `reference_risks[]`（機械可読 kind、文言はフロント i18n）: `measurement-id` = 値が修正されると別 ID が生まれ引用が宙に浮く / `scope-missing` = 同一ソースの singleton マップのキー列（= ファイルの名前空間）を含まないため、追記時に別親の行が同じ ID に混入 | 「ID の作り方」は答えられなくても「この参照は 3 年後も生きていてほしいか」なら答えられる。K7 の抽象文言を引用の帰結の言葉に置換 |
| C4 | scoped 候補 | scope-missing 検出時、候補チップを**親キー前置形に書き換え**（現キー + 実証済み候補すべて）。ランキングは（全列測定値か, 測定値列数, 列数）昇順 — `{No}/{(hkl)}` が `{No}/{2theta}` に勝つ。`scoped: true` を付けチップを強調 | 一意キーの上位集合は一意（親列が空の行は考慮から落ちるだけ）なので再検査不要。正解がワンクリック第 1 候補になる |
| C5 | singleton の候補チップ | 折り畳み `details`「本当は 1 件ではなく、行ごとに分かれるべきですか？」の中に退避 | singleton が事故（本当は行ごと）の場合の救済は残しつつ、正常系を騒がせない。C1 と対 |
| C6 | 親の一意決定 | scope 注入は同一ソースに singleton マップが**ちょうど 1 つ**のときのみ。0 または 2 以上は沈黙 | 決定論の証拠は推測しない（骨格 evidence の既存原則） |

## 2. 実装

- **step0** `skeleton_annotate.py`: `_collapse_kind()` / `_entity_preview()`（キー groupby、
  同値列 = プロパティ、割れ列 = partial なら conflict・singleton なら varying、キー列優先
  8 列上限・conflict は上限に食われない）/ `_scoped_candidates()` / `_inject_scope_risks()`
  （全マップ注釈後の 2 パス目）。既存フィールドは不変更（後方互換）。
- **UI** `SkeletonGate.tsx`: collapse_kind 分岐（旧サーバは `is_unique` にフォールバック）、
  カード描画、リスク文言、`confirmCollides` を partial 限定に。`workbench.json`（ja/en）
  `skeleton.evidence.*` に追加キー。`App.css` `.skeleton-entity-card*` /
  `.skeleton-candidate-chip--scoped`。
- 互換性: 旧フロント × 新サーバ = 追加フィールド無視で従来表示。新フロント × 旧サーバ =
  フォールバックで従来表示。

## 3. 実データ検証（xrd_参考文献.txt, PDF card 03-065-2664, 47 peaks）

`sample/{No}` + `peak/{2theta}` の骨格（ユーザーが実際に迷った状態）に対して:

- sample → `singleton`。カード = No / CSD / Name / Chemical Formula / Space Group / Cell
  など定数 8 列 + 「ほか 10 列」+ varying 4 列（2theta, d, I, (hkl)）。
- peak → `unique` + リスク 2 件（measurement-id: 2theta / scope-missing: 親 Material, 列 No）
  + scoped 候補の先頭 = **`{No} + {(hkl)}`**。

**ユーザーが迷った 3 判断すべてが、機械の第 1 出力として提示される**ことを確認。
チップ 1 クリックで `peak/{No}/{(hkl)}` に置換されることをブラウザで実証。

## 4. 非目標（今回やらない）

- ID 欄を隠して件数承認だけにする UI（「Material 1 件・Peak 47 件、合っていますか？」）—
  カード + 件数見出しまでで一旦様子見。完全形は将来の検討。
- unique マップのカードから親マップ帰属列（No, Name…）を除く整理 — 列の帰属裁定は
  per-map 段の関数従属 advisory（PR #311）の領分。骨格段階では「行が実際に持つ値」を
  正直に見せる。
- JSON / XML ソースのカード — 骨格 evidence 全体が tabular 限定（既存の `checkable: false`
  規約に従う）。
