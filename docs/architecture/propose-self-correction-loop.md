# ADR: propose の自己修正ループ（設計品質の底上げ・TODO ④）

Status: Accepted (2026-07-01)
Related: [ingestion-execution-safety.md](ingestion-execution-safety.md), [phase5-declarative-substrate.md](phase5-declarative-substrate.md), [step0-rml-emission.md](step0-rml-emission.md)

## 背景

本番ドッグフードで判明した最大のボトルネックは **AI propose の一発品質**。`propose` は 1 回の
LLM 呼び出しで 9 セクションの設計 Markdown（§9 に宣言 RML マッピング）を出す。決定論的な検証層
（`asterism.rml_validate` / `rml_safety` / T9）は誤りを**捕まえる**が、AI が一発で正しく出せない。
弱いモデル（Claude クレジット切れ時の gpt-oss / Qwen 等）ほど悪化し、列幻覚・関数パラメータ IRI 誤り
（`fn:p_field1` vs `fn:p_field`）・不正 Turtle・非 Tier-0 関数・非 iri_safe・存在しないソースファイル名を
繰り返す。従来は人が「AI に修正を依頼」を手で数回押していた（`composeFixComment` が検証結果を refine
コメントに落として再実行）。

**④ = この手動ループをサーバ側で自動化する。**

## 決定

`propose` を **propose → materialize(抽出) → 検証 → 失敗を refine コメント化 → refine → 再検証** の
自己修正ループにする。ラウンド上限付き、収束/停滞/truncation で停止、常に「最良（issue 最小）」の設計を返す。

### 不変条件（破らない）

- **信頼境界は不可侵**（[生成コード非実行](ingestion-execution-safety.md)）。ループの仕事は LLM を
  Tier-0 閉集合の**中へ押し戻す**こと。閉集合を広げる「修正」は絶対に採らない。ループ出力も ingest 前に
  `assert_rml_safe` + `validate_rml_design` を再通過する。**hard 422 ingest gate は唯一の本ゲートのまま**。
- 自己修正は**設計/オーサリング層のみ**。Ask/ingest ランタイムは LLM-free のまま。
- 検証フィードバックは決定論・LLM-free。LLM を使うのは fix 生成（refine）だけ。

### 配置

`api/src/asterism_api/design_loop.py`（api 層）。ループは step0（propose/refine/materialize）＋ substrate
（assert_rml_safe/validate_rml_design/substitute_run_id）＋ `asterism.functions.REGISTRY` を**同時に**必要とし、
api/main.py は既にこの 3 つを import 済み。step0→ingest の依存反転を避け、Tier-0 オラクル（レジストリ在中）も
自然に組める。純粋部（`classify`/`render_feedback`/`build_oracle`/`normalize key`）は I/O から分離し LLM 無しで
ユニットテスト可能に保つ。

### ループ

- ラウンド0 = `propose_schema`（1 回・従来通り）。ラウンド1..N = `refine_schema` を**再利用**（新しい多ターン
  入口は作らない＝`complete()` は単ターン契約・cache 安定な SYSTEM_PROMPT を保つ）。
- 各ラウンド: `materialize_schema(write=False)` で §9 RML を抽出 → **rml_ttl が None なら「§RML 欠落」を出して
  即停止**（他に検証しようがない） → `substitute_run_id` → **`assert_rml_safe` を先に**（不正 Turtle を捕まえる
  唯一の層。`validate_rml_design` は unparseable Turtle で黙って [] を返す）→ `validate_rml_design`
  （列/パラメータ/ソースファイルの did-you-mean 付き最強フィードバック・propose が inspect 済みの実 CSV 相手）。
- フィードバックは `composeFixComment` のサーバ版（純関数）＋ **Tier-0 オラクル付録**（正しいファイル名・BOM 安全な
  実列・全 REGISTRY 関数と厳密なパラメータ名）を**USER メッセージに**（cache 安全）。弱モデルの再幻覚を「閉じた
  メニューから選べ」で抑える＝全判定が一致した最強レバー。refine には **単一結合文字列**（手動 composeFixComment と
  同一形＝実績のあるプロンプト形状）で渡す。
- **トラップ検証（T1-T10）も同じオラクルに入れる（2026-08-16 追記）**。同じ materialize 済みバンドルに対して
  `validate_schema` を回し、`fail` を issue 化する（`warn` は wizard も止めないので対象外）。バンドルの構成は
  `/api/materialize` と**同一フィールド**＝ループの「収束」は wizard のゲート通過と一致する（source CSV を渡さない
  ので T1/T6 は両方で skip）。issue の message は wizard の `runAiFix` と同形＝**症状＋決定論修正レシピ**
  （症状だけでは弱モデルは無限ループする＝2026-07-14 の T4 事故）。キーは trap ID（レシピ本文は導出語を含み揺れる
  ため、停滞検出をすり抜ける）。
  **なぜ必要だったか**: それまでループはこのゲートを回しておらず、trap 不合格は「収束」の**後**に materialize で
  初めて出た。wizard はそこで停止し、人間の「AI に直してもらう」クリックが**欠けていたラウンドそのもの**だった
  （実測 2026-08-16: 70 行の XRD ファイルで約 5 クリック）。
- **trap issue があるラウンドは全文 refine を強制**。surgical repair は §9 だけを再生成するので、実際に落ちる
  trap の在処（T4/T7＝§7 MIE・T5＝図ドキュメント）に届かない。surgical のままだと 1 コールを空振りし、次の
  ラウンドで停滞判定＝trap を残したまま止まる。
