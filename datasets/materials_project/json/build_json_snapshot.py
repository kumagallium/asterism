"""Generate a **nested JSON** snapshot of the Materials Project facts (#19).

This is the JSON twin of ``seed/build_seed.py``: it reads the same real, citable
facts from ``seed/csv/materials_project.csv`` and emits ``mp.json`` — an array of
records whose crystal-structure fields are nested under a ``structure`` object.
The nesting is deliberate: it exercises Asterism's JSON-source path, where ingest
tabularizes the JSON to CSV (``asterism.tabularize``) — a nested object flattens to
dot-path leaf columns (``structure.space_group_symbol``) — so the companion
``mp.rml.ttl`` reads ``mp.csv`` via ``rml:referenceFormulation ql:CSV`` and the
substrate derives that CSV from this snapshot on the fly.

Why a snapshot, not a live API call: Materials Project is an HTTP API, but the
reproducible, declarative path is *API → JSON snapshot → JSON ingest* (the
snapshot is the persisted, citable source). A live-API connector (auth / paging)
is a later, heavier step; the snapshot is the minimal non-CSV dogfood.

Pure stdlib + deterministic output (sorted, fixed formatting) so the committed
``mp.json`` diffs cleanly. Content-authoring tool, not runtime code.

Provenance: Materials Project (CC-BY 4.0; A. Jain et al., APL Materials 1,
011002, 2013), resolved for the host phases of Starrydata's thermoelectric
samples (experiments/mp-linking-poc, link_mp.py --mode live).

Usage: python build_json_snapshot.py   # writes mp.json next to this script
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

MP_PAGE = "https://next-gen.materialsproject.org/materials/"

HERE = Path(__file__).resolve().parent
CSV_PATH = HERE.parent / "seed" / "csv" / "materials_project.csv"
JSON_PATH = HERE / "mp.json"


def _record(row: dict[str, str]) -> dict[str, object]:
    """One MP material as a nested JSON record (structure fields under ``structure``)."""
    mpid = row["mp_id"].strip()
    return {
        "mp_id": mpid,
        # mp:formula — the bridge to starrydata's sd:compositionString (plain literal).
        "formula": row["formula"].strip(),
        "mp_page": f"{MP_PAGE}{mpid}",
        "structure": {
            "space_group_symbol": row["space_group_symbol"].strip(),
            "space_group_number": int(row["space_group_number"].strip()),
            "crystal_system": row["crystal_system"].strip(),
        },
    }


def main() -> int:
    with CSV_PATH.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda r: (r["formula"], r["mp_id"]))
    records = [_record(r) for r in rows]
    JSON_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {JSON_PATH} ({len(records)} materials)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
