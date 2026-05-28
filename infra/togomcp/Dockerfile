# Self-built togomcp image for csv2rdf-mcp Phase 1+.
#
# 設計プラン §3 / option-b-architecture.md の Option B で togomcp は MIT な
# Python パッケージ。**公式 Docker image はまだ存在しない**ため、Phase 1 段階
# では自前で build して compose に組み込む。
#
# 方針 (memory/csv2rdf_mcp_phase05_decision.md):
# - togomcp の commit SHA を引数として固定 (TOGOMCP_REF)。手動で上げる
#   ことで dbcls 側の breaking change を踏まない。
# - 公式 image が出たらこの Dockerfile を消して compose.yaml の image: 行を
#   切り替えるだけで済む。
# - 配布: csv2rdf-mcp 側の ghcr.io/kumagallium/csv2rdf-togomcp:vX.Y.Z として
#   将来 push する想定 (Phase 2)。それまでは compose の build: で都度 build。

FROM python:3.11-slim AS base

# togomcp commit to pin. Update via:
#   docker compose build --build-arg TOGOMCP_REF=<new-sha> togomcp
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

# Oxigraph が起動するまで待つ entrypoint
COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh

# 非 root で動かす (TOGOMCP_DIR は read-only bind mount のため UID は柔軟に)
RUN useradd --create-home --uid 1000 togomcp
USER togomcp
WORKDIR /home/togomcp

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# togo-mcp-server は HTTP transport (0.0.0.0:8000) で待ち受ける。
# stdio が必要なら togo-mcp-local を直接呼び出す (`docker compose run togomcp togo-mcp-local`)。
CMD ["togo-mcp-server"]
