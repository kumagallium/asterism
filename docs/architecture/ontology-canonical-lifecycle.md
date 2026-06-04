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

### 3.1 P3 実装設計（提案・未実装 / 2026-06-04）

§3 の確定事項（IRI 不変＋dataset version／retract=tombstone／物理削除は原則しない）を、現状の実装に接地して具体化する。**コードはまだ書かない**——本節は実装前のレビュー用設計。

#### 現状の接地（コードで確認・2026-06-04）

- canonical = **Oxigraph の default graph**。draft = `…/starrydata/graph/draft/{dataset_id}`（`substrate.draft_graph_iri`）。promote = `MOVE GRAPH <draft> TO DEFAULT`（`substrate.promote_draft_to_canonical`・人間ゲート後）。
- registry = **ファイル**（`api.registry`）。dataset meta に `ingested`/`promoted`/`alignment` 等。
- 書き込み能力は**既に内部に存在**: `OxigraphClient.sparql_update` を promote の MOVE が使用。**公開 `/api/sparql` だけが read-only**（UPDATE 拒否）。よって P3 は「書き込みを新規導入」ではなく、**専用・検証済み・graph スコープのライフサイクル操作を足す**（`/api/sparql` の read-only 不変条件は維持）。

#### 中心設計判断: canonical を per-dataset named graph に

**問題**: 現 promote は全 dataset を共有 default graph に流し込むため、特定 dataset の triple を後から**スコープして** retract/再昇格/delete できない（どの triple がどの dataset 由来か graph では区別不能）。

**提案（P3 の核）**: canonical を **per-dataset named graph** `…/graph/canonical/{dataset_id}` に置く（default graph 一括をやめる）。すると lifecycle は graph レベルの clean な操作になる:
- 再昇格 = `DROP GRAPH <canonical/{id}>` → `MOVE <draft/{id}> TO <canonical/{id}>`（stale triple が残らない＝版間で消えたプロパティも確実に消える）。
- retract = canonical graph はそのまま、control graph に tombstone マーク（下記）。
- delete = `DROP GRAPH <canonical/{id}>`。

**読み取り経路**: Ask / `schema_summary` / typed tools は **全 canonical graph の UNION（＋後方互換で default graph）** を読む。MCP tools は既に「`GRAPH ?g UNION default`」型（`substrate.py` 既述）なので大半は整合。**移行**: 既存の default graph 上の starrydata canonical は、(i) そのまま default として読み続ける（UNION に含める）か (ii) 一度 `…/graph/canonical/starrydata` へ MOVE する。推奨 = **(ii) 一度だけ MOVE**（全 dataset を同じ規則に揃える）。`要決定`(移行を今やるか)

#### 版（version）モデル

- **IRI 不変**（確定）。entity IRI は版をまたいで安定（再 ingest は set 意味で IRI 単位に上書き）。
- dataset version = registry meta の **単調増加整数 `version`**。(再)昇格のたびに +1。
- registry に**版ログ**（`versions: [{version, promoted_at, triple_count, alignment, bundle_ref}]`）を持つ。各版の materialize 済みバンドル（schema/RML）を registry に残し**版を再現可能**にする。
- canonical graph は**最新版の triple のみ**（現在の真実）を保持。点的（point-in-time）な過去版 triple は既定で保持しない（graph 肥大回避）。完全な過去版 triple が要るなら `…/graph/canonical/{id}/v{N}` を将来追加（今はしない）。`要決定`(点的版が要るか)
- 控えめな control 語彙（`asterism:` 名前空間）: `asterism:datasetVersion`、`asterism:status`（`active`/`retracted`/`deleted`）、`prov:generatedAtTime`、`prov:invalidatedAtTime`。registry meta ＋ control graph に記録。

#### tombstone（retract の既定）

