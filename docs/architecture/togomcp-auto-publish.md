# ADR: promote 時の togomcp 自動配信（MIE 投影）

- **Status**: 合意済み（2026-08-09）
- **関連**: [option-b.md](option-b.md)（togomcp 併設構成）/ [ontology-canonical-lifecycle.md](ontology-canonical-lifecycle.md)（promote/retract/delete）/ ai-assisted-step0-workflow.md §6 #10（T10 例示クエリ実行検証）

## 背景

Option B 構成は当初から dbcls/togomcp を併設し、`data/togomcp/`（togomcp の公開契約
`TOGOMCP_DIR`）に **starrydata 1 件だけを手動配置**してきた。一方 step0 の propose §7 は
すべての新規データセットに MIE を書かせ、registry が `mie.yaml` として保持している——
が、promote してもその MIE は registry 止まりで、togomcp からは存在しないのと同じだった。

「togomcp を vendor しパッケージ内部の data ディレクトリへ直接書き込む」構成も考え
られるが、上流の内部構造への密結合になるため採らない。

## 決定

**promote 成功時に、registry の MIE を決定論投影して `TOGOMCP_DIR` レイアウトへ書き出す。**

1. **結合面は変えない**: 書き出し先は togomcp が仕様として公開する `TOGOMCP_DIR`
   （`mie/<id>.yaml` + `resources/endpoints.csv`）のみ。togomcp を import せず、
   パッケージ内部にも触れない。`ASTERISM_TOGOMCP_DIR` 未設定なら機能ごと無効
   （疎結合の維持。設定値=出力先のひとつ、というだけの関係）。
2. **投影であってバイトコピーではない**: registry の MIE は api の canonical
   FROM-merge 前提（GRAPH なし例示クエリ）で書かれるが、togomcp は**生ストア**
   endpoint に接続し、そこでは default graph は空。素通し配信すると全例示が 0 行
   ——T10 が検出する「実データで動かない例」をこちら側で量産することになる。
   よって配信時に決定論で:
   - `schema_info.endpoint` / `schema_info.graphs` を現在値に固定
   - データセット節を持たない各例示クエリへ `FROM <live-graph>` + `FROM NAMED
     <live-graph>` を注入（`asterism.substrate.scope_query_to_graph`。自前の
     FROM を持つクエリは作者のスコープ指定を尊重して素通し）
   - live graph は part5 の**バージョングラフ** `…/canonical/{id}/v{n}` で
     re-promote ごとに変わるため、投影は毎回の promote で上書き（冪等）
3. **ライフサイクル整合**: retract / delete で MIE ファイルと endpoints.csv 行を
   除去、reinstate で再配信。**draft は決して配信しない**（promoted のみ）。
4. **best-effort**: 配信失敗は promote を落とさない（ontology projection /
   crosswalk rebuild と同じ契約）。結果は promote レスポンスの `togomcp` キーで開示。

## 制約（正直に）

- togomcp は `endpoints.csv` を起動時に読み、`find_databases` をキャッシュする
  （pin 中の 54ab0d0 を確認）。新規データセットが一覧に載るのは
  `docker compose restart togomcp` 後。`get_MIE_file` は毎回ファイルを読むため即時。
- 生ストア endpoint は canonical スコープ外（draft グラフ等）も GRAPH 指定で読める
  **第二の読み取り面**である。これは本 ADR 以前からの Option B の性質で、compose は
  既に loopback 限定を既定としている。retract 時の unlisting はカタログ衛生であって
  アクセス取り消しではない——機微データ運用では togomcp を公開しないこと。
- 手動管理の同梱 starrydata 行・MIE は温存（同期は自データセット id の行だけを触る）。

## T10 との関係

T10（例示クエリの parse + draft 実行検証）が配信前の品質ゲート。設計時に T10 を
通った例示だけが、投影で live graph にピン留めされて配信される。
「検証済みの説明書が、promote と同時に DBCLS の棚に並ぶ」一本道になる。
