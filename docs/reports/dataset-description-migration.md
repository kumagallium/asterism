# 既存データセットの説明を三つ組へ移す — 実データ（コピー）で一周した記録

- 日付: 2026-09-07
- ADR: [`../architecture/dataset-description-in-the-store.md`](../architecture/dataset-description-in-the-store.md)
- 対象コード: PR #575（`asterism.metadata`）・PR #576（`asterism-metadata migrate`）

## Q（問い）

1. 手元にある実物の `mie.yaml` は、三つ組にして投影し直すと**意味を落とさずに**戻るか
2. 移行コマンドは決定論・冪等か（2 回目に何も変えないか）
3. メタグラフをストアに入れても `schema_summary` の件数は変わらないか
4. togomcp への配信文書は、ストア経由の投影でも従来と互換か

## Method（やり方）

- 実データは **コピー**に対して実施（原本は読み取りのみ）:
  - アプリ本体 `~/Library/Application Support/Asterism/sources/registry`（3 件・すべて未公開）
  - review 環境 `~/Documents/Asterism-review/sources/registry`（4 件・公開済み 3 / 未公開 1）
- ストアはアプリ本体の `oxigraph_store` のコピーを、同梱の `oxigraph 0.5.9` で `127.0.0.1:17878` に立てた
- 手順: `schema_summary` を取る → `migrate`（dry-run）→ `migrate --apply --store` → もう一度 `--apply --store` → `schema_summary` を取る → 公開済み 3 件について `project_mie(原本)` と `project_mie(ストアから投影した mie.yaml)` を比べる

## Result（結果）

### 1. 往復（7 件すべて等価）

| registry | dataset | triples | 節 | dry-run | apply | 再 apply |
|---|---|---|---|---|---|---|
| アプリ本体 | dataset-34ce6866 | 59 | schema_info / sample / examples / anti_patterns | ok | written | unchanged |
| アプリ本体 | dataset-6b7eb764 | 59 | 同上 | ok | written | unchanged |
| アプリ本体 | dataset-76328ef4 | 121 | 同上 | ok | written | unchanged |
| review | periodictablecsv-381c132b（公開済み） | 253 | 同上 | ok | written +stored=253 | unchanged +stored=253 |
| review | xrd-346deb24（公開済み） | 324 | 同上 | ok | written +stored=324 | unchanged +stored=324 |
| review | xrd-47441557（公開済み） | 176 | 同上 | ok | written +stored=176 | unchanged +stored=176 |
| review | xrd-ebd2bdfb（未公開） | 162 | 同上 | ok | written | unchanged |

skip 0・error 0。未公開 4 件はストアに書かれていない（ADR §4 どおり）。

history/ 配下の 30 件を含めた **39 ファイル**でも、ビルダ→投影の往復が全件等価だった（PR #575 の検証）。

### 2. 冪等性

2 回目の `--apply --store` は 7 件すべて `unchanged`。ローカルの 3 ファイルは mtime も含めて不変。ストア側は置き換え（DROP → POST）なので件数が同じことを確認した。

### 3. `schema_summary` の件数

| | 前 | 後 |
|---|---|---|
| canonical graphs | 0 | 0 |
| meta graphs | 0 | 3 |
| classes / predicates | 0 / 0 | 0 / 0（**一致**） |

手元のストアには公開済みの実コーパスが無いので、件数はどちらも 0 である（→ Limitations）。メタグラフ 3 枚（753 三つ組）が入っても canonical スコープには 1 件も混ざらなかった。

ストアに入った説明（`dcterms:title` / `dcterms:description`）:

| dataset | title | description |
|---|---|---|
| periodictablecsv-381c132b | Periodic Table of Elements – RDF Dataset | 原子番号をキーにした化学元素の属性集合。… |
| xrd-346deb24 | XRD Reference Dataset | 日本語と英語のX線回折（XRD）測定結果を RDF化したデータセット。… |
| xrd-47441557 | （無し） | （無し） |

xrd-47441557 の `mie.yaml` には元から `title` / `description` が無い（`keywords` / `categories` だけ）。説明を発明しない、という方針どおり空のままである。

### 4. togomcp 配信の互換

公開済み 3 件について、`project_mie(原本の mie.yaml)` と `project_mie(ストアから投影した mie.yaml)` を比べた:

| dataset | バイト一致 | `yaml.safe_load` の等価 | `normalize_document` の等価 |
|---|---|---|---|
| periodictablecsv-381c132b | ✗ | ✗ | **✓** |
| xrd-346deb24 | ✗ | ✗ | **✓** |
| xrd-47441557 | ✗ | ✗ | **✓** |

差分の中身は 3 種類だけで、いずれも ADR §9 が定めた等価関係の範囲内:

1. `keywords` / `categories` の**並び**（投影は文字列順に揃える。RDF の直付けは集合なので元の並びは保てない）
2. `schema_info` の**キー順**（`version` などの未知キーが既知キーの後ろに移る）
3. 例クエリの**キー順**（`name` は既知キーでないので `query` の後ろに移る）

togomcp は `schema_info.keywords` / `categories` を検索の**集合**として読み、例クエリはキー名で引くので、どれも配信先の挙動を変えない。

### 5. 失われるもの

YAML の `#` コメントは往復で消える（ADR §9 の予告どおり）。実データでは **1 件**（dataset-34ce6866、2 行）に起きた。原本は `mie.yaml.authored` に残っている。他の 2 件で「投影後にも `#` がある」ように見えるのは、SPARQL のブロックスカラーの中のコメント（内容の一部）であり、YAML のコメントではない。

## Conclusion（結論）

- 実物の `mie.yaml` 7 件（形はばらばら）は、すべて意味を落とさずに三つ組へ移せる。移行は決定論・冪等
- メタグラフは canonical スコープの外にあり、`schema_summary` の件数は変わらない
- togomcp への配信は意味的に同じ文書になる。バイト一致はしない（集合の並びとキー順）

## Limitations（限界）

- 手元のストアに**公開済みの実コーパスが無い**ため、`schema_summary` の件数比較は 0 対 0 である。「混ざらない」は構造（`_from_merge` が canonical だけを列挙する）から言えるが、実コーパス上での数字は本番で改めて取る価値がある
- togomcp 側で実際に読ませてはいない（`project_mie` の出力を比べただけ）
- `--store` の対象はコピーしたストアであり、本番の control graph には触れていない

## Reproduce（再現）

```bash
# 1. コピーを作る（原本は触らない）
cp -R ~/Documents/Asterism-review/sources/registry /tmp/reg-copy
cp -R ~/Library/Application\ Support/Asterism/oxigraph_store /tmp/store-copy
/Applications/Asterism.app/Contents/Resources/backend/oxigraph serve -l /tmp/store-copy -b 127.0.0.1:17878 &

# 2. 流す
cd ingest
uv run asterism-metadata migrate --registry /tmp/reg-copy                                   # dry-run
uv run asterism-metadata migrate --registry /tmp/reg-copy --apply --store http://127.0.0.1:17878
uv run asterism-metadata migrate --registry /tmp/reg-copy --apply --store http://127.0.0.1:17878  # 全件 unchanged

# 3. 見る
curl -s http://127.0.0.1:17878/query -H 'Content-Type: application/sparql-query' \
  -H 'Accept: application/sparql-results+json' \
  --data 'SELECT ?g (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } FILTER(STRSTARTS(STR(?g), "https://kumagallium.github.io/asterism/graph/meta/")) } GROUP BY ?g'
```
