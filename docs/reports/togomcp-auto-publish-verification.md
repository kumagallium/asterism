# togomcp 自動配信の実機一周検証（+DB 名正規化バグの発見と修正）

- **Date**: 2026-08-12
- **対象**: v0.12.0 の togomcp 自動配信（#322）+ T10（#321）+ 本レポートと同 PR の DB 名正規化修正

## Question

promote した asterism データセットは、**本物の togomcp**（dbcls、pin 54ab0d0）から
`find_databases` → `get_MIE_file` → `run_sparql` の正規ワークフローで実際に発見・照会
できるか。retract / reinstate / delete はカタログと整合するか。

## Method

常用スタックと完全分離した検証 compose（oxigraph 17878 / togomcp 18000 /
upload-api 18080・別コンテナ名・別 project）を起動し、API だけで一周:

1. 最小データセット（熱電測定 3 行 CSV・Mapping IR 1 map・例示クエリ 2 本入り MIE）を
   `/api/materialize` → `/source` → `/ingest` → `/promote`
2. `data/togomcp/` の配信物（mie/*.yaml・endpoints.csv）を検分
3. togomcp を JSON-RPC で直接叩くプローブで restart 前後の可視性を測定
4. 配信 MIE の例示クエリを**無編集で** `run_sparql` に渡し実行
5. retract → reinstate → delete(force) でカタログ整合を確認

## Result

- materialize: T1-T10 が本番コンテナで動作（T10 = 例示 2 本 parse pass）。
- promote 応答 `togomcp: {published: true}`、投影 MIE は endpoint/graphs を現在の
  live version graph に固定し、両例示クエリへ `FROM` + `FROM NAMED` を文法位置に注入。
- **バグ発見**: 初回配信（dataset id `verify-togomcp-815bc56a` そのまま）では
  `get_MIE_file` は読めるのに **`find_databases` から無言で消えた**。原因は togomcp の
  `load_sparql_endpoints` が DB 名を `lower().replace(" ", "_").replace("-", "")` で
  キー化し、そのキー名で `<key>.yaml` を探すため（キー `verifytogomcp815bc56a` の
  yaml が不在 → keywords 空 → 検索不一致）。**修正**: 配信側が最初から正規形
  （`togomcp_sync.togomcp_database`）でファイル名と行を書く。
- 修正後の再 ingest + re-promote（version 2）で一周成功:
  - `find_databases("thermoelectric")` → **starrydata と並んで
    `verifytogomcp815bc56a` がヒット**（matched_keywords=[thermoelectric]）
  - `get_MIE_file` → 投影済み MIE（graphs=…/v2）
  - `run_sparql`（例示クエリ無編集）→ 実データ 3 行が ZT 降順で返る
    （SnSe 2.3 / PbTe 1.4 / Bi2Te3 0.95）
- ライフサイクル: retract → MIE ファイル・CSV 行とも消滅（starrydata 手動行は温存）。
  reinstate → 再配信され graphs は**実ストアの liveGraph ポインタから解決した v2**
  （fake では到達できなかった経路）。delete(force) → unlist。
- 反映タイミング（実測）: 本 compose の togomcp は起動時 overlay 方式のため、
  **`get_MIE_file` 含む全ツールが restart 後に反映**（restart 前は "No MIE file"）。

## Conclusion

「T10 を通った検証済み MIE が、promote と同時に live graph へピン留めされて
togomcp の棚（公開 RDF 群と同じカタログ）に並ぶ」一本道が、本物の togomcp で
end-to-end に成立する。前提として DB 名は togomcp の正規形で配信する必要があり、
本 PR がそれを実装した（正規形は応答 `togomcp.database` で開示される）。

## Limitations

- togomcp は pin（54ab0d0）での検証。上流が正規化規則を変えれば追従が必要
  （規則は `togomcp_database` の docstring に出典付きで記録）。
- 検証データは 3 行の合成 CSV。大規模データでの配信は promote と同じ O(1)
  （ファイル書き出しのみ）だが未計測。
- ハイフン除去により理論上は異なる id が同名に潰れ得る（registry id はランダム
  接尾辞を持つため実質衝突しない）。

## Reproduce

```bash
# リポジトリ直下（compose.verify.yaml は untracked の検証用: oxigraph/togomcp/
# upload-api を 17878/18000/18080 で起動し ASTERISM_TOGOMCP_DIR=/data/togomcp を設定）
docker compose -p asterism-verify -f compose.verify.yaml up -d --build
# materialize → source → ingest → promote を API で実行（X-Asterism-Token 必須）
# 配信確認: data/togomcp/mie/<正規形>.yaml と resources/endpoints.csv
docker restart asterism_verify_togomcp
# JSON-RPC (initialize → tools/call) で find_databases / get_MIE_file / run_sparql
```
