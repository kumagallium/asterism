# csv2rdf-mcp

> CSV in, SPARQL/MCP out. PROV-O first-class. Self-hostable, sovereignty-first.

`csv2rdf-mcp` ingests CSV files (starting with the [starrydata](https://github.com/starrydata) dataset of thermoelectric/battery/magnetic measurement curves), converts them to RDF, and exposes the result as both a SPARQL 1.1 endpoint and an MCP server so AI agents can search and cite the data.

Designed to compose with [Crucible](https://github.com/) (MCP registry) and [Graphium](https://github.com/) (PROV-aware desktop notebook) without forcing data through a SaaS proxy.

## Status

**Phase 0.5 — dependency validation.** The technology choices below (SPARQL backend, ingester) are being decided in `docs/internal/phase05-decisions.md`. The implementation in Phase 1 will follow that document.

See:
- [`docs/internal/design-plan.md`](docs/internal/design-plan.md) — full design (Japanese), source of truth
- [`docs/internal/handoff-phase05.md`](docs/internal/handoff-phase05.md) — Phase 0.5 brief
- [`docs/internal/phase05-decisions.md`](docs/internal/phase05-decisions.md) — adoption decisions (initial + §7 supplementary)
- [`docs/internal/option-b-architecture.md`](docs/internal/option-b-architecture.md) — recommended Phase 1 architecture (Oxigraph + togomcp hybrid), role split with the DBCLS team
- [`docs/internal/crucible-registration.md`](docs/internal/crucible-registration.md) — registering csv2rdf-mcp on Crucible (Oxigraph runs separately on `mcp-net`)
- [`docs/ontology/`](docs/ontology/) — Phase 1 ontology with Mermaid class diagram, RDFS/OWL TBox, and WebVOWL instructions for visual review
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
| **0.5** | **Dependency validation (togopackage / Oxigraph / Morph-KGC)** | **in progress** |
| 1 | Starrydata fixed-schema E2E (CSV → RDF → SPARQL → MCP) | not started |
| 2 | Minimal custom MCP tools | not started |
| 3 | Generic CSV → RDF (schema inference) | not started |
| 4 | Graphium integration (citation blocks) | not started |

## Quickstart (after Phase 1)

> The commands below are **not yet wired**. They are reproduced from the design plan so contributors know what to expect once Phase 1 lands.

```bash
git clone https://github.com/kumagallium/csv2rdf-mcp
cd csv2rdf-mcp
docker compose up -d
# Drop a CSV into data/sources/csv/ — the watcher converts and indexes it
curl 'http://localhost:10005/sparql?query=SELECT%20*%20WHERE%20%7B%20%3Fs%20%3Fp%20%3Fo%20%7D%20LIMIT%2010'
```

## License

Apache-2.0. See [`LICENSE`](LICENSE).

## Acknowledgements

Built on the shoulders of the [DBCLS](https://dbcls.rois.ac.jp/) ecosystem (rdf-config, togopackage, togomcp), [Oxigraph](https://github.com/oxigraph/oxigraph), [QLever](https://github.com/ad-freiburg/qlever), and [Morph-KGC](https://github.com/morph-kgc/morph-kgc).
