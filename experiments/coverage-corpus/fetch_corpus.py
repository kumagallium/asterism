#!/usr/bin/env python3
"""Fetch + deterministically downsample the Track C coverage corpus.

This is a **provenance / reproducibility** script, not part of any shipped
package. It downloads a curated set of *diverse, real, license-clean* public
datasets (mostly `vega-datasets`, BSD-3-Clause; plus open government / CC0 /
open-metadata sources) and writes a small evenly-sampled slice of each into
``datasets/<id>/source/``. The committed slices make the coverage experiment
runnable offline; this script lets a reviewer regenerate them and audit where
each row came from.

Why downsample: the coverage report only needs each dataset's *column shapes*
(types, sample values, multi-valued / JSON cells) — not millions of rows. We
keep an evenly-spaced slice (first..last) so seasonal / categorical / temporal
diversity survives while the repo stays tiny.

Network is reached via ``curl`` (matches the sandbox's allowed egress). Each
source's licence + URL is recorded in ``SOURCES.md`` (kept in sync by hand).

Usage::

    python3 experiments/coverage-corpus/fetch_corpus.py          # fetch all
    python3 experiments/coverage-corpus/fetch_corpus.py cars stocks  # subset
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASETS = HERE / "datasets"

VEGA = "https://raw.githubusercontent.com/vega/vega-datasets/main/data"
CROSSREF = (
    "https://api.crossref.org/works?rows=40"
    "&select=DOI,title,author,published,container-title,type,is-referenced-by-count,subject"
    "&mailto=asterism-coverage@example.org"
)
OPENLIBRARY = (
    "https://openlibrary.org/search.json?q=science&limit=40"
    "&fields=title,author_name,first_publish_year,isbn,language,subject,number_of_pages_median"
)
GITHUB = "https://api.github.com/search/repositories?q=stars:%3E20000&sort=stars&per_page=40"


def _curl(url: str) -> bytes:
    """Fetch ``url`` and return raw bytes (raises on non-zero curl exit)."""
    out = subprocess.run(
        ["curl", "-sS", "-L", "--max-time", "60", url],
        check=True,
        capture_output=True,
    )
    return out.stdout


def _evenly_spaced(n_total: int, n_keep: int) -> list[int]:
    """Indices of an evenly-spaced slice of ``n_keep`` items from ``n_total``.

    Always includes the first and last item so the slice spans the full range
    (seasons, years, categories). Deterministic — no RNG, so the committed
    slice is byte-stable across re-runs.
    """
    if n_total <= n_keep:
        return list(range(n_total))
    if n_keep <= 1:
        return [0]
    step = (n_total - 1) / (n_keep - 1)
    return sorted({round(i * step) for i in range(n_keep)})


def _downsample_csv(raw: bytes, n_keep: int) -> str:
    text = raw.decode("utf-8-sig")
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        return ""
    header, rows = reader[0], reader[1:]
    keep = _evenly_spaced(len(rows), n_keep)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for i in keep:
        writer.writerow(rows[i])
    return buf.getvalue()


def _downsample_json_array(raw: bytes, n_keep: int, *, record_key: str | None = None) -> str:
    data = json.loads(raw.decode("utf-8"))
    records = data[record_key] if record_key else data
    keep = _evenly_spaced(len(records), n_keep)
    sliced = [records[i] for i in keep]
    return json.dumps(sliced, ensure_ascii=False, indent=1) + "\n"


def _downsample_geojson(raw: bytes, n_keep: int) -> str:
    data = json.loads(raw.decode("utf-8"))
    feats = data.get("features", [])
    keep = _evenly_spaced(len(feats), n_keep)
    data["features"] = [feats[i] for i in keep]
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def _crossref(raw: bytes, n_keep: int) -> str:
    items = json.loads(raw.decode("utf-8"))["message"]["items"][:n_keep]
    return json.dumps(items, ensure_ascii=False, indent=1) + "\n"


def _openlibrary(raw: bytes, n_keep: int) -> str:
    """OpenLibrary search docs → trimmed records (multi-valued author/subject/isbn)."""
    docs = json.loads(raw.decode("utf-8")).get("docs", [])[:n_keep]
    out = []
    for d in docs:
        out.append(
            {
                "title": d.get("title"),
                "author_name": d.get("author_name"),  # array of strings
                "first_publish_year": d.get("first_publish_year"),
                "isbn": (d.get("isbn") or [])[:5],  # array; trim
                "language": d.get("language"),  # array, often ["eng"]
                "subject": (d.get("subject") or [])[:8],  # array; trim
                "number_of_pages_median": d.get("number_of_pages_median"),
            }
        )
    return json.dumps(out, ensure_ascii=False, indent=1) + "\n"


def _github(raw: bytes, n_keep: int) -> str:
    """GitHub repo search items → trimmed records (topics[]/owner{}/license{}/bool)."""
    items = json.loads(raw.decode("utf-8")).get("items", [])[:n_keep]
    out = []
    for it in items:
        owner = it.get("owner") or {}
        lic = it.get("license") or {}
        out.append(
            {
                "full_name": it.get("full_name"),
                "owner": {"login": owner.get("login"), "type": owner.get("type")},
                "language": it.get("language"),
                "license": {"spdx_id": lic.get("spdx_id"), "name": lic.get("name")},
                "topics": it.get("topics"),  # array of strings
                "stargazers_count": it.get("stargazers_count"),
                "forks_count": it.get("forks_count"),
                "open_issues_count": it.get("open_issues_count"),
                "archived": it.get("archived"),  # boolean
                "created_at": it.get("created_at"),  # ISO dateTime
                "html_url": it.get("html_url"),
            }
        )
    return json.dumps(out, ensure_ascii=False, indent=1) + "\n"


# (dataset id, source filename, url, kind, n_keep)
SPECS: list[tuple[str, str, str, str, int]] = [
    ("seattle-weather", "seattle-weather.csv", f"{VEGA}/seattle-weather.csv", "csv", 90),
    ("cars", "cars.json", f"{VEGA}/cars.json", "json", 60),
    ("movies", "movies.json", f"{VEGA}/movies.json", "json", 50),
    ("stocks", "stocks.csv", f"{VEGA}/stocks.csv", "csv", 80),
    ("gapminder", "gapminder.json", f"{VEGA}/gapminder.json", "json", 80),
    ("penguins", "penguins.json", f"{VEGA}/penguins.json", "json", 70),
    ("disasters", "disasters.csv", f"{VEGA}/disasters.csv", "csv", 120),
    (
        "unemployment-industry",
        "unemployment-across-industries.json",
        f"{VEGA}/unemployment-across-industries.json",
        "json",
        80,
    ),
    ("co2-concentration", "co2-concentration.csv", f"{VEGA}/co2-concentration.csv", "csv", 80),
    ("airports", "airports.csv", f"{VEGA}/airports.csv", "csv", 90),
    ("earthquakes", "earthquakes.geojson", f"{VEGA}/earthquakes.json", "geojson", 40),
    ("crossref-works", "crossref-works.json", CROSSREF, "crossref", 40),
    ("openlibrary-books", "openlibrary-books.json", OPENLIBRARY, "openlibrary", 40),
    ("github-repos", "github-repos.json", GITHUB, "github", 40),
]


def _render(kind: str, raw: bytes, n_keep: int) -> str:
    if kind == "csv":
        return _downsample_csv(raw, n_keep)
    if kind == "json":
        return _downsample_json_array(raw, n_keep)
    if kind == "geojson":
        return _downsample_geojson(raw, n_keep)
    if kind == "crossref":
        return _crossref(raw, n_keep)
    if kind == "openlibrary":
        return _openlibrary(raw, n_keep)
    if kind == "github":
        return _github(raw, n_keep)
    raise ValueError(f"unknown kind: {kind}")


def main(argv: Sequence[str]) -> int:
    wanted = set(argv) if argv else None
    failures: list[str] = []
    for ds_id, fname, url, kind, n_keep in SPECS:
        if wanted and ds_id not in wanted:
            continue
        dest = DATASETS / ds_id / "source" / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = _curl(url)
            content = _render(kind, raw, n_keep)
        except Exception as exc:  # provenance script: report and continue
            failures.append(f"{ds_id}: {exc}")
            sys.stderr.write(f"  ✗ {ds_id}: {exc}\n")
            continue
        dest.write_text(content, encoding="utf-8")
        sys.stdout.write(f"  ✓ {ds_id} → {dest.relative_to(HERE)} ({len(content):,} bytes)\n")
    if failures:
        sys.stderr.write(f"\n{len(failures)} dataset(s) failed to fetch.\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