- **決定論修復をラウンド前に（2026-08-17/18 追記）**。`_evaluate` は verdict のあと `_REPAIRS`（identity transform
  の除去 → 数値列の datatype 押印）を順に試し、**再 verdict で issue が厳密に減った時だけ採用**する。機械が編集内容を
  厳密に知っている advisory は LLM に頼まない（LLM 先・決定論フォールバックの一般則の例外）。
- **no-progress はモード別（2026-08-18 追記）**。surgical（§9 のみ・guided JSON）と全文 refine は別の道具。
  surgical が動かせない keyset（同一 keyset 再来 or 出力破棄）は**全文 refine を 1 回**試してから諦める。実測
  （gpt-oss-120b・XRD）で surgical は破棄 ×2、人間の全文 refine は 38→2 まで進んだ＝機械が回せたラウンド。
- **§9 の YAML は `spec_yaml.load_spec_yaml`（YAML 1.2 boolean のみ）で読む（2026-08-18 追記）**。列名 `No`
  が `False` に化けて解読不能メッセージ（`transform['False']`）になり 3 クリック無変化だった。IR を読む箇所は
  すべてこのローダ経由＝素の `yaml.safe_load` を IR に使うと罠が再発する。
- **停止条件（優先順）**: (a) 収束＝issue ゼロ; (b) env-bail＝LLM 例外全般（truncation/429/quota）＋
  registry/rdflib import 失敗 → 最良を保持・非ループ; (c) refine truncated（`complete` False）→ 直前の
  complete な `effective_schema_md` を保持し停止; (d) 停滞/循環＝正規化 issue キー集合が既出 or 直前と同一 → 停止;
  (e) max_rounds（api 既定 5・`autocorrect=0` で kill-switch）。既定を 3→5 にしたのは trap 分の仕事が増えたため
  ＝人間が手で回していた回数（実測 ~5）より機械の上限が少ないと、結局尻尾を人間に返すことになる。
- 常に **`effective_schema_md`** を前進値に（`refined_md` でなく）。**最良（issue 最小）**の設計を snapshot し
  **それを返す**。返す設計に対応する `remaining_issues` を surface（最終ラウンドでなく返却設計のもの＝skew 修正）。
- usage は毎ラウンド記録（`last_usage` は毎回上書き）。tag は round0=`propose` / refine=`propose.autocorrect`
  （台帳の多重計上回避）。

### ストリーミング

`jobs.start_coro` でラウンド進捗を SSE `running` フレームに出す（`{phase, round, issue_count, categories, message}`）。
弱モデルは遅い（1 ラウンド 1-6 分 × 最大3）＝不透明なスピナー長時間は不可。ループは**同期の純関数**（`on_progress`
コールバック）に保ち、api コルーチンが**1 つの `asyncio.to_thread`** で回して `loop.call_soon_threadsafe` で emit へ
ブリッジ（emit をワーカースレッドから直接呼ばない＝cross-thread `Event` の罠回避）。tmpdir は finally で掃除。

### API / UI

- `/api/propose` は形を保ち、`autocorrect`（int・既定 `Settings.autocorrect_rounds`=5・
  `ASTERISM_AUTOCORRECT_ROUNDS` で上書き・0 で無効）を Query 追加。
  done 結果に `autocorrect` サマリ（converged / terminal_reason / rounds / remaining_issues / tabular_only /
  coverage_dropped）を additive で追加。`proposal_md` は最良設計。
- UI: ラウンド進捗（既存 JobProgress に message 流す）＋ **正直な**収束/best-effort バナー。手動「AI に修正を依頼」は
  fallback として温存し、remaining_issues を手動ボックスに prefill。

## 既知の限界（正直に UI/ROADMAP に明記）

- **収束 ≠ ingest-ready**。ループは静的ゲート通過を意味するだけ。Morph-KGC 実行意味論（native JSON 入れ子等）や
  ループの csv_dir と ingest 永続 source の差は捕まえない。**422 gate が本ゲート**。
- **JSON/XML ソースは列レベル検証されない**（`validate_rml_design` は tabular のみ）。非 tabular 設計の「収束」は
  バナーで「表形式のみ検証・JSON/XML の参照は未検証」と明示。
- **非 iri_safe IRI** は既存列から出るとどの静的検証も捕まえない＝ループでは自己修正不可（propose の HARD RULE 頼み）。
- **erase-to-green**: 追い詰めた弱モデルが列/マッピングを消して issue ゼロにしうる。第一段は `rml:reference` 数の
  低下を `coverage_dropped` として surface（soft・停止条件にはしない）。厳密な coverage 不変は follow-up。
- **キャンセル**: SSE 切断でジョブは走り続ける（in-flight LLM は止まらない）。max_rounds が spend を bound。
  disconnect→cancel 配線は follow-up。

## テスト

scripted mock LLM（bad→good を返す）＋実検証器を小さな fixture CSV に。収束 / 停滞・循環で最良返却 /
max_rounds / refine-truncation で直前 complete 保持 / env-bail（registry-missing・429）非ループ /
不正 Turtle が assert_rml_safe で捕まる（convergence hole 回帰） / T9 三重層の dedup / オラクルが厳密な列・
ファイル名・パラメータ local-name を列挙 / `autocorrect=0` ≡ plain propose / 純 render/normalize ユニット /
api SSE でラウンド running フレーム→done に autocorrect サマリ。
