"""Phase 1 full-stack subset 生成: papers の SID と整合する samples + curves を切り出す。

Phase 0.5 で作った papers_100.csv は SID 1-112 程度。
samples.csv は SID を持つので、それでフィルタする。
curves.csv も SID を持つので、それでフィルタする。

使い方:
    python make_full_subset.py /path/to/starrydata_dataset/
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "subset"


def filter_csv(src: Path, dst: Path, key: str, allowed: set[str]) -> int:
    n = 0
    # NOTE: starrydata の curves.csv と samples.csv は UTF-8 BOM 付き。
    # utf-8-sig で開けば DictReader が "﻿SID" ではなく "SID" を見るようになる。
    with src.open(encoding="utf-8-sig", newline="") as fi, dst.open(
        "w", encoding="utf-8", newline=""
    ) as fo:
        reader = csv.DictReader(fi)
        writer = csv.DictWriter(fo, fieldnames=reader.fieldnames or [])
        writer.writeheader()
        for row in reader:
            if row.get(key, "").strip() in allowed:
                writer.writerow(row)
                n += 1
    return n


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <starrydata_dataset_dir>", file=sys.stderr)
        return 2
    src_dir = Path(sys.argv[1])
    papers_src = src_dir / "starrydata_papers.csv"
    samples_src = src_dir / "starrydata_samples.csv"
    curves_src = src_dir / "starrydata_curves.csv"
    OUT.mkdir(parents=True, exist_ok=True)

    # First 100 papers (SID).
    sids: set[str] = set()
    papers_dst = OUT / "papers_100.csv"
    with papers_src.open(encoding="utf-8-sig", newline="") as fi, papers_dst.open(
        "w", encoding="utf-8", newline=""
    ) as fo:
        reader = csv.DictReader(fi)
        writer = csv.DictWriter(fo, fieldnames=reader.fieldnames or [])
        writer.writeheader()
        for i, row in enumerate(reader):
            if i >= 100:
                break
            writer.writerow(row)
            sids.add(row.get("SID", "").strip())
    print(f"papers: 100 rows -> {papers_dst} (SIDs: {sorted(int(s) for s in sids if s.isdigit())[:10]}...)")

    samples_dst = OUT / "samples_for_papers_100.csv"
    n_samples = filter_csv(samples_src, samples_dst, "SID", sids)
    print(f"samples: {n_samples} rows -> {samples_dst}")

    curves_dst = OUT / "curves_for_papers_100.csv"
    n_curves = filter_csv(curves_src, curves_dst, "SID", sids)
    print(f"curves: {n_curves} rows -> {curves_dst}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
