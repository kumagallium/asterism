#!/usr/bin/env python3
"""Crosswalk HUB CLI — a thin wrapper over the productized runtime.

The build logic now lives in the package: :mod:`asterism.crosswalk_runtime` reads the
live store, delegates the Turtle to the tested pure :mod:`asterism.crosswalk`, writes
the hub graph + control flag, and persists the registry scaffold. This script is just
a manual entry point (the api endpoint ``POST /api/crosswalk/build`` is the product
path). The participation config is the persisted ``crosswalk-bridge/crosswalk.yaml``;
``--default`` bootstraps the demo-stack config.

Usage (run with the ingest venv, e.g. ``ingest/.venv/bin/python``):
  build.py             # rebuild the hub from the persisted crosswalk.yaml
  build.py --default   # write + build the demo-stack config (starrydata x MP x demo)
  build.py --remove    # tear down the hub graph + control flag

Env: CSV2RDF_OXIGRAPH_URL (default http://127.0.0.1:7878),
     CSV2RDF_REGISTRY_ROOT (default /data/sources/registry).
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from asterism import crosswalk_runtime
from asterism.crosswalk import XW
from asterism.crosswalk_runtime import (
    RuntimeConcept,
    RuntimeCrosswalkConfig,
    RuntimeParticipant,
)
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig

OXI = os.environ.get("CSV2RDF_OXIGRAPH_URL", "http://127.0.0.1:7878")
REGISTRY = Path(os.environ.get("CSV2RDF_REGISTRY_ROOT", "/data/sources/registry"))

# Demo-stack bootstrap config — the participants currently in the registry. Adding a
# dataset = append a RuntimeParticipant (or author it via the UI / api). These ids are
# specific to the local demo registry, so they live here (a dev convenience), not in
# the library.
_SD = "https://kumagallium.github.io/asterism/starrydata/ontology#compositionString"
_MP = "https://kumagallium.github.io/asterism/materials_project/ontology#formula"
DEFAULT_CONFIG = RuntimeCrosswalkConfig(
    concepts=(
        RuntimeConcept(
            name="composition",
            class_iri=f"{XW}Composition",
            link_predicate=f"{XW}hasComposition",
            normalizer="composition",
            participants=(
                RuntimeParticipant("starrydata-b05ccaa7", "starrydata", _SD),
                RuntimeParticipant("materials-project-67d305ce", "materials_project", _MP),
                RuntimeParticipant("dataset-63a36bfa", "thermoelectric_demo", _SD),
            ),
        ),
    ),
)


async def main() -> None:
    client = OxigraphClient(OxigraphConfig(base_url=OXI))
    try:
        if "--remove" in sys.argv:
            await crosswalk_runtime.remove_hub(client)
            print(f"removed hub graph {crosswalk_runtime.HUB_GRAPH} + control flag")
            return
        if "--default" in sys.argv:
            config = DEFAULT_CONFIG
            crosswalk_runtime.save_config(REGISTRY, config)
        else:
            config = crosswalk_runtime.load_config(REGISTRY)
            if config is None:
                print("no crosswalk.yaml in the registry; run with --default to bootstrap")
                return
        outcome = await crosswalk_runtime.build_hub(
            client, config, built_at=datetime.now(UTC).isoformat()
        )
        crosswalk_runtime.write_registry_scaffold(REGISTRY, config, outcome)
        print(f"hub built -> {outcome.hub_graph} ({outcome.triple_count} triples)")
        for concept, keys in outcome.shared.items():
            print(f"  {concept}: {len(keys)} shared")
        for ds, n in outcome.links.get("composition", {}).items():
            print(f"    {ds:24s} {n} links")
        for s in outcome.participants_skipped:
            print(f"  skipped {s['dataset_id']} ({s['reason']})")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
