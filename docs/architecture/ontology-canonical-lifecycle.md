# オントロジー / canonical のライフサイクルと starrydata 脱結合 (設計決定)

起案: 2026-06-03 / 設計セッション (人間 + Claude)
status: **合意済み**（2026-06-03 ユーザー確定）— 旧 `要決定` 4 件は §6 のとおり確定（推奨どおり採用）。「どう作るか」は固定。実装の着手判断（今やる範囲）は §6 段階を参照（今回 = **P2 まで先行**）。

前提 ADR: [`ontology-mapping-boundary-and-provenance.md`](ontology-mapping-boundary-and-provenance.md)（engine vs content 境界・per-dataset TBox・外部上位語彙の再利用・typed MCP 表面）、[`phase5-workbench-materialize-gate.md`](phase5-workbench-materialize-gate.md)（draft 隔離→canonical 昇格・alignment）。本書はこれらを**覆さず**、未設計だった「層の整理・ライフサイクル(CRUD/版)・starrydata の core からの降格」を埋める。

---

## 背景（なぜ書くか）

開発者から3つの疑問が出た。いずれも現状の設計が**未確定／看板と不整合**な箇所を突いている。

1. **オントロジーと canonical はレイヤーが違うのか** — 用語が2つの軸（層 と 状態）を兼ねていて曖昧。
2. **starrydata に特化しすぎ・最初から搭載されていて、他系で使いにくい** — 北極星「starrydata に閉じない汎用基盤」と、現状 core に焼き込まれた starrydata 実装の緊張。
3. **オントロジー / canonical の CRUD ライフサイクルがイメージできない** — 実際に「作る」しか整っておらず、更新・削除・版が未設計。

## 現状の事実（コードで確認・2026-06-03）

- starrydata 語彙はコードに焼き込み: `asterism.starrydata.DEFAULT_ONTOLOGY`、typed 4 ツール（`property_ranking`/`sample_search`/`provenance_of`/`template_curve_fetch`）は `sd:` 前提。demo-agent の決定論ルーティング `_route` も starrydata キーワード。
- substrate が Oxigraph に投入するのは **RML を CSV に当てて生成した ABox（インスタンス）だけ**。**TBox（`model.yaml`）は registry にファイル保存され、graph には入らない**。
- Ask の `schema_summary` は **canonical の ABox から語彙を逆算**（`?s a ?class` と使用述語を数える）。formal TBox は読んでいない。
- **delete エンドポイントは無い**（`/api/sparql` は read-only、DROP/DELETE 不可）。canonical/オントロジーの削除・版は未実装。
- starrydata オントロジーの **自動 seed は core に無い**（デモ用 `compose.demo.yaml` の seed loader だけが入れる）。

---

## TL;DR

### 結論（提案）

1. **2軸で語を固定する。** レイヤー軸 = **TBox（オントロジー＝型/語彙）** vs **ABox（インスタンス＝実データ）**。状態軸 = **draft（隔離・未 vet）→ canonical（公式・引用可）**。「canonical」は *ABox の状態*であって TBox の対義語ではない。
2. **TBox は content（ファイル）を一次ソースとしつつ、昇格時に専用の "オントロジー graph" にも投影する（任意・additive）。** Ask は今まで通り ABox 逆算をベースにしつつ、ラベル/階層など TBox 由来の意味で**補強できる**ようにする。TBox 無しでも動く不変条件は維持。`要決定`
3. **ライフサイクルを TBox / canonical の両方に明文化する。** Create は既存。**Update（再昇格）・Delete（DROP/retract）・版（dataset version）** を第一級にする。`要決定`（版の粒度）
4. **starrydata を core の既定から「example dataset」に降格する。** core は**スキーマ非依存だけ**を出荷。starrydata の typed ツール・`DEFAULT_ONTOLOGY`・seed・QUDT マップは `datasets/starrydata/`（content）へ寄せ、参照実装＝プラグイン化。`要決定`（降格の段階）
5. **typed ツールは per-ontology に一般化する。** starrydata 専用4ツールは「ある語彙に対する typed クエリ束」の一例。将来は (a) 各 dataset が typed ツール定義を content として持つ／(b) スキーマからテンプレ生成、のいずれか。それまでの汎用経路は #18 escape。`要決定`（(a) か (b) か）

### 採用しない案

- **canonical を TBox の対概念として扱い続ける** — 層と状態の混同が温存され、議論が毎回ずれる。
- **オントロジーを graph の第一級オブジェクトにし content ファイルを廃止する** — 前提 ADR の「design triangle（TBox/Mermaid/MIE/ingester は同時更新）を content として co-locate」を壊す。graph 投影は *additive な投影* に留める。
- **starrydata を core から即時に全削除する** — 動作中のデモ・既存 IRI を壊す。降格は段階的・後方互換で。

