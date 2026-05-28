# 設計プラン / handoff からの逸脱ログ

設計プランに沿って進められなかった判断や、handoff からの逸脱はここに理由付きで残す。

| 日付 | 項目 | 元の指示 | 実際の判断 | 理由 |
|---|---|---|---|---|
| 2026-05-27 | GitHub owner | `m-kumagai/csv2rdf-mcp` (handoff §2) | `kumagallium/csv2rdf-mcp` | `m-kumagai` は public repo 0 件・2017 作成のサブアカウントで、現在の gh CLI 認証は `kumagallium` (active, 24 repos)。手戻り回避のためユーザに確認した上で `kumagallium` を採用。将来 `m-kumagai` 側に移したくなったら `gh repo transfer` で移送可。インスタンス IRI には GitHub Pages URL を使う前提なので、最終的に IRI で参照する owner 名は **Phase 1 の §4.0 IRI 永続化戦略決定時に再検討する**。 |
