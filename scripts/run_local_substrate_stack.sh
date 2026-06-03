#!/usr/bin/env bash
# Run a local substrate-enabled api against an existing Oxigraph, so you can
# exercise the Phase 5 human-gated ingest (#15) in the browser yourself.
#
#   scripts/run_local_substrate_stack.sh        # starts the api on :8085
#   # then, in another terminal:
#   VITE_API_PROXY=http://127.0.0.1:8085 npm --prefix ui run dev
#   # open the printed Vite URL, go to ワークベンチ → propose (needs your API key)
#   # → 確定・保存 → 「Oxigraph へ投入（人間ゲート）」
#
# Env overrides:
#   API_PORT        (default 8085)        port for the local api
#   OXIGRAPH_URL    (default http://localhost:7878)
#   STATE_DIR       (default /tmp/c2r-local)  registry / drop / logs root
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
API_PORT="${API_PORT:-8085}"
OXIGRAPH_URL="${OXIGRAPH_URL:-http://localhost:7878}"
STATE_DIR="${STATE_DIR:-/tmp/c2r-local}"
VENV_PY="$REPO/api/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "error: $VENV_PY not found. Create api/.venv first (see docs)." >&2
  exit 1
fi

echo "==> ensuring morph-kgc (substrate extra) is installed in api/.venv"
if ! "$VENV_PY" -c "import morph_kgc" 2>/dev/null; then
  uv pip install --python "$VENV_PY" morph-kgc
fi

echo "==> checking Oxigraph at $OXIGRAPH_URL"
if ! curl -fsS --max-time 5 -X POST "$OXIGRAPH_URL/query" \
  -H 'Content-Type: application/sparql-query' --data 'ASK{?s ?p ?o}' >/dev/null; then
  echo "error: Oxigraph not reachable at $OXIGRAPH_URL." >&2
  echo "  start it with: docker compose -f compose.yaml up -d oxigraph" >&2
  exit 1
fi

mkdir -p "$STATE_DIR"/{registry,csv,rdf,err}

echo "==> starting substrate-enabled api on :$API_PORT  (Oxigraph: $OXIGRAPH_URL)"
echo "    registry: $STATE_DIR/registry"
echo
echo "    Next, in another terminal, start the UI pointed at this api:"
echo "      VITE_API_PROXY=http://127.0.0.1:$API_PORT npm --prefix ui run dev"
echo
cd "$REPO"
exec env \
  CSV2RDF_OXIGRAPH_URL="$OXIGRAPH_URL" \
  CSV2RDF_REGISTRY_ROOT="$STATE_DIR/registry" \
  CSV2RDF_DROP_ROOT="$STATE_DIR/csv" \
  CSV2RDF_RDF_ROOT="$STATE_DIR/rdf" \
  CSV2RDF_ERROR_ROOT="$STATE_DIR/err" \
  CSV2RDF_JOBS_LOG="$STATE_DIR/jobs.jsonl" \
  "$VENV_PY" -m uvicorn csv2rdf_api.main:build_app --factory \
    --host 127.0.0.1 --port "$API_PORT"
