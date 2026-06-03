# demo-agent: ARiSE grounded-answer service (consuming layer, separate from core).
FROM python:3.11-slim
WORKDIR /app

# Install the in-repo asterism packages so real mode can import the typed tools
# (asterism_mcp.tools) + the Oxigraph client (asterism.oxigraph_client), plus the
# web deps. mcp pulls fastmcp; that is fine inside this consuming container.
COPY ingest /src/ingest
COPY mcp /src/mcp
RUN pip install --no-cache-dir /src/ingest /src/mcp fastapi "uvicorn[standard]"

COPY demo-agent/app.py /app/app.py
EXPOSE 8090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
