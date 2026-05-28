"""Phase 0.5 — morph-kgc 経由で author を展開するための前処理。

CSV の中の "author" 列が JSON 配列文字列なので、これを JSON Lines に展開して
別ファイルに書き出す。これにより morph-kgc は rml:JSONPath で iterate できる。

(つまり「declarative」を維持するためには CSV を一旦バラさないといけない。)
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <papers.csv> <authors.jsonl>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    n = 0
    with src.open(encoding="utf-8", newline="") as fi, dst.open(
        "w", encoding="utf-8"
    ) as fo:
        reader = csv.DictReader(fi)
        for row in reader:
            sid = row["SID"]
            try:
                authors = json.loads(row["author"]) if row["author"] else []
            except json.JSONDecodeError:
                continue
            for i, a in enumerate(authors):
                if not isinstance(a, dict):
                    continue
                rec = {
                    "sid": sid,
                    "idx": i,
                    "given": a.get("given", ""),
                    "family": a.get("family", ""),
                }
                fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    print(f"wrote {n} author records -> {dst}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
