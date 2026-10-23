# Graphium とつなぐ（判断・規則・観察を Asterism に育てて、AI が検索して戻す）

[Graphium](https://github.com/kumagallium/Graphium) は人がノート・判断・来歴を書く記録の道具、
Asterism はそれを**検索できる知識グラフ**として持つ側。この見本は、その 1 周を閉じるための
ファイル一式です。Asterism 側に Graphium 固有のコードはありません — 使うのは汎用の
`append_rows` と、ここに置いた設計の見本だけです。

```
Graphium で保存 ──export_asterism_claims──▶ flat JSON ──append_rows──▶ Asterism の知識グラフ
      ▲                                                                       │
      └──────── save_answer（答え＋出典 source_url）◀── AI が sparql_query ◀──┘
```

## ファイル

| ファイル | 何か |
|---|---|
| `mapping-ir.yaml` | Graphium の書き出し（型ごとの JSON＋子の JSON）を読む設計（Mapping IR）。型は共有語 `sv:judgment` `sv:rule` `sv:interpretation` と `sosa:Observation`。主語は `https://kumagallium.github.io/Graphium/claim/{id}`。どの map も `optional: true` |
| `proposal.md` | 上の IR を `POST /api/materialize` に渡すときの文書（§9 に IR を入れたもの） |
| `sample/*.json` | Graphium の `export_asterism_claims` が返す形の見本（判断 1・規則 1・解釈 1・観察 2 と子の表） |
| `anomaly.rq` | 「規則に反する観察」を探す SPARQL（違和感検知）。出典は `sv:source_url` |
| `loop.md` | 実機で 1 周通した記録（増分と上げ直しの両方） |

## 契約（Graphium → Asterism の行）

- 1 件 1 オブジェクト・**キーは全行に出す**（無い値は `null`・配列は `[]`）
- `id`・`iri`・`source_url`（Graphium のノートの URL）を必ず持つ
- 参照（`trigger_iri` など）と `evidence` は**完全 IRI の配列**
- 値は `value_number`（数）と `value_text`（文字）に分ける
- 子の表（`rule_terms.json`・`observation_terms.json`）は親の id（`rule_id`／`observation_id`）を持つ
- 設計の `source:` は表にした名前（`judgments.json` → `judgments.csv`）。ファイル自体は JSON のまま渡す

## 手順

### 0. 共有語を作る（1 回だけ）
Asterism のチャットで「judgment・rule・interpretation を用語として足して」と頼んで承認する
（または `POST /api/vocab/shared` に slug・kind=class・label と最初の問いを送る）。
観察は標準語 `sosa:Observation` をそのまま使うので作らない。

### 1. 最初の設計と承認（1 回だけ・人がやる）
1. Graphium の設定で「Asterism と連携する」をオンにし、`export_asterism_claims` か書き出しメニューで
   JSON を出す（0 件の型のファイルは出ない）
2. Asterism のチャットにそのファイルを添えて設計し、承認して公開する。かんたんの設計が
   配列や入れ子をうまく読めないときは、`proposal.md` を `POST /api/materialize` に渡し、
   ファイルを `POST /api/datasets/{id}/source` で付けて、取り込み → 公開する
3. `anomaly.rq` が通れば設計は合っている

### 2. 以後は保存のたびに（AI がやる）
Claude Desktop などに Graphium と Asterism の両方の MCP をつなぎ、次の順で頼む。
**既定は上げ直し（全量・冪等）**。日々の増分だけなら `append_rows` でもよいが、直した・消した知見は上げ直しでしか届かない。

| 順 | ツール | 何を渡す |
|---|---|---|
| ① | Graphium `export_asterism_claims` | `asterism`（語の基底・知見の基底・型の語）。全量 |
| ② | Asterism `resync_rows` | `dataset_id`・`files`（①の全ファイル。0 件の種類は `[]`） |
| ③ | Graphium `mark_asterism_sent` | ②で入った `id` と `dataset_id` |

増分だけのとき:

| 順 | ツール | 何を渡す |
|---|---|---|
| ① | Graphium `export_asterism_claims` | `onlyUnsent: true`・`asterism`（語の基底・知見の基底・型の語） |
| ② | Asterism `append_rows` | `dataset_id`・`file_name`（例 `observations.json`）・`rows`・`child_files`（例 `observation_terms.json`） |
| ③ | Graphium `mark_asterism_sent` | ②で足せた `id` と `dataset_id`（次回の `onlyUnsent` が二重送信を防ぐ） |

### 3. 検索して戻す
| 順 | ツール | 何を渡す |
|---|---|---|
| ④ | Asterism `find_datasets` → `sparql_query`（または typed tool） | 問い（例: 650 K 以上で分解した観察に反するものは？） |
| ⑤ | Graphium `save_answer` | 問い・答え・`citations`（答えの出典 = `source_url` の知見 id） |

## 守っていること
- Asterism のストアに入るのは承認済みの設計の行だけ。追記は設計を変えない
- 主語は `claimBaseIri/{id}`。`example.org` は使わない
- 写しは作り直せる（Asterism の行は Graphium の主張の投影・読み取り専用）。Graphium の主張を Asterism で直さない — 直すのは Graphium、意味（型・設計）を直すのは Asterism。1 つの物の家は 1 つ、写しは機械が作る
- `append_rows` では同一トリプルだけが重なる。同じ id で値が違う行は並ぶ。訂正は上げ直し
- 既存の MCP ツールの名前・引数は変えていない
