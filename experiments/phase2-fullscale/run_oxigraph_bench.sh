#!/usr/bin/env bash
# Phase 2 #5 full-scale benchmark — stage 2+3 orchestration.
#
# Spins up a FRESH, dedicated Oxigraph on port 7900 (does not touch the dev
# compose stack), loads the full-scale TTLs (curl streams from disk so we don't
# blow up Python memory on the multi-hundred-MB curves file), times the load,
# reports store size, then runs the query latency benchmark.
#
# Prereqs: stage 1 (convert.py) has produced work/{papers,samples,curves}.ttl.
#   bash experiments/phase2-fullscale/run_oxigraph_bench.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${HERE}/work"
STORE="${WORK}/oxigraph_store"
PORT=7900
GRAPH_BASE="https://kumagallium.github.io/csv2rdf-mcp/starrydata/graph"
NAME=phase2bench_oxigraph

rm -rf "${STORE}"; mkdir -p "${STORE}"
docker rm -f "${NAME}" >/dev/null 2>&1 || true

echo "[bench] starting fresh Oxigraph on :${PORT}"
docker run -d --name "${NAME}" -p ${PORT}:7878 \
  -v "${STORE}:/data" \
  ghcr.io/oxigraph/oxigraph:latest serve --location /data --bind 0.0.0.0:7878 >/dev/null
for _ in $(seq 1 30); do curl -sf "http://localhost:${PORT}/query?query=ASK%7B%7D" >/dev/null 2>&1 && break; sleep 1; done

load_one () {
  local kind="$1"; local ttl="${WORK}/${kind}.ttl"
  [ -s "$ttl" ] || { echo "[bench] SKIP ${kind} (no TTL)"; return; }
  local mb; mb=$(du -m "$ttl" | cut -f1)
  local t0; t0=$(date +%s)
  curl -sf -X POST --data-binary "@${ttl}" \
    -H 'Content-Type: text/turtle; charset=utf-8' \
    "http://localhost:${PORT}/store?graph=${GRAPH_BASE}/${kind}"
  local t1; t1=$(date +%s)
  echo "[bench] ${kind}: ${mb} MB loaded in $((t1 - t0))s"
}

LOAD_START=$(date +%s)
for k in papers samples curves; do load_one "$k"; done
LOAD_END=$(date +%s)
echo "[bench] total load: $((LOAD_END - LOAD_START))s"

echo -n "[bench] total triples: "
curl -sf "http://localhost:${PORT}/query" \
  --data-urlencode 'query=SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }' \
  -H 'Accept: text/csv' | tail -1

echo -n "[bench] store size on disk: "
du -sh "${STORE}" | cut -f1

echo "[bench] running query latency benchmark..."
python3 "${HERE}/bench_queries.py" --url "http://localhost:${PORT}" --runs 5 \
  --out "${WORK}/query_results.json"

echo "[bench] done. Leaving ${NAME} running; stop with: docker rm -f ${NAME}"
