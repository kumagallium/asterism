# demo-agent: ARiSE grounded-answer service (consuming layer, separate from core).
FROM python:3.11-slim
WORKDIR /app

# Install the in-repo asterism packages so real mode can import the typed tools
# (asterism_mcp.tools) + the Oxigraph client (asterism.oxigraph_client), plus the
# web deps. mcp pulls fastmcp; that is fine inside this consuming container.
# anthropic + openai back the multi-provider Ask loop (Claude + any OpenAI-compatible
# endpoint, e.g. Sakura AI Engine); httpx (for the usage POST) comes via ingest.
# ingest/ and mcp/ pyproject.toml both set `readme = "../README.md"`, so hatchling
# needs /src/README.md to exist when it builds their metadata (the api image does
# the same). Without this the build fails: "Readme file does not exist: ../README.md".
COPY README.md /src/README.md
COPY ingest /src/ingest
COPY mcp /src/mcp
RUN pip install --no-cache-dir /src/ingest /src/mcp fastapi "uvicorn[standard]" anthropic openai

# #20 P2-2b: ship the dataset content (identity descriptors + the QUDT map) so the
# engine reads its source of truth from datasets/ in prod, not embedded fallbacks.
# .dockerignore keeps generated/licensed seed artifacts (csv, *.ttl) out of the image.
COPY datasets /app/datasets
ENV ASTERISM_DATASETS_ROOT=/app/datasets

COPY demo-agent/app.py /app/app.py
EXPOSE 8090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
