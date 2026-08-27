# ID の引っ越し — 実機一周の検証スクリプト

ADR [`id-move-after-publish.md`](../../docs/architecture/id-move-after-publish.md) /
レポート [`docs/reports/id-move-after-publish.md`](../../docs/reports/id-move-after-publish.md)。

公開したあとに「データの数えかた」を直しても、配ってしまった引用が切れないことを、
**実 Oxigraph + 実 api** で確かめる。LLM は使わない（設計は手書きの §9 を投げる）。

| スクリプト | 何を見るか |
|---|---|
| `roundtrip.py` | ①誤った数えかたで公開 → ②その ID を引用として控える → ③数えかたをやり直して再公開 → ④控えた ID を開く |
| `chain_and_blocked.py` | v1→v2→v3 のチェーン追跡 ／ 旧 ID の列がいまのファイルに無い場合 |
| `shots.py` | 公開画面と数えかたの画面を CDP ヘッドレス Chrome で撮る（UI dev サーバが要る） |

## 準備

```bash
docker run -d --name asterism-idmove-ox -p 127.0.0.1:7894:7878 \
  ghcr.io/oxigraph/oxigraph:latest serve --location /data --bind 0.0.0.0:7878

cd api
uv venv .venv --python 3.11
uv pip install -e '../ingest[substrate]'   # substrate extra が無いと ingest が落ちる
uv pip install -e '.[dev]'
CSV2RDF_OXIGRAPH_URL=http://127.0.0.1:7894 CSV2RDF_REGISTRY_ROOT=/tmp/v/registry \
CSV2RDF_DROP_ROOT=/tmp/v/csv CSV2RDF_RDF_ROOT=/tmp/v/rdf CSV2RDF_ERROR_ROOT=/tmp/v/errors \
CSV2RDF_JOBS_LOG=/tmp/v/jobs.jsonl ASTERISM_IRI_BASE=https://lab.example.jp \
ASTERISM_API_TOKEN=verify-token uv run asterism-api --host 127.0.0.1 --port 8137
```

`ASTERISM_API_TOKEN` が無いと書き込み系は 503（fail-closed）で一周できない。

## 実行

```bash
cd api
uv run python ../experiments/id-move/roundtrip.py
uv run python ../experiments/id-move/chain_and_blocked.py <roundtrip が出した dataset_id>
```

`shots.py` はさらに UI dev サーバ（`VITE_API_PROXY=http://127.0.0.1:8137 npm run dev -- --port 5199`）と
CDP つき Chrome（`--headless=new --remote-debugging-port=9333 --window-size=1180,1000`）が要る。
ウィザードの状態は `sessionStorage` に載るので、そこへ実データセットの id を置いて各画面に着地する。