---

## 1. 概念モデル — 2軸で語を固定

```
                  TBox（オントロジー＝型・語彙）        ABox（インスタンス＝実データ）
                ┌────────────────────────────┐   ┌────────────────────────────┐
  レイヤー        「Specimen クラスがある／            「spec-wc は Specimen／
  （意味の階層）     hardnessHV は整数」                 その測定は hardnessHV=2200」
  一次の置き場     registry / datasets（ファイル）       Oxigraph のグラフ
  状態軸          （現状ライフサイクル無し→§3で付与）    draft ──promote──▶ canonical
  Ask は読む？     △（今は読まない／§2で任意補強）        ○（canonical だけ）
```

- **オントロジー = TBox レイヤー**。型・語彙の定義。「中身」は持たない。
- **canonical = ABox の "公式状態"**。型に従って実際に入っているデータの vet 済み版。
- 両者はレイヤーが違い、かつ**別の軸**（状態 draft/canonical は今 ABox にしか無い）。
- 含意: 「オントロジーを Ask したい」のではなく「**そのオントロジーで表現された実データ（canonical の ABox）を Ask したい**」が正しい言い方。

## 2. オントロジー(TBox) の居場所

**決定（提案）**: 一次ソースは content ファイル（registry / `datasets/{name}/`）。前提 ADR の design triangle co-location を維持。**加えて、昇格時に TBox を専用のオントロジー named graph（例 `…/graph/ontology/{name}`）へ投影する**（任意・additive）。

- 理由: Ask の `schema_summary` は今 ABox 逆算で動く（TBox 不要で動く＝良い不変条件）。だが TBox を graph に置けば、**クラス/述語の人間可読ラベル・階層・domain/range** を escape の接地に使え、回答の質が上がる。
- 不変条件: **TBox graph が無くても Ask は動く**（ABox 逆算が baseline）。TBox graph は enrichment であって依存にしない。
- `要決定`: TBox を canonical(default) graph に混ぜるか、別の `graph/ontology/{name}` に分けるか。推奨 = **別 graph**（ABox と TBox を物理分離＝引用面 canonical の純度を保つ／schema_summary は両方を読める）。

## 3. ライフサイクル（CRUD + 版）

現状と目標を表で固定する（✓ 実装済 / △ 部分 / ✗ 無し → ◎ 目標）。

| 操作 | オントロジー(TBox) 現状→目標 | canonical(ABox) 現状→目標 |
|---|---|---|
| Create | ✓ propose/refine→registry ファイル → ◎ ＋昇格時に ontology graph 投影 | ✓ 設計→ingest(draft)→promote(MOVE) |
| Read | ✓ Gallery 表示 → ◎ ＋ schema_summary が ontology graph も参照 | ✓ Ask / SPARQL |
| Update | △ refine 再生成・alignment(Reuse/New) → ◎ **版付き再昇格**（旧版を残す） | △ 再 ingest(冪等)のみ → ◎ **再昇格で差し替え＋版** |
| Delete | ✗ → ◎ registry delete ＋ DROP ontology graph | ✗ → ◎ **retract**（canonical→archive or DROP draft/canonical graph） |

**新たに要る操作（提案）**:
- **再昇格 (re-promote)**: 設計を直して再 materialize→ingest→promote。旧 canonical をどう扱うか（上書き / 版で残す）。
- **retract / demote**: 昇格を取り消す（canonical からデータ/語彙を撤去）。引用された IRI の扱い（tombstone か物理削除か）。
- **delete**: dataset 丸ごと（registry ＋ draft/canonical/ontology graph）。
- `要決定`: **版（versioning）の粒度** — dataset 単位の version か、IRI に版を埋めるか（前提 ADR は「v0.1.0 の旧 IRI は歴史スナップショットとして据え置き」＝IRI は不変が原則）。推奨 = **IRI は不変、dataset メタに version を持ち、graph 名 or registry で世代管理**（引用安定性を壊さない）。

含意: retract/delete は「引用できる事実」方針（引用された IRI が消えると下流が壊れる）と緊張する。**canonical からの物理削除は原則避け、retract は draft への差し戻し or tombstone を既定**にする方向を推奨。`要決定`

## 4. starrydata の core からの降格

**問題**: 北極星は「starrydata に閉じない」。だが現状 core に starrydata が焼き込まれている（§現状の事実）。

**決定（提案・段階的）**: starrydata を「**core の既定**」から「**datasets/ 配下の参照実装（example）**」へ降格。core はスキーマ非依存のみ出荷。

