# `skills/public/` — togomcp v2.18.0 以降が起動時に必須とするディレクトリ

`dbcls/togomcp` v2.18.0 (2026-09-17, `feat(workflows): serve public skills via
get_workflow and skill:// resources`) から、`togo_mcp` パッケージは **import 時**に
`$TOGOMCP_DIR/skills/public/` を読む（`rdf_portal.py` の
`_skills.load_registry(SKILLS_DIR)`）。ディレクトリが無いと
`SkillRegistryError: skills root not found` で `togo-mcp-local` が起動前に落ち、
クライアント側には `MCPError: Connection closed` としてしか見えない。

この smoke test は MIE と endpoints.csv だけを検証したいので、ワークフロー
(Agent Skills) は **空**で良い。`load_registry` はディレクトリ直下の
`<name>/SKILL.md` だけを拾い、この README のような通常ファイルは無視する。

本番 (`infra/togomcp/entrypoint.sh`) はパッケージ同梱の `data/` を丸ごとコピーした
overlay を `TOGOMCP_DIR` にするので、同梱の `skills/public/` がそのまま乗り、
この問題は起きない。
