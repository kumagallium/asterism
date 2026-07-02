# スケーラブルな宣言的取り込み（大規模データ対応・設計メモ）

決定母体: [`phase5-workbench-materialize-gate.md`](phase5-workbench-materialize-gate.md)（#15 人間ゲート・draft 隔離）/ [`phase5-declarative-substrate.md`](phase5-declarative-substrate.md)（substrate・Tier 0 関数）
status: 設計確定（決定事項あり）→ 取り込み(ingest) S1-S3 実装済 ＋ **昇格(promote) フラグ化 実装済（§promote）** ＋ **part5 バージョン付きグラフ実装済（§promote/part5・replace/delete の大 DROP もクリティカルパス外へ）**

## 0. 何を直すか

#15 の対話 ingest（`POST /api/datasets/{id}/ingest`）は **設計サブセット向け**にしか動かない。実データ（例: 全 starrydata = curves 23.3万行 / samples 14.4万行 / papers 5.6万行 ＝ 約320MB）を投入すると、morph-kgc は **593万トリプルを 54 秒で生成**できる一方、その後の **Oxigraph への一括 POST が 300 秒でも完了せず 504**（実測）になる。

starrydata は**全件こそが本体**であり、「サブセットしか入らない」のは製品として成り立たない。その場しのぎ（タイムアウト延長）ではなく、**宣言経路そのものを大規模対応に作り直す**。

## 1. 現状の3つの天井（なぜ単一 POST は破綻するか）

`materialize_to_graph` → `ingest_graph_to_oxigraph` の現経路:

1. **全グラフをメモリに保持**: morph-kgc `materialize()` は内部で N-Triples 文字列の `set` を作り、**さらに巨大文字列へ連結して rdflib `Graph` にパース**する（三重コピー）。数百万トリプルで数 GB。
2. **巨大な1回 POST**: その `Graph` を turtle 文字列へ直列化し、**1 リクエスト**で `/store` へ送る。メモリ＋リクエストサイズの天井。
3. **同期・進捗なし**: HTTP リクエストを塞いだまま実行。タイムアウト依存で、UI は「取り込み中…」のまま**無進捗**。

## 2. 決定（方式）

ユーザー確定（2026-06-06）= **「ストリーミング＋背景ジョブで一本化」**。同じ「取り込み」ボタン1つが**数行〜全件までスケール**し、**実進捗**が見える。3つの天井を順に外す。

| # | 論点 | 決定 | 理由 |
|---|---|---|---|
| D1 | morph-kgc の出力先 | **N-Triples ファイルへ書き出し**（メモリに全グラフを溜めない）。morph-kgc 自身が `output_file` / グループ単位ファイル書き出しを持ち、ログも「大規模は CLI で」と推奨 | 天井①を撤去。rdflib グラフ＋巨大文字列の三重コピーをやめる |
| D2 | Oxigraph への投入 | **N-Triples ファイルをチャンク（N 行ずつ）に分けて `/store?graph=…` へ逐次 POST**（Graph Store Protocol の POST は追記）。各チャンクは小さく、進捗を出せる | 天井②を撤去。1回巨大 POST をやめ、per-request サイズを有界化 |
| D3 | 実行モデル | **背景ジョブ化（既存 `JobManager` 流用）＋ SSE 進捗**（生成フェーズ → 投入フェーズ %）。HTTP は即 202 + job_id を返す | 天井③を撤去。リクエストを塞がず、リロード/切断でも replay 復帰（propose と同じ） |
| D4 | 信頼モデル | **不変**: 宣言経路のみ（morph-kgc が RML 解釈）・呼べるのは Tier 0 関数（`asterism.functions`）だけ・**生成コードは走らない**。morph-kgc を CLI/関数どちらで回しても RCE 面は同一（vetted ライブラリ＋宣言入力） | [`ingestion-execution-safety.md`](ingestion-execution-safety.md) を覆さない |
| D5 | draft 隔離 | **不変**: 投入先は `…/graph/draft/{id}`（隔離）。チャンク投入でも同一 graph に追記。Ask は既定 canonical のみ参照 | #15 D2 を維持。citable-facts 方針を守る |
| D6 | 失敗時の原子性 | チャンク投入の途中失敗は **draft graph を DROP してから再試行**（部分投入を残さない）。job は error イベントで終了 | draft は使い捨て・再投入安全（再昇格前提）なので DROP-and-retry で十分 |

