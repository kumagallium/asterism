"""Phase 0.5 用の starrydata subset 生成スクリプト。

設計プラン §10 / handoff §4.1 に従い、starrydata の papers.csv の先頭 100 行を切り出す。
出力先: experiments/phase05/data/papers_100.csv

使い方:
    python make_subset.py /path/to/starrydata_papers.csv

依存なし (標準ライブラリのみ)。
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "data" / "papers_100.csv"
N = 100


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <path/to/starrydata_papers.csv>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    if not src.is_file():
        print(f"not found: {src}", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with src.open(encoding="utf-8", newline="") as fi, OUT.open(
        "w", encoding="utf-8", newline=""
    ) as fo:
        reader = csv.reader(fi)
        writer = csv.writer(fo)
        header = next(reader)
        writer.writerow(header)
        for i, row in enumerate(reader):
            if i >= N:
                break
            writer.writerow(row)
    print(f"wrote {N} rows -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
