# `data/togomcp/` — togomcp の `TOGOMCP_DIR`

このディレクトリは [`compose.yaml`](../../compose.yaml) で togomcp container に
`TOGOMCP_DIR=/data/togomcp` 経由で渡される。

`dbcls/togomcp` v0.1.0 のソース ([`server.py`](https://github.com/dbcls/togomcp/blob/main/togo_mcp/server.py)) は以下を `TOGOMCP_DIR` から読む:

| Path | 役割 |
|---|---|
| `mie/<dbname>.yaml` | MIE (Metadata Interoperability Exchange) ファイル |
| `resources/endpoints.csv` | DB 名 → SPARQL endpoint URL のマッピング |
| `resources/MIE_prompt.md` | (optional) MIE_prompt.md 読み込み |
| `resources/togomcp_usage_guide_v5.md` | (optional) AI 向け usage guide |
| `resources/structured_query_insight.md` | (optional) |
| `docs/togomcp-intro.html` | (optional) intro HTML |
| `kw_search/` | (optional) keyword-search 命令 |
| `rdf-config/` | (optional) rdf-config テンプレ |

このリポジトリでは **必要最低限の 2 ファイル**だけを置く:

- [`resources/endpoints.csv`](resources/endpoints.csv) — `starrydata` → `http://oxigraph:7878/query` を登録
- [`mie/starrydata.yaml`](mie/starrydata.yaml) — starrydata の MIE 本番版

`resources/` 内の他のファイル (MIE_prompt.md / togomcp_usage_guide_v5.md など) は
**togomcp container の `/togo_mcp/data/resources/` にデフォルトで含まれている**
が、`TOGOMCP_DIR` を上書きすると **そちらが読まれなくなる**。

Phase 1 段階の運用案 (どちらか選ぶ):

1. **空欠落で運用** — Phase 1 では MIE と endpoints.csv だけあれば
   ツール (`get_MIE_file`, `run_sparql`, `get_graph_list`) は動く。warning は出る
2. **デフォルトをコピーして同梱** — `dbcls/togomcp/togo_mcp/data/resources/` から
   ファイルをコピーしてバージョン管理する。プロビジョニングを `Makefile` に
   `make pull-togomcp-defaults` として用意する想定。Phase 2 で対応

> 関連: [`docs/architecture/option-b.md`](../../docs/architecture/option-b.md) §3 / §7
