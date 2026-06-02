# ARiSE grounded-answer demo — runbook

End-to-end "体感" demo: drop the starrydata knowledge graph behind an AI that
answers materials questions **grounded in the data**, with **citations**, a
**provenance trace**, and **honest data-quality handling** (it excludes
physically impossible values instead of inventing a record).

This overlay (`compose.demo.yaml`) adds two services on top of the base stack
without editing `compose.yaml`.

## 1. Generate the seed (once)

Builds a small, real subset from your local starrydata dataset via the actual
ingester. Licensed source data stays out of the repo (everything lands under
`demo-agent/seed/`, which should be gitignored).

```bash
python scripts/make_demo_subset.py --src ../starrydata_dataset --n-papers 40
# -> demo-agent/seed/{papers,samples,curves}.ttl
```

To showcase the **data-quality exclusion** (the AI dropping a physically
impossible ZT > 3.5 instead of inventing a record), force-include a paper that
has an outlier curve, e.g. `--include-sids 12345`, or seed a larger subset —
the full dataset has ZT values up to ~13000 from mislabeled axes. With a clean
40-paper subset the exclusion count is simply 0.

## 2. Bring up the stack

```bash
docker compose -f compose.yaml -f compose.demo.yaml up -d --build
# oxigraph (7878) + the existing services + oxigraph-seed (loads the TTL) + demo-agent (8090)
```

`oxigraph-seed` waits for Oxigraph, loads the seed, and exits. `demo-agent`
runs in **real mode** (it sees `CSV2RDF_OXIGRAPH_URL`).

## 3. Ask grounded questions

```bash
# highest ZT — note the honest exclusion of digitization-error outliers
curl -s localhost:8090/demo/ask -H 'content-type: application/json' \
  -d '{"question":"ZTが最も高い熱電材料は？"}' | jq

# highest Seebeck coefficient
curl -s localhost:8090/demo/ask -H 'content-type: application/json' \
  -d '{"question":"Seebeck係数が最大のサンプルは？"}' | jq

# composition search
curl -s localhost:8090/demo/ask -H 'content-type: application/json' \
  -d '{"question":"Bi2Te3のサンプルはある？"}' | jq

# trace a cited result down its PROV chain (curve -> sample -> paper -> digitization -> ingestion)
curl -s "localhost:8090/demo/provenance?iri=<curve_iri_from_a_citation>" | jq
```

The UI (owned by the Claude Code session) renders these same `/demo/ask` and
`/demo/provenance` responses as the Ask view + citation cards + provenance trace.

## Notes

- **Mock mode**: without `CSV2RDF_OXIGRAPH_URL`, demo-agent returns fixtures —
  handy for UI development with no backend (`uvicorn app:app --port 8090`).
- **No LLM required**: answers are composed deterministically from the typed
  tools' structured output, so the demo is fully reproducible and sovereign.
  An LLM can later be slotted into `app._compose_*` for free-form questions
  without changing the `/demo/*` contract.
- **Boundary**: the answer generation lives here (consuming layer), never in
  the Claude-free csv2rdf core (ADR §1/§5).