## 3. データフロー（縦串・改修後）

```
[カタログ/ワークベンチ「取り込み」]  →  POST /api/datasets/{id}/ingest  → 202 {job_id}
        │（背景ジョブ・to_thread）
        ▼  ① 生成フェーズ
[substrate] morph-kgc が RML を解釈実行（Tier 0 関数のみ）
        │   → N-Triples を **ディスクのファイル**へ書き出し（メモリ有界）
        │   SSE: {phase:"materialize", triples: 5_931_668}
        ▼  ② 投入フェーズ
[substrate] N-Triples ファイルを N 行ずつ読み、チャンクを
        │   POST /store?graph=draft/{id} へ逐次（追記）
        │   SSE: {phase:"upload", done: 2_400_000, total: 5_931_668}  ← 実進捗
        ▼
[Oxigraph] draft named graph に蓄積（隔離）
        │
        ▼  done: {triple_count}
[UI] 進捗バー → 完了 → 「共有データに昇格」へ
```

## 4. 代替案と却下理由

- **単一 POST のタイムアウト延長のみ**（現 #129）: その場しのぎ。メモリ天井（数 GB）も残り、全件は 504。**安全網としては残す**が解ではない。
- **Oxigraph オフライン・バルクローダ**（`oxigraph load`・最速）: store ファイルへ直接ロードするため **api と oxigraph の co-location（store パス共有）が前提**。別コンテナ構成では使えない。将来、自前ホスト Oxigraph と同一ノードに置く場合の最適化として保留（D2 の HTTP チャンクは構成非依存で常に動く）。
- **morph-kgc `materialize_oxigraph`**（in-process pyoxigraph Store へ）: リモート HTTP Oxigraph には直接効かない（in-process store をさらにダンプ→送出が要る）。D1+D2 と等価以上の利点が無いので不採用。

## 5. スケール特性（どこまで伸びるか・残る限界）

- **生成（morph-kgc）**: CSV を pandas で読むため、ピークメモリは概ね **入力 CSV サイズ＋最大マッピンググループ**に比例（全出力ではない）。320MB CSV / 593万トリプルは実機で 54 秒・許容内。さらに大きい CSV は morph-kgc の chunksize / DB ソース等が次の打ち手（本 ADR の範囲外）。
- **投入（チャンク POST）**: per-request メモリは **チャンク行数**で有界（例 5 万行/チャンク）。総時間は Oxigraph の取り込み速度に比例し、進捗が見えるので無進捗ハングにならない。
- **本番の最大規模**（自前ホスト・co-location 可能時）は D2 を Oxigraph バルクローダに差し替える余地を残す（インターフェース `stream_*_to_oxigraph` の実装差し替えで吸収）。

## 6. 段階実装

1. **S1 substrate ストリーミング**（本筋の核）: `materialize_to_nt_file(rml, csv_dir) -> Path`（morph-kgc ファイル出力・rdflib グラフ非生成）＋ `stream_nt_file_to_oxigraph(path, client, graph_iri, *, chunk_lines, on_progress) -> int`（チャンク追記 POST・進捗コールバック）。既存 `materialize_to_graph`/`ingest_graph_to_oxigraph` は後方互換で温存（小規模・テスト用）。
2. **S2 背景 ingest ジョブ**: `JobManager` に ingest ジョブ種別を追加。`POST …/ingest` を 202 + job_id 化、`GET …/jobs/{id}/stream` で SSE 進捗（materialize/upload フェーズ）。失敗は D6 の DROP-and-error。
3. **S3 UI 進捗**: `IngestControl` を SSE 購読化（`JobProgress` 相当の進捗バー＋triples カウント）。job_id を sessionStorage 保存しリロード復帰（propose と同じ）。
4. （任意・後続）co-location 時の Oxigraph バルクローダ経路（D2 実装差し替え）。

