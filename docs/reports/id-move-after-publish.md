# 公開したあとに「データの数えかた」を直しても、配った引用は切れない

- 日付: 2026-08-27
- 対象: `step0/id_move.py` / `ingest/substrate.py`（転送台帳）/ `api` の promote・ingest・
  `/describe` / `ui/src/kantan/KantanWizard.tsx` S4・S8
- 決定: [`docs/architecture/id-move-after-publish.md`](../architecture/id-move-after-publish.md)

## Q（問い）

かんたんモードは公開後の再設計で**意味と単位しか直せない**。種類と ID（データの数えかた）は
`canBackToGate` の `!redesigning` で閉じられていた。数えかたの誤り —— 500 行が 1 件に潰れて
いた、1 行 = 1 試料にすべきだった —— は公開して使ってみて初めて分かることが多い。

**ID の作り方を後から直しても、配ってしまった引用が切れないようにできるか。**

## Method（方法）

1. コード読解で、公開後に ID を変えると実際に何が起きるかを確認
   （`substrate.promote_to_canonical` の supersede 経路）。
2. 転送台帳を決定論で作り、`/describe` がそれを辿るように実装。
3. **実機一周**: Oxigraph（docker・隔離）+ api（隔離 registry・`ASTERISM_IRI_BASE=https://lab.example.jp`）で
   「①誤った数えかたで公開 → ②その ID を引用として控える → ③数えかたをやり直して再公開 →
   ④控えた ID を開く」を実行。台帳の中身は Oxigraph に直接 SPARQL を投げて確認。
4. 2 世代（v1→v2→v3）と「引っ越せない」ケース（旧 ID の列がいまのファイルに無い）も実機で確認。
5. UI は実 API・実データのまま CDP ヘッドレス Chrome で各画面を撮影。

素材は 3 行の熱電測定 CSV（`paper_id, sample_id, composition, seebeck`）。
最初の設計は **論文単位でしか数えていない**（`sample/{paper_id}`）＝これが後で誤りと分かる。

## Result（結果）

### ① 閉じていた理由は、実装レベルでも正しかった

公開後に ID の作り方を変えると、promote が旧 version graph を `pendingDrop` に入れ、背景の
掃除が **旧 IRI のトリプルを物理削除**する。実機の再公開直後、ストアはこうなっていた:

```
   9 件  …/graph/canonical/dataset-aa7840fe/v2     ← 新しいデータ
   3 件  …/graph/moved/dataset-aa7840fe            ← 転送台帳（新設）
   8 件  …/graph/ontology/dataset-aa7840fe
   2 件  …/graph/control
```

`v1` は消えている。旧 IRI `…/sample/P1` を主語に持つ実データは **0 件**（台帳を除いた実測）。
台帳が無ければ、この時点で引用は 404 になる。

### ② 台帳は決定論で作られ、実測で 3 件

再取り込み時、`/api/datasets/{id}/id-move` はこう答えた（**計画値ではなく実測**）:

```json
{ "changes_ids": true, "fully_movable": true, "forwarded": 3,
  "moved": [{ "name": "sample", "source": "thermo.csv",
    "old_template": "…/resource/sample/{paper_id}",
    "new_template": "…/resource/sample/{paper_id}-{sample_id}" }],
  "unchanged": [], "blocked": [] }
```

LLM は 1 回も呼ばれていない。台帳の RML は旧 subject template を subject、新 subject template を
object に置いただけのもので、既存の `assert_rml_safe` / `validate_rml_design` を両方通る。

### ③ 配った引用は、いま何を返すか

```
GET /describe?iri=…/resource/sample/P1  → 200
  「この ID は、いくつかの ID に分かれました」+ 行き先 2 件

Accept: text/turtle → 200
  <…/sample/P1> <http://purl.org/dc/terms/isReplacedBy> <…/sample/P1-S1> .
  <…/sample/P1> <http://purl.org/dc/terms/isReplacedBy> <…/sample/P1-S2> .
```

**ここが `owl:sameAs` を採らなかった判断の実証**である。論文単位の 1 件は、試料ごとに
数え直すと 2 件に分かれた。sameAs だったら「`P1-S1` と `P1-S2` は同じもの」という
嘘の事実を公開していた。isReplacedBy は向きを持つので、1→多を嘘なしで書ける。

### ④ 2 世代先まで辿れる

もう一度数えかたを変えて（v2→v3）から、**v1 で配った ID** を開いた:

| 開いた ID | 応答 |
|---|---|
| `…/sample/P1`（v1・実データはとうに無い） | → `…/sample/P1-S1-Bi2Te3`, `…/sample/P1-S2-Bi2Te2.7Se0.3` |
| `…/sample/P1-S1`（v2・実データは無い） | → `…/sample/P1-S1-Bi2Te3` |
| `…/sample/P1-S1-Bi2Te3`（v3） | 実データ本体（`composition "Bi2Te3"`, `seebeck 210`） |

