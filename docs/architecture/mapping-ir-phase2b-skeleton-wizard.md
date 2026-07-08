# ADR: Mapping IR Phase 2b — round-0 の段階分割生成とウィザード UI（骨格の早期人間ゲート）

Status: Proposed (2026-07-08)
Related: [mapping-ir-compiler.md](mapping-ir-compiler.md)（Phase 1・§2.3/§9(c) で本件を分離）,
[mapping-ir-phase2-guided-repair.md](mapping-ir-phase2-guided-repair.md)（Phase 2a・§2.4 で本件を「次段」と明記）,
[propose-self-correction-loop.md](propose-self-correction-loop.md)（自己修正ループ・per-map 再生成の母体）,
[external-standard-alignment.md](external-standard-alignment.md)（§7 grounding 合流の判断）

## 1. 背景

Phase 1（[Mapping IR + 決定論コンパイル](mapping-ir-compiler.md)）で **§9 の構文事故クラスは原理消滅**し、
Phase 2a（[guided JSON + §9 外科的修復](mapping-ir-phase2-guided-repair.md)）で **修正ラウンドの形崩れと
全文再生成の無駄**が消えた。両 ADR は残りを明示的に次段へ送っている:

- Phase 1 §2.3:「段階分割そのもの（クラス選定→プロパティ表の複数呼び出し化）は **Phase 2 として分離**し、
  本 ADR では単一呼び出しのまま §9 だけ差し替える」。§9(c) も「いきなり完全段階分割は契約変更が大きく
  呼び出し回数も増える…段階分割は Phase 2 の発展として分離」。
- Phase 2a §2.4:「round-0 の完全分割（§1-8 と §9 を別呼び出しで生成・**ウィザード UI**）は次段。本 ADR の
  範囲は修正ラウンドのみ＝ユーザーが現に使っている round-0 経路を無改修に保つ」。

**本 ADR はその「次段」= Phase 2b を確定する。** 残るボトルネックは round-0 の **「9 セクション一発生成」**
そのもの。構文は治ったが、弱いモデルほど **骨格（どのクラスを立て、subject の主キーを何にするか）の意味ミス**
を一発で作り込む。Phase 1 の本番ドッグフード（Qwen3.6-35B-A3B・`dataset-548d5ca3`）でも意味判断は概ね妥当
だったが、主キー選定は最も外しやすい判断で、かつ **最も高コストの誤り**である:

- subject の主キーを間違える（例: curve の主キーが `sample_id` 単独 → 全曲線が 1 サンプルに潰れる）と、
  その map の **全プロパティ・全 IRI** が誤った土台の上に乗る。§9 を後から直しても、既に組み上がった
  プロパティ表・散文（§1-8）・model.yaml まで巻き添えになる。
- 現状は誤りが **9 セクション全部生成された後** にしか露見しない。人間が Turtle 相当の IR を読み下して
  気づき、「AI に修正を依頼」で全体を作り直す。弱モデルでは収束が遅い。

**着眼点**: round-0 を段階生成にし、**骨格（クラス構成＋subject キー）を最小の構造化出力で先に出して、
プロパティを一切生成する前に人間が確認・修正できる早期ゲートを挟む**。骨格が正しければ、その後の
per-map 生成（Phase 2a の guided JSON）と決定論コンパイル（Phase 1）は既に堅い。骨格の意味ミスという
「唯一まだ高コストな誤り」を、文書化される前にワンクリックで潰す。

これは同時に **phase5 の理念**（人間が承認するのは列→述語の対応＋関数参照であってコードではない）を
UI の粒度でも実現する: レビュー面が「9 セクションの完成物を読む」から「まず骨格の表を確認 → 各 map の
プロパティを見る」へと、判断の順序が意味構造に一致する。

## 2. 決定（提案）

round-0 を **3 段のパイプライン**に分割する。各段は凍結 SYSTEM_PROMPT の小さな構造化呼び出しで、
Phase 1/2a の資産（`mapping_ir` パーサ・`mapping_ir_json_schema`・`replace_mapping_spec_block`・
`run_design_loop` のオラクル/進捗/キャンセル）を再利用する。**最終アーティファクトは従来と同じ §1-9
Markdown**（materialize 契約・下流全て不変）。

1. **骨格ステップ（人間ゲート付き）**: `{maps: [{name, source, iterator?, subject, note?}]}` だけを
   guided JSON で生成（プロパティ無し・§3）。inspection の **一意性統計を強く提示**（T1）して主キー選定を
   支える。**生成後にジョブを一旦完了して骨格を返し、人間が編集可能なテーブルで確認・修正**する（§5・§6）。

