"""Disposable real-stack verification of the document-ontology layer MVP.

Runs every §B.4 gate against a REAL Oxigraph (port 7879 — NOT the shared 7878)
through the PRODUCTION code paths (materialize -> stream -> Oxigraph, the typed
query tools over the canonical FROM-merge, the crosswalk runtime). Throwaway.

    docker run -d --rm --name asterism_doclayer_verify -p 7879:7878 \
        ghcr.io/oxigraph/oxigraph:latest serve --location /data --bind 0.0.0.0:7878
    PYTHONPATH=ingest/src ingest/.venv/bin/python scripts/verify_document_ontology_mvp.py
"""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
from pathlib import Path

from asterism import substrate
from asterism.crosswalk_runtime import (
    RuntimeConcept,
    RuntimeCrosswalkConfig,
    RuntimeParticipant,
    build_hub,
)
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.query_tools import load_query_tools, run_query_tool
from asterism.substrate import (
    canonical_graph_iri,
    materialize_to_nt_file,
    stream_nt_file_to_oxigraph,
)

BASE = "http://127.0.0.1:7879"
ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "datasets" / "papers"
MP = ROOT / "datasets" / "materials_project"
PAPER = "https://kumagallium.github.io/asterism/papers/resource/paper/PMC5951533"
SD = "https://kumagallium.github.io/asterism/starrydata/ontology#"

# A small starrydata-shaped composition ABox overlapping the MP seed (for the
# crosswalk baseline). Real starrydata rows are licensed; these are demo values.
SD_TTL = f"""
@prefix sd: <{SD}> .
<https://ex/sample/a> a sd:Sample ; sd:compositionString "Bi2Te3" .
<https://ex/sample/b> a sd:Sample ; sd:compositionString "PbSe" .
<https://ex/sample/c> a sd:Sample ; sd:compositionString "SnSe" .
<https://ex/sample/d> a sd:Sample ; sd:compositionString "NotInMP999" .
"""

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


