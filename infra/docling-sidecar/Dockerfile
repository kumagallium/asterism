# Docling PDF→structure sidecar — the ML step, isolated OUT of the api image.
#
# Asterism keeps the ingest path deterministic + ML-free (CLAUDE.md). Born-digital
# PDF needs layout-aware ML (Docling) to recover structure, so that heavy dependency
# lives ONLY in this separate service. The api calls it over the closed network
# (ASTERISM_DOCLING_URL) and degrades with a clear 4xx if it is absent.
#
# Self-contained build context (this dir only, no monorepo deps):
#   docker build -f infra/docling-sidecar/Dockerfile -t asterism-docling-sidecar infra/docling-sidecar
#
# Spec: GPU NOT required (born-digital, OCR off). ~4-8 GiB RAM is comfortable;
# concurrency is pinned to 1 conversion at a time (app.py) so peak RAM is bounded.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    # Bake model artifacts into the image so the container runs OFFLINE + reproducibly.
    HF_HOME=/opt/models \
    DOCLING_ARTIFACTS_PATH=/opt/models/docling

# Runtime libs some Docling/torch wheels dlopen (image ops, OpenMP). Kept minimal —
# OCR is off, so no tesseract.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch first (the default PyPI torch wheel pulls CUDA = multi-GB bloat we do
# not need). Then the rest. Pinned docling = reproducible conversion.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch
COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-download the layout/table models so the FIRST request does not block on a network
# fetch and the image is self-contained (offline determinism). Best-effort: if the
# warm-up cannot reach the model host at build time, the models download on first use.
RUN python -c "from docling.document_converter import DocumentConverter; DocumentConverter()" \
    || echo "model warm-up skipped (will download on first /convert)"

COPY app.py .

EXPOSE 8090

# Single worker: model memory is loaded per process, and app.py already serialises
# conversions to one at a time — more workers would just multiply peak RAM.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