- **物理削除しない**（確定＝引用安定性）。**control graph** `…/graph/control` に dataset 単位のマーク: `<dataset-iri> asterism:status "retracted" ; prov:invalidatedAtTime "…"^^xsd:dateTime`。
- 読み取り経路は retract 済み dataset を**既定で除外**（canonical UNION から外す or `FILTER NOT EXISTS` で control の retract マークを除外）。ただし IRI は解決可能なまま（`DESCRIBE`/直接参照は生きる＝引用が 404 しない）。
- reinstate（取り消し）= control の retract マークを除去（再び canonical UNION に入る）。

#### delete

- **draft delete**: `DROP GRAPH <draft/{id}>` ＋ registry エントリ削除。安全（未引用）。
- **canonical delete**: 既定は**非推奨**（引用 IRI が消える）。`force=true` ＋警告を要求。実行時は `DROP GRAPH <canonical/{id}>` ＋ registry 削除 ＋ control に `asterism:status "deleted"` の tombstone を残す（宙ぶらりんの引用に「削除済み」と答えられる＝沈黙させない）。既定操作は delete でなく **retract** に誘導。

#### §2 ontology graph 投影（P3 で同時に）

- 昇格時に dataset の TBox（registry の `model.yaml` / MIE のクラス・述語宣言）を `…/graph/ontology/{dataset_id}` に **RDFS/OWL** として投影（additive）。`rdfs:Class`/`rdf:Property`/`rdfs:label`/`rdfs:domain`/`rdfs:range`（MIE shape 由来）。
- `schema_summary` は **canonical(ABox) ＋ ontology graph(TBox) の両方**を読み、label/階層で回答を補強。**不変条件維持**: ontology graph 無しでも ABox 逆算で Ask は動く（TBox は enrichment、依存にしない）。

#### API 表面（新規・すべて人間ゲート / `/api/sparql` は read-only 維持）

- `POST /api/datasets/{id}/retract` → tombstone（「canonical から外す」の既定）
- `POST /api/datasets/{id}/reinstate` → tombstone 除去
- `DELETE /api/datasets/{id}?scope=draft|all&force=true` → delete（canonical は force 必須）
- 再昇格 = 既存 ingest→promote フローに **version +1** ＋（per-dataset canonical graph 採用後は）DROP+MOVE を内包。新エンドポイント不要（`promote` が版を上げる）。
- すべて内部で `OxigraphClient.sparql_update` の**スコープされた graph 操作**を使う（汎用 UPDATE passthrough は作らない＝攻撃面を増やさない）。

#### 段階（P3 内のサブステップ・各 PR CI green）

1. **canonical を per-dataset graph 化**。✅ **完了（PR #106 読み取り＋PR #107 promote 切替）**。
   - **実機検証（read-only on 実 Oxigraph :7878）**: `FROM <g>` は GRAPH-less クエリをその graph に向け、`FROM <存在しない>` は 0＝**FROM 指定時は default を読まない**ことを確認。これを踏まえ、後方互換（移行不要）の **GRAPH-union 方式**を採用: canonical 読み取り＝`{ body } UNION { GRAPH ?g { body } FILTER(STRSTARTS(?g, "…/asterism/graph/canonical/")) }`＝**default graph ∪ 各 canonical named graph、draft/control/ontology は prefix FILTER で除外**。
   - **読み取り側を全 read tool に適用済（PR #106）**: `_canonical_scope()` ヘルパ＋`schema_summary`(`_graph_clause` None 経路)・`sample_search`・`property_ranking`(本体＋count)・`template_curve_fetch`・`provenance_of`(全 OPTIONAL の phantom 行回避に必須アンカー`?e ?p ?o`を追加)。**現状は canonical graph が存在しない＝挙動不変**（default branch のみ一致）。実機で canonical-scope クエリが default の3クラスのみ返し**735万件の draft を除外**することを確認。テスト harness を `ConjunctiveGraph`(quad)化＋canonical 読取/draft 除外の新テスト。
   - ✅ **promote 切替 着地（PR #107）**: `promote_draft_to_canonical(client, draft, canonical)` が `MOVE GRAPH <draft> TO GRAPH <canonical/{id}>`（default でなく）。MOVE は宛先を置換するので**再昇格で旧版 triple が残らない**。`alignment_report` の canonical 側を canonical-scope union に。registry meta に `canonical_graph` 記録（retract/delete が target にする）。promote API レスポンスの `canonical_graph` が `canonical/{id}` に。
   - **既存 default データの移行は任意**（読み取りが default も canonical も読むため、旧 default データ・seed は読めたまま）。強制移行はせず、必要時に一括 MOVE する運用。
