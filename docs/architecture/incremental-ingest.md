# 追記/差分取り込み（incremental ingest・装置が出し続ける CSV をライブに育てる）

決定母体:
[`scalable-declarative-ingestion.md`](scalable-declarative-ingestion.md)（ストリーミング＋背景ジョブ＋part5 バージョン付きグラフ・promote フラグ化）/
[`phase5-workbench-materialize-gate.md`](phase5-workbench-materialize-gate.md)（#15 人間ゲート・draft 隔離）/
[`ontology-canonical-lifecycle.md`](ontology-canonical-lifecycle.md)（IRI 不変・物理削除回避・retract/version）/
[`phase2-watcher.md`](phase2-watcher.md)（ファイル投下ウォッチャ）/
[`crosswalk-hub.md`](crosswalk-hub.md)（育つ橋・派生プロジェクション）

status: **設計確定 → 追記(append)経路 最小実装済**（substrate `run_append_ingest` ＋ api `POST /api/datasets/{id}/append` ＋ registry `mark_appended`／追記式 source 永続化）。実 morph-kgc + 実 Oxigraph で「CSV 追加 → トリプル増加・既存 IRI 不変」を実証済。

---

## 0. 何を直すか

今の宣言経路の取り込みは **スナップショット置換**である。

- `attach_source` は dataset の source 集合を **rmtree でリセット**してから保存する（＝「足す」ができず「置き換える」）。
- `ingest` は source 集合 **全体**を Morph-KGC で再マテリアライズし、新しいバージョングラフ `canonical/{id}/v{n}` へストリーム投入する。
- `promote` が `liveGraph` ポインタを `v{n}` に差し替え、旧版は背景でドロップ。

これは「DB ダンプを定期的に丸ごと再エクスポートする」型のソースには正しい。IRI が**決定論的な複合キー**なので、同じ行は同じ IRI を生み、再取り込みは冪等。だが —

**ZEM 等の測定装置は CSV を連続的に吐き続ける。** 1 バッチごとに source 全体を再マテリアライズすると、コストは毎回 **O(これまでの全行)** になる（追記のたびに二乗的に重くなる）。`attach_source` のリセット仕様では、そもそも「バッチを足す」ことができない。

ゴール: **新規バッチだけを O(新規行) でマテリアライズし、既存の canonical 版にそのまま追記して、トリプルをライブに育てる**追記経路を、信頼モデル（生成コード非実行・Tier0 関数のみ・read-only 不変）を一切崩さずに用意する。

---

## 1. 用語: append（追記）と delta（差分）を分ける

「差分取り込み」には2つの異なる意味があり、難易度も安全性も違う。混同しないために分ける。

| 種別 | ソースの性質 | 必要な操作 | 本 ADR の扱い |
|---|---|---|---|
| **append（追記のみ）** | 行は**増えるだけ**。既存行は編集も削除もされない（装置の連続ログ＝典型） | 新規バッチをマテリアライズ → 既存 canonical 版へ **POST-merge で追記** | ✅ **実装する**（本丸） |
| **delta（更新・削除あり）** | 既存行が**書き換わる/消える**（マスタ更新・訂正） | 旧トリプルの**特定と削除** ＋ 新トリプルの追加 | ⛔ **追記経路では行わない**（§4 で代替を示す） |

装置フィードは定義上 append（過去の測定は不変・新しい測定が増える）。これが「ライブに育てる」の実体。**更新・削除は append では扱わない** ── 理由は §4。

---

## 2. 決定（方式）

