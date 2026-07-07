# ADR: Ask 品質と汎用性 — 特化ゼロの品質ゲート群

- **状態**: 実装済（PR [#256](https://github.com/kumagallium/asterism/pull/256) /
  [#257](https://github.com/kumagallium/asterism/pull/257) /
  [#258](https://github.com/kumagallium/asterism/pull/258) /
  [#259](https://github.com/kumagallium/asterism/pull/259)、2026-07-07）
- **決定者**: kumagallium + Claude Code
- **背景となる実測**: 本番 (`asterism.e4m.jp`) で AI 設計データセット
  `dataset-318ace06`（MeasurementCurve 233,078 / Sample 104,846 / promoted）に
  対し「高い ZT の材料リスト」が全滅した確定診断（2026-07-07 実査）。

## 問題 — 4 層の複合故障

1. **エンジン内の語彙特化**: Ask の LLM ルーティングは starrydata だけ
   engine コードのハードコードツール（`sd:` 直書き）を特別扱いし、新しい
   （AI 設計の）データセットには構造的に絶対マッチしないツールを提示していた。
2. **query_tools の検証の穴**: AI 下書きツールが `prov:` を PREFIX 宣言なしで
   使ったまま保存でき、実行時に Oxigraph の不透明な 400 で全 Ask が死んだ。
   RML 側には T9 / validate_rml_design / design_loop の多層検証があるのに、
   query_tools には保存前検証が存在しなかった（非対称）。
3. **1-shot の AI 下書き**: 語彙の閉集合を知らされない LLM は、もっともらしい
   述語（RML が map していない `sd:composition` 等）を推測し、恒久 0 行ツール
   を作る。人間が手で差し戻すしかなかった。
4. **設計の質＝質問可能性の欠落**: 測定エンティティが材料エンティティへ一切
   リンクせず（テーブル毎の孤島）、数値系列は JSON 文字列のみ（yMax 相当の
   集計述語なし）。**どんなに良いツールを書いても答えられないスキーマ**が
   検証もなくすり抜けていた。

## 決定

いずれも**スキーマ非依存・ドメイン非依存**（材料科学への言及を機構に入れない。
`pref_product_ideal_over_cost` / 「何かに特化した状態を避ける」方針）。

### D1. query_tools に RML と対称の品質ゲート（#256）

- `asterism.query_tools.lint_query_tool`: テンプレートをダミー引数で
  レンダリングし、**store と同一パーサ（pyoxigraph・空 in-memory Store）**で
  パース検証＋決定論チェック（未宣言 PREFIX / FILTER 専用変数）。
  rdflib のパーサは未宣言 prefix を通すため不採用（実測）。
- 保存 / propose は lint **errors で 400**（理由明示）、warnings は
  非ブロッキングでレビューアに開示。
- **ロードは寛容化**: 壊れた宣言は個別 skip＋警告ログ。1 ツールの壊れ content
  がデータセット全体の typed surface（Ask/MCP）を殺さない。厳格さは保存時
  ゲートへ移設。
- 実行時: ゲート以前に保存された壊れテンプレートの store 失敗は lint detail
  付きの明確なメッセージに翻訳（Ask エージェントが正直に説明できる）。
  lint clean での失敗は 5xx のまま（実 store 障害を隠さない）。

### D2. Ask ルーティングの完全 content 化（#257）

- demo-agent の `_PROPERTY_RANKING_TOOL` / `_SAMPLE_SEARCH_TOOL`
  （最後のハードコード語彙）を撤去。**全データセットが宣言ツール
  `<dataset>__<tool>` で平等**にルーティングされる（starrydata の等価
  テンプレートは `datasets/starrydata/query_tools.yaml` に既存 = #112 の
  等価証明済み content）。
- no-key showcase（`_typed_answer`）も宣言ツールをエンジン実行する形に置換。
  外れ値除外件数の正直な開示は「cap なしでもう 1 回実行して数える」ことで保持
  （エンジン拡張なし・決定論）。
- `_ASK_SYSTEM` のツール選択ガイダンスをデータセット非依存化（`[verified ·
  dataset:X]` の説明文を読んで選ぶ。plausibility cap の一般習慣も記述）。

### D3. tool_propose の閉集合オラクル＋自己修正ループ（#258）

- `asterism.rml_validate.extract_rml_vocabulary`: RML が実際に materialize
  する閉集合（宣言 @prefix + `rr:class` / `rr:predicate` IRI）を決定論抽出。
- `lint_query_tool(vocabulary=...)`: 閉集合外 term と同ラベル PREFIX の
  IRI ずれを warning 化（0 行ツールの2大失敗族）。
- `asterism_api.tool_loop`（design_loop #239 の相似形）: propose → 決定論 vet
  → 欠陥＋**閉集合オラクル**を添えて refine、最大 3 ラウンド・best-draft-wins・
  env-bail。オラクルは初回 propose にも注入（design_loop の実証知見の移植）。
  `body.autocorrect=false` が単発 kill-switch。人間ゲート（レビュー→保存）は
  不変。

### D4. 設計の質＝「取り込めるか」でなく「問えるか」（#259）

- `asterism.rml_validate.design_advisories`: TriplesMap の**連結性解析**
  （`rr:parentTriplesMap` join / subject IRI テンプレート再利用が辺）。
  非連結成分が複数あれば 1 件の actionable advisory（rr:class ラベルで成分列挙
  ＋リンク方法）。**非ブロッキング**が意図: 非連結でも取り込みは合法、ただし
  横断質問に答えられない事実を materialize 時と design_loop の修正ラウンドで
  伝える（=弱モデルが join を張るよう自己修正できる）。
- propose SYSTEM_PROMPT に汎用の質問可能性原則: **ENTITY LINKING**（複数
  エンティティは 1 連結成分・例外は §5 で明示）と **JSON 保持系列の集計述語
  必須**（Tier-0 `float_array_max`/`float_array_min`。SPARQL は文字列内を
  ランキングできない）。

## 却下した代替案

- **rdflib での構文検証**: 未宣言 prefix を通す（実測）＝今回の事故を
  捕まえられない。pyoxigraph は本番 Oxigraph と同一パーサで方言差ゼロ、
  morph-kgc の pin（<0.4）と共存する `>=0.3` を採用。
- **ハードコードツールの温存＋content 併走**: 「新データセットだけ二級市民」
  という非対称の温存であり、特化を避ける方針に反する。excluded_implausible
  件数の engine 内実装は宣言ツール＋追加実行で代替。
- **連結性チェックの ingest ブロッキング化**: 意図的に無関係なソース群も
  ありうるため advisory に留め、design_loop で修正を促す。

## 残課題（後続）

- `asterism_mcp.tools` の `sd:` 2 関数（property_ranking / sample_search）の
  完全退去 — 現在は scripts（静的デモ資産生成）とテストのみが使用。
  provenance_of の表示ラベル特化も同枠（「表示ツール宣言化」）。
- excluded_implausible 相当の宣言化（query_tools への companion-count 拡張の
  要否判断）。
- 本番 `dataset-318ace06` の再設計 dogfood（修正後ループで一周し、
  connectivity advisory → join 追加 → zt ランキングツールまで実機で確認）。
- UI: propose 応答の `warnings` / `rounds` の表示（API は返却済み）。