2. **per-map ステップ**: 確定した骨格の各 map について、その map の `properties[]`（= Mapping IR の
   プロパティ表）を guided JSON で生成（§4）。map ごとの小さい呼び出し＝進捗が map 単位で見え、失敗は
   その map だけ再生成。これは構造的に Phase 2a の `_surgical_spec_repair`（§9 の一部だけ再生成→
   決定論 splice）の一般化であり、**同じオラクル・検証・no-progress 停止**を per-map に適用する。

3. **文書ステップ**: §1-8（散文・IRI scheme・model.yaml・MIE）を LLM で生成し、§9 は **組み上がった
   Mapping IR を決定論で埋める**（`replace_mapping_spec_block` で splice）。§1-8 は組み上がった IR を
   コンテキストに与えて生成する＝散文と §9 が食い違わない。

**既存の単一呼び出し経路 `POST /api/propose` は残す**（後方互換）。段階分割は **新しい経路**として足し、
既存経路・legacy 生 RML・手動 `/api/refine`・非 guided プロバイダ（Anthropic 含む）を一切壊さない。
段階分割は guided JSON 対応プロバイダ（さくら vLLM 等）で最大の利得を出すが、非対応でも「小さい
focused な呼び出し＋早期ゲート」の利得は残る（プロンプト契約で JSON を返させ同じパーサが受ける）。

## 3. 骨格スキーマ（skeleton = Mapping IR の subject 部分集合）

骨格は Mapping IR から **`properties[]` を落とした部分集合**。`mapping_ir_schema.mapping_ir_json_schema`
の map オブジェクトが現在 `required: ["name","source","subject","properties"]` なのに対し、骨格スキーマは
`required: ["name","source","subject"]`（`properties` を含めない）。subject サブスキーマは**そのまま再利用**。

```yaml
# 骨格ステップの出力（guided JSON・yaml 等価形）
version: 1
maps:
  - name: paper                       # TriplesMap 名
    source: papers.csv                # inspection の列挙名（コピー厳守・実在チェック）
    subject:
      template: "sdr:paper/{SID}"      # subject IRI テンプレート（{列} プレースホルダ）
      classes: [sd:Paper, schema:ScholarlyArticle]
    note: "1 行 = 1 論文。SID が一意キー（inspection: 12,345 distinct / 12,345 rows）"
  - name: sample
    source: samples.csv
    subject:
      template: "sdr:sample/{SID}-{sample_id}"   # 複合キー（早期ゲートで人が確認する最重要点）
      classes: [sd:Specimen]
  - name: curve
    source: curves.csv
    subject:
      template: "sdr:curve/{SID}-{sample_id}-{prop_x}-{prop_y}"  # ← 主キー誤りが起きやすい箇所
      classes: [sd:Curve]
```

骨格スキーマで**閉じるフィールド**（Phase 1 §3 の subject 語彙のみ・未知キーはコンパイルエラー）:

| フィールド | 意味 | 検証（骨格段で前倒し） |
|---|---|---|
| `maps[].name` | TriplesMap 名 | 識別子形式 |
| `maps[].source` | ソースファイル名 | **実在チェック**（did-you-mean） |
| `maps[].iterator` | XML のみ: XPath iterator | XML ソース時のみ許可 |
| `subject.template` / `subject.constant` | subject IRI（排他） | プレースホルダ列の**実在チェック** |
| `subject.classes` | `rr:class`（複数可） | CURIE 解決可能 |
| `subject.transform` | template 内列の可読セグメント化 | 単一入力 Tier-0・多値不可 |
| `note`（骨格専用・任意） | 主キー選定の根拠メモ（人間ゲートの補助） | 検証なし・最終 artifact に持ち越さない |

**設計原則**: 骨格段の LLM が書くのは「どの表からどのクラスを、どのキーで立てるか」だけ。プロパティ表・
関数・datatype は per-map 段に委ねる。骨格の検証（列/ソース実在）は既存 `validate_mapping_ir` の
subject 部分をそのまま流用でき、**誤りを最速タイミングで返す**（人間が直す前に did-you-mean を出す）。

**主キー品質のためのプロンプト（T1）**: inspection の **列ごとの distinct 数 / 行数 / 複合一意性** を
骨格 SYSTEM_PROMPT の直近コンテキストに強く出す。狙いは「`sample_id` 単独では一意でない（SID との複合で
一意）」を LLM に気づかせること。ただし **保証はしない**（§11）— だからこそ人間ゲートが本命。

