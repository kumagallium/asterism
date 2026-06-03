#!/usr/bin/env bash
# Full-dataset ingest into the DEFAULT graph of a *running* asterism stack.
#
# This is the production counterpart to run_oxigraph_bench.sh. The bench script
# loads each kind into a NAMED graph on a throwaway Oxigraph at :7900 — which,
# after the #26 default-graph fix, is the wrong target: the MIE example queries
# (and Phase 1 smoke) are GRAPH-less and only see the default graph. This script
# instead converts the full CSVs and bulk-loads them into the DEFAULT graph of
# the Oxigraph backing the deployed stack, in place.
#
# Run this ON THE HOST that owns the deployed Oxigraph store (e.g. dify-server).
#
# Prerequisites
#   1. This repo checked out at a commit that INCLUDES PR #33 (safe_url scheme
#      fix). Without it, 19 papers with URL="unknown" emit the invalid IRI
#      <unknown> and Oxigraph's atomic bulk load rejects the whole papers.ttl.
#         git -C <repo> log --oneline | grep -q safe-url   # sanity
#   2. ingest venv built:
#         cd ingest && uv venv .venv --python 3.11 && . .venv/bin/activate \
#           && uv pip install -e '.[dev]'
#   3. The full dataset CSVs present locally: $DATASET/starrydata_{papers,samples,curves}.csv
#   4. docker compose stack up; $STORE is the Oxigraph bind-mount directory
#      (the host path mapped to /data in the oxigraph service).
#
# Usage
#   DATASET=/path/to/starrydata_dataset \
#   STORE=/path/to/asterism/data/oxigraph_store \
#   COMPOSE_FILE=/path/to/asterism/compose.yaml \
#   OXI_SERVICE=oxigraph \
#   WORK=/path/to/scratch/work \
#   bash load_full_default_graph.sh
#
# Set REUSE_TTL=1 to skip conversion if $WORK/{papers,samples,curves}.ttl exist.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"

DATASET="${DATASET:?set DATASET=/path/to/starrydata_dataset}"
STORE="${STORE:?set STORE=/path/to/data/oxigraph_store (oxigraph bind-mount)}"
COMPOSE_FILE="${COMPOSE_FILE:-${REPO}/compose.yaml}"
OXI_SERVICE="${OXI_SERVICE:-oxigraph}"
WORK="${WORK:-${HERE}/work}"
OXI_IMAGE="${OXI_IMAGE:-ghcr.io/oxigraph/oxigraph:latest}"
REUSE_TTL="${REUSE_TTL:-0}"

echo "[load] repo=${REPO}"
echo "[load] dataset=${DATASET}  store=${STORE}  service=${OXI_SERVICE}"

# --- stage 1: convert CSV -> TTL ------------------------------------------
mkdir -p "${WORK}"
need_convert=1
if [ "${REUSE_TTL}" = "1" ] \
   && [ -s "${WORK}/papers.ttl" ] && [ -s "${WORK}/samples.ttl" ] && [ -s "${WORK}/curves.ttl" ]; then
  echo "[load] REUSE_TTL=1 and TTLs exist — skipping conversion"
  need_convert=0
fi
if [ "${need_convert}" = "1" ]; then
  echo "[load] converting full CSVs (rdflib; ~13 min)..."
  ( cd "${REPO}/ingest" && . .venv/bin/activate \
      && python "${HERE}/convert.py" --src "${DATASET}" --out "${WORK}" )
fi

# --- stage 2: confirm + wipe store ----------------------------------------
echo
echo "[load] About to STOP '${OXI_SERVICE}', WIPE '${STORE}', and bulk-load the"
echo "       full dataset into the DEFAULT graph. Existing store contents are lost."
read -r -p "[load] Proceed? [y/N] " ans
[ "${ans}" = "y" ] || [ "${ans}" = "Y" ] || { echo "[load] aborted."; exit 1; }

docker compose -f "${COMPOSE_FILE}" stop "${OXI_SERVICE}"
find "${STORE}" -mindepth 1 -delete
echo "[load] store wiped ($(find "${STORE}" -mindepth 1 | wc -l | tr -d ' ') entries)"

# --- stage 3: offline bulk load into the DEFAULT graph --------------------
# No --graph flag => default graph. Files load in parallel.
echo "[load] bulk loading (offline)..."
docker run --rm \
  -v "${STORE}:/data" \
  -v "${WORK}:/work:ro" \
  "${OXI_IMAGE}" \
  load --location /data \
       --file /work/papers.ttl /work/samples.ttl /work/curves.ttl

docker compose -f "${COMPOSE_FILE}" start "${OXI_SERVICE}"

# --- stage 4: verify -------------------------------------------------------
# Resolve the host port the oxigraph service publishes (e.g. 7878).
PORT="$(docker compose -f "${COMPOSE_FILE}" port "${OXI_SERVICE}" 7878 2>/dev/null | sed 's/.*://')"
PORT="${PORT:-7878}"
URL="http://localhost:${PORT}/query"
for _ in $(seq 1 30); do
  curl -sf --data-binary 'ASK{?s ?p ?o}' -H 'Content-Type: application/sparql-query' "${URL}" >/dev/null 2>&1 && break
  sleep 1
done
echo -n "[load] total triples (default graph): "
curl -s --data-binary 'SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }' \
  -H 'Content-Type: application/sparql-query' -H 'Accept: text/csv' "${URL}" | tail -1
echo -n "[load] named-graph triples (expect 0): "
curl -s --data-binary 'SELECT (COUNT(*) AS ?c) WHERE { GRAPH ?g {?s ?p ?o} }' \
  -H 'Content-Type: application/sparql-query' -H 'Accept: text/csv' "${URL}" | tail -1
echo "[load] done."