2. **version モデル**（registry 版ログ＋meta `version`＋control 語彙）。✅ **着地（PR #105）**: `mark_promoted` が monotonic `version` を bump し append-only `versions` ログを記録（再昇格で +1・点的版は持たない＝要決定②）。promote API が `version` を返す。substrate に lifecycle graph IRI ヘルパ（`canonical_graph_iri`/`ontology_graph_iri`・dataset-neutral 名前空間 `…/asterism/graph/`）＋control 語彙定数を追加（step 1/3/4 で使用・現状は未配線で挙動不変）。
3. **retract/reinstate**（tombstone＋読み取り除外）。✅ **完了（PR #108）**: control graph `…/graph/control` に `<canonical/{id}> asterism:status "retracted" ; prov:invalidatedAtTime …` を書き、canonical scope が `FILTER NOT EXISTS` で除外（**物理削除しない＝IRI は解決可能なまま**）。canonical scope は `substrate.canonical_scope_where()` に一本化し mcp も import（Ask/alignment が同一スコープ＝drift なし）。reinstate=control の marker 削除。新 API `POST /api/datasets/{id}/retract|reinstate`（人間ゲート・`/api/sparql` は read-only 維持）。registry meta に `status`。実 Oxigraph で retract 対応クエリの構文も確認。
4. **delete**（draft/all＋force＋tombstone 痕跡）。✅ **完了（PR #109）**: `DELETE /api/datasets/{id}?force=`。promoted（引用可能 canonical あり）は **`force=true` 必須**（既定は 409＋retract 誘導）、design/draft のみは自由削除。実行＝draft graph DROP＋（promoted なら）canonical graph DROP＋control に `deleted` tombstone（宙ぶらりん引用に痕跡）＋registry dir 削除。substrate `drop_graph`(DROP SILENT)/`tombstone_deleted`、registry `delete_dataset`。
5. **ontology graph 投影**＋`schema_summary` の TBox 補強。⬜ 未（additive・step 1 と独立に可能）。

#### 横断参照（cross-dataset link）= FROM-merge への進化（#20 P3「1+2」）

per-dataset canonical graph ＋ GRAPH-union 読み取りでは、**1つの join が2つの別 canonical graph に跨ると繋がらない**（`GRAPH ?g { A . B }` は ?g を群全体で1つに束縛＝同一 graph 内のみ）。北極星「様々なデータを共通オントロジーで紐付けて横断取得」を満たすには、canonical graph 群を **`FROM` で1つの query dataset に合体**する（`SELECT … FROM <c1> FROM <c2> … WHERE { GRAPH-less }`）。**実 Oxigraph＋rdflib で「FROM 無し＝0件／FROM 有り＝跨ぎ結合成立」を実証**。`FROM` は実 default graph を置換するので、legacy/seed を canonical graph へ一度移行（`migrate_default_to_canonical`）してから合体対象に含める。retracted は FROM リストから除外。
- 🟡 **基盤 着地（PR #110・未配線）**: `canonical_graphs(client)`（canonical 列挙・retracted 除外・sorted）／`canonical_from_clauses(graphs)`／`migrate_default_to_canonical(client, target)`／`LEGACY_DATASET_ID`。
- ✅ **配線 完了（FROM-merge wiring）**: 全 read tool（`schema_summary` graph=None／`sample_search`／`property_ranking`／`provenance_of`／`template_curve_fetch`）が GRAPH-union 廃止＝plain body＋注入 `FROM <canonical/*>`。汎用 escape（mcp `sparql_query`＋api `/api/sparql`）は共有 `substrate.canonical_merge_query()` で `FROM`＋`FROM NAMED` 注入（plain は跨ぎ結合・`GRAPH ?g` は canonical 限定＝draft 非漏洩／自前 `FROM` は尊重）。escape は実行クエリを `effective_query` で開示（demo-agent Ask 開示パネルも反映）。**raw default graph を vacate**: seed→`canonical/legacy`・watcher 既定→`canonical/legacy`・api 起動時に既存 default を `canonical/legacy` へ一度移行（ADD+CLEAR＝冪等・merge-safe）。canonical graph 不在時は FROM 空＝real default 読取（移行前も安全）。**実 Oxigraph(:7878) read-only で FROM+FROM NAMED の clause 形を実機確認**（draft 7.3M を stand-in に merged read 成立）＋**rdflib で cross-dataset JOIN（A の sample→B の paper title）成立**を実証。
- ⬜ **残**: per-read の canonical 列挙 round-trip のキャッシュ（必要時）。CI smoke（togomcp 直 default 経路）は asterism read tool 非経由のため不変。