| # | 論点 | 決定 | 理由 |
|---|---|---|---|
| **A1** | 追記の単位 | **新規バッチ CSV だけをマテリアライズ**（source 全体ではない）。Morph-KGC を**そのバッチ1ファイル**に対して実行 | コストを O(新規行) に。スナップショット再取り込みの O(全行) を回避 |
| **A2** | 投入先 | 新版グラフを作らず、dataset の **現 live グラフ**（`liveGraph` が指す `canonical/{id}/v{n}`・pre-part5 は key グラフ）へ **Graph Store POST で追記（merge）** | 「既存 canonical 版に追記」（ゴール (b)）。ポインタ差替も DROP も不要 ＝ ライブに育つ |
| **A3** | 冪等性 | IRI が決定論的複合キーなので、**同一行は同一トリプル** → Oxigraph の集合意味論で **merge 時に dedup**。同じバッチを二度追記しても entity トリプルは増えない | §3 の前提。再送・重複ファイルに強い |
| **A4** | 信頼モデル | **不変**: 同じ substrate（Morph-KGC が RML を解釈）＋ **Tier 0 関数のみ**。生成コードは走らない。追記は `materialize_to_nt_file` → `stream_nt_file_to_oxigraph`（既存の vetted 経路）の再利用 | [`ingestion-execution-safety.md`](ingestion-execution-safety.md) を覆さない |
| **A5** | read-only エスケープ | **不変**: 追記は Graph Store Protocol の **POST（書込専用の取込面）**で行う。任意 SPARQL `UPDATE` は使わない。`/api/sparql` は read-only のまま | store/MCP split の露出方針を守る |
| **A6** | 人間ゲート | 追記の前提＝dataset が**既に promoted**（live グラフが存在）。スキーマと初版データは promote 時に**一度人間がゲート済**。以降の同型バッチがライブに育つことは、その live フィードへ追記する**明示的行為そのもの**がゲート | per-バッチの promote ゲートは**意図的に外す**（§5 でトレードオフ明記）。snapshot dataset が事故で育たないよう、追記は promoted を必須にし専用エンドポイントで意図を明示 |
| **A7** | 再現性（source 蓄積） | 追記バッチ CSV は source 集合へ**追記式に永続化**（リセットしない・名前衝突は seq 付与で回避）。後でスナップショット再取り込みすれば**全バッチを再生成**できる | registry バンドル＋source 集合が graph を完全再現（citable-facts 方針）。append＝速い増分投影、snapshot＝再ベースライン |
| **A8** | provenance/版 | 追記イベントは registry meta の **`appends` ログ**（`versions` と対）に記録: `{seq, source_files, triples_in_batch, appended_at}`。data グラフは**純マテリアライズ結果のまま**（手書きトリプルを混ぜない） | 来歴・版管理は control グラフ＋meta が持つ既存パターンを踏襲。alignment を汚さない |
| **A9** | crosswalk 連携 | 追記で canonical scope が変わる（新組成の出現）→ ハブは stale。応答に `crosswalk_stale` を返し、**冪等な再ビルド**（PUT 置換）契約を明示（§7） | ハブは派生プロジェクション＝再読込で育つ。高頻度フィードは debounce 再ビルド |

### 2.1 append と snapshot の関係（LSM 的再ベースライン）

```
append（速い増分・O(新規)）           snapshot（再ベースライン・O(全件)）
  device → batch.csv                     source 集合（全バッチの和）を
  → materialize(batch)                   丸ごと再マテリアライズ
  → POST-merge → live graph v_n          → 新版 v_{n+1} へストリーム
  （ライブに育つ・dedup）                 → promote が live を差替・v_n をドロップ
        │                                       ▲
        └── source 集合へ batch を蓄積 ──────────┘
            （append が貯めた CSV を snapshot が読む）
```

append は live グラフへの**断片的な追記**を貯めていく。長く回すと（断片化・version の説明責任の観点で）たまに snapshot 再取り込みで **コンパクション/再ベースライン**すると綺麗になる ── これは**既存の snapshot 経路そのまま**。IRI 決定論により両者は**同じトリプル集合に収束**する。append-mode と snapshot を混ぜても、source 集合が全バッチの和である限り矛盾しない。

---

## 3. 冪等性の前提（なぜ追記が安全か）

追記が安全なのは IRI が**決定論的**だから:

- subject/object IRI は RML テンプレートが**キー列**から組み立てる（複合キー）。同じ入力行 → 必ず同じ IRI → 同じトリプル。
- Oxigraph は名前付きグラフ内で**集合意味論**（同一トリプルは1つ）。よって `live` グラフへの POST-merge は、
  - **新規行** → 新トリプルが増える、
  - **再送された既存行** → 同一トリプルなので**何も増えない（dedup）**。
- ⇒ 既存トリプル・既存 IRI は**触らないので自明に不変**（追記はトリプルを足すだけ・既存を消さない）。これがゴール3「既存 IRI 不変」の根拠。

**前提が崩れる条件**（RML 作者が避けるべき・T9 と同じ規律）:
- 非決定論的な IRI 生成（blank node のままにする・行内容に依らない UUID をミントする）→ 同じ行が毎回別 IRI になり dedup が効かず**重複が増殖**する。substrate の IRI スキームは**キー列由来テンプレート**で決定論的なので問題ないが、append-mode の RML はとくに「行のキーから安定 IRI を作る」ことが要件。
- provenance を **RML 内で**バッチ非依存にミントする（例: 取込時刻を IRI に含む activity を RML が吐く）→ バッチごとに別ノードが増える。本 ADR は provenance を **data グラフでなく meta `appends` ログ**に持たせる（A8）ので、data グラフは純粋に冪等。

