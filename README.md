# Asterism

[![Live demo](https://img.shields.io/badge/live%20demo-asterism%2Fdemo-3f6f49?logo=html5&logoColor=white)](https://kumagallium.github.io/asterism/demo/)
[![CI](https://github.com/kumagallium/asterism/actions/workflows/ci.yml/badge.svg)](https://github.com/kumagallium/asterism/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![release](https://img.shields.io/github/v/tag/kumagallium/asterism?sort=semver&label=release&color=blue)](https://github.com/kumagallium/asterism/tags)
[![SPARQL 1.1](https://img.shields.io/badge/SPARQL-1.1-orange.svg)](https://www.w3.org/TR/sparql11-query/)
[![MCP](https://img.shields.io/badge/MCP-server-purple.svg)](https://modelcontextprotocol.io/)

> Connect your private and open data through shared ontologies — so AI can reach what it couldn't. Every answer grounded, and traceable to its source.

**▶ Live demo (no server, no AI — real SPARQL in your browser): <https://kumagallium.github.io/asterism/demo/>.** Ask materials questions over a small [starrydata](https://github.com/starrydata) subset and get deterministic answers — each carrying its citable IRIs, the exact SPARQL run, and a provenance trace — plus a cross-dataset join of Starrydata measurements with [Materials Project](https://next-gen.materialsproject.org/) crystal structures. Runs entirely client-side (oxigraph-wasm); nothing is sent anywhere.

**Asterism** turns scattered structured data into a connected knowledge graph you can query and trust. Data points are the stars; shared ontologies are the lines that connect them into a recognizable figure — one your tools, and your AI, can finally read. It exposes the result as both a SPARQL 1.1 endpoint and an MCP server, with full provenance. CSV is the first input type (starting with the [starrydata](https://github.com/starrydata) dataset of thermoelectric/measurement curves); the ingestion substrate is declarative and source-agnostic, extending to JSON/API sources without per-dataset code.

Two properties make the graph trustworthy:

- **Provenance is first-class (PROV-O).** Every entity is a `prov:Entity`; every ingest / digitization / link is a `prov:Activity`. A cited number can always be traced back to the figure, paper, and run it came from.
- **No generated code is executed.** Ingestion is declarative (RML / Morph-KGC) and may only call a closed, vetted function library — so onboarding new data is a *reviewable mapping*, not arbitrary code. See [`docs/architecture/ingestion-execution-safety.md`](docs/architecture/ingestion-execution-safety.md) and [`docs/architecture/phase5-declarative-substrate.md`](docs/architecture/phase5-declarative-substrate.md).

## Status

Live execution state: **[`docs/ROADMAP.md`](docs/ROADMAP.md)**. Design decisions: **[`docs/architecture/`](docs/architecture/)** (ADRs).

Shipped today: a CSV → RDF ingester (papers / samples / curves), a watcher + HTTP upload API (drop a CSV → auto-reindex), QUDT unit normalization, WebPlotDigitizer provenance, and a 12M-triple benchmark; a SPARQL endpoint and an MCP server with typed tools (`template_curve_fetch`, `sample_search`, `property_ranking`, `provenance_of`) for grounded, cited answers; an AI-assisted **"Step 0"** schema builder for arbitrary CSVs (CLI); and a reproducible demo tagged `v0.1.0`.

In progress (see ROADMAP): a declarative substrate that generalizes ingestion from "CSV → RDF" to "structured source → RDF" (JSON/API already proven), a shared vetted function library, and a schema-aware query layer so questions can be asked over any onboarded ontology — not just the built-in one. A researcher-facing **web UI** (schema-design workbench, dataset catalog, and a grounded **Ask** view) is in active development under [`ui/`](ui/) (React, served by the API); today's stable surfaces are the CLI, the HTTP upload API, directory-drop, and MCP.

## Design principles

1. **Sovereign by default.** Data never leaves the closed server; graduation to public archives (e.g. Zenodo) is explicit and PROV-tracked.
2. **PROV-O is the lingua franca.** Every emitted entity is a `prov:Entity`; every run is a `prov:Activity`; citations stay queryable by IRI.
3. **Reviewable ingestion, no codegen.** Declarative mappings plus a closed, vetted function library; transforms with no matching function degrade to raw strings rather than blocking onboarding.
4. **Self-hostable, single deployment.** `docker compose up` is the supported install. No multi-tenant SaaS surface.
5. **A figure, not a pile.** The name is the thesis: data are stars, ontologies are the lines, the connected figure is the knowledge. The IRI namespace is that figure's identity and stays stable.

## Roadmap

The living roadmap is **[`docs/ROADMAP.md`](docs/ROADMAP.md)**. In short: ingestion is generalizing to *structured source → RDF* (CSV today, JSON/API proven), with a schema-aware query/Ask layer and a source-onboarding UI ahead.

## Quickstart

```bash
git clone https://github.com/kumagallium/asterism
cd asterism
docker compose up -d --build

# Drop a CSV into the kind-specific directory; the watcher picks it up (no token).
cp /path/to/starrydata_papers.csv data/sources/csv/papers/

# …or upload via HTTP. The write surface is token-gated (fail-closed): set a
# token first (also put it in .env so the container sees it), then send it.
export ASTERISM_API_TOKEN=$(openssl rand -hex 32)   # restart compose after setting in .env
curl -F file=@papers.csv -H "X-Asterism-Token: $ASTERISM_API_TOKEN" \
  http://localhost:8080/upload/papers

# Inspect ingest history (read-only, open)
curl http://localhost:8080/jobs | jq

# SPARQL directly against the (loopback-only) Oxigraph — for local debugging.
# Oxigraph itself has no auth; never publish 7878 on a shared network.
curl -G http://localhost:7878/query \
  --data-urlencode 'query=SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }'

# Call template_curve_fetch (self-built MCP) for the raw x/y of one curve
# (any MCP client works; here we use the python fastmcp Client)
python -c "
import asyncio
from fastmcp import Client
async def main():
    async with Client('http://localhost:8002/mcp') as c:
        r = await c.call_tool('template_curve_fetch', {
            'curve_iri': 'https://kumagallium.github.io/asterism/starrydata/resource/curve/1-1-1',
        })
        print(r.structured_content)
asyncio.run(main())
"
```

For **Step 0** — designing a schema for a *new* (non-starrydata) CSV. This is a local CLI workflow; `propose` / `refine` call an LLM, so they need an API key:

```bash
pip install -e step0                  # installs the asterism-* CLIs
export ANTHROPIC_API_KEY=sk-...       # required only by propose/refine

# 1. inspect structure (types / JSON / uniqueness, incl. composite keys)
asterism-inspect mydata.csv --fk id
# 2. let the LLM draft the design artifacts (TBox / Mermaid / MIE / ingester)
asterism-propose mydata.csv --domain "measurement curves; PROV-O; no blank nodes" > proposal.md
# 3. (optional) feed back review comments
asterism-refine proposal.md --comment "use a composite (paper_id, sample_id) key" > refined.md
# 4. split the Markdown into individual files
asterism-materialize refined.md --name mydata --output-dir out/
# 5. validate the bundle against the *full* CSV (8-trap check; exit 0/1, CI-friendly)
asterism-validate --mie out/mydata-mie.yaml --ingester out/mydata.py --csv mydata.csv
```

## Security / deploying with sensitive data

The quickstart above is for **local, single-user** use. Asterism is designed to be
self-hosted over confidential, unpublished data, so the defaults are safe-by-default:

- **Loopback by default.** Every published port in `compose.yaml` (Oxigraph `7878`,
  upload-api `8080`, togomcp `8000`, MCP front `8002`) and `compose.demo.yaml`
  (`8090`) binds to `127.0.0.1`. Oxigraph has *no* authentication and a writable
  `/update` endpoint — never publish `7878` on a shared network. Sibling services
  reach it in-network via `http://oxigraph:7878`.
- **Raw SPARQL is off by default.** The arbitrary read-only SPARQL escape (MCP
  `sparql_query`, `/api/sparql`, `/demo/sparql`) is withheld unless you set
  `ASTERISM_EXPOSE_RAW_SPARQL=1`. Typed, human-vetted query tools are always on.
  Even when enabled the escape is **scoped to promoted canonical graphs**: a
  caller-supplied `FROM`/`FROM NAMED` must name an allowlisted graph (no reaching
  into unreviewed drafts), `SERVICE` federation is blocked (SSRF/exfiltration), and
  `schema_summary` only introspects canonical/ontology graphs.
- **Ingestion never runs generated code.** AI-/operator-authored RML is validated
  fail-closed before Morph-KGC runs it — only the closed Tier 0 function set
  (`asterism.functions`) and confined CSV/JSON file sources are allowed (no DuckDB
  `rml:query`, no absolute / `..` / URL sources). See
  [`docs/architecture/ingestion-execution-safety.md`](docs/architecture/ingestion-execution-safety.md).

To expose any surface **beyond localhost**: put it behind an authenticating reverse
proxy (TLS + auth), set `ASTERISM_API_TOKEN` to gate the write/design and SPARQL
endpoints, keep the store on a network with no outbound egress, and for a sensitive
*remote* store use the typed-only topology B
([`compose.mcp-front.yaml`](compose.mcp-front.yaml),
[`docs/architecture/store-mcp-split.md`](docs/architecture/store-mcp-split.md)).

## License

Apache-2.0. See [`LICENSE`](LICENSE).

## Acknowledgements

Built on the shoulders of the [DBCLS](https://dbcls.rois.ac.jp/) ecosystem (rdf-config, togopackage, togomcp), [Oxigraph](https://github.com/oxigraph/oxigraph), [QLever](https://github.com/ad-freiburg/qlever), and [Morph-KGC](https://github.com/morph-kgc/morph-kgc).
