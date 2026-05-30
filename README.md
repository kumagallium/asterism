# csv2rdf-mcp

> CSV in, SPARQL/MCP out. PROV-O first-class. Self-hostable, sovereignty-first.

`csv2rdf-mcp` ingests CSV files (starting with the [starrydata](https://github.com/starrydata) dataset of thermoelectric/battery/magnetic measurement curves), converts them to RDF, and exposes the result as both a SPARQL 1.1 endpoint and an MCP server so AI agents can search and cite the data.

Designed to compose with [Crucible](https://github.com/) (MCP registry) and [Graphium](https://github.com/) (PROV-aware desktop notebook) without forcing data through a SaaS proxy.

## Status

**Phase 2 — drop CSV → auto reindex.** Phase 1 (papers + samples + curves ingester, MIE, compose, CI) is live; Phase 2 adds an upload API + watcher so dropping a CSV automatically reindexes Oxigraph. The technology choices behind the stack (Oxigraph backend + togomcp MCP server) are documented in [`docs/architecture/phase05-decisions.md`](docs/architecture/phase05-decisions.md).

See:
- [`docs/architecture/option-b.md`](docs/architecture/option-b.md) — Phase 1 architecture (Oxigraph + togomcp hybrid), role split with the DBCLS team
- [`docs/architecture/phase05-decisions.md`](docs/architecture/phase05-decisions.md) — backend / ingester adoption rationale (Oxigraph, Python rdflib)
- [`docs/architecture/phase2-watcher.md`](docs/architecture/phase2-watcher.md) — Phase 2 watcher + upload API design
- [`docs/architecture/phase2-template-curve-fetch.md`](docs/architecture/phase2-template-curve-fetch.md) — Phase 2 self-built MCP server (`template_curve_fetch`)
- [`docs/architecture/phase2-qudt.md`](docs/architecture/phase2-qudt.md) — Phase 2 QUDT quantity/unit normalization (synonym unification)
- [`docs/architecture/phase2-digitization.md`](docs/architecture/phase2-digitization.md) — Phase 2 DigitizationActivity (WebPlotDigitizer provenance)
- [`docs/architecture/crucible-registration.md`](docs/architecture/crucible-registration.md) — registering csv2rdf-mcp on Crucible (Oxigraph runs separately on `mcp-net`)
- [`docs/ontology/`](docs/ontology/) — Phase 1 ontology with Mermaid class diagram, RDFS/OWL TBox, and WebVOWL instructions for visual review
- [`experiments/phase2-fullscale/`](experiments/phase2-fullscale) — Phase 2 full-scale benchmark (12M triples): conversion / load / query latency + findings
- [`experiments/phase05/`](experiments/phase05) — spike code and logs for togopackage / Oxigraph / Morph-KGC
- [`experiments/phase05b/`](experiments/phase05b) — supplementary spike (togopackage Virtuoso backend)

## Design principles

1. **Sovereign by default.** Data never leaves the closed server. Graduation to public archives (Zenodo) is explicit and PROV-tracked.
2. **PROV-O is the lingua franca.** Every entity emitted by the pipeline is a `prov:Entity`; every ingest run is a `prov:Activity`. Notebooks (Graphium) cite by IRI and the citation graph stays queryable.
3. **Self-hostable, single deployment.** `docker compose up` is the supported install. No multi-tenant SaaS surface.
4. **Multi-scope ready.** Personal / Lab / Org deployments can coexist; data graduates between them with PROV bundles, not copies.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Repo scaffold, license, CI skeleton | done |
| 0.5 | Dependency validation (togopackage / Oxigraph / Morph-KGC) | done |
| 1 | Starrydata fixed-schema E2E (CSV → RDF → SPARQL → MCP) | done |
| **2** | **Watcher + upload API (drop CSV → auto reindex)** | **in progress** |
| 3 | Generic CSV → RDF (schema inference) | not started |
| 4 | Graphium integration (citation blocks) | not started |

## Quickstart

```bash
git clone https://github.com/kumagallium/csv2rdf-mcp
cd csv2rdf-mcp
docker compose up -d --build

# Drop a CSV into the kind-specific directory; the watcher picks it up.
cp /path/to/starrydata_papers.csv data/sources/csv/papers/

# …or upload via HTTP:
curl -F file=@papers.csv http://localhost:8080/upload/papers

# Inspect ingest history
curl http://localhost:8080/jobs | jq

# SPARQL directly against Oxigraph
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
            'curve_iri': 'https://kumagallium.github.io/csv2rdf-mcp/starrydata/resource/curve/1-1-1',
        })
        print(r.structured_content)
asyncio.run(main())
"
```

## License

Apache-2.0. See [`LICENSE`](LICENSE).

## Acknowledgements

Built on the shoulders of the [DBCLS](https://dbcls.rois.ac.jp/) ecosystem (rdf-config, togopackage, togomcp), [Oxigraph](https://github.com/oxigraph/oxigraph), [QLever](https://github.com/ad-freiburg/qlever), and [Morph-KGC](https://github.com/morph-kgc/morph-kgc).
