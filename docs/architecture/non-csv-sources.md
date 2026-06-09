# 非CSVソースの取り込み（#19・JSON ファースト・設計メモ / ADR）

決定母体: [`phase5-declarative-substrate.md`](phase5-declarative-substrate.md)（substrate・Tier 0 関数）/ [`scalable-declarative-ingestion.md`](scalable-declarative-ingestion.md)（ストリーミング ingest・promote）/ [`ontology-canonical-lifecycle.md`](ontology-canonical-lifecycle.md)（dataset 一般化）

status: **JSON 着地**（step0 inspector/propose ＋ api/substrate/registry ＋ UI ソース切替 ＋ Materials Project dogfood・実 Morph-KGC＋実 Oxigraph 検証済）。API/DB は宣言的に可能だが未実装（§5）。

## 0. 何を可能にするか

UI の「データを追加」ソース切替（CSV / JSON / API / DB）を、CSV 以外でも実接続する。最初の対象は **JSON ファイル**。研究データは CSV だけでなく JSON（API レスポンス・エクスポート）でも来る。「あらゆる構造化ソース」を謳う以上、CSV に閉じない経路が要る。

## 1. なぜ JSON ファーストか（ユーザー確定 2026-06-09）

| 選択肢 | 評価 |
|---|---|
| **JSON ファイル**（採用） | 最小。Morph-KGC が `ql:JSONPath` でネイティブ読込＝**取り込みエンジンは無改修**。再現性は CSV と同形（`source/` に永続化）。auth/paging/secret 不要。ライブ API も実体は *fetch→JSON→この経路* なので JSON が基盤になる。 |
| ライブ API | Morph-KGC の `http_api` ソースで宣言可能だが、`http_api_df` 設定＋認証トークン config＋paging が要る。取り込みごとに外部依存・レート制限・再現性スナップショット設計を抱える＝重い。後続（§5）。 |
| DB（RDB） | Morph-KGC の RDB ソースで宣言可能だが、接続情報/secret 管理が要り、自リポジトリ完結・再現性の原則と相性が悪い。後続。 |

ドッグフード題材＝**Materials Project**（既に PoC・content backbone あり・自然形がネスト JSON・starrydata と組成で横断結合できる）。ライブ API は叩かず **JSON スナップショット**を取り込む（ユーザー確定）。

## 2. 鍵となる技術事実

取り込みは宣言的（Morph-KGC が RML を解釈）。`rml:logicalSource` は CSV/JSON/RDB/HTTP-API 等に対応＝**バックエンドは RML の logicalSource を非CSVにすれば取り込める**。実環境の **morph-kgc 2.10** はソースに `data_file.py`（JSON ファイル）/`http_api.py`（ライブ API）/`python_data.py` を持ち、いずれも JSONPath 対応。ソース型は拡張子か `referenceFormulation` で判定（`mapping_parser._complete_source_types`）。`_read_json` は `rml:source`（ローカルパス or `http(s)://` URL）→ `rml:iterator` で records → `pandas.json_normalize` でネストをドットキーに平坦化 → `rml:reference` のドットパスで参照。**※参照フィールドに null を含む行は drop される**（疎な JSON は行が消える）。

ギャップは「設計フロー（inspect/propose）」と「UI 配線」だけ。

## 3. 決定（方式）

