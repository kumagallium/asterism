# Self-built togomcp image for asterism Phase 1+.
#
# **Build context は repo root** (compose.yaml と Crucible のサブディレクトリ
# build 規約に合わせる)。Dockerfile を直接 docker build する場合も `docker
# build -f infra/togomcp/Dockerfile .` のように root から指定する。
#
# Crucible 登録時:
#   - github_url: https://github.com/kumagallium/asterism
#   - subdir: infra/togomcp  (Crucible は context=root, Dockerfile=subdir/Dockerfile
#     を自動で指定する)
#   - env_vars: OXIGRAPH_HOST=asterism-oxigraph  などを必要に応じて
#
# 方針 (docs/internal/option-b-architecture.md §9):
# - togomcp の commit SHA を ARG TOGOMCP_REF で固定 (dbcls 側の breaking change
#   を踏まない)。手動 build:
#     docker build --build-arg TOGOMCP_REF=<sha> -f infra/togomcp/Dockerfile -t togomcp .
# - 公式 image が出たら本 Dockerfile を削除し compose.yaml の build: → image: に切り替え

FROM python:3.11-slim AS base

ARG TOGOMCP_REF=54ab0d0857bb31610276499076a62dff57448fbd

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# git は togomcp の git+https インストールに必要
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# togomcp をピン留めしてインストール
RUN pip install "git+https://github.com/dbcls/togomcp.git@${TOGOMCP_REF}"

# asterism 側の MIE / endpoints.csv 差分を image に焼き込む。
# Crucible は bind mount を使わない (deployer は `docker run -p ... image` のみ)。
# 開発時の compose は `volumes: - ./data/togomcp:/data/togomcp:ro` で上書きすれば
# host 側で MIE を編集 → 即反映できる。
COPY data/togomcp /data/togomcp

# Oxigraph 起動待ち + TOGOMCP_DIR overlay 構築 entrypoint
# NOTE: Crucible の deployer は BuildKit を使わない legacy builder なので
# `COPY --chmod=...` 構文は不可。一段増えるが COPY + RUN chmod で互換性を取る。
COPY infra/togomcp/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

# 非 root で動かす
RUN useradd --create-home --uid 1000 togomcp \
    && chown -R togomcp:togomcp /data/togomcp
USER togomcp
WORKDIR /home/togomcp

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# togo-mcp-server は HTTP transport (0.0.0.0:8000) で待ち受ける。
# stdio が必要なら `docker run --rm togomcp togo-mcp-local` 等で呼び出し可。
CMD ["togo-mcp-server"]
