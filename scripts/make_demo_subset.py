#!/usr/bin/env python3
"""Generate a small, REAL demo seed from the full starrydata dataset.

Selects the first N papers (by SID) that actually have a ``ZT`` curve — so the
demo's headline question ("highest ZT") always has data — then filters samples
+ curves to those SIDs and runs the real asterism ingester to produce the Turtle
the demo loads into Oxigraph.

Licensed source data stays OUT of the repo: everything is written under
``datasets/starrydata/seed/`` (gitignore that dir). Reproducible: same inputs + N
give the same seed.

Usage:
    python scripts/make_demo_subset.py --src ../starrydata_dataset --n-papers 40
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "ingest" / "src"))

from asterism.starrydata import (  # noqa: E402
    ingest_curves,
    ingest_papers,
    ingest_samples,
)

# starrydata x/y columns are large JSON arrays; lift the csv field cap.
csv.field_size_limit(10_000_000)


def _denul(fi):
    """Strip stray NUL bytes some starrydata rows carry (csv rejects them)."""
    return (line.replace("\x00", "") for line in fi)


def _select_zt_sids(curves_csv: Path, n: int) -> set[str]:
    """First N distinct SIDs that have at least one ZT curve."""
    picked: list[str] = []
    seen: set[str] = set()
    with curves_csv.open(encoding="utf-8-sig", newline="") as fi:
        for row in csv.DictReader(_denul(fi)):
            if row.get("prop_y") == "ZT":
                sid = row.get("SID")
                if sid and sid not in seen:
                    seen.add(sid)
                    picked.append(sid)
                    if len(picked) >= n:
                        break
    return set(picked)


def _subset(src: Path, name: str, sids: set[str], out_csv: Path) -> int:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    in_csv = src / f"starrydata_{name}.csv"
    with (
        in_csv.open(encoding="utf-8-sig", newline="") as fi,
        out_csv.open("w", encoding="utf-8", newline="") as fo,
    ):
        reader = csv.reader(_denul(fi))
        writer = csv.writer(fo)
        header = next(reader)
        writer.writerow(header)
        sid_idx = header.index("SID") if "SID" in header else None
        for row in reader:
            if sid_idx is not None and sid_idx < len(row) and row[sid_idx] in sids:
                writer.writerow(row)
                written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=_REPO.parent / "starrydata_dataset")
    ap.add_argument("--n-papers", type=int, default=40)
    ap.add_argument(
        "--include-sids",
        default="",
        help="comma-separated SIDs to force-include (e.g. a known >3.5 ZT "
        "outlier so the data-quality exclusion is visible in the demo)",
    )
    ap.add_argument(
        "--out", type=Path, default=_REPO / "datasets" / "starrydata" / "seed"
    )
    args = ap.parse_args(argv)

    src: Path = args.src
    out: Path = args.out
    csv_dir = out / "csv"

    sids = _select_zt_sids(src / "starrydata_curves.csv", args.n_papers)
    if args.include_sids:
        sids |= {s.strip() for s in args.include_sids.split(",") if s.strip()}
    if not sids:
        print("no ZT curves found in source dataset", file=sys.stderr)
        return 1

    n_pap = _subset(src, "papers", sids, csv_dir / "papers.csv")
    n_sam = _subset(src, "samples", sids, csv_dir / "samples.csv")
    n_cur = _subset(src, "curves", sids, csv_dir / "curves.csv")
    print(
        f"subset: SIDs={len(sids)} papers={n_pap} samples={n_sam} curves={n_cur}",
        file=sys.stderr,
    )

    sp = ingest_papers(csv_dir / "papers.csv", out / "papers.ttl")
    ss = ingest_samples(csv_dir / "samples.csv", out / "samples.ttl")
    sc = ingest_curves(csv_dir / "curves.csv", out / "curves.ttl")
    print(
        f"triples: papers={sp.triples_out} samples={ss.triples_out} "
        f"curves={sc.triples_out}",
        file=sys.stderr,
    )
    print(f"seed written to {out}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
