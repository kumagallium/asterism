# 本番デプロイをプル型（Dewy）に置き換えられるか — asterism-prod 実機 PoC

- 日付: 2026-08-31（実測はすべて同日 JST・asterism-prod 実機）
- branch: `feat/dewy-pull-deploy`
- 関連: [`production-deployment.md`](../architecture/production-deployment.md)（現行構成）・
  [`infra/dewy/`](../../infra/dewy/)（導入物と運用手順）・
  [Dewy](https://github.com/linyows/dewy) v2.20.0（MIT・linyows）

## Question

asterism-prod の手動デプロイ（SSH ログイン → `git pull` → `docker compose build`）を、
**プル型の宣言的デプロイ**に置き換えられるか。具体的には 1 サービス（api）について
「**レジストリへ新リリースを置く → サーバ側 agent が自動検出 → ゼロダウンタイムで
デプロイ → graceful restart**」の一周と**ロールバック**が、サーバへの inbound 経路
（SSH 鍵・deploy 鍵・webhook）を一切増やさずに実機で成立するか。

## Method

```
[GitHub Actions]                            [asterism-prod（さくらのクラウド）]
 git tag api-vX.Y.Z を push                   systemd: dewy-asterism-api.service
   └ build (linux/amd64)                        dewy container が GHCR を 30s 間隔で poll
     └ push ──▶ ghcr.io/kumagallium/              ├ 新 semver タグ検出 → docker pull
                asterism-api:vX.Y.Z ◀── poll ──── ├ 新コンテナ起動（dewy-poc 網・127.0.0.1:: 採番）
                （public・pull 認証不要）           ├ GET /health 合格を確認（不合格なら破棄）
                                                  ├ 内蔵 TCP プロキシ :18080 のバックエンド切替
                                                  └ 旧コンテナ drain → graceful stop（イメージ 7 世代保持）
```

- **供給側** = [`prod-api-release.yml`](../../.github/workflows/prod-api-release.yml)。
  `api-vX.Y.Z` タグ push で既存の `infra/upload-api/Dockerfile` を linux/amd64 ビルドし
  GHCR へ push。イメージは public リポジトリの可視性を継承して public になったため、
  **サーバ側に pull 認証も不要**（credential ゼロ）。
- **サーバ側** = Dewy v2.20.0 バイナリ（checksums.txt 検証済み）＋
  [`dewy-asterism-api.service`](../../infra/dewy/dewy-asterism-api.service)。
  既存 compose スタックには**一切触れない**並行導入: コンテナは専用ネットワーク
  `dewy-poc` に入り、`/health` が oxigraph ping を含むため**使い捨て oxigraph** を併走。
- **観測** = ① `journalctl`（検出→pull→起動→health→切替→drain の全工程ログ）
  ② 0.3 秒間隔の連続ヘルスプローブ（`curl :18080/health`・切替を跨いで記録）
  ③ `docker inspect` の image ID 照合（ロールバックが「同一バイト」かの証明）。

## Result

**一周のタイムライン（api-v0.0.2 のリリース・すべて実測）:**

| 時刻 (JST) | 事象 |
|---|---|
| 14:27:08 | `git push origin api-v0.0.2`（人の操作はこれで終わり） |
| 14:28:38 | GitHub Actions ビルド完了・GHCR へ push（90 秒） |
| 14:28:42 | Dewy がポーリングで新リリース検出 → pull |
| 14:28:46 | 新コンテナ起動（`asterism-api-1788154122-0`） |
| 14:28:49 | `/health` 合格 → プロキシに新バックエンド追加 → 旧を除去 → 旧 graceful stop（timeout 30s） |
| 14:28:50 | デプロイ完了 = **タグ push から 102 秒で本番反映・サーバログイン 0 回** |

**ロールバック（v0.0.3 = v0.0.1 の再発行）:**
Dewy は「レジストリ上の最新 semver」を追うため、巻き戻しは**検証済み旧イメージへの
前進タグ**で表現する（`docker buildx imagetools create` による manifest 付替のみ・数秒・
再ビルド無し）。14:30:30 検出 → 14:30:34 切替完了。稼働コンテナの image ID は

```
v0.0.1_id = sha256:513aa66e57511fc691f4e696acc8bbea40b9a6d139fe53818ed07043805d922a
v0.0.3_id = sha256:513aa66e57511fc691f4e696acc8bbea40b9a6d139fe53818ed07043805d922a
```

と**バイト同一**＝「検証済みの旧版に戻った」ことを digest で証明できた。

**ゼロダウンタイム:** 0.3 秒間隔の連続プローブ **863 リクエストすべて 200**
（更新切替とロールバック切替の両方を跨いで途切れ 0・タイムアウト 0）。

**fail-closed（意図せず得られた実証）:** 導入初回、コンテナが非 root で起動する
Dewy の既定（`--user` 自動注入）と root 前提の api イメージが衝突し、新コンテナが
起動 3 秒で crash する状態を作ってしまった。このとき Dewy は **health 不合格の新版を
自動破棄し、昇格させなかった**（14:23:17 ログ: `Failed to start container, rolling
back` → 旧状態のまま）。「壊れたリリースを push しても本番は倒れない」の実地確認。

**セキュリティ境界:** Dewy admin API は `127.0.0.1:17539`。プロキシ `:18080` は全 IF
bind だが、ufw が default deny (incoming) で 18080 の許可ルールは無く**外部非露出**を
実機確認（到達はループバックのみ）。通信はサーバ→GHCR の outbound HTTPS のみで、
**inbound の鍵・トークン・webhook は 1 つも増えていない**。

## Conclusion

**成立。** 「レジストリに置く＝デプロイ」（望ましい状態はレジストリ上の最新 semver
タグという宣言であり、サーバ側 agent がそこへ収束する）が、api 1 サービスについて
実機で一周した。health ゲート付き切替・drain・7 世代保持・数秒ロールバックまで
Dewy 標準機能のみで達成し、独自スクリプトはゼロ。現行の手動デプロイ
（SSH → build 待ち）に対し、人の操作は「タグを push する」1 点に縮む。

## Limitations（どこまでが実機・どこからが計画か）

- **実機で実証済み** = 上記すべて（検出・自動デプロイ・ゼロダウンタイム切替・
  health 不合格時の自動破棄・digest 同一のロールバック・非露出の確認）。
- **PoC の枠** = 対象は api 1 サービスのみ。本番トラフィックはまだ従来の compose
  api が処理しており（並行導入・本番非破壊）、caddy の upstream を Dewy プロキシへ
  向ける本配線は次段。PoC コンテナが繋ぐ oxigraph は使い捨てで、本番データ・
  `asterism.env` のシークレットは未注入。
- **計画（手順書のみ）** = 全 6 サービスへの横展開・さくらのオブジェクトストレージ
  （S3 互換）への レジストリ切替（[`infra/dewy/README.md`](../../infra/dewy/README.md) §次段）。
- Dewy の制約 = `img://` レジストリは監査ログ（audit tracking）非対応（公式 docs 明記）。
  ロールバック用 `workflow_dispatch` 経路は本 PR が main に入ってから有効
  （PoC ではローカルの既存 GHCR 認証で re-tag を発行した）。

## Reproduce

```bash
# 供給側（リリース）— 人の操作はこれだけ
git tag api-v0.0.2 && git push origin api-v0.0.2

# サーバ側の初期導入（1 回だけ・infra/dewy/README.md の全文）
#   dewy v2.20.0 の checksum 検証つき導入 → dewy-poc 網 + 使い捨て oxigraph →
#   systemd unit 配置 → enable --now

# 観測
sudo journalctl -u dewy-asterism-api -f          # 検出→pull→health→切替→drain
curl -s http://127.0.0.1:18080/health            # {"status":"ok","oxigraph":true}
docker ps --filter label=dewy.app                # 稼働タグの確認
docker inspect -f '{{.Id}}' ghcr.io/kumagallium/asterism-api:v0.0.1  # digest 照合

# ロールバック（v0.0.1 へ）
gh workflow run prod-api-release.yml -f tag=v0.0.3 -f source_tag=v0.0.1
```

Actions 実測 run: [v0.0.1 (71s)](https://github.com/kumagallium/asterism/actions/runs/33359851055)・
[v0.0.2 (90s)](https://github.com/kumagallium/asterism/actions/runs/33360587523)

## Addendum（2026-08-31 同日）— サプライチェーン対策の実装と実証

**契機**: 「デプロイ構成を公開リポジトリに書いてよいか」の検討。分析の結論=構成の
公開自体は攻撃面を増やさない（ドメイン・ホスト構成は既に ROADMAP で公開済み・
GitOps の標準実践）が、プル型の本質として**「レジストリに書ける権限 = 本番実行
権限」**が単一信頼点になる。その直接の手当てとして以下を実装・実機実証した。

**実装**（詳細 = [`infra/dewy/README.md`](../../infra/dewy/README.md) §セキュリティ）:
①タグ Ruleset `protect-api-release-tags`（`api-v*` の作成/変更/削除を admin のみに。
v0.0.9 push 時の `remote: Bypassed rule violations` でルール発動と admin bypass 双方を
実地確認）②全ビルドへの **cosign keyless 署名**（GitHub OIDC・Rekor 記録・秘密鍵レス。
署名は digest に紐づくため re-tag ロールバックも検証を通る）③サーバ `verify-latest.sh`
を before-deploy-hook に配線 ④Dependabot（SHA 固定 action の更新）⑤systemd
ハードニング（NoNewPrivileges/PrivateTmp）。

**実証（本番 journal・2026-08-31）**: 未署名の最新（v0.0.8）に対し hook が
`SIGNATURE VERIFICATION FAILED` → `ERROR Before deploy hook failure` を記録。
署名付き v0.0.9 の検出時は `signature OK` → デプロイ成功。**A/B 両方が実ログで確認できた**。

**発見（upstream 制約）**: Dewy v2.20.0 の container 経路は **before-hook 失敗でも
デプロイを続行する**（`lifecycle.go` `applyContainerDeployment` — エラーをログするのみ。
docs の「before 失敗で中止」は server/assets 経路の動作）。実際、未署名 v0.0.8 の
検証失敗後もデプロイは続行された（上記ログ）。したがって現状の署名検証は
**強制ゲートでなく検知**である。upstream が container 経路でも中止するよう修正すれば、
同じ配線のまま fail-closed になる（issue 報告は別途判断）。

**残（ユーザー操作 or 判断待ち)**: 専用実行ユーザーへの切替（`useradd` 1 行）・
`--notifier`（Slack webhook 提供時に 1 行）・upstream issue。
