# Phase 2 #3: 自作 MCP `template_curve_fetch`

設計プラン §6 自作ツール一覧の最小実装。Phase 1 が curve の x/y 配列を
`sd:xValuesJSON` / `sd:yValuesJSON` の JSON literal で保持しているため、AI が
「300 K の Seebeck 値」のような点取得を行うには JSON を解凍する必要があった。
本 MCP server がその解凍を担当する。

## 全体図

```
═══════════════════════════════════════════════════════════════════════
  AI クライアント (Claude Desktop / Graphium / Cline / Dify)
═══════════════════════════════════════════════════════════════════════
              │ MCP (stdio or HTTP)
              ▼
┌─────────────────────────────────────┐   ┌──────────────────────────┐
│  togomcp (Phase 1)                   │   │  csv2rdf-mcp (Phase 2 #3) │
│   run_sparql, get_MIE_file 等        │   │   template_curve_fetch    │
│   汎用                                │   │   starrydata 専用         │
└─────────────────────────────────────┘   └──────────────────────────┘
              │ SPARQL                          │ SPARQL SELECT ?p ?o
              ▼                                  ▼
              ┌─────────────────────────────────────────┐
              │  Oxigraph (Phase 1 backend, port 7878)   │
              └─────────────────────────────────────────┘
```

## ファイル配置

```
mcp/
├── pyproject.toml
├── src/
│   └── csv2rdf_mcp/
│       ├── __init__.py
│       ├── server.py        # FastMCP wiring + CLI (HTTP/stdio transport)
│       └── tools.py         # template_curve_fetch 本体 (テスト容易性のため分離)
└── tests/
    ├── test_tools.py        # tool 本体 (mocked Oxigraph)
    └── test_server.py       # FastMCP 登録 / call_tool ラウンドトリップ
```

## tool: `template_curve_fetch`

入力:
- `curve_iri: str` — フル IRI、例: `https://kumagallium.github.io/csv2rdf-mcp/starrydata/resource/curve/1-1-1`
- `max_points: int | None = None` — 先頭 N 点だけ返す (preview 用、None なら全件)

出力 (dict):
- `iri`, `found`, `truncated`: 識別/状態フラグ
- `property_x`, `property_y`, `unit_x`, `unit_y`: 文字列 (Phase 2 はまだ QUDT 化していないので literal そのまま)
- `figure_name`, `comments`, `raw_figure_id`, `identifier`, `of_sample`: メタ
- `x_min`, `x_max`, `y_min`, `y_max`: float (Phase 1 集約値そのまま)
- `point_count`: int (truncate 前の全点数)
- `x`, `y`: `list[float]` (空配列もありうる; max_points 適用後)

エラー応答:
- 不正な IRI (http(s):// で始まらない) → `ValueError` → MCP 経由では `{"found": false, "error": ...}` で返る
- IRI に対応する triple なし → `CurveNotFoundError` → MCP 経由では同上

## SPARQL

```sparql
SELECT ?p ?o WHERE {
  { GRAPH ?g { <curve_iri> ?p ?o } }
  UNION
  { <curve_iri> ?p ?o }
}
```

`GRAPH ?g { ... } UNION { ... }` で「named graph に居る」「default graph に居る」両方を吸収。
これにより Phase 0.5 smoke test (default graph)、Phase 2 watcher (named graph) 両方の
データソースに対して同じ tool が動く。

## トランスポート

| 用途 | コマンド | port |
|---|---|---|
| compose / Crucible / Dify | `csv2rdf-mcp --transport http` (default) | 8002 (/mcp) |
| Claude Desktop / Cline / Cursor | `csv2rdf-mcp --transport stdio` | n/a |

stdio は FastMCP の標準動作で、サブプロセスとして起動された場合に使う。

## 環境変数

| 変数 | default | 説明 |
|---|---|---|
| `CSV2RDF_OXIGRAPH_URL` | `http://oxigraph:7878` (compose 内) | Oxigraph SPARQL endpoint |

## 設計判断メモ

- **`tools.py` を `server.py` から分離した理由**: FastMCP の transport を上げ下げせずに
  tool 本体だけ単体テストするため。`call_tool()` ラウンドトリップは `test_server.py`
  で別途検証する。
- **`SELECT ?p ?o` を使い `DESCRIBE` を避けた理由**: Oxigraph の DESCRIBE は Turtle で
  返るので Python 側で rdflib parse が必要。SELECT なら JSON-LD 風の dict が直接
  返るのでパースが軽い。raw triple を AI 用に整形する用途には充分。
- **`max_points` truncation**: curves.csv の x/y は数十〜数百点が普通だが将来 1k+ も
  ありうる。AI のコンテキスト枠を食わないよう先頭 N 点取得オプションを用意。
  truncate 前の `point_count` は別途返すので AI は「全体は N 点だが手元では先頭 100 点
  を見ている」と認識できる。

## Phase 2 #3 で意図的に外したもの

- **複数 curve のバッチ取得** — まずは 1 curve / 1 call で。並列発火が必要になったら
  AI 側で複数 call すれば足りる。
- **数値 resampling / interpolation** — クライアント側で実施 (`x=300` を補間で求める等)。
  サーバ側でやると tool の単一責務が崩れる。
- **認証** — closed-server / mcp-net 前提 (upload-api と同じ方針)。
- **QUDT 単位/量正規化** — Phase 2 #2 で別途。

## 撤退路

このサーバが落ちても Phase 1 機能 (togomcp + Oxigraph SPARQL) はそのまま使える。
AI には「raw x/y を取りたければ togomcp の `run_sparql` で `sd:xValuesJSON` を直接
読んで」と促せばよい (使いにくくはあるが動作する)。
