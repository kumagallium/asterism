# 実機で 1 周通した記録（2026-10-23）

Graphium で保存 → Asterism に追記 → AI が Asterism の MCP で検索 → 答えを Graphium に戻す、を
手元で 1 周閉じた記録。Asterism は `feat/graphium-loop`（`asterism-local`・同梱の oxigraph）、
Graphium は `feat/asterism-push` の MCP（`scripts/bundle-mcp.mjs` で束ねた stdio サーバ）。

## 使ったもの

| 側 | 何 | 値 |
|---|---|---|
| Graphium | vault | 一時の vault（`GRAPHIUM_ROOT`）。知見 6 件（規則 1・観察 3・解釈 1・判断 1）。本物の vault には型（`typeSlug`）付きの知見がまだ無いため |
| Asterism | データセット | `graphium-44da84da`「Graphium の知見」（設計は `mapping-ir.yaml`） |
| Asterism | 共有語 | `sv:judgment`（判断）・`sv:rule`（規則）・`sv:interpretation`（解釈）を `POST /api/vocab/shared` で鋳造。観察は `sosa:Observation` |
| 鍵 | — | アプリ内 `/mcp`（`http://127.0.0.1:<port>/mcp`）。ループバックからの要求に書き込みトークンを注入する既存の仕組みに乗る（案 1a）。新しい鍵なし |

## 0. 初回の設計（人が 1 回）

1. `proposal.md`（§9 に `mapping-ir.yaml`）を `POST /api/materialize` → 設計を保存（検証の指摘 0 件）
2. `sample/*.json` 6 ファイルを `POST /api/datasets/{id}/source` → `POST …/ingest` → **89 トリプル**
3. `POST …/promote` → 公開。`find_datasets` に種類「判断・規則・解釈・観察」で出る（共有語の表示名が効いている）
4. `anomaly.rq` → 規則「焼成温度 ≥ 650 K → 分解 present」に反する観察は **B（660 K・分解しなかった）だけ**。出典 `source_url` = `https://kumagallium.github.io/Graphium/app/#knowledge/claim/obs-660k-intact`

## 1 周

| 順 | ツール | 入れたもの | 返ったもの |
|---|---|---|---|
| ① Graphium | `mark_asterism_sent` | 初回の 5 件の id・`datasetId` | `marked: 5` |
| ② Graphium | `export_asterism_claims` | `onlyUnsent: true`・語の基底・知見の基底・型の語 | `observations.json` 1 件・`observation_terms.json` 2 件。5 件は `skipped: already-sent` |
| ③ Asterism | `append_rows` | `dataset_id`・`file_name: observations.json`・`rows`（試料 C・670 K・分解しなかった）・`child_files: {observation_terms.json: …}` | `triples_in_batch: 21`・`append_seq: 1`。**同じものをもう一度送る → `idempotent_replay: true`**（増えない） |
| ④ Graphium | `mark_asterism_sent` | `obs-670k-intact-c` | `marked: 1` |
| ⑤ Asterism | `sparql_query`（`anomaly.rq`） | — | **B と C の 2 件**。どちらも `source_url` 付き |
| ⑥ Graphium | `save_answer` | 問い「650 K 以上で焼成した観察のうち、分解の規則に反するものはどれか」・答え（本文に `[[source:<id>]]`）・`citations` 5 件 | 回答ページ `3ed1002c-…`（kind `answer`・`derivedFromClaims` に 5 件） |

## 確かめたこと

- 追記後の永続化ソース `source/observations.json` は 3 行（2 → 3。上書きされていない）
- 設計の `source:` は `judgments.csv` などの表の名前だが、バッチは `judgments.json` のまま通る（名前は兄弟の `.csv` で照合）
- バッチに無い種類（判断・規則・解釈・規則の項目）は `[]` の代役で 0 行として通る
- 答えの出典は `source_url`（Graphium のノートの URL）で、`save_answer` の `citations` にはその知見 id を渡した

## 直した知見が反映される 1 周（上げ直し・2026-10-23 追記）

同期の既定は**上げ直し（全量・冪等）**。`append_rows` は Graph Store POST で足すだけなので、Graphium 側で知見を直した・消した・作り直した（新しい id で同じ内容）ときは届かない。`resync_rows` が source を全置換して ingest → promote を走らせ、新しい版に差し替える。

| 順 | ツール | 入れたもの | 返ったもの |
|---|---|---|---|
| ① Graphium | 観察 B を直す（「分解しなかった」→「分解した」・値 `absent` → `present`） | — | — |
| ② Graphium | `export_asterism_claims`（`onlyUnsent` 無し＝全量） | 語の基底・知見の基底・型の語 | 6 ファイル（観察 3・子 6 ほか） |
| ③ Asterism | `resync_rows` | `dataset_id`・`files`（②の全ファイル） | `version: …/graphium-44da84da/v2`・`triples: 111`・`promoted: true` |
| ④ Asterism | `sparql_query`（`anomaly.rq`） | — | **C だけ**（直した B は出ない。直す前は B と C の 2 件） |
| ⑤ Asterism | B の結果の値 | — | `…/claim/obs-660k-intact/result/1` に `present` だけ（古い `absent` は無い・主語 IRI は同じ） |
| ⑥ Graphium | `mark_asterism_sent`（全 id） | — | `alreadySent: 6`（台帳は id で持つので上げ直しでは増えない） |
| ⑦ Asterism | `append_rows`（試料 D・680 K）を v2 に | — | 増分は上げ直しの後もそのまま効く（v2 に 21 トリプル・`anomaly.rq` に C と D） |

