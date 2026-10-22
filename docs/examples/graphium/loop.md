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

## 残った穴・次の一歩

- 本物の vault に型付きの知見が無い。Graphium の「Asterism と連携する」をオンにし、知見に型（judgment / rule / interpretation / sosa:Observation）を付けるところから
- 送信後に Graphium 側で知見を**直した**場合、`onlyUnsent` は出さない（id で判定）。Asterism の行も古いまま。更新の運び方は未着手
- 保存のたびに自動で送るフック（案 2・Graphium が直接 POST）は未着手。今は Claude が ①〜④ を順に呼ぶ
- `_claim_properties` の子の表は親→子の向きの線（`sv:condition` 等）を持たず、子→親（`sv:of_rule` / `sv:of_observation`）だけ。Asterism の設計の助言が「つながっていない」と言うのはこのため（問いは通る）
- 「下がった／上がった」の寄せ（状態語彙）は範囲外のまま
