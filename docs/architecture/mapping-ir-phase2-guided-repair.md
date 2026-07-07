# ADR: Mapping IR Phase 2 — guided JSON と §9 外科的修復

Status: Accepted (2026-07-07)
Related: [mapping-ir-compiler.md](mapping-ir-compiler.md)（Phase 1・§2.3 で本件を分離）,
[propose-self-correction-loop.md](propose-self-correction-loop.md)

## 1. 背景

Phase 1（Mapping IR + 決定論コンパイル）で構文事故クラスは原理消滅したが、実測
（`docs/reports/mapping-ir-compiler-equivalence.md` Addendum）で残った摩擦は 2 つ:

1. **修正ラウンドが全文書き換え**: IR の 1 行を直すために `refine_schema` が 9 セクション
   全体（~8k 出力トークン）を再生成する。reasoning モデルでの途中切断が実発生
   （gpt-oss run2）し、無関係セクションの巻き添え変更も起きうる。
2. **発明形は生成後にしか捕まえられない**: `optional:` フィールド・`function: str`・
   `schema:author*` は validator の targeted 誘導で直せるようになったが、
   そもそも生成させない手段（構造化出力）を使っていない。

## 2. 決定

1. **IR の JSON Schema を単一の真実源から導出**する（`asterism_step0.mapping_ir_schema.
   mapping_ir_json_schema(function_names)`）。Tier-0 メニューを `function:`/`transform:`
   の **enum** として埋め込み（`function: str` は生成不能）、`additionalProperties: false`
   （`optional:` は生成不能）、述語/クラスの末尾 `*?+` を pattern で禁止（cardinality
   マーカーは生成不能）。**grammar-friendly に oneOf を使わない** — object 形の排他などの
   意味規則は strict パーサが引き続き唯一のゲート（スキーマは生成を狭めるだけで、検証を
   置き換えない）。
2. **OpenAI 互換クライアントに structured outputs**: 呼び出し前に `response_schema`
   属性を設定（`should_cancel` 等と同じ「属性で渡す」既存パターン＝`LLMClient` プロトコル
   不変）。サーバが拒否したら per-call で `json_schema → json_object → off` と段階
   フォールバック（各 1 回・`last_notes` に記録）。Anthropic クライアントは属性を無視
   （プロンプト契約で JSON を返させ、同じパーサが受ける）。
3. **自動修正ループを §9 外科的修復に切替**（IR 経路のみ）: 修正ラウンドは
   `spec_repair.SPEC_REPAIR_SYSTEM_PROMPT`（凍結・cache 安定）＋「現 spec + issues +
   oracle」の user message で **§9 だけを再生成**し、`replace_mapping_spec_block` が
   文書へ決定論的に splice する（他セクションはバイト不変）。修復出力が spec として
   パース不能なら**そのラウンドを破棄**（schema 不変→次周の no-progress 検知で有界停止）。
   legacy 生 RML 経路と手動 `/api/refine` は従来の全文 refine のまま。
4. **round-0 の完全分割（§1-8 と §9 を別呼び出しで生成・ウィザード UI）は次段**。
   本 ADR の範囲は修正ラウンドのみ＝ユーザーが現に使っている round-0 経路を無改修に保つ。

## 3. 効果（設計時の見積り・実測はレポートに追記）

- 修正ラウンドの出力 ~8k → 数百トークン（≈10x 減）・全文途中切断の消滅・
  巻き添え変更の構造的消滅。
- guided 対応サーバ（vLLM 系＝さくら）では、実測で観測した発明 3 家系
  （unknown field / 型キャスト / cardinality マーカー）が**修正ラウンドで再発不能**。
- 非対応サーバ・Anthropic でも、外科的修復自体の利得（小さい出力・splice）は全て残る。

## 4. 不変条件

Phase 1 と同一: 修復済み spec も **parse → validate → compile → RML ゲート**を全通過
（guided は生成を狭めるだけ）。信頼境界・IRI 安定性・materialize 契約・422 本ゲート不変。

## 5. 検証

- スキーマが**全コミット済み IR fixture**（golden e2e + 収束した実測 spec）を受理し、
  **未収束の実測 spec（gpt-oss run2 の `optional:`/`*` 入り）を拒否**することをテストでピン。
- fake OpenAI で response_format の送出と 2 段フォールバックをピン。
- scripted LLM で「外科的修復プロンプトが使われ・§9 のみ差し替わり・非 spec 出力は
  有界停止」をピン。修復不能時の挙動＝best 設計返却は従来と同じ。