## 4. 呼び出し構成（骨格 → per-map → 文書）とキャッシュ／レイテンシ

```
[inspect] → ①骨格(1 call) → 〈人間ゲート: 確認・編集〉 → ②per-map(N calls) → ③文書(§1-8, 1 call) → §9 決定論 splice → 従来の §1-9 Markdown
              guided JSON        編集可能テーブル         guided JSON×N       散文               replace_mapping_spec_block
```

- **各段は独立した凍結 SYSTEM_PROMPT**（`SKELETON_SYSTEM_PROMPT` / `PERMAP_SYSTEM_PROMPT` /
  `DOCUMENT_SYSTEM_PROMPT`）。Phase 2a の `SPEC_REPAIR_SYSTEM_PROMPT` と同じく **byte-stable = プロンプト
  キャッシュ安定**。round-per-round のフィードバックは USER メッセージに載せる（自己修正ループと同じ規律）。
- **per-map の直列/並列**: map 間に依存はない（各 map は独立の TriplesMap）ので **並列化可能**。ただし
  骨格を全 map 分コンテキストに載せる（他 map の subject を参照してリンク先 template を書けるように）。
  弱モデル・レート制限を考え、**既定は直列（進捗が map 単位で綺麗に出る）**、`asyncio.gather` の
  並列は将来のスループット最適化（実装は直列で始める・YAGNI）。
- **§1-8 を後にする理由**: model.yaml（§6）・MIE（§?）・IRI scheme の散文は、**組み上がった IR を
  参照して**書けば §9 と食い違わない。骨格段の `note` と確定 IR を DOCUMENT_SYSTEM_PROMPT に与える。
- **§6 model.yaml の決定論下書き（任意・将来）**: model.yaml はクラス/プロパティの宣言なので、確定骨格の
  `classes` と per-map の `predicate` から**機械的に下書き**できる余地がある（[ontology-projection.md](ontology-projection.md)
  の promote 時投影と同型）。本 ADR では **文書ステップに含めて LLM に書かせる**（散文の一貫性優先）。
  決定論下書きは Phase 2b 後の最適化候補として記録に留める。
- **レイテンシ**: 呼び出し回数は 1 → (2 + N) に増えるが、各呼び出しは小さく guided で **truncation
  リスクが下がり**、per-map は失敗した map だけ再生成できる（全文再生成の消滅は Phase 2a の延長）。
  弱モデルの「1 ラウンド 1-6 分 × 全文」より、**focused な小呼び出しの積み上げ**の方が体感が良く、
  途中経過（骨格・map 単位）が見える。人間ゲートの待ちは体感レイテンシを分割する（骨格だけ先に見える）。

**自己修正ループとの合流**: per-map ステップは `run_design_loop` の中で回す。各 map の properties 生成 →
`validate_mapping_ir`（列/関数/引数の did-you-mean）→ issue があれば oracle 付きで **その map だけ**
再生成 → no-progress で有界停止。文書ステップ後の完全 IR に対しては、**既存の RML ゲート（`assert_rml_safe`
→ `validate_rml_design` → T9 → 422）を最終防衛線として全通過**させる（Phase 1 §8 不変）。

## 5. API / SSE 契約とジョブ対話化

現状（実コード確認済み）:

- `POST /api/propose`（multipart）→ `{job_id}`（202）→ SSE `GET /api/jobs/{job_id}/stream`。進捗は
  `emit(phase=..., message=...)`、`run_design_loop` が `{phase, round, issue_count, categories, message}`
  を出す。done ペイロード = `{proposal_md, inspection_md, metadata, autocorrect:{...}}`。
- **`JobManager` は one-shot**（`start_coro(make_coro)` → DONE/ERROR/CANCELLED・wall-clock timeout・
  cancel event）。**途中で人間入力を待って再開する primitive は無い**。

**決定: 骨格の人間ゲートは「2 ジョブ構成」で実装する（推奨）。** JobManager を one-shot に保ったまま、
段階分割を **2 本の独立ジョブ**に割る:

- `POST /api/propose/skeleton`（multipart・既存 propose と同じ入力）→ `{job_id}`。ジョブは inspect →
  骨格生成 → 骨格検証 を回し、**done ペイロード = `{skeleton, inspection_md, source_token}`**。
  `skeleton` は編集可能な骨格 IR（JSON）、`source_token` は per-request tmpdir に置いたソースの
  サーバ側ハンドル（continue が同じソースを検証に使うため・D7 のキーは持ち越さない）。
