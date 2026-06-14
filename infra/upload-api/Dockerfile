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

# pandoc — the OPTIONAL Word (.docx) -> JATS converter for the document-ontology
# layer (a .docx is structured OOXML, so pandoc transcribes it deterministically;
# the result is parsed by the defusedxml structurer downstream). A deliberate
# image addition: the API only ever runs pandoc as a hardened, bounded subprocess
# on the source-attach path, and degrades with a clear 4xx if it is absent — so a
# build that omits this line still works for CSV/JSON/XML, just not Word.
RUN apt-get update \
    && apt-get install -y --no-install-recommends pandoc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the sibling packages in dependency order: ingest + step0 first, then
# api (which depends on BOTH asterism-ingest and asterism-step0). The
# api/pyproject.toml uses ``[tool.uv.sources]`` for local dev under uv; pip relies
# on those siblings already being installed when it resolves the api package — so
# they MUST be pip-installed here (asterism-step0 is not on any index, so omitting
# it makes ``pip install /app/api`` fail with "No matching distribution").
#
# Extras pulled so production runs the FULL workbench flow, not a half image:
#   - ingest[substrate] -> Morph-KGC. The human-gated ingest (POST
#     /api/datasets/{id}/ingest) + the watcher run approved RML through the
#     declarative substrate; ``asterism.substrate`` imports ``morph_kgc`` lazily,
#     so WITHOUT this the container starts but every ingest fails at runtime
#     (RuntimeError: morph-kgc not installed). #15.
#   - step0[anthropic] -> the Anthropic SDK. ``/api/propose`` uses step0's
#     ``AnthropicLLMClient`` (imports ``anthropic`` lazily at call time), so
#     without it propose fails at runtime. The user still brings their own API
#     key per request (never baked into the image).
#
# All pyproject.toml files reference ``readme = "../README.md"`` so hatchling
# needs to find /app/README.md during build.
COPY README.md /app/README.md
COPY ingest/pyproject.toml /app/ingest/pyproject.toml
COPY ingest/src /app/ingest/src
COPY step0/pyproject.toml /app/step0/pyproject.toml
COPY step0/src /app/step0/src
COPY api/pyproject.toml /app/api/pyproject.toml
COPY api/src /app/api/src

RUN pip install "/app/ingest[substrate]" \
    && pip install "/app/step0[anthropic]" \
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