## 7. 不変条件（破らない）

- 生成コードを実行しない（宣言経路・Tier 0 のみ）。IRI 不変。
- draft 隔離・Ask は既定 canonical のみ。`/api/sparql` は read-only。
- 既存の小規模経路（`materialize_to_graph`）とテストは温存（additive）。

## §promote. メモリ有界な昇格（MOVE 廃止・フラグ化）

status: 実装済み（substrate + api + registry・全 backend テスト緑）

### 何を直すか

取り込み（§2-6）を S1-S3 で memory-bounded 化したが、**昇格（promote）が残った OOM の主犯**だった。旧 promote は `MOVE GRAPH <draft/{id}> TO GRAPH <canonical/{id}>` を実行し、Oxigraph はこれでグラフ全体をメモリ展開する（実測 **~1.5 GB / 100万トリプル**で線形増大）。全 starrydata（約1,200万）では ~18 GB を要求して使い捨て 8GB 上限 Oxigraph を OOM-kill し、実機では Docker VM ごと枯渇させて **PC をフリーズ**させた（ROADMAP 2026-06-08）。データ量でなく**操作**が主犯。`DROP GRAPH` も大グラフで重い。

### 決定（方式）

**MOVE/大 DROP をクリティカルパスから排除し、draft↔canonical をメタ操作（フラグ）化する。**