---

## 4. なぜ update/delete を追記経路でやらないか（重要な設計判断）

「既存行が書き換わった／消えた」を反映するには、live グラフから**特定のトリプルを削除**する必要がある。これは却下する:

1. **citation 安定性に反する。** [`ontology-canonical-lifecycle.md`](ontology-canonical-lifecycle.md) §3 確定② で、canonical の物理削除は引用を壊すので避ける、と決めている。live グラフから surgical に DELETE すると、既に出した引用 IRI が解決しなくなる。
2. **read-only エスケープ面を侵食する。** 行レベル delete は store への `DELETE`（任意 UPDATE）を必要とし、A5（取込は POST-merge のみ・SPARQL は read-only）を崩す。
3. **diff の計算が高い。** 「どのトリプルが消えたか」を知るには旧マテリアライズと新マテリアライズを突き合わせる必要があり、O(全件) の差分計算 ＝ append の O(新規) 利点が消える。

**代替（採用）= snapshot 再バージョン。** 更新/削除を伴う訂正は、**source 集合を正しい全体に直して snapshot 再取り込み**する（新版 `v_{n+1}` を作り、promote が live を差替、旧版を背景ドロップ）。これは既存経路で、

- 新版は**正しい全体**（訂正後）を持つ。
- 旧版は**ドロップされるまで配信され続ける**（ギャップなし・part5）。
- IRI は不変（同じキー → 同じ IRI）。消えた行はそのバージョンに現れないだけ。
- 旧引用は、必要なら version ログ＋retract/tombstone で「この版で消えた」と説明できる。

⇒ **append = 成長（行が増える）。snapshot 再バージョン = 訂正/削除。** 役割を分けることで、append を O(新規)・冪等・read-only 整合に保てる。

---

## 5. 可視性と一貫性のトレードオフ（明示）

| | snapshot（version＋pointer swap） | append（live グラフへ POST-merge） |
|---|---|---|
| 切替の原子性 | **原子的**（promote が `liveGraph` を O(1) 差替） | **非原子的**（チャンク追記が逐次見える） |
| 読み手が見るもの | 旧版 or 新版（torn read なし） | 「新規トリプルの一部がまだ見えない」ことはあるが、**単調増加なので古い/壊れたデータは見えない** |
| コスト | O(全件)/取込 | **O(新規)/バッチ** |
| liveness | promote まで不可視 | **即ライブ**（A6 のゲート緩和込み） |

append は**原子的スナップショット可視性**を**「O(新規) ＋ 即ライブ」**と引き換える。装置フィード（測定は単調に増える・最新を早く見たい）には正しい取引。厳密な原子切替が要る訂正は snapshot（§4）。

---

## 6. ウォッチャ連携

既存の [Phase2 ウォッチャ](phase2-watcher.md) は starrydata 専用（`papers/samples/curves` の kind ごとにハードコード ingester → `canonical/legacy` へ追記）で、**すでに append モデル**（POST-merge・冪等・1 ingest = 1 IngestionActivity）を体現している。本 ADR の汎用 append は、これを**宣言経路（RML・任意 dataset）へ一般化**する。

```
<drop_root>/datasets/<id>/*.csv      （dataset ごとの投下口）
   │  watchfiles awatch + settle（部分書込ガード・既存 _settle 流用）
   ▼
[watcher] settled CSV を1バッチとして
   → run_append_ingest(rml, batch_dir, client, id, live_graph)   ← エンドポイントと同じ seam
   → live グラフへ POST-merge（O(新規)）
   → jobs.jsonl に追記イベント
```

`POST /api/datasets/{id}/append` と watcher は **同じ orchestration 関数 `asterism.substrate.run_append_ingest`** を呼ぶ（seam を1つに）。装置やスクリプトは**エンドポイントへ直接 POST** してもよい（長寿命 watcher を立てずに済む）。per-dataset の投下口を持つ汎用 watcher 配線は P4 スコープ（既存 watcher の kind 配線を dataset 配線へ一般化）として残す ── 本 ADR は seam（`run_append_ingest`）とエンドポイントを先に確定する。

---

## 7. crosswalk 再ビルドフック

