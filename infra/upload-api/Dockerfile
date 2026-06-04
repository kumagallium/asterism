# Self-built asterism upload API image (Phase 2).
#
# Bundles the FastAPI service + the watcher loop into a single Python process.
# Both packages live in the monorepo (api/ + ingest/), so build context MUST be
# the repo root.
#
# Build:
#   docker build -f infra/upload-api/Dockerfile -t asterism-upload-api .
#
# Crucible compatibility: legacy docker builder (no BuildKit). We use
# ``COPY`` + ``RUN chmod`` instead of ``COPY --chmod`` for the same reason
# as ``infra/togomcp/Dockerfile``.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the sibling packages in dependency order: ingest first, then api
# (which depends on asterism-ingest). The api/pyproject.toml uses
# ``[tool.uv.sources]`` for local dev under uv; pip relies on the fact that
# asterism-ingest is already installed when it resolves the api package.
#
# Both pyproject.toml files reference ``readme = "../README.md"`` so hatchling
# needs to find /app/README.md during build.
COPY README.md /app/README.md
COPY ingest/pyproject.toml /app/ingest/pyproject.toml
COPY ingest/src /app/ingest/src
COPY api/pyproject.toml /app/api/pyproject.toml
COPY api/src /app/api/src

RUN pip install /app/ingest \
    && pip install /app/api

# #20 P2-2b: ship the dataset content (identity descriptors + the QUDT map) so the
# engine reads its source of truth from datasets/ in prod, not embedded fallbacks.
# .dockerignore keeps generated/licensed seed artifacts (csv, *.ttl) out of the image.
COPY datasets /app/datasets
ENV ASTERISM_DATASETS_ROOT=/app/datasets

# /data/sources is provided by a compose bind mount; the watcher auto-creates
# per-kind subdirs on startup. On macOS / Linux the bind mount is typically
# root-owned, so we run the container as root for MVP. The closed-server
# threat model (mcp-net only, no Internet exposure) makes the UID boundary
# low-value here; tightening to a non-root user is tracked as a Phase 3 item.
RUN mkdir -p /data/sources
WORKDIR /root

EXPOSE 8080

# asterism-api invokes uvicorn with the factory-mode app builder.
CMD ["asterism-api", "--host", "0.0.0.0", "--port", "8080"]