- `POST /api/propose/continue`（body = 確定骨格 + `source_token` + LLM ヘッダ）→ `{job_id}`。ジョブは
  per-map → 文書 → §9 splice → 既存自己修正ループ を回し、**done ペイロード = 現行 propose と同一形**
  （`{proposal_md, inspection_md, metadata, autocorrect}`）＝ **materialize 以降は完全に無改修**。

理由（[pref: 実装コスト度外視・プロダクト理想](../architecture/design-rationale.md) に沿って理想案を推奨）:

- Phase 2b の**中核価値は「早期の人間ゲート」**（handoff §1）。骨格を確定してから per-map を回すことで、
  誤った土台の上にプロパティ・散文を積む無駄が構造的に消える。auto-continue（骨格を確定せず一気に
  生成し後から骨格を編集）だと、**誤骨格の上に全 map を生成 → 後で全部作り直し**になり、価値が薄れる。
- JobManager に「一時停止して入力待ち→再開」を足すのは、状態機械・タイムアウト・SSE 再接続・cancel の
  全てに波及する大改修。**2 ジョブ split は既存 one-shot モデルにそのまま乗り**、各ジョブが独立に
  cancellable/timeout/heartbeat のまま。SSE も既存 `phase` フレームに `skeleton` / `map:<name>` /
  `document` を **additive に足すだけ**（新フレーム種別を発明しない）。
- `source_token`（continue が再アップロード不要でソースを検証に使う仕掛け）は、既存の per-request
  tmpdir を **skeleton ジョブ完了後も continue まで保持**する TTL 付きハンドルにするだけ
  （JobManager の TTL/掃除と同じ寿命規律）。

> **実装メモ（2026-07-08・着地）**: 初回実装は `source_token` を採らず、continue で**ソースを再
> アップロード**する（UI はウィザード中 `File[]` を保持済み＝再送は容易）。ステートレスで、
> skeleton→continue 間の**孤児 tmpdir が原理的に発生しない**（§11 の当該限界が消滅）。実装した
> エンドポイントは `POST /api/propose/skeleton`（done=`{skeleton, inspection_md, metadata}`）＋
> `POST /api/propose/continue`（multipart: 確定骨格 JSON `skeleton` + ソース再添付）。大きい CSV の
> 再送を避ける `source_token`（TTL 付きハンドル）は後続 PR の最適化として保留。

**却下した対話化案**（§9 に詳細）: (i) 単一ジョブを一時停止して入力待ち（JobManager 大改修）、
(ii) auto-continue + 事後編集（早期ゲートの価値を失う）。

**SSE の additive 拡張**（両ジョブ共通）:
- skeleton ジョブ: `emit(phase="inspect"|"skeleton", message=...)` → done。
- continue ジョブ: `emit(phase="map:<name>", index=i, total=N, message=...)` を map ごとに →
  `emit(phase="document")` → 既存の `round` フレーム（自己修正）→ done。
- 既存クライアント（`autocorrect` サマリだけ読む等）は未知 `phase` を無視すれば動く（後方互換）。

## 6. UI ウィザード（WorkbenchView）と i18n

現状（実コード確認済み）: `WorkbenchView.tsx` は既に **`step` state（`Step` 型）** を持ち、
`subscribeJob`/`resumeJob`/`cancelJob`（EventSource）・sessionStorage ジョブ replay・`useTranslation()`・
`locales/{ja,en}/workbench.json` が揃っている。ウィザードの器は既にある。

Phase 2b で足すステップ（inspect と review の間に挿入・additive）:

1. **骨格確認カード**（新規）: skeleton ジョブの done を受け、**編集可能テーブル**で表示。
   行 = map、列 = 名前 / ソース / subject テンプレート / クラス / **主キー列（テンプレートのプレースホルダ）**。
   - 各行にインライン検証（did-you-mean を skeleton ジョブが既に付けている）。
   - 主キー列は inspection の一意性統計をツールチップで見せる（`sample_id` 単独は distinct < rows の
     警告など）＝ handoff が挙げた「curve の主キーが `sample_id` 単独」級のミスをワンクリックで直す UX。
   - 「この骨格で続行」ボタン → 確定骨格を `POST /api/propose/continue` へ。