#### この設計の `要決定`（実装着手前にユーザー確認したい点）

1. **per-dataset canonical graph 化と既存 default データの移行**を今 P3 でやるか（推奨 yes＝lifecycle の前提）。
2. **点的（point-in-time）過去版 triple** を保持するか（推奨 no＝registry の版ログ＋再現可能バンドルで足りる・graph 肥大回避）。
3. **canonical delete の force** をそもそも提供するか（推奨 yes だが既定は retract へ誘導・force は明示時のみ）。

## 4. starrydata の core からの降格

**問題**: 北極星は「starrydata に閉じない」。だが現状 core に starrydata が焼き込まれている（§現状の事実）。

**決定（提案・段階的）**: starrydata を「**core の既定**」から「**datasets/ 配下の参照実装（example）**」へ降格。core はスキーマ非依存のみ出荷。

| 対象 | 現状→達成 | 目標 |
|---|---|---|
| `DEFAULT_ONTOLOGY` 等の定数 | ✅ **P2-2a/2b 達成**: identity を `datasets/starrydata/dataset.toml` に宣言、api/mcp/watcher は汎用ローダ `load_dataset()` で解決（定数 import 撤去）。`asterism.starrydata` の DEFAULT_* は descriptor 由来＋wheel-only fallback | core は既定語彙を持たない |
| typed 4 ツール | `asterism_mcp` core（`sd:` 前提のまま） | per-dataset の typed ツール定義（§5）。core MCP は汎用 `schema_summary`/`sparql_query` のみ既定登録（**P4**） |
| QUDT マップ・seed | ✅ **P2-2b 達成**: QUDT 表＝content を `datasets/starrydata/qudt_map.yaml` の唯一の正へ（engine `qudt.py` は core 据え置き・ローダ経由読み込み・不在時 graceful）。seed（load.py）を `datasets/starrydata/seed/` へ物理移動 | `datasets/starrydata/`。表の per-dataset 化は P4 |
| demo-agent `_route` | ✅ **#18 で汎用化**（型付き無ければ LLM escape）。starrydata ルートは依然キーワード固定 | starrydata ルートは starrydata dataset の設定として注入（P4） |

- 不変条件: **既存 starrydata IRI と動作中デモを壊さない**（前提 ADR の「IRI 不変」）。降格は後方互換＝旧 import パスの薄い再エクスポートやエイリアスで段階移行。
- `要決定`: 降格の**段階**（(i) いま定数/seed を datasets/ へ移し core を汎用化 → (ii) typed ツールを per-dataset 化、の2段階を推奨）。どこまで今やるか。

## 5. typed ツールの一般化（決定論・型付きを汎用に）

product_direction「決定論・型付きを主役、LLM は escape」を**新しい系でも**成り立たせるには、starrydata 専用 typed ツールに相当するものを per-ontology に用意できる必要がある。現状それが無く、新系は LLM escape 頼みになる（#18 で埋めた汎用経路）。

