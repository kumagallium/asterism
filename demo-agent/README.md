# demo-agent (ARiSE grounded-answer demo)

A thin **consuming layer** that powers the demo's "Ask → grounded answer +
citations + provenance" screen. It is intentionally **separate from csv2rdf
core**: the runtime LLM call must not live in the Claude-free core API
(see [`../docs/architecture/ontology-mapping-boundary-and-provenance.md`](../docs/architecture/ontology-mapping-boundary-and-provenance.md) §1/§5).

## Status

**Mock.** `app.py` returns fixtures so the UI can build against a stable
contract. The real version will call the csv2rdf typed MCP tools
(`sample_search` / `property_ranking` / `provenance_of`) plus an LLM.

## Contract

The UI calls only these two endpoints (full shapes in
[`../../handoff_to_claude_code_arise_demo.md`](../../handoff_to_claude_code_arise_demo.md) §3):

- `POST /demo/ask` → `{answer, citations[], notes[]}`
- `GET /demo/provenance?iri=<iri>` → `{iri, chain[]}`

## Run

```bash
pip install fastapi uvicorn
cd demo-agent
uvicorn app:app --port 8090 --reload
# health check
curl localhost:8090/health
# canned grounded answer
curl -X POST localhost:8090/demo/ask -H 'content-type: application/json' \
  -d '{"question":"ZTが最も高い熱電材料は？"}' | jq
```

## Next (wiring the real agent)

1. Replace the `/demo/ask` fixture with: classify the question → call the right
   typed MCP tool (`property_ranking` for "highest ZT", `sample_search` for
   "samples with Bi2Te3") → have the LLM compose the answer from the tool's
   structured result, carrying `excluded_implausible` into `notes`.
2. Replace `/demo/provenance` with a pass-through to `provenance_of(iri)`.
3. Keep this service separate from `mcp/` and `api/` (the core stays Claude-free).