- 上げ直しは**必ず全量をアップロード**する（アップロード無しの再 ingest は積み増した `source/` を読み直すので古い行が残る）
- ingest 直後〜promote までの間は `promoted` が下りるので `append_rows` は 409。`resync_rows` は promote まで待ってから返る
- Ask は切り替えまで旧版（v1）を読み、旧版は `pendingDrop` で消える

## トピックを概念として写す（2026-10-23 追記）

Graphium のトピック（概念・材料・手法…の話題ページ）を `skos:Concept` の行として同じデータセットに写し、派生元の知見へ `prov:wasDerivedFrom` で結ぶ。用語（共有語 `sv:`）にはしない — Asterism の「問いの無い用語は作らない」門があるので、用語にするかは人が後で決める。

| 順 | ツール | 入れたもの | 返ったもの |
|---|---|---|---|
| ① Asterism | `POST /api/materialize`（`dataset_id` 付き＝同じデータセットの設計を置き直す） | `mapping-ir.yaml`（map `topic` を足した版） | 検証の指摘 0 件・公開状態は保たれる |
| ② Graphium | `export_asterism_claims(includeTopics: true)` | 語の基底・知見の基底・型の語 | 7 ファイル（`topics.json` 2 件: 焼成温度 concept・試料 B material） |
| ③ Asterism | `resync_rows` | 7 ファイル全量 | `version: …/v5`・`triples: 133` |
| ④ Asterism | `sparql_query` | `skos:Concept` → `prov:wasDerivedFrom` → 知見の数 | 焼成温度 → 4 件・試料 B → 2 件。`source_url` は `…/app/#knowledge/topic/<id>` |
| ⑤ Graphium | `mark_asterism_sent`（トピックの id も） | — | `marked: 2`。次の `onlyUnsent` は 0 件 |

- `find_datasets` の種類に「トピック」が加わる（判断・規則・解釈・観察・トピック）
- 設計を変えるのは人（①）。`resync_rows` は承認済みの設計で写しを作り直すだけ
- 次の一歩: 同じ名前のトピックが他のデータセットの値と重なれば Asterism の crosswalk が候補を出す（候補 C）。用語にする（候補 A の後半）は、問いを添えて `POST /api/vocab/shared` か、チャットで「焼成温度を用語にして」

## 材料名でつなぐ（候補 C・2026-10-23 追記）

Graphium のトピック（material）の名前が、別のデータセットの値と重なれば、Asterism の「つながり（crosswalk）」が候補を出す。LLM 不要・コード変更なし。

| 順 | 何を | 返ったもの |
|---|---|---|
| ① Asterism | 2 つ目のデータセット「熱電材料の試料表」（`te-samples/samples.csv`・材料／焼成温度／ZT）を設計 → 取り込み → 公開 | 16 トリプル |
| ② Graphium → Asterism | 材料トピック Bi2Te3・PbTe（派生元の知見つき）を `includeTopics` で書き出し → `resync_rows` | v6・152 トリプル |
| ③ Asterism | `POST /api/crosswalk/discover`（2 データセット・`min_shared_keys: 1`） | 候補 1 件: 試料表 `材料` × Graphium `skos:prefLabel`（重なり Bi2Te3・PbTe、正規化 identity） |
| ④ Asterism | 候補の `build_config` をそのまま `POST /api/crosswalk/material/build` | ハブ `crosswalk-material`（14 トリプル・links 試料表 3・Graphium 2） |
| ⑤ Asterism | `crosswalk.rq`（試料の ZT × その材料のトピックの知見） | 5 行: Bi2Te3（ZT 0.88／0.95）→「660 K で焼成した試料 A は分解した」「焼成温度を 620 K に下げる」・PbTe（1.4）→「670 K で焼成した試料 C は分解しなかった」 |

- つなぐ鍵は**名前の文字列**。表記が違えば（Bi₂Te₃ と Bi2Te3）candidates に出ない — 正規化は閉じた集合（identity ほか）から選ぶ
- 作るのは人（④）。候補を出すまでは機械

## 残った穴・次の一歩

- 本物の vault に型付きの知見が無い。Graphium の「Asterism と連携する」をオンにし、知見に型（judgment / rule / interpretation / sosa:Observation）を付けるところから
- 知見を直した・消したときは上げ直し（`resync_rows`）。`onlyUnsent` は id で判定するので増分では届かない（上の節）
- 保存のたびに自動で送るフック（案 2・Graphium が直接 POST）は未着手。今は Claude が ①〜④ を順に呼ぶ
- `_claim_properties` の子の表は親→子の向きの線（`sv:condition` 等）を持たず、子→親（`sv:of_rule` / `sv:of_observation`）だけ。Asterism の設計の助言が「つながっていない」と言うのはこのため（問いは通る）
- 「下がった／上がった」の寄せ（状態語彙）は範囲外のまま