選択肢 `要決定`:
- **(a) content として typed ツールを書く** — 各 dataset が「この語彙に対する典型クエリ束（MIE の sparql_query_examples 相当）」を宣言し、core がそれを typed MCP tool として公開。人手 vet＝信頼アンカーのガバナンス点（Tier0 追加と同じ哲学）。
- **(b) スキーマからテンプレ生成** — schema_summary ＋ MIE から決定論的に typed クエリを生成（#18 の選択肢で見送った案）。汎用だがテンプレに収まらない問いは扱えない。
- 推奨 = **(a) を主、(b) を補助**。引用できる事実は人が vet した typed ツールで、それ以外は #18 escape。

含意: これが入ると「型付き主役・LLM escape」が **starrydata 以外でも**成立する。#18 escape はその時も「未整備な語彙への入口」として残す。

### 5.1 P4 実装（(a) 宣言エンジン・段階着地）

**設計の発見**: 既存 typed 4 ツールは2種に分かれる。① **クエリツール**（`sample_search`/`property_ranking`＝MIE `sparql_query_examples` 形＝パラメータ付き SELECT→表）は完全に宣言化できる。② **表示ツール**（`provenance_of`/`template_curve_fetch`＝SPARQL＋非自明な Python 後処理＝chain 構築／JSON 配列復号）は純宣言化が難しい。§5(a) の「sparql_query_examples 相当」は①が対象。

- ✅ **P4-1 エンジン着地（query_tools engine・additive）**: `asterism.query_tools`（schema-agnostic engine）＝ `datasets/{name}/query_tools.yaml` を読み、宣言ツール（name/params/SPARQL テンプレ/result マッピング）を **型安全バインド**（string→escape literal／number・integer→検証＋clamp／iri→検証 `<IRI>`／enum→whitelist・**生連結なし＝injection 不可**）し、ミニテンプレ（`{{p}}` スカラ＋`{{#p}}`/`{{^p}}` 任意ブロック）を render→**canonical FROM-merge** で実行（typed ツールも Ask と同一の cross-dataset scope）。テンプレは load 時に read-only(SELECT/ASK) 検証。信頼モデル=Tier0 と同じ（人手 vet・実行時生成なし）。**starrydata を content 化**: `datasets/starrydata/query_tools.yaml` に `property_ranking`/`sample_search` を宣言し、**content 駆動＝既存ハードコード版と結果一致を equivalence test で証明**（generality の証明・ハードコード版は温存=additive）。cross-dataset join も実 rdflib で実証。ingest 141/mcp 38 緑。
- ⬜ **P4-2 残（次 PR）**: 宣言ツールを **MCP tool として動的公開**（FastMCP 3.x は signature 由来 schema＝`__signature__` 合成 or explicit-schema パスで動的登録）＋demo-agent の `_route` を **dataset-driven**（starrydata キーワード固定をやめ宣言ツールから routing）。続いて hardcoded 4 ツールを content へ委譲（または置換）。② 表示ツール（provenance/curve）の一般化は別途。QUDT 表 per-dataset 化（§4）もここで合流可。

## 6. 段階・残課題・要決定一覧