2. **per-map 進捗**（新規）: continue ジョブの `map:<name>` フレームを map チェックリストで表示
   （✓ 済 / ⏳ 生成中 / ⟳ 再生成中）。既存 `JobProgress`・`lastPulseAt` の liveness 表示を再利用。
3. **従来のレビュー画面**（不変）: continue done の `proposal_md` を既存の proposal ビューへ。
   materialize・refine・昇格は一切変更なし。

- **後方互換の入口**: 「一括生成（従来）」も UI に残す（既存 `proposeCsvs` → `/api/propose`）。
  ウィザードは既定の推奨経路にするが、非 guided プロバイダや「骨格確認は不要」ユーザー向けに一括も選べる。
- **i18n**: 新規文字列は `locales/{ja,en}/workbench.json` に追加（日本語ファースト・`import.meta.glob`
  自動ロード）。骨格テーブルの列見出し・警告・ボタンラベル・per-map ステータス。
- **sessionStorage**: skeleton の `job_id` と骨格編集中の state を退避（既存 `JOB_STORAGE`/`WB_STORAGE`
  規律）＝タブ再読込・SSE 一時切断からの復帰。continue も同様に replay 可能に。
- **検証ポートの罠**: Claude Preview は `VITE_DEMO_MODE=mock` を強制注入するので、実データ検証は
  `VITE_API_PROXY` を OS env で通す（`/api` proxy 経由は mock スイッチ非経由＝実データ）。

## 7. grounding（外部標準接地）合流の判断

**決定: Phase 2b のスコープ外（別 PR）。** per-map ステップの述語選択に
[`/api/ground`](external-standard-alignment.md)（PR #225 で着地済み・決定論クローズドセット検索）の
実在候補を出せば「新規 mint より再利用」が自然に効く — その **注入点は per-map ステップが正しい**。
だが grounding 検索基盤は既に独立して動いており、Phase 2b の骨格＋ウィザードとは直交する。今 per-map に
束ねると PR が膨らみ検証マトリクスが倍化する。**Phase 2b 着地後の follow-up** として、per-map の
`predicate` 候補に `ground_terms` を差し込む小 PR を推奨（本 ADR §7 を参照点として残す）。

## 8. 不変条件（破らない）

- **信頼境界不変**: LLM 生成物は非実行。骨格も per-map も **Tier-0 閉集合を狭める方向にのみ**働く
  guided JSON。組み上がった IR はコンパイル後 `assert_rml_safe` + `validate_rml_design` + T9 +
  hard 422 ingest gate を全通過（**422 が本ゲートのまま**）。**検証の唯一のゲートは strict パーサ +
  validator**（guided は生成を狭めるだけ・検証を置き換えない）。
- **materialize 契約**: 最終アーティファクトは英語見出しの §1-9 Markdown・§9 は yaml フェンス 1 個・
  additive 抽出・`complete` 判定。段階分割しても **materialize 以降（registry/ingest/promote/UI）は
  無改修**。旧 proposal（turtle ブロックのみ）・legacy 生 RML・手動 `/api/refine` の互換を壊さない。
- **汎用性**: 骨格スキーマ・per-map プロンプト・文書プロンプトはドメイン非依存（材料特化の語彙・規則を
  入れない）。主キー選定支援も「一意性統計を出す」という汎用手段のみ。
- **IRI 安定性**: 複合キー・iri_safe・template percent-encode の決定論（Phase 1 §4）を変えない。
  骨格→per-map→コンパイルの経路でも、同じ骨格からは同じ IRI が出る。
- **Ask/ingest ランタイムは LLM-free**のまま（変更は設計/オーサリング層のみ）。
- **既存 `POST /api/propose` は無改修**（段階分割は新経路として追加）。

## 9. 代替案（棄却）

- **(a) 単一ジョブを一時停止して骨格入力を待つ（対話型ジョブ）**: JobManager に suspend/resume/入力
  チャネルを新設する大改修。状態機械・timeout・heartbeat・SSE 再接続・cancel の全てに波及し、one-shot
  の単純さ（実績のある堅い土台）を捨てる。2 ジョブ split で同じ UX が既存モデルのまま得られる。
- **(b) auto-continue（骨格を確定せず一気に生成し、骨格は事後編集）**: 早期ゲートの中核価値を失う。
  誤骨格の上に全 map・散文を積んでから直す＝Phase 2a までの「全部作ってから直す」と本質的に同じ。
  骨格を先に確定する意味が消える。
