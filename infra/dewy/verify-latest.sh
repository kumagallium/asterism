#!/bin/sh
# Dewy before-deploy-hook: レジストリ最新 semver タグの cosign 署名を検証する。
#
# Dewy はデプロイ対象のタグを hook に渡さない（hooks.go: 環境変数の継承のみ）ため、
# Dewy と同じ「最新 semver」をこのスクリプトが独立に解決して検証する。解決が万一
# ズレても「最新が未署名なら失敗」という安全側の挙動になる。
#
# ⚠ 既知の制約（Dewy v2.20.0）: container 経路は before-hook が失敗しても
#   デプロイを続行する（lifecycle.go:346-355 — docs の「失敗で中止」は server/assets
#   経路のみ）。したがって現状これは強制ゲートではなく「検知」— 未署名イメージが
#   デプロイされると journal に ERROR が残る。upstream が修正されれば同じ配線のまま
#   fail-closed のゲートになる。
#
# 検証内容: 「このイメージはこのリポジトリの prod-api-release.yml が GitHub OIDC で
# 署名したものか」。鍵ファイル不要（keyless・Rekor 透明性ログ照合）。
set -eu

IMAGE_PATH="kumagallium/asterism-api"
IMAGE="ghcr.io/$IMAGE_PATH"

TOKEN=$(curl -fsS "https://ghcr.io/token?scope=repository:$IMAGE_PATH:pull" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")

# 最新 semver タグを解決（vX.Y.Z のみ・pre-release 除外 = Dewy の既定と同じ）
TAG=$(curl -fsS -H "Authorization: Bearer $TOKEN" \
  "https://ghcr.io/v2/$IMAGE_PATH/tags/list?n=1000" | python3 -c "
import json, re, sys
tags = [t for t in json.load(sys.stdin).get('tags', []) if re.fullmatch(r'v\d+\.\d+\.\d+', t)]
tags.sort(key=lambda t: tuple(map(int, t[1:].split('.'))))
print(tags[-1] if tags else '')")

[ -n "$TAG" ] || { echo "verify-latest: no semver tags found" >&2; exit 1; }

echo "verify-latest: checking signature of $IMAGE:$TAG"
cosign verify \
  --certificate-identity-regexp '^https://github\.com/kumagallium/asterism/\.github/workflows/prod-api-release\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "$IMAGE:$TAG" > /dev/null 2>&1 \
  || { echo "verify-latest: SIGNATURE VERIFICATION FAILED for $IMAGE:$TAG — image is not signed by prod-api-release.yml" >&2; exit 1; }

echo "verify-latest: signature OK ($IMAGE:$TAG)"