| # | 論点 | 決定 |
|---|---|---|
| D1 | 取り込みエンジン | **無改修**。`absolutize_rml_sources` は `rml:source` 文字列への正規表現置換でフォーマット非依存。Morph-KGC が RML の `referenceFormulation` で CSV/JSON を読み分ける。`materialize_to_nt_file`→`stream_nt_file_to_oxigraph`（§scalable）も N-Triples 段で同一＝ソース形式に非依存。 |
| D2 | inspect | **同じ inspection Markdown 契約**を保ったまま JSON 対応（`inspect_json` / `inspect_source_set`＝拡張子ディスパッチ）。JSON レコードを `json_normalize` と同じドットパスに平坦化し、既存の型推論・一意性・FK 機構へ流す。iterator（`$[*]` / `$.key[*]`）を inspection に載せる。CSV の出力はバイト不変。 |
| D3 | propose | **単一のキャッシュ可能 SYSTEM_PROMPT**のまま §9 に CSV/JSON 両対応の logicalSource ガイダンスを追記。per-call のソース種別は inspection ブロック（`## CSV:` / `## JSON:` ＋ iterator ＋ ドットパス）が伝える＝プロンプトはバイト安定＝prompt caching 温存。 |
| D4 | RML 形 | JSON は `rml:source "<file>.json"` ＋ `rml:referenceFormulation ql:JSONPath` ＋ `rml:iterator "<inspection の iterator>"`。`rml:reference` / `rr:template` は **プレーンなドットパス**（`structure.spacegroup`・`$`接頭辞なし）。Tier 0 関数・テンプレートはソース種別に依らず同一（不変条件）。 |
| D5 | アップロード受理 | step0 / source / ingest 経路は `csv\|json\|geojson` を受理（`_validate_source_name`）。レガシー `/upload/{kind}`（starrydata watcher drop）は CSV 限定のまま。 |
| D6 | source 永続化・再現性 | **Task E と同形**。アップロード JSON を registry `<id>/source/` に永続化、ingest は再添付なしで永続 source を使用。meta に `source_kind`（csv\|json）を記録（`list_source_files` は `.json`/`.geojson` も列挙）。UI のラベル/ピッカーが `source_kind` に従う。 |
| D7 | UI ソース切替 | CSV / JSON のピルを実動化（`SUPPORTED_SOURCES`）。API/DB は「近日」据え置き。ピッカーの `accept`（`.csv` ↔ `.json,.geojson`）・ラベル・FK ヒントが選択種別に追従。inspect/propose/materialize/ingest 呼び出しは FormData のまま（バックエンドが拡張子でディスパッチ）。 |

## 4. データフロー（CSV と共通・差は logicalSource だけ）

```
[データを追加: ソース=JSON] → 構造解析(inspect_json: iterator + dot-path leaves)
   → AI 設計(propose: §9 が ql:JSONPath + iterator + dot-path RML を生成)
   → 保存(materialize) → source/ に mp.json 永続化(source_kind=json)
   → 取り込み(ingest 202): Morph-KGC が JSON を N-Triples 化 → チャンク stream → canonical/{id}/v{n}
   → 昇格(promote, フラグ flip) → Ask の引用面に登場（横断結合可）
```

## 5. 検証（実 Morph-KGC＋実 Oxigraph・使い捨てスタック）

Materials Project = ネスト JSON スナップショット（`datasets/materials_project/json/mp.json`・11 材料）＋ JSONPath RML（`mp.rml.ttl`）:

- **drop-in 等価**: `materialize_to_graph` で **143 triples**、直接 seed の `mp.ttl` の Material/CrystalStructure facts と**集合一致**。ネストの `structure.space_group_symbol` がドットパスで解決。
- **実 Oxigraph**: production の `materialize_to_nt_file`→`stream_nt_file_to_oxigraph`（= api ingest と同コード）で 143 triples を使い捨て Oxigraph(:7879) に投入＝`mp:Material` 11。
- **横断結合**: canonical FROM-merge で MP 結晶構造 × starrydata ZT を `mp:formula == sd:compositionString` で結合 → PbSe(Fm-3m,Cubic,1.018) / Bi2Te3(R-3m,Trigonal,0.914) / SnSe(Pnma,Orthorhombic,0.822) / ZnO(P6_3mc,Hexagonal,0.30)。

共有 :7878 / 他セッションの api には非干渉（専用 worktree＋使い捨て :7879・検証後撤去）。

## 6. 不変条件（破らない）

- 生成コード非実行（宣言経路・Tier 0 のみ）・IRI 不変・read-only `/api/sparql`・draft 隔離（未 promoted は不可視）。
- 既存の CSV 経路・テスト・inspection Markdown 出力はバイト不変（additive）。

## 7. 残（後続）

- **ライブ API ソース**: Morph-KGC `http_api`（`http_api_df`＋認証トークン＋paging）。実体は *fetch→JSON スナップショット→本 ADR の JSON 経路* の前段に fetch を足す形で拡張でき、再現性のため取得 JSON を `source/` にスナップショット保存する。
- **DB（RDB）ソース**: 接続情報/secret の扱いを決めてから。
- **validate.py の JSON 対応**: T1/T2 は CSV をファイル読みする。現状は `asterism-validate` CLI（`source_csvs` 明示時）のみで走り、api の materialize/ingest 経路には乗らないため JSON 取り込みには影響しない。JSON ソースで CLI validate を使う場合は要一般化。
- **配列要素への Tier 0 関数**: JSON の数値配列 leaf を Morph-KGC は list セルとして渡す。`fn:float_array_*` は JSON 文字列配列を前提（starrydata CSV 由来）。JSON で配列集約が要る場合は JSON 文字列化が要る（MP dogfood は scalar のみで非該当）。