- **(c) 段階分割せず round-0 を単一呼び出しのまま、骨格レビュー UI だけ足す**: 骨格は §9 全体の一部
  でしかなく、単一生成では「骨格だけ先に」を実現できない（生成前に骨格を抜き出せない）。分割が前提。
- **(d) per-map を LLM でなく完全決定論に**: プロパティ（列→述語→関数→datatype）の選択は意味判断で
  あり、Tier-0 メニューからの選択とはいえ列と述語の対応付けは LLM の仕事。決定論化は不可能
  （それができるなら propose 自体が要らない）。per-map は guided JSON で **選択を狭める**のが正解。
- **(e) §1-8 を per-map より先に生成**: 散文（model.yaml/IRI scheme/MIE）が未確定の IR を参照でき
  ず、後で §9 と食い違う。IR を組み上げてから散文＝一貫性が保てる（§4）。

## 10. 受け入れ基準・検証計画

1. **純関数テスト**: 骨格スキーマが Mapping IR の subject 部分集合として正しい（`properties` 無しを
   受理・不正 subject を拒否）。per-map 生成→組み立て→`replace_mapping_spec_block` splice が、
   単一呼び出しで作った等価 IR と**同じ §1-9 Markdown**（§9 バイト一致・§1-8 は散文差のみ）を生む
   ことを scripted LLM でピン。
2. **後方互換**: 既存 `POST /api/propose`（単一呼び出し）・旧 proposal fixture の materialize・
   手書き RML 経路（papers/MP）が**不変**。step0/api/ingest テスト全緑。
3. **SSE additive**: skeleton/continue の 2 ジョブが `skeleton`/`map:<name>`/`document` フレームを
   出し、未知 phase を無視する既存クライアントが動くことを fake job でピン。continue done が現行
   propose done と同一形。
4. **実測（本丸）**: `dataset-548d5ca3` と同じ Starrydata CSV を **Qwen3.6-35B-A3B / gpt-oss-120b** で:
   - **骨格の主キーが複合になるか**（`curve` の subject が `sample_id` 単独でなく複合キーになるか・
     人間ゲート前の初回品質と、ゲートでの修正のしやすさ）。
   - **per-map の初回品質**（map 単位の issue 数・再生成回数・truncation ゼロ）。
   - 単一呼び出し（現行）との比較（収束時間・総トークン・最終 triples 一致）。
   - `docs/reports/` に Q→Method→Result→Conclusion→Limitations→Reproduce 形式でレポート（恒久方針・
     [equivalence レポート](../reports/mapping-ir-compiler-equivalence.md)の続編）。
5. **UI 検証**: 実 `VITE_API_PROXY` で骨格確認テーブルの編集→continue→per-map 進捗→レビュー→
   materialize→昇格まで実ブラウザで一周（ja/en）。sessionStorage replay・SSE 一時切断復帰。

## 11. 既知の限界（正直に）

- **意味エラーは残る**: 骨格の人間ゲートは主キー・クラス選定の**修正機会を最速で与える**が、正しさを
  保証しない。ユーザーが誤ったまま確定すれば誤った IR が組まれる（従来同様 422 gate と最終レビューが
  防衛線）。一意性統計の提示も助言であって強制ではない。
- **per-map は骨格を信頼する**: 骨格が確定済みなので per-map は subject を疑わない。骨格ゲートを
  すり抜けた誤りは per-map では捕まらない（設計上の割り切り＝人間ゲートが骨格の責任範囲）。
- **XML/JSON ソースの参照は列レベル検証されない**（Phase 1 §11・`validate_mapping_ir` は tabular のみ）。
  骨格の subject template のプレースホルダも XML iterator 相対パスは不透明に素通し。
- **非 guided プロバイダ**では骨格/per-map も形崩れしうる（プロンプト契約頼み）。パーサが弾き自己修正で
  押し戻すが、guided 対応（さくら vLLM 等）ほどの原理保証はない。
- **2 ジョブ split とソース**: 初回実装は continue でソースを**再アップロード**（`source_token` 不使用）
  ＝ステートレスで、skeleton→continue 間の孤児 tmpdir が原理的に発生しない。大きい CSV の再送を避ける
  `source_token`（TTL 付きハンドル）は後続 PR の最適化（§5 実装メモ）。
- **呼び出し回数の増加**: 1 → (2 + N + 自己修正ラウンド)。per-map 並列化と guided の truncation 減で
  相殺を狙うが、レート制限の厳しいプロバイダでは総レイテンシが伸びうる（既定直列のため）。
