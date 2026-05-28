# csv2rdf-mcp self-built FastMCP server image (Phase 2 #3).
#
# Bundles the FastMCP tools server (template_curve_fetch + future starrydata-
# specific tools) into a single Python process. Both csv2rdf-ingest (for the
# OxigraphClient) and csv2rdf-mcp-tools live in this monorepo, so build
# context MUST be the repo root.
#
# Build:
#   docker build -f infra/csv2rdf-mcp/Dockerfile -t csv2rdf-mcp .
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
# (which depends on csv2rdf-ingest). Both pyproject.toml reference
# ``readme = "../README.md"`` so /app/README.md must exist at build time.
COPY README.md /app/README.md
COPY ingest/pyproject.toml /app/ingest/pyproject.toml
COPY ingest/src /app/ingest/src
COPY mcp/pyproject.toml /app/mcp/pyproject.toml
COPY mcp/src /app/mcp/src

RUN pip install /app/ingest \
    && pip install /app/mcp

# Closed-server / mcp-net deployment — running as root is acceptable for
# Phase 2; tightening to a non-root user is tracked alongside the upload-api
# Phase 3 follow-up.
WORKDIR /root

EXPOSE 8002

# csv2rdf-mcp's CLI defaults to HTTP transport on 0.0.0.0:8002.
CMD ["csv2rdf-mcp", "--host", "0.0.0.0", "--port", "8002"]