| # | 論点 | 決定 |
|---|---|---|
| P1 | 投入先 | ingest は最終 **per-dataset canonical graph `…/graph/canonical/{id}` へ直接ストリーミング**（draft graph を作らない）。S1-S3 のストリーミングそのまま＝~2 GB 一定。 |
| P2 | citability の表現 | **control graph のフラグ** `<canonical/{id}> asterism:status "promoted"`。グラフの存在でなくフラグが Ask scope を決める。 |
| P3 | citable scope (FROM-merge) | `canonical_graphs()` を「control の **promoted** マーカ列挙」に変更（`SELECT ?g WHERE { GRAPH <control> { ?g asterism:status "promoted" } }`）。**O(#datasets)・triple 非走査**（旧 name-index 走査の perf 問題も解消）。未 promoted（フラグ無）／retracted／deleted は不在＝draft 隔離はフラグで担保。 |
| P4 | promote | **control に1トリプル立てるだけ（`mark_graph_promoted`）= O(1)・MOVE/COUNT なし**。triple 数は ingest 時に registry meta へ記録した値を読む。 |
| P5 | 再取り込み(replace) | フラグ clear（`clear_status`・un-publish）→ DROP → re-stream → 要再 promote。フラグを先に落とすので「半置換のグラフが Ask に見える」窓が無い。 |
| P6 | retract / reinstate / delete | retract=status→`retracted`、reinstate=status→`promoted`（フラグ必須なので「マーカ削除」でなく promoted を立て直す）、delete=canonical graph を DROP＋`deleted` tombstone。 |

不変条件は不変: 生成コード非実行・Tier 0 のみ・IRI 不変・read-only `/api/sparql`・**未 promoted は引用されない**（隔離は MOVE でなくフラグで担保）。

### ライフサイクル

```
ingest      → canonical/{id} へ直接 stream（control にフラグ無＝Ask 不可視）
promote     → control に <canonical/{id}> status "promoted"（O(1)）→ Ask 可視
retract     → status "retracted"（Ask から除外・IRI/データは残る）
reinstate   → status "promoted"
re-ingest   → clear_status → DROP → re-stream → 要再 promote
delete      → DROP canonical/{id} ＋ status "deleted" tombstone
```

### アップグレード安全網（既存データ）

citability がフラグ依存に変わるため、旧 MOVE 方式で昇格済みのデータは起動時に promoted フラグを backfill しないと不可視になる。api の lifespan で **registry の `promoted=True`（かつ未 retracted）な各 dataset の canonical graph と、legacy bulk graph（データがある時のみ・cheap ASK で判定）にフラグを冪等 backfill** する。毎起動安全（未 promoted な draft は registry が promoted=False なので決して立てない／retracted は据え置き）。

### part5: バージョン付きグラフ（replace/delete の大 DROP をクリティカルパス外へ）

status: 実装済み（substrate + api + registry + 背景スイーパ・全 backend テスト緑）

§promote のフラグ化で初回 ingest→promote は MOVE/大 DROP ともゼロになったが、**再取り込み(replace)/delete はまだ大 DROP をクリティカルパスに残していた**（#134 で 300s timeout・DROP は MOVE と違いメモリ非複製ゆえ OOM 主犯ではないが大グラフで重い）。part5 でこれも外す。

**バージョン付きデータグラフ＋ポインタ切替＋背景ドロップ:**

| # | 論点 | 決定 |
|---|---|---|
| V1 | データグラフ | 各 ingest を**新しいバージョングラフ `canonical/{id}/v{n}`** へストリーミング（`n` は registry の単調 `data_seq`・ドロップ後も再利用しないので衝突しない）。`canonical/{id}` は **control の subject(=dataset key)** で、(part5 前に昇格した dataset では) データグラフも兼ねる。 |
| V2 | citable ポインタ | control graph が dataset key に `liveGraph <…/v{n}>` を持つ。`canonical_graphs()` は **`COALESCE(liveGraph, key)`**＝promoted な各 dataset の live バージョン（live 無＝part5 前データは key グラフ）を返す。 |
| V3 | 再取り込み(replace) | 旧 live を**触らず**新版 `v{n+1}` を投入（staged）。Ask は再ストリーム中ずっと旧版を配信＝**ギャップなし・un-publish 不要・DROP なし**。 |
| V4 | promote | `liveGraph` を staged 版へ差し替え＋`status promoted`＝**control 書込のみ(O(1))**。旧 live 版は **`pendingDrop` キュー**へ enqueue（背景ドロップ）。 |
| V5 | delete | live/staged データグラフを `pendingDrop` へ enqueue＋(promoted なら)`deleted` tombstone を立てて**即応答**。大 DROP はリクエスト経路に無い。 |
| V6 | 背景スイーパ | api lifespan の周期タスク（既存 watcher と並ぶ）が `pendingDrop` グラフを**チャンク DELETE**（`chunked_drop_graph`）で掃除しマーカ除去。初回 tick は**クラッシュ復旧**（掃除途中で落ちた orphan を回収）。 |
| V7 | 大グラフの reclaim | **単発 `DROP GRAPH` はグラフ全体をメモリ展開し 8GB Oxigraph を OOM-kill する（実測）**ので、reclaim は `DELETE { GRAPH <g> {?s ?p ?o} } WHERE { SELECT … LIMIT N }` の**バッチ削除**（既定 10万行/バッチ・`ASK` で空になるまでループ）。各バッチは有界＝メモリ一定。**チャンク化は任意でなく必須**。 |

**surgical control 書込**: status/liveGraph/stagedGraph を1つずつ replace（DELETE-all しない）＝retract/reinstate が `liveGraph` を保存（同じ版が戻る）。**後方互換**: part5 前に昇格した dataset（key グラフにデータ・live 無）は `COALESCE` で従来どおり citable。再取り込み時は **key グラフが実データを持つ場合のみ orphan**（cheap ASK で判定）＝旧版リーク無し。**起動 backfill** は status＋liveGraph（registry meta から）を復元。

不変条件は不変: 生成コード非実行・IRI 不変・read-only `/api/sparql`・draft 隔離（未 promoted な staged 版は `liveGraph` に指されないので不可視）。

**実測（使い捨て 8GB Oxigraph・5M×2 再取り込み）**: stream v1 ピーク 2.08GiB／**v1 を live のまま v2 再ストリーム（10M 同居）ピーク 2.38GiB**／**re-promote 0.010s（O(1)）**／**5M v1 のチャンク sweep ピーク 4.23GiB・OOM なし**（同じ 5M を**単発 `DROP GRAPH`** すると 8GB を超えて OOM-kill＝V7 の根拠）。全経路でメモリ一定・コンテナ無傷（RestartCount 0）。

#### part5-leak: 孤児バージョングラフの回収（リトライ起因ゴミ増殖の監査 residual #2）

status: 実装済み（substrate + api + registry・backend テスト緑）

part5 のバージョングラフには**回収漏れ**があった。バージョングラフ `canonical/{id}/v{n}` は ingest で作られ、`stagedGraph`→（promote で）`liveGraph` に順に指される。だが `data_seq` は**成功時の `mark_ingested` でしか前進しない**ため、次の 4 経路で**どのポインタからも指されない孤児**が Oxigraph に無限に溜まった（最大級・非有界の leak）:

- **A. 成功 ingest の再実行**（SSE 切断を失敗と誤認する既知 failure mode が典型）: `set_staged_graph` はポインタを上書きするだけで旧 staged 版を enqueue せず→旧 `v{n}` が孤児化。
- **B. 失敗リトライ**: `data_seq` 不変ゆえ同 `v{n}` を DROP 無しで再利用。プロセス kill / `CancelledError`（`except Exception` は `BaseException` 非捕捉）で残った部分グラフと**マージ**され、stale 行・per-attempt な `{__run_id__}` activity IRI が citable に昇格する恐れ。
- **C. delete**: 現行 live/staged のみ enqueue＝再取り込みで溜まった孤児版を取り残す。
- **D. 修正前から既存**の孤児（本機構導入前に leak した版）。

**修正＝ライフサイクル全体の多層防御**（各層は独立の穴を塞ぐ）:

| 層 | 対象 | 決定 |
|---|---|---|
| L1 | A（根本） | `set_staged_graph` が**上書きされる旧 staged 版を `pendingDrop` へ enqueue**（`promote_to_canonical` が旧 live を enqueue するのと対称）。set された staged は必ず未 promoted（promote が staged ポインタを clear する不変条件）ゆえ live を誤ドロップしない。同一版の再記録は no-op enqueue。 |
| L2 | B（残骸マージ） | ingest 開始時に `reserve_data_seq` で**バージョン番号を毎回 persist 予約**（従来 `next_data_seq` は非永続 peek）。失敗・kill・cancel でも次の試行は**必ず新しい空グラフ**＝再利用が起きない＝残骸とマージしない。放棄された版は失敗経路の drop か L3 が回収（単調性はギャップを許容）。 |
| L3 | A/B/D（安全網） | **起動時 reconciliation** `reconcile_orphan_versions`＝`liveGraph`/`stagedGraph` のどちらからも指されないバージョングラフを全列挙し enqueue。起動時のみ（in-flight ingest 無し＝ポインタ未書込の投入中グラフを孤児と誤認しない）。既存 leak・クラッシュgap の孤児も回収。 |
| L4 | C | delete が `all_version_graphs(dataset_id)` で**当該 dataset の全 `v{n}` を列挙**して enqueue（live/staged ポインタが指さない孤児版も削除時に確実回収・再起動を待たない）。 |

列挙（`all_version_graphs`/`referenced_version_graphs`）は空 GGP `GRAPH ?g {}` + name filter＝**graph 名インデックス直読み**（triple 非走査・O(#graphs)）で V2 の perf 特性を維持。回収本体は既存の `pendingDrop`→背景スイーパ→`chunked_drop_graph`（V6/V7）に委譲＝リクエスト経路外・メモリ有界。**不変条件維持**: 孤児版は定義上 promoted されていない＝never citable ゆえ、その回収が reader に影響しない。関連＝[[asterism-incremental-append]]（append リトライの run-id 決定化は residual #3・別 PR）。

### 見積（修正後）

full starrydata 1,200万件が **RAM 4〜8 GB / SSD 30〜50 GB** の小型クラウド1台で回る（MOVE 方式が要求した 18〜28 GB が不要に）。

### 検証規律

使い捨て 8GB 上限 Oxigraph（`docker run --memory=8g --memory-swap=8g … oxigraph serve`）に合成 1,000万〜1,200万トリプルを投入→ promote がメモリ一定（数百 MB）で通るのを `docker stats` で実測してから、ユーザーの :7878/実機で全 starrydata を通す。
