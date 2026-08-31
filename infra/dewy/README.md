# Dewy — プル型宣言的デプロイ（asterism-prod）

[Dewy](https://github.com/linyows/dewy)（MIT・OSS）によるプル型デプロイの導入。
サーバ側 agent がレジストリ（GHCR）をポーリングし、新しい semver タグを検出すると
**pull → 起動 → health 合格 → プロキシ切替 → 旧コンテナ drain** をゼロダウンタイムで
自動実行する。SSH 鍵・deploy 鍵・webhook 等の inbound 経路は一切増やさない。

現状のスコープ = **api 1 サービスの PoC**（compose 本体と並行稼働・非破壊）。
実証記録は [`docs/reports/dewy-pull-deploy-poc.md`](../../docs/reports/dewy-pull-deploy-poc.md)。

```
[GitHub Actions]                         [asterism-prod]
 git tag api-vX.Y.Z                        systemd: dewy-asterism-api.service
   └─ build → push ─▶ ghcr.io ◀── poll ──── dewy container（30s 間隔）
       ghcr.io/kumagallium/                  ├─ 新タグ検出 → pull → 起動
       asterism-api:vX.Y.Z                   ├─ GET /health 合格を確認
                                             ├─ :18080 プロキシを新コンテナへ切替
                                             └─ 旧コンテナ drain → 停止（7 世代保持）
```

## 前提

- イメージ `ghcr.io/kumagallium/asterism-api` が **public**（pull 認証不要 =
  サーバに credential を置かない）。
- api コンテナの `/health` は oxigraph への ping を含むため、PoC では使い捨て
  oxigraph（`dewy-poc-oxigraph`）を専用ネットワーク `dewy-poc` に並走させる。

## サーバ初期セットアップ（1 回だけ）

```bash
# 1. dewy バイナリ（checksum 検証つき）
cd /tmp
curl -fsSL -O https://github.com/linyows/dewy/releases/download/v2.20.0/dewy_linux_x86_64.tar.gz
curl -fsSL -O https://github.com/linyows/dewy/releases/download/v2.20.0/checksums.txt
grep linux_x86_64 checksums.txt | sha256sum -c -
tar xzf dewy_linux_x86_64.tar.gz dewy && sudo install -m 0755 dewy /usr/local/bin/dewy
dewy --version

# 2. 作業ディレクトリ + PoC 用の隔離ネットワークと使い捨て oxigraph
sudo mkdir -p /opt/dewy/asterism-api && sudo chown ubuntu:ubuntu /opt/dewy/asterism-api
docker network create dewy-poc
docker run -d --name dewy-poc-oxigraph --network dewy-poc --restart unless-stopped \
  -v dewy-poc-oxigraph-data:/data ghcr.io/oxigraph/oxigraph:latest \
  serve --location /data --bind 0.0.0.0:7878

# 3. systemd ユニット（このディレクトリのファイルを配置して起動）
sudo cp dewy-asterism-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dewy-asterism-api
journalctl -u dewy-asterism-api -f   # 検出→pull→起動→health→切替 のログが流れる
```

## リリース（デプロイはこれだけ）

```bash
git tag api-v0.0.2 && git push origin api-v0.0.2
```

GitHub Actions（`.github/workflows/prod-api-release.yml`）がイメージをビルドして
GHCR へ push → 30 秒以内に Dewy が検出し、ゼロダウンタイムで切り替わる。
サーバへのログインは不要。`workflow_dispatch`（tag 入力）でも同じことができる。

確認（サーバ上・任意）:

```bash
curl -s http://127.0.0.1:18080/health          # {"status":"ok","oxigraph":true}
docker ps --filter label=dewy.managed          # 現行コンテナとそのイメージタグ
```

## ロールバック

Dewy は「レジストリ上の最新 semver」を追うため、巻き戻しは**検証済み旧イメージへの
前進タグ**（roll-forward）で表現する。ビルドは走らず manifest の付け替えだけなので数秒:

```bash
# v0.0.2 に問題 → v0.0.1 のイメージを v0.0.3 として再発行
gh workflow run prod-api-release.yml -f tag=v0.0.3 -f source_tag=v0.0.1
```

また、新イメージが `/health` に合格しない場合は**そもそも昇格されない**
（Dewy が新コンテナを破棄し、旧バージョンが動き続ける = fail-closed）。

## 撤去（PoC をやめるとき）

```bash
sudo systemctl disable --now dewy-asterism-api
sudo rm /etc/systemd/system/dewy-asterism-api.service && sudo systemctl daemon-reload
docker rm -f $(docker ps -aq --filter label=dewy.managed) 2>/dev/null
docker rm -f dewy-poc-oxigraph && docker network rm dewy-poc
docker volume rm dewy-poc-oxigraph-data
```

既存の compose スタックには最初から触れていないため、影響はない。

## 次段（PoC の外・計画）

- caddy の `/api` upstream を Dewy プロキシ（`host.docker.internal:18080`）へ向け、
  本番トラフィックを Dewy 管理コンテナに移す（compose の api は退役）。
- demo-agent / docling への横展開（同じ unit の複製）。
- さくらのオブジェクトストレージ（S3 互換）を使う場合は `--registry
  "s3://jp-north-1/<bucket>/<prefix>?endpoint=https://s3.isk01.sakurastorage.jp"`
  形式に差し替え可能（Dewy README 記載の設定例）。GHCR 停止時の代替経路。