各再取り込みはその回の old→new しか書かない。v1 の ID は**もうどこにも存在しない v2 の ID**を
経由して v3 に届いている。これが台帳を version graph の外に置いた理由の実証でもある
（中に置けば、次の promote が転送記録ごと落とす）。

### ⑤ 「引っ越せない」は黙って起きない

旧 ID を綴る列がいまのファイルに無い場合（列名が変わった元ファイルに差し替え）:

```json
{ "changes_ids": true, "fully_movable": false, "forwarded": 0,
  "blocked": [{ "source": "thermo.csv", "reason": "missing_columns",
                "missing_columns": ["paper_id"] }] }
```

公開画面はこれを**警告色**で出し、理由と戻り道を添える。公開は止めない（所有者の判断）。

### ⑥ 扉を開けるには、ボタンを出すだけでは足りなかった

`canBackToGate` から `!redesigning` を外しても、実機ではボタンが出なかった。見直しは
S6 から始まり、その初期化が **ブラウザの元ファイルを明示的に捨てる**（「サーバに永続化
されているから」）。`hasSource = files.length > 0 || !!stagingId` は常に false になり、
骨格（`gateSkeleton`）もゲートを通っていないので存在しない。

サーバは両方持っていた。`POST /api/datasets/{id}/recount` が、保存済み Mapping IR から
`skeleton_from_full_ir` で骨格を、永続 source から staging（ADR source-staging.md）を作って
返す。実機では公開済みデータの S4 が**実データの証拠つき**で開いた:

> ✓ 3 行すべてが別々の ID になります / この ID でできるもの: Sample 3 件

## Conclusion（結論）

**主張は成立する。** 公開後に ID の作り方を直しても、配った引用は生き続ける。旧 IRI は
書き換えられず（`instance-iri-base.md` の不変性は維持）、行き先を指す小さな事実が別グラフに
追記されるだけである。K21 は「label を後から足しても IRI は直らない」部分は今も正しく、
「だから公開後に S4 へ戻れない」という帰結だけが改訂される。

残ったもの:

- **UI の実 LLM dogfood 未実施** — S4 で数えかたを実際に変えて per-map 生成まで回す一周は、
  実 LLM キーが要るため未検証。骨格ゲートより後ろは既存経路で変更がない。
- **本番デプロイ未実施**。

## Limitations（限界）

- 素材は 3 行 1 ファイル。台帳の生成コスト（Morph-KGC をもう一度流す）を大きなデータで
  測っていない。行数に比例するはずだが、実測はしていない。
- `blocked` の判定は取り込みゲートと同じヘッダ読み（`rml_validate.read_csv_header`）を通すが、
  preamble 由来の列（`source-dialect.md` のヘッダメタデータ）を持つファイルでは未検証。
- 台帳は追記のみで、上限を設けていない。再設計を何十回も繰り返した場合の台帳の肥大は
  未検討（追跡は 8 ホップ・行き先 50 件で打ち切る）。
- `/describe` 以外の読み口（Ask の typed tools、SPARQL）は台帳を読まない。これは設計どおり
  （転送はデータの主張ではない）だが、「古い IRI で SPARQL を書いた人」は救われない。

## Reproduce（再現）

```bash
# 1) 隔離した Oxigraph
docker run -d --name asterism-idmove-ox -p 127.0.0.1:7894:7878 \
  ghcr.io/oxigraph/oxigraph:latest serve --location /data --bind 0.0.0.0:7878

# 2) 隔離した registry で api（morph-kgc 入りの venv が必要）
cd api && uv venv .venv --python 3.11 && uv pip install -e '../ingest[substrate]' && uv pip install -e '.[dev]'
CSV2RDF_OXIGRAPH_URL=http://127.0.0.1:7894 CSV2RDF_REGISTRY_ROOT=/tmp/v/registry \
CSV2RDF_DROP_ROOT=/tmp/v/csv CSV2RDF_RDF_ROOT=/tmp/v/rdf CSV2RDF_ERROR_ROOT=/tmp/v/errors \
CSV2RDF_JOBS_LOG=/tmp/v/jobs.jsonl ASTERISM_IRI_BASE=https://lab.example.jp \
ASTERISM_API_TOKEN=verify-token uv run asterism-api --host 127.0.0.1 --port 8137

# 3) 一周（①公開 → ②引用 → ③数えかたやり直し → ④配った ID を開く）
uv run python experiments/id-move/roundtrip.py
uv run python experiments/id-move/chain_and_blocked.py <dataset_id>

# 4) ユニット
cd step0 && uv run pytest tests/test_id_move.py -q   # 15 passed
cd api   && uv run pytest tests/test_id_move.py -q   # 16 passed
```

`ASTERISM_API_TOKEN` を設定しないと書き込み系は 503（fail-closed）で一周できない。
api の venv に `[substrate]` extra を入れ忘れると ingest が
「morph-kgc is required」で失敗する（ジョブ台帳 `jobs.jsonl` にだけ出る）。