| 対象 | 現状 | 目標 |
|---|---|---|
| `DEFAULT_ONTOLOGY` 等の定数 | `asterism.starrydata`（core ingest pkg） | `datasets/starrydata/` の設定へ。core は既定語彙を持たない |
| typed 4 ツール | `asterism_mcp` core | per-dataset の typed ツール定義（§5）。core MCP は汎用 `schema_summary`/`sparql_query` のみ既定登録 |
| QUDT マップ・seed・Gallery fixture | core / demo | `datasets/starrydata/` |
| demo-agent `_route` | starrydata キーワード固定 | 汎用化（型付きが無ければ escape）。starrydata ルートは starrydata dataset の設定として注入 |

- 不変条件: **既存 starrydata IRI と動作中デモを壊さない**（前提 ADR の「IRI 不変」）。降格は後方互換＝旧 import パスの薄い再エクスポートやエイリアスで段階移行。
- `要決定`: 降格の**段階**（(i) いま定数/seed を datasets/ へ移し core を汎用化 → (ii) typed ツールを per-dataset 化、の2段階を推奨）。どこまで今やるか。

## 5. typed ツールの一般化（決定論・型付きを汎用に）

product_direction「決定論・型付きを主役、LLM は escape」を**新しい系でも**成り立たせるには、starrydata 専用 typed ツールに相当するものを per-ontology に用意できる必要がある。現状それが無く、新系は LLM escape 頼みになる（#18 で埋めた汎用経路）。

選択肢 `要決定`:
- **(a) content として typed ツールを書く** — 各 dataset が「この語彙に対する典型クエリ束（MIE の sparql_query_examples 相当）」を宣言し、core がそれを typed MCP tool として公開。人手 vet＝信頼アンカーのガバナンス点（Tier0 追加と同じ哲学）。
- **(b) スキーマからテンプレ生成** — schema_summary ＋ MIE から決定論的に typed クエリを生成（#18 の選択肢で見送った案）。汎用だがテンプレに収まらない問いは扱えない。
- 推奨 = **(a) を主、(b) を補助**。引用できる事実は人が vet した typed ツールで、それ以外は #18 escape。

含意: これが入ると「型付き主役・LLM escape」が **starrydata 以外でも**成立する。#18 escape はその時も「未整備な語彙への入口」として残す。

## 6. 段階・残課題・要決定一覧

**段階（提案）**:
- P1（小・低risk）: §1 の用語を ROADMAP/UI/Gallery に反映（概念図常設）。delete/retract の**設計のみ**確定。
- P2: §4-(i) starrydata 定数/seed を `datasets/starrydata/` へ、core を汎用化（後方互換）。
  - **実装上の発見**: `asterism.starrydata` は定数だけでなく **starrydata 専用 ingester（~840 行）と汎用テキスト/パースヘルパが同居**し、多数の module（functions/watcher/mcp/api/step0）が import している。よって P2 は「移動」ではなく**untangling**。安全な順で刻む。
  - ✅ **P2-1 完了**: 汎用ヘルパ（`slugify`/`parse_issued`/`parse_float_array`/`strip_quoted`/`safe_url`）を新 `asterism.text` に抽出。`asterism.starrydata` は後方互換で re-export。Tier0 `functions.py` は `asterism.text` を直接 import（**汎用 core が starrydata module に依存しなくなった**）。ingest 94 / mcp 29 / demo-agent 9 緑。
  - ⬜ **P2-2 以降（残）**: `DEFAULT_ONTOLOGY`/`DEFAULT_RESOURCE` 等の*既定*を core から外し dataset 設定へ（mcp/api が定数 import 中＝seam が要る）。seed（`demo-agent/seed/`）・`qudt_map.yaml`/`qudt.py` を `datasets/starrydata/` へ。`watcher.py` の starrydata 既定 path。
- P3: §3 のライフサイクル実装（再昇格・retract・delete・版）＋ §2 の ontology graph 投影。
- P4: §5-(a) per-dataset typed ツール機構。
- 並行: 2個目の実 dataset（非 starrydata）を P2 の検証として投入（#19 と接続）。

**確定事項（2026-06-03 ユーザー確定・すべて推奨どおり）**:
1. §2: TBox は **別 `graph/ontology/{name}`** に投影（canonical に混ぜない）。schema_summary は両 graph を読む。
2. §3: **IRI 不変＋dataset version** で世代管理。**retract = tombstone（無効化マーク）を既定**（canonical からの物理削除は原則しない＝引用安定性を守る）。
3. §4: starrydata 降格は **今回 P2 まで先行**（定数/seed/QUDT を `datasets/starrydata/` へ移し core を汎用化・後方互換）。typed ツール per-dataset 化（P4）は後。
4. §5: typed ツール一般化は **(a) content 宣言を主**、**(b) スキーマ生成を補助**。引用できる事実は人が vet した typed ツール、それ以外は #18 escape。

---

関連: [[ontology-mapping-boundary-and-provenance]] [[phase5-workbench-materialize-gate]] [[phase5-declarative-substrate]] [[product-direction-citable-facts]]