[crosswalk ハブ](crosswalk-hub.md)は canonical scope から**観測を読んで派生**するプロジェクション（共有組成 → `xw:Composition`）。追記で新しい組成が canonical に出ると、ハブは stale になる。

- 追記は応答に **`crosswalk_stale: true`** を返す（呼び手が再ビルドを判断できる）。
- 再ビルドは**冪等**: `build_turtle`（tested pure ライブラリ）で現 canonical scope から観測を読み直し、ハブ graph を **PUT で置換**（`experiments/crosswalk-hub/build.py` の経路）。追記 N 回ぶんをまとめて1回で吸収できる。
- トリガ方針: 高頻度フィードでは**追記ごとに再ビルドしない**（debounce / 閾値 / オンデマンド）。ハブは「>= 2 dataset で共有」の値だけをミントするので、再ビルドコストは観測数に比例し O(新規) とは別系統 ── まとめて回すのが正解。

MVP は `crosswalk_stale` を返し再ビルド契約を明文化するに留める（ハブの api/MCP 配線は未実装＝spike のまま）。配線後は debounce 再ビルドをこのフックに接続する。

---

## 8. データフロー（縦串・追記経路）

```
[装置/スクリプト/将来の per-dataset watcher]
        │  batch.csv（新規測定だけ）
        ▼
POST /api/datasets/{id}/append   （前提: dataset は promoted ＝ live グラフあり）
        │  ① batch を source 集合へ追記式に永続化（A7・再現性）
        ▼  ② materialize（このバッチだけ・Tier0 のみ・生成コード非実行）
[substrate] Morph-KGC が batch.csv の RML を解釈 → N-Triples ファイル（メモリ有界）
        │
        ▼  ③ POST-merge（追記）
[substrate] N-Triples を live グラフ <canonical/{id}/v{n}> へストリーム
        │   Graph Store POST = merge ＝ 既存トリプルは dedup・新規だけ増える
        ▼
[Oxigraph] live グラフが育つ（即ライブ・Ask が引用可能）
        │
        ▼  ④ registry meta に追記イベント記録（appends ログ・triples_in_batch）
[応答] {live_graph, triples_in_batch, dataset, crosswalk_stale}
```

---

## 9. 安全条件チェックリスト（不変条件を一つも崩さない）

- [x] **生成コード非実行**: 追記は既存 substrate（Morph-KGC + Tier0）の再利用。新しい実行面はない（A4）。
- [x] **Tier0 関数のみ**: バッチ用 RML も同じ T9 閉集合検証下。エンジン無改修。
- [x] **read-only 不変**: 追記は Graph Store POST（取込面）。`/api/sparql`・MCP `sparql_query` は read-only のまま。任意 UPDATE なし（A5）。
- [x] **draft 隔離**: 追記先は**既に promoted な** live グラフ（人間ゲート済の citable フィード）。未 promote の dataset には追記できない（promoted 必須・409）。snapshot dataset の事故昇格を防ぐ（A6）。
- [x] **IRI 不変**: 追記はトリプルを足すだけ・既存を消さない → 既存 IRI は自明に不変（§3）。
- [x] **再現性**: バッチ source を蓄積（A7）。snapshot 再取り込みで全体を再生成可能。

---

## 10. スコープと残件

**本 ADR で実装（最小・実）**:
- `asterism.substrate.run_append_ingest(...)` ── append の orchestration seam（materialize batch → live グラフへ POST-merge）。エンドポイントと watcher が共有。
- `POST /api/datasets/{id}/append` ── 前提検証（promoted・RML あり・バッチ必須）→ source 追記永続化 → run_append_ingest → `mark_appended`。
- `registry.mark_appended(...)` ＋ 追記式 source 永続化（リセットしない・名前衝突回避）。
- ingest/api テスト ＋ 実 morph-kgc + 実 Oxigraph 実証（CSV 追加 → トリプル増・既存 IRI 不変・同一バッチ再追記で増えない）。

**残件（後続）**:
- per-dataset 汎用 watcher 配線（投下口 → append）。本 ADR は seam を確定済（P4）。
- crosswalk 再ビルドの api/MCP 配線＋debounce トリガ（§7）。
- 大バッチの背景ジョブ化（現状は同期・小バッチ前提。ingest と同じ JobManager + SSE に載せ替え可能）。
- live グラフの正確な triple 数の安価な追跡（現状 meta は `triples_in_batch` を記録・正確な総数は必要時に SPARQL COUNT）。
