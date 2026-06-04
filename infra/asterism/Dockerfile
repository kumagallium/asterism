# asterism self-built FastMCP server image (Phase 2 #3).
#
# Bundles the FastMCP tools server (template_curve_fetch + future starrydata-
# specific tools) into a single Python process. Both asterism-ingest (for the
# OxigraphClient) and asterism-mcp-tools live in this monorepo, so build
# context MUST be the repo root.
#
# Build:
#   docker build -f infra/asterism/Dockerfile -t asterism .
#
# Crucible compatibility: legacy docker builder (no BuildKit). We use
# ``COPY`` + ``RUN chmod`` instead of ``COPY --chmod`` for the same reason
# as ``infra/togomcp/Dockerfile`` and ``infra/upload-api/Dockerfile``.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install sibling packages in dependency order: ingest first, then mcp tools
# (which depends on asterism-ingest). Both pyproject.toml reference
# ``readme = "../README.md"`` so /app/README.md must exist at build time.
COPY README.md /app/README.md
COPY ingest/pyproject.toml /app/ingest/pyproject.toml
COPY ingest/src /app/ingest/src
COPY mcp/pyproject.toml /app/mcp/pyproject.toml
COPY mcp/src /app/mcp/src

RUN pip install /app/ingest \
    && pip install /app/mcp

# #20 P2-2b: ship the dataset content (identity descriptors + the QUDT map) so the
# engine reads its source of truth from datasets/ in prod, not embedded fallbacks.
# .dockerignore keeps generated/licensed seed artifacts (csv, *.ttl) out of the image.
COPY datasets /app/datasets
ENV ASTERISM_DATASETS_ROOT=/app/datasets

# Closed-server / mcp-net deployment — running as root is acceptable for
# Phase 2; tightening to a non-root user is tracked alongside the upload-api
# Phase 3 follow-up.
WORKDIR /root

EXPOSE 8002

# asterism's CLI defaults to HTTP transport on 0.0.0.0:8002.
CMD ["asterism", "--host", "0.0.0.0", "--port", "8002"]