async def main() -> int:
    cfg = OxigraphConfig(base_url=BASE)
    async with OxigraphClient(cfg) as client:
        if not await client.ping():
            print("oxigraph not reachable at", BASE, file=sys.stderr)
            return 2

        # --- load + promote starrydata(demo) + materials_project --------------
        await client.post_turtle_bytes(SD_TTL.encode(), graph_iri=canonical_graph_iri("starrydata"))
        await substrate.mark_graph_promoted(client, canonical_graph_iri("starrydata"))
        mp_ttl = (MP / "seed" / "mp.ttl").read_bytes()
        await client.post_turtle_bytes(mp_ttl, graph_iri=canonical_graph_iri("materials_project"))
        await substrate.mark_graph_promoted(client, canonical_graph_iri("materials_project"))

        config = RuntimeCrosswalkConfig(
            concepts=(
                RuntimeConcept(
                    name="composition",
                    class_iri="https://kumagallium.github.io/asterism/crosswalk/ontology#Composition",
                    link_predicate="https://kumagallium.github.io/asterism/crosswalk/ontology#hasComposition",
                    normalizer="composition",
                    participants=(
                        RuntimeParticipant("starrydata", "starrydata", SD + "compositionString"),
                        RuntimeParticipant(
                            "materials_project",
                            "materials_project",
                            "https://kumagallium.github.io/asterism/materials_project/ontology#formula",
                        ),
                    ),
                ),
            )
        )
        base = await build_hub(client, config, built_at="2026-06-11T00:00:00Z")
        baseline_shared = dict(base.shared)
        print("  crosswalk baseline shared:", baseline_shared, "total:", base.shared_total)

        # baseline mp tool (must be unchanged after the doc layer lands)
        mp_tools = {t.name: t for t in load_query_tools("materials_project")}
        mp_before = await run_query_tool(
            client, mp_tools["structure_by_composition"], {"composition": "Bi2Te3"}
        )

        # --- GATE: production materialize path on the REAL JATS ---------------
        rml = (PAPERS / "jats" / "PMC5951533.rml.ttl").read_text()
        nt = materialize_to_nt_file(rml, PAPERS / "jats")
        rml_graph = canonical_graph_iri("papersrml")
        n_rml = await stream_nt_file_to_oxigraph(nt, client, rml_graph)
        check(
            "production materialize (ql:XPath, real JATS) -> Oxigraph",
            n_rml == 86,
            f"{n_rml} skeleton triples streamed via materialize_to_nt_file+stream",
        )

        # --- load + promote the paper structure graph ------------------------
        paper_ttl = (PAPERS / "seed" / "paper.ttl").read_bytes()
        await client.post_turtle_bytes(paper_ttl, graph_iri=canonical_graph_iri("papers"))
        await substrate.mark_graph_promoted(client, canonical_graph_iri("papers"))
        n_paper = await client.graph_triple_count(canonical_graph_iri("papers"))

        # RML output is a subset of the promoted paper graph (declarative is faithful):
        # count triples that appear in BOTH the RML skeleton graph and the paper graph.
        sub = await client.sparql_select(
            f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{rml_graph}> {{ ?s ?p ?o }} "
            f"GRAPH <{canonical_graph_iri('papers')}> {{ ?s ?p ?o }} }}"
        )
        in_both = int(sub["results"]["bindings"][0]["n"]["value"])
        check("declarative RML output ⊆ promoted paper graph", in_both == n_rml,
              f"{in_both}/{n_rml} RML triples present in the paper graph")

        # --- GATE: idempotency (re-post same seed -> same graph) -------------
        await client.post_turtle_bytes(paper_ttl, graph_iri=canonical_graph_iri("papers"))
        n_paper2 = await client.graph_triple_count(canonical_graph_iri("papers"))
        check("idempotent re-ingest (same JATS -> same graph)", n_paper == n_paper2,
              f"{n_paper} == {n_paper2} triples after re-post")
        # build tool is deterministic (same bytes on re-run)
        subprocess.run(
            [sys.executable, str(PAPERS / "seed" / "build_paper_graph.py"),
             str(PAPERS / "jats" / "PMC5951533.xml"), "/tmp/_paper_rebuild.ttl"],
            check=True, capture_output=True, text=True,
            env={"PYTHONPATH": str(ROOT / "ingest" / "src"), "PATH": "/usr/bin:/bin"},
        )
        h1 = hashlib.sha256((PAPERS / "seed" / "paper.ttl").read_bytes()).hexdigest()
        h2 = hashlib.sha256(Path("/tmp/_paper_rebuild.ttl").read_bytes()).hexdigest() if Path("/tmp/_paper_rebuild.ttl").exists() else "?"
        check("deterministic build tool (byte-identical re-run)", h1 == h2, f"sha256 {h1[:12]} == {h2[:12]}")

        # --- GATE: structure round-trip on the real store --------------------
        rt = await client.sparql_select(
            """
            PREFIX po: <http://www.essepuntato.it/2008/12/pattern#>
            PREFIX doco: <http://purl.org/spar/doco/>
            PREFIX nif: <http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#>
            PREFIX prov: <http://www.w3.org/ns/prov#>
            SELECT ?sec ?para ?sent ?ctx WHERE {
              GRAPH ?g {
                <%s> po:contains ?sec . ?sec a doco:Section ; po:contains ?para .
                ?para a doco:Paragraph ; po:contains ?sent . ?sent a doco:Sentence ;
                      nif:referenceContext ?ctx ; prov:wasQuotedFrom <%s> .
              }
            } LIMIT 1
            """ % (PAPER, PAPER)
        )
        check("structure round-trip paper↔sec↔para↔sent (po:contains + nif:referenceContext)",
              len(rt["results"]["bindings"]) == 1)

        # --- GATE: recall tools over the canonical FROM-merge ----------------
        tools = {t.name: t for t in load_query_tools("papers")}
        st = await run_query_tool(client, tools["search_text"], {"query": "PPMS"})
        ppms = st["items"][0] if st["items"] else {}
        check("search_text finds the PPMS method sentence",
              st["count"] >= 1 and ppms.get("structural_path") == "4",
              ppms.get("sentence_iri", ""))

        node = PAPER + "/sec/sec4-materials-11-00649/para/1/sent/0"
        qc = await run_query_tool(client, tools["quote_with_citation"], {"node": node})
        row = qc["items"][0] if qc["items"] else {}
        check("quote_with_citation: verbatim + IRI + path + PROV",
              qc["count"] == 1 and "thermal transport option" in row.get("verbatim", "")
              and row.get("source_format") == "jats" and row.get("structural_path") == "4",
              f"parser={row.get('parser')} offsets={row.get('begin_index')}–{row.get('end_index')}")

        fp = await run_query_tool(client, tools["fetch_passage"],
                                  {"paper": "PMC5951533", "section": "4"})
        check("fetch_passage returns the Methods paragraphs",
              fp["count"] == 2 and any("argon atmosphere" in i["text"] for i in fp["items"]))

        # --- GATE: data↔text fusion -----------------------------------------
        fusion_ttl = (PAPERS / "fusion" / "fusion.ttl").read_bytes()
        await client.post_turtle_bytes(fusion_ttl, graph_iri=canonical_graph_iri("papersfusion"))
        await substrate.mark_graph_promoted(client, canonical_graph_iri("papersfusion"))
        mp_ = await run_query_tool(client, tools["measurement_provenance"], {})
        frow = mp_["items"][0] if mp_["items"] else {}
        check("fusion: curve -> figure + measurement-condition sentence (both cited)",
              mp_["count"] == 1 and frow.get("figure_label") == "Figure 3"
              and "physical properties measurement system" in frow.get("condition_text", ""),
              f"{frow.get('composition')} → {frow.get('figure_iri','').rsplit('/',1)[-1]} + "
              f"{frow.get('condition_sentence_iri','').rsplit('/',2)[-1] if frow.get('condition_sentence_iri') else ''}")

        # --- GATE: crosswalk regression (hub unchanged after doc-layer promote) -
        after = await build_hub(client, config, built_at="2026-06-11T00:00:00Z")
        check("crosswalk regression: hub shared compositions UNCHANGED after paper promote",
              dict(after.shared) == baseline_shared,
              f"shared {sorted(v for vs in baseline_shared.values() for v in vs)} (total {after.shared_total})")
        mp_after = await run_query_tool(
            client, mp_tools["structure_by_composition"], {"composition": "Bi2Te3"}
        )
        check("crosswalk regression: existing mp typed tool UNCHANGED",
              mp_after["items"] == mp_before["items"],
              f"structure_by_composition(Bi2Te3) stable: {mp_after['count']} row")

    print("\n=== SUMMARY ===")
    npass = sum(1 for _, ok, _ in results if ok)
    for name, ok, _ in results:
        print(f"  {'✓' if ok else '✗'} {name}")
    print(f"{npass}/{len(results)} gates PASS")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