**段階（提案）**:
- P1（小・低risk）: §1 の用語を ROADMAP/UI/Gallery に反映（概念図常設）。delete/retract の**設計のみ**確定。
- P2: §4-(i) starrydata 定数/seed を `datasets/starrydata/` へ、core を汎用化（後方互換）。
  - **実装上の発見**: `asterism.starrydata` は定数だけでなく **starrydata 専用 ingester（~840 行）と汎用テキスト/パースヘルパが同居**し、多数の module（functions/watcher/mcp/api/step0）が import している。よって P2 は「移動」ではなく**untangling**。安全な順で刻む。
  - ✅ **P2-1 完了**: 汎用ヘルパ（`slugify`/`parse_issued`/`parse_float_array`/`strip_quoted`/`safe_url`）を新 `asterism.text` に抽出。`asterism.starrydata` は後方互換で re-export。Tier0 `functions.py` は `asterism.text` を直接 import（**汎用 core が starrydata module に依存しなくなった**）。ingest 94 / mcp 29 / demo-agent 9 緑。
  - ✅ **P2-2a 完了**: starrydata の identity（ontology/resource/graph/agent IRI）を **content として `datasets/starrydata/dataset.toml` に宣言**。汎用ローダ `asterism.datasets.load_dataset(name)`（`ASTERISM_DATASETS_ROOT` or 親方向探索、best-effort=None 退行）を追加。`asterism.starrydata` の `DEFAULT_*` 定数は descriptor から取得（不在時は埋め込みリテラルにフォールバック=wheel-only install 対応）。contract test で descriptor＝SoT を固定。**将来の非 starrydata dataset は dataset.toml を置くだけで identity を宣言できる**＝engine 側にハードコード不要。ingest 98/mcp 29/demo-agent 9 緑。
  - ✅ **P2-2b 完了**（方針=**実装コスト度外視・プロダクト理想**: engine/content 完全分離＋content が本番の唯一の正）:
    - 定数 import 撤去（PR #97）: api `Settings`・mcp `tools.py`・`watcher.py` が `asterism.starrydata` の `DEFAULT_ONTOLOGY`/`DEFAULT_RESOURCE`（watcher は graph_base も）の定数 import をやめ、汎用ローダ `load_dataset("starrydata")` で descriptor 解決（env override 維持・wheel-only fallback）。starrydata *ingester*（IngestConfig/ingest_*）依存は engine-for-starrydata として残置（per-dataset 化は P4）。
    - **datasets/ を 3 image に同梱**（PR #97）: upload-api / asterism-mcp / demo-agent に `COPY datasets` ＋ `ASTERISM_DATASETS_ROOT`。本番でも descriptor・QUDT 表が content の唯一の正として live。`.dockerignore` で生成 seed を除外。
    - QUDT 表 content 化（PR #98）: `qudt_map.yaml` を `datasets/starrydata/` の唯一の正へ。**engine `qudt.py` は core 据え置き**（`functions.py` Tier0 が既に core engine として import）、ローダ経由読み込み・不在時は警告＋空 map で graceful degrade。pkg `artifacts` 同梱コピー廃止（二重管理回避）。**判断**: code=engine は core / table=content は datasets/。表の per-dataset 化（今は `starrydata` に規約 key）は P4 で typed ツール一般化と合流。
    - seed 物理移動（PR #99）: `demo-agent/seed/{load.py,.gitignore}` → `datasets/starrydata/seed/`。参照（compose.demo.yaml / make_demo_subset.py / verify_demo.py / DEMO.md）更新。
    - ingest 100 / mcp 29 / api 33 / demo-agent 9 緑、各 CI green でマージ。
- P3: §3 のライフサイクル実装（再昇格・retract・delete・版）＋ §2 の ontology graph 投影。**実装設計は §3.1 に詳細化済み（提案・未実装）**。着手前に §3.1 末尾の `要決定` 3 点を確認。
- P4: §5-(a) per-dataset typed ツール機構。**P4-1 エンジン着地（§5.1）**: `asterism.query_tools` 宣言エンジン＋starrydata content 化＋equivalence 証明（additive）。残=P4-2 MCP 動的公開＋demo-agent dataset-driven routing。
- 並行: 2個目の実 dataset（非 starrydata）を P2 の検証として投入（#19 と接続）。

**確定事項（2026-06-03 ユーザー確定・すべて推奨どおり）**:
1. §2: TBox は **別 `graph/ontology/{name}`** に投影（canonical に混ぜない）。schema_summary は両 graph を読む。
2. §3: **IRI 不変＋dataset version** で世代管理。**retract = tombstone（無効化マーク）を既定**（canonical からの物理削除は原則しない＝引用安定性を守る）。
3. §4: starrydata 降格は **今回 P2 まで先行**（定数/seed/QUDT を `datasets/starrydata/` へ移し core を汎用化・後方互換）。typed ツール per-dataset 化（P4）は後。
4. §5: typed ツール一般化は **(a) content 宣言を主**、**(b) スキーマ生成を補助**。引用できる事実は人が vet した typed ツール、それ以外は #18 escape。

---

関連: [[ontology-mapping-boundary-and-provenance]] [[phase5-workbench-materialize-gate]] [[phase5-declarative-substrate]] [[product-direction-citable-facts]]
