#!/usr/bin/env python3
"""Crosswalk HUB — one shared bridge that GROWS as datasets are added.

Not a grand unified ontology: a thin, purpose-scoped hub with two terms
(``xw:Composition`` / ``xw:hasComposition``). Each participating dataset declares
"my <predicate> carries a composition string" (a CROSSWALK RULE). The builder:

1. collects every dataset's distinct normalized compositions,
2. mints ONE ``xw:Composition`` per composition shared by >= 2 datasets (the
   join-relevant set — singletons add no cross-dataset value),
3. links each dataset's entities to that shared IRI (``xw:hasComposition``),
4. records build provenance (which datasets, which normalization, when).

Adding a dataset = add a rule -> rebuild -> the SAME hub grows (a composition the
new dataset shares with any existing one becomes a shared entity, and its entities
link into the existing IRI). N datasets map into ONE hub, not N^2 pairwise bridges.

Usage:
  crosswalk_hub.py [label ...]    # build the hub from these participating datasets
                                  # (default: all). e.g. to show growth:
  crosswalk_hub.py starrydata materials_project
  crosswalk_hub.py starrydata materials_project thermoelectric_demo
  crosswalk_hub.py --remove       # tear down (graph + control flag + registry ds)

Reads via the api (/api/sparql, read-only FROM-merge); writes the hub graph +
control flag straight to Oxigraph. Idempotent (PUT replaces the hub graph).
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import os

API = os.environ.get("ASTERISM_API_SPARQL", "http://127.0.0.1:8086/api/sparql")
OXI = os.environ.get("CSV2RDF_OXIGRAPH_URL", "http://127.0.0.1:7878")
REGISTRY = Path(os.environ.get("CSV2RDF_REGISTRY_ROOT", "/data/sources/registry"))
BUILT_AT = "2026-06-09T12:00:00+00:00"  # fixed (deterministic re-runs)

NS = "https://kumagallium.github.io/asterism"
SD = f"{NS}/starrydata/ontology#"
MP = f"{NS}/materials_project/ontology#"
XW = f"{NS}/crosswalk/ontology#"
XW_RES = f"{NS}/crosswalk/resource/composition/"
XW_ACT = f"{NS}/crosswalk/resource/build/latest"
HUB_GRAPH = f"{NS}/graph/canonical/crosswalk"
CONTROL_GRAPH = f"{NS}/graph/control"
STATUS_PRED = f"{NS}/vocab#status"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
PROV = "http://www.w3.org/ns/prov#"
NORMALIZATION_ID = "fold-subscripts+strip-whitespace/v1"
DATASET_ID = "crosswalk-bridge"


@dataclass(frozen=True)
class Rule:
    """One dataset's participation: which predicate carries the composition."""
    label: str
    predicate: str          # full IRI of the composition-bearing predicate
    graph_substr: str       # substring identifying the dataset's canonical graph


# The CROSSWALK REGISTRY. Adding a dataset = appending a Rule. The hub spans
# whatever is listed here; nothing else changes (datasets stay decoupled).
RULES: dict[str, Rule] = {
    "starrydata": Rule("starrydata", f"{SD}compositionString", "starrydata-b05ccaa7"),
    "materials_project": Rule("materials_project", f"{MP}formula", "materials-project-67d305ce"),
    "thermoelectric_demo": Rule("thermoelectric_demo", f"{SD}compositionString", "dataset-63a36bfa"),
}

_SUBS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
# SPARQL mirror of normalize(): fold unicode subscripts -> ascii, strip spaces.
_SPARQL_NORM = (
    'REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE('
    'REPLACE(REPLACE({v},"₀","0"),"₁","1"),"₂","2"),"₃","3"),"₄","4"),"₅","5"),'
    '"₆","6"),"₇","7"),"₈","8"),"₉","9")," ","")'
)


def normalize(formula: str) -> str:
    return formula.translate(_SUBS).replace(" ", "")


def sparql_read(query: str) -> list[dict]:
    req = urllib.request.Request(
        API, data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req)).get("results", {}).get("bindings", [])


def oxi_put_graph(graph: str, turtle: str) -> None:
    url = f"{OXI}/store?graph={urllib.parse.quote(graph, safe='')}"
    req = urllib.request.Request(url, data=turtle.encode(), method="PUT",
                                 headers={"Content-Type": "text/turtle"})
    urllib.request.urlopen(req).read()


def oxi_delete_graph(graph: str) -> None:
    url = f"{OXI}/store?graph={urllib.parse.quote(graph, safe='')}"
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="DELETE")).read()
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise


def oxi_update(update: str) -> None:
    req = urllib.request.Request(
        f"{OXI}/update", data=urllib.parse.urlencode({"update": update}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    urllib.request.urlopen(req).read()


def distinct_norm_comps(rule: Rule) -> set[str]:
    rows = sparql_read(
        f"SELECT DISTINCT ?comp WHERE {{ GRAPH ?g {{ ?e <{rule.predicate}> ?comp }} "
        f'FILTER(CONTAINS(STR(?g), "{rule.graph_substr}")) }}'
    )
    return {normalize(r["comp"]["value"]) for r in rows}


def links_for_shared(rule: Rule, shared: set[str]) -> dict[str, list[str]]:
    """For this dataset, (norm-key -> entity IRIs) restricted to SHARED comps."""
    vals = " ".join(f'"{k}"' for k in sorted(shared))
    rows = sparql_read(
        f"SELECT ?e ?norm WHERE {{ GRAPH ?g {{ ?e <{rule.predicate}> ?comp }} "
        f'FILTER(CONTAINS(STR(?g), "{rule.graph_substr}")) '
        f"BIND({_SPARQL_NORM.format(v='?comp')} AS ?norm) "
        f"VALUES ?norm {{ {vals} }} }}"
    )
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["norm"]["value"], []).append(r["e"]["value"])
    return out


def build(labels: list[str]) -> None:
    rules = [RULES[x] for x in labels]
    # Pass 1: each dataset's distinct normalized compositions.
    per_ds = {r.label: distinct_norm_comps(r) for r in rules}
    # shared = a composition present in >= 2 participating datasets (join-relevant).
    counts: dict[str, int] = {}
    for s in per_ds.values():
        for k in s:
            counts[k] = counts.get(k, 0) + 1
    shared = {k for k, n in counts.items() if n >= 2}

    lines = [
        f"@prefix xw: <{XW}> .", f"@prefix rdfs: <{RDFS}> .",
        f"@prefix owl: <{OWL}> .", f"@prefix prov: <{PROV}> .", "",
        "# --- thin crosswalk TBox ---",
        'xw:Composition a owl:Class ; rdfs:label "Composition (crosswalk)" ; '
        'rdfs:comment "A normalized chemical composition shared across datasets." .',
        'xw:hasComposition a owl:ObjectProperty ; rdfs:label "has composition" .',
        "",
        "# --- build provenance (the crosswalk is a derived, dated CLAIM) ---",
        f'<{XW_ACT}> a prov:Activity ; rdfs:label "crosswalk hub build" ; '
        f'xw:normalization "{NORMALIZATION_ID}" ; '
        f'xw:participatingDatasets "{", ".join(labels)}" ; '
        f'prov:endedAtTime "{BUILT_AT}"^^<http://www.w3.org/2001/XMLSchema#dateTime> .',
        "",
        "# --- shared composition entities + crosswalk links (the growing hub) ---",
    ]
    per_ds_links = {r.label: links_for_shared(r, shared) for r in rules}
    link_counts: dict[str, int] = {}
    for key in sorted(shared):
        comp_iri = f"{XW_RES}{urllib.parse.quote(key, safe='')}"
        lines.append(
            f'<{comp_iri}> a xw:Composition ; rdfs:label "{key}" ; '
            f"prov:wasGeneratedBy <{XW_ACT}> ."
        )
        for r in rules:
            for e in per_ds_links[r.label].get(key, []):
                lines.append(f"<{e}> xw:hasComposition <{comp_iri}> .")
                link_counts[r.label] = link_counts.get(r.label, 0) + 1
    turtle = "\n".join(lines) + "\n"
    n_triples = turtle.count("\n")

    oxi_put_graph(HUB_GRAPH, turtle)
    oxi_update(
        f"INSERT DATA {{ GRAPH <{CONTROL_GRAPH}> {{ "
        f'<{HUB_GRAPH}> <{STATUS_PRED}> "promoted" . }} }}'
    )
    write_registry_dataset(labels, len(shared), n_triples)

    print(f"hub built over {labels}")
    print(f"  shared compositions (in >= 2 datasets): {len(shared)}")
    for r in rules:
        print(f"    {r.label:20s} {link_counts.get(r.label, 0)} links")
    print(f"  hub graph <{HUB_GRAPH}> (promoted) ; registry dataset '{DATASET_ID}'")


def write_registry_dataset(labels: list[str], n_comp: int, n_triples: int) -> None:
    d = REGISTRY / DATASET_ID
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": DATASET_ID,
        "name": "crosswalk hub (composition across datasets)",
        "created_at": BUILT_AT, "complete": True, "exit_code": 0,
        "classes": ["Composition"], "class_count": 1,
        "has_ingester": False, "has_mie": False, "has_rml": False,
        "ingested": True, "promoted": True, "status": "active",
        "triple_count": n_triples, "triples_promoted": n_triples,
        "canonical_graph": HUB_GRAPH, "warnings": [], "traps": [],
        "crosswalk_participants": labels, "crosswalk_shared_compositions": n_comp,
    }
    (d / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    (d / "model.yaml").write_text("- Composition:\n")
    (d / "diagram.md").write_text(
        "```mermaid\nclassDiagram\n  class Composition\n  class Sample\n"
        "  class Material\n  Sample --> Composition : hasComposition\n"
        "  Material --> Composition : hasComposition\n```\n"
    )
    (d / "query_tools.yaml").write_text(QUERY_TOOLS)


QUERY_TOOLS = r"""# Cross-dataset CROSSWALK tools — they live with the HUB, not with either source
# dataset, because they belong to the JOIN. xw:Composition is the deterministic
# join key, so these are reproducible, citable, key-free verified tools.
tools:
  - name: zt_by_crystal_structure
    title: "ZT (max) by crystal structure — starrydata x Materials Project"
    description: >
      For each composition shared between starrydata and Materials Project (joined
      via the crosswalk xw:Composition, not raw string match), report the crystal
      structure (space group, crystal system) from Materials Project and the peak
      thermoelectric ZT from starrydata. Pass max_zt to drop digitization outliers.
    parameters:
      - name: limit
        type: integer
        default: 20
        minimum: 1
        maximum: 200
        description: rows to return
      - name: max_zt
        type: number
        required: false
        description: only ZT <= this value (data-quality guard)
    query: |
      PREFIX sd: <https://kumagallium.github.io/asterism/starrydata/ontology#>
      PREFIX mp: <https://kumagallium.github.io/asterism/materials_project/ontology#>
      PREFIX xw: <https://kumagallium.github.io/asterism/crosswalk/ontology#>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      SELECT ?composition ?space_group ?crystal_system (MAX(?zt) AS ?zt_max)
      WHERE {
        ?comp a xw:Composition ; rdfs:label ?composition .
        ?sample xw:hasComposition ?comp .
        ?curve sd:ofSample ?sample ; sd:propertyY "ZT" ; sd:yMax ?zt .
        ?material xw:hasComposition ?comp ; mp:hasCrystalStructure ?st .
        ?st mp:spaceGroupSymbol ?space_group ; mp:crystalSystem ?crystal_system .
        FILTER(isNumeric(?zt))
        {{#max_zt}}FILTER(?zt <= {{max_zt}}){{/max_zt}}
      }
      GROUP BY ?composition ?space_group ?crystal_system
      ORDER BY DESC(?zt_max)
      LIMIT {{limit}}
    result:
      item:
        composition: composition
        space_group: space_group
        crystal_system: crystal_system
        zt_max: { var: zt_max, number: true }
  - name: datasets_for_composition
    title: "Which datasets report a given composition (via the crosswalk hub)"
    description: >
      List the named graphs (datasets) that have an entity linked to the crosswalk
      composition matching the given label — shows how many sources the hub joins
      for one composition.
    parameters:
      - name: composition
        type: string
        required: true
        description: 'normalized composition label, e.g. "Bi2Te3" or "Ba8Ga16Ge30"'
    query: |
      PREFIX xw: <https://kumagallium.github.io/asterism/crosswalk/ontology#>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      SELECT ?dataset_graph (COUNT(DISTINCT ?e) AS ?entities)
      WHERE {
        ?comp a xw:Composition ; rdfs:label {{composition}} .
        ?e xw:hasComposition ?comp .
        GRAPH ?dataset_graph { ?e a ?cls }
      }
      GROUP BY ?dataset_graph
      ORDER BY DESC(?entities)
    result:
      item:
        dataset_graph: dataset_graph
        entities: { var: entities, number: true }
"""


def remove() -> None:
    oxi_delete_graph(HUB_GRAPH)
    oxi_update(
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH}> {{ "
        f"<{HUB_GRAPH}> <{STATUS_PRED}> ?o . }} }}"
    )
    import shutil
    if (REGISTRY / DATASET_ID).exists():
        shutil.rmtree(REGISTRY / DATASET_ID)
    print(f"removed hub graph, control flag, and registry dataset '{DATASET_ID}'")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--remove" in sys.argv:
        remove()
    else:
        build(args or list(RULES))
