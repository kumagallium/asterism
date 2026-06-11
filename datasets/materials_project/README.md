# materials_project — Asterism example dataset (#19)

The **second, non-starrydata dataset**. It exists to prove that Asterism is not
specific to starrydata: a new dataset brings its own vocabulary, its own typed
query tools, and joins to the others across a shared concept — with no engine
change (ADR `docs/architecture/ontology-canonical-lifecycle.md` §4/§5).

## What it is

Idealized **crystal structures** from the [Materials Project](https://next-gen.materialsproject.org/),
keyed by reduced host formula. This is the *structure* dimension Starrydata
lacks: in PSPP (process–structure–property–performance), Starrydata holds
process / property / performance, but not structure. Each entry is the idealized
host phase of a real Starrydata thermoelectric sample.

## Provenance & license

Structural facts (mp-id, space group symbol/number, crystal system) are real and
come from the **Materials Project** (CC-BY 4.0). They were resolved for the host
phases of Starrydata's samples in `experiments/mp-linking-poc` (`link_mp.py
--mode live`). Attribution: *A. Jain et al., The Materials Project: A materials
genome approach to accelerating materials innovation, APL Materials 1, 011002
(2013)*. Because MP data is CC-BY, the CSV and generated `mp.ttl` are committed
(unlike the starrydata demo seed, which is derived from a licensed source).

## Ontology

```mermaid
classDiagram
    class Material {
        mp:mpId
        mp:formula
        schema:url
    }
    class CrystalStructure {
        mp:spaceGroupSymbol
        mp:spaceGroupNumber
        mp:crystalSystem
        mp:idealizedFrom
    }
    Material --> CrystalStructure : mp:hasCrystalStructure
    CrystalStructure --> Material : mp:ofMaterial
```

- Namespace `mp:` = `https://kumagallium.github.io/asterism/materials_project/ontology#`
- Resources `…/materials_project/resource/{material,structure}/{mp-id}`
- Reused upper vocabulary: `prov:Entity`, `schema:url`, `schema:Dataset` (shared
  with starrydata — the "共有の語彙" surface).

## The bridge to starrydata

`mp:formula` carries the reduced host formula (e.g. `"Bi2Te3"`) as a plain string
literal — the **same value** Starrydata records as `sd:compositionString`. A
cross-dataset SPARQL join on that literal, run through the canonical FROM-merge,
links a measured thermoelectric property to the crystal structure that explains
it. For the seed subset, the joinable host compositions with ZT curves are
**PbSe, Bi2Te3, SnSe, Ba8Ge43, ZnO** (doped samples need host normalization —
see the PoC's `normalize_host`; out of scope for this seed).

## Typed query tools (`query_tools.yaml`)

| tool | what it does |
|---|---|
| `structure_by_composition` | crystal structure for a formula (space group, system, mp-id) |
| `materials_by_space_group` | materials sharing a space group symbol |
| `materials_by_crystal_system` | materials in a crystal system (enum) |
| `thermoelectric_structure` | **cross-dataset**: rank ZT (starrydata) + attach structure (this dataset) |

The MCP server auto-registers these as typed tools (`asterism.query_tools` +
`build_server`); the cross-dataset tool returns rows only when both datasets are
in canonical scope.

## Files

| file | role |
|---|---|
| `dataset.toml` | declared identity (namespace IRIs) |
| `model.yaml` | rdf-config TBox → projected to the ontology graph at promote (#20 step5) |
| `query_tools.yaml` | typed, parameterized, read-only SPARQL tools (content) |
| `seed/csv/materials_project.csv` | real MP facts — the source of truth (also for the propose/materialize dogfood) |
| `seed/build_seed.py` | content tool: CSV → `mp.ttl` (deterministic, stdlib) |
| `seed/mp.ttl` | generated ABox (committed) |
| `seed/load.py` | load `mp.ttl` into the `canonical/materials_project` named graph |
| `json/build_json_snapshot.py` | content tool: CSV → **nested** `mp.json` (#19 JSON-source dogfood) |
| `json/mp.json` | nested JSON snapshot (committed) — the persisted, citable non-CSV source |
| `json/mp.rml.ttl` | declarative CSV RML (JSON tabularized at ingest) — produces the *same* facts as `mp.ttl` |

## JSON source path (#19 — non-CSV ingestion dogfood)

Materials Project is the dogfood for Asterism's **non-CSV source** support. MP is
natively an HTTP API; the reproducible, declarative path is *API → JSON snapshot →
JSON ingest* — the snapshot is the persisted, citable source (a live-API connector
with auth/paging is a later, heavier step). `json/mp.json` is that snapshot, with
crystal-structure fields **nested** under a `structure` object on purpose: it
exercises the JSON path end to end, where a nested object flattens to dot-path
leaf fields (`structure.space_group_symbol`). Ingest **tabularizes the JSON to CSV**
(`asterism.tabularize`: nested objects → dot-path columns, arrays → JSON-string
cells), so `json/mp.rml.ttl` reads `mp.csv` via `rml:referenceFormulation ql:CSV`
and the substrate derives that CSV from `mp.json` on the fly — the JSON stays the
citable source of record (see `architecture/native-json-denormalization.md`).

**Proven drop-in (real Morph-KGC + real Oxigraph, disposable stack):**
`mp.json` + `mp.rml.ttl` materialize to **143 triples, set-equal to the directly
seeded `mp.ttl`** — the JSON-source path is interchangeable with the direct seed.
Streamed through the production substrate (`materialize_to_nt_file` →
`stream_nt_file_to_oxigraph`, the same code `POST /api/datasets/{id}/ingest` runs)
into a real Oxigraph, then a canonical FROM-merge join against a Starrydata ZT
graph on `mp:formula == sd:compositionString` returns the structure↔property rows:

| formula | space group | crystal system | ZT (Starrydata) |
|---|---|---|---|
| PbSe | Fm-3m | Cubic | 1.018 |
| Bi2Te3 | R-3m | Trigonal | 0.914 |
| SnSe | Pnma | Orthorhombic | 0.822 |
| ZnO | P6_3mc | Hexagonal | 0.30 |

So a non-CSV (JSON) dataset designed and ingested through the normal flow lands as
citable facts that join across datasets — with no engine change. See ADR
`docs/architecture/non-csv-sources.md`.

## Seed-load (local)

```bash
python seed/load.py http://localhost:7878
# -> loads mp.ttl into …/asterism/graph/canonical/materials_project
```
