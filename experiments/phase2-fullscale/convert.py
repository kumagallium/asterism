"""Phase 2 #5 full-scale benchmark — stage 1: CSV -> Turtle conversion.

Converts the *full* starrydata CSVs (papers 56k / samples 144k / curves 233k)
with the production ingester and records wall-clock, triple counts, and output
size per kind. This closes the Phase 0.5 residual risk #1 ("Oxigraph full-scale
performance unverified") — see docs/architecture/phase05-decisions.md §4.

Run with the ingest venv:
    cd ingest && . .venv/bin/activate
    python ../experiments/phase2-fullscale/convert.py \
        --src ../../starrydata_dataset \
        --out ./experiments/phase2-fullscale/work

Outputs:
    <out>/{papers,samples,curves}.ttl   (gitignored — large)
    <out>/convert_results.json
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from csv2rdf.starrydata import IngestConfig, ingest_curves, ingest_papers, ingest_samples

_INGESTERS = {
    "papers": ingest_papers,
    "samples": ingest_samples,
    "curves": ingest_curves,
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--src", type=Path, required=True, help="dir with starrydata_*.csv")
    p.add_argument("--out", type=Path, required=True, help="output dir for TTLs")
    p.add_argument(
        "--kinds",
        nargs="+",
        default=["papers", "samples", "curves"],
        choices=list(_INGESTERS),
    )
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cfg = IngestConfig()  # emit_prov=True (default), full QUDT + digitization

    results = []
    for kind in args.kinds:
        csv_path = args.src / f"starrydata_{kind}.csv"
        ttl_path = args.out / f"{kind}.ttl"
        err_path = args.out / f"{kind}.errors.jsonl"
        print(f"[{kind}] converting {csv_path} ...", flush=True)
        t0 = time.perf_counter()
        stats = _INGESTERS[kind](csv_path, ttl_path, cfg, error_log_path=err_path)
        dt = time.perf_counter() - t0
        size_mb = ttl_path.stat().st_size / (1024 * 1024)
        row = {
            "kind": kind,
            "rows_in": stats.rows_in,
            "rows_ok": stats.rows_ok,
            "rows_err": stats.rows_err,
            "triples": stats.triples_out,
            "seconds": round(dt, 1),
            "rows_per_sec": round(stats.rows_in / dt) if dt else None,
            "ttl_mb": round(size_mb, 1),
        }
        results.append(row)
        print(
            f"[{kind}] done: {stats.rows_in} rows -> {stats.triples_out} triples "
            f"in {dt:.1f}s ({size_mb:.1f} MB TTL, {stats.rows_err} row errors)",
            flush=True,
        )

    (args.out / "convert_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    total_triples = sum(r["triples"] for r in results)
    total_s = sum(r["seconds"] for r in results)
    print(f"\nTOTAL: {total_triples} triples in {total_s:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
