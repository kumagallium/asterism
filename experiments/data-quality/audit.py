#!/usr/bin/env python3
"""Starrydata RDF data-quality audit.

Scans a SPARQL endpoint for physically-impossible or suspicious values and
writes a Markdown report of offending curves/papers. Motivated by the ZT
finding (digitization errors produced ZT up to ~13000, while real peak ZT is
~2.5-3.1).

Dependency-light: standard library only (urllib). Read-only (SELECT only).

Usage:
    python audit.py --endpoint http://localhost:7878/query --out REPORT.md
    python audit.py --endpoint http://10.0.0.1:7878/query        # print only

Checks are data-driven (see CHECKS). Each is a definitely-wrong invariant or a
review-worthy heuristic; extend the list as new data-quality rules are found.
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

PREFIXES = """
PREFIX sd: <https://kumagallium.github.io/asterism/starrydata/ontology#>
PREFIX schema: <https://schema.org/>
"""


@dataclass
class Check:
    key: str
    title: str
    severity: str  # "impossible" | "suspicious" | "info"
    why: str
    count_query: str
    sample_query: str = ""
    cols: list[str] = field(default_factory=list)


# --- the audit rules -------------------------------------------------------
# "impossible" = violates physics / aggregate invariants (definitely a bug).
# "suspicious" = beyond the realistic range; worth human review.
# "info"       = descriptive surfacing for eyeballing.
CHECKS: list[Check] = [
    Check(
        key="zt_impossible",
        title="ZT peak above the physical ceiling or negative",
        severity="impossible",
        why="ZT >= 0 always, and real peak ZT tops out ~3.1. yMax > 3.5 or < 0 "
        "indicates a mislabeled axis or digitization/scale error.",
        count_query="""SELECT (COUNT(*) AS ?n) WHERE {
  ?c a sd:Curve ; sd:propertyY ?py ; sd:yMax ?y .
  FILTER((LCASE(STR(?py))="zt" || CONTAINS(LCASE(STR(?py)),"figure of merit"))
         && (?y > 3.5 || ?y < 0))
}""",
        sample_query="""SELECT ?y ?py ?comp ?title WHERE {
  ?c a sd:Curve ; sd:propertyY ?py ; sd:yMax ?y ; sd:ofSample ?s .
  ?s sd:fromPaper ?p .
  OPTIONAL { ?s sd:compositionString ?comp }
  OPTIONAL { ?p schema:name ?title }
  FILTER((LCASE(STR(?py))="zt" || CONTAINS(LCASE(STR(?py)),"figure of merit"))
         && (?y > 3.5 || ?y < 0))
} ORDER BY DESC(?y) LIMIT 30""",
        cols=["y", "py", "comp", "title"],
    ),
    Check(
        key="zt_suspicious",
        title="ZT peak in the record-questionable band (3.0, 3.5]",
        severity="suspicious",
        why="Above the well-established record territory (~3.1). Real, but worth "
        "verifying against the source figure before quoting as a 'record'.",
        count_query="""SELECT (COUNT(*) AS ?n) WHERE {
  ?c a sd:Curve ; sd:propertyY ?py ; sd:yMax ?y .
  FILTER((LCASE(STR(?py))="zt" || CONTAINS(LCASE(STR(?py)),"figure of merit"))
         && ?y > 3.0 && ?y <= 3.5)
}""",
        sample_query="""SELECT ?y ?comp ?title WHERE {
  ?c a sd:Curve ; sd:propertyY ?py ; sd:yMax ?y ; sd:ofSample ?s .
  ?s sd:fromPaper ?p .
  OPTIONAL { ?s sd:compositionString ?comp }
  OPTIONAL { ?p schema:name ?title }
  FILTER((LCASE(STR(?py))="zt" || CONTAINS(LCASE(STR(?py)),"figure of merit"))
         && ?y > 3.0 && ?y <= 3.5)
} ORDER BY DESC(?y) LIMIT 30""",
        cols=["y", "comp", "title"],
    ),
    Check(
        key="ymin_gt_ymax",
        title="Inconsistent y aggregates (yMin > yMax)",
        severity="impossible",
        why="The pre-computed aggregates are inconsistent — a bug in the curve's "
        "y[] parsing or aggregation.",
        count_query="""SELECT (COUNT(*) AS ?n) WHERE {
  ?c a sd:Curve ; sd:yMin ?lo ; sd:yMax ?hi . FILTER(?lo > ?hi)
}""",
        sample_query="""SELECT ?lo ?hi ?fig WHERE {
  ?c a sd:Curve ; sd:yMin ?lo ; sd:yMax ?hi . OPTIONAL { ?c sd:figureName ?fig }
  FILTER(?lo > ?hi)
} LIMIT 30""",
        cols=["lo", "hi", "fig"],
    ),
    Check(
        key="xmin_gt_xmax",
        title="Inconsistent x aggregates (xMin > xMax)",
        severity="impossible",
        why="x[] aggregate inconsistency.",
        count_query="""SELECT (COUNT(*) AS ?n) WHERE {
  ?c a sd:Curve ; sd:xMin ?lo ; sd:xMax ?hi . FILTER(?lo > ?hi)
}""",
        sample_query="""SELECT ?lo ?hi ?fig WHERE {
  ?c a sd:Curve ; sd:xMin ?lo ; sd:xMax ?hi . OPTIONAL { ?c sd:figureName ?fig }
  FILTER(?lo > ?hi)
} LIMIT 30""",
        cols=["lo", "hi", "fig"],
    ),
    Check(
        key="pointcount_nonpositive",
        title="Curve with pointCount <= 0",
        severity="impossible",
        why="A digitized curve must have at least one (x,y) point.",
        count_query="""SELECT (COUNT(*) AS ?n) WHERE {
  ?c a sd:Curve ; sd:pointCount ?pc . FILTER(?pc <= 0)
}""",
        sample_query="""SELECT ?pc ?fig WHERE {
  ?c a sd:Curve ; sd:pointCount ?pc . OPTIONAL { ?c sd:figureName ?fig }
  FILTER(?pc <= 0)
} LIMIT 30""",
        cols=["pc", "fig"],
    ),
    Check(
        key="temp_below_abs_zero",
        title="Temperature x-axis below absolute zero",
        severity="impossible",
        why="xMin < -273.16 is below 0 K regardless of whether the axis is K or "
        "degC — an impossible temperature.",
        count_query="""SELECT (COUNT(*) AS ?n) WHERE {
  ?c a sd:Curve ; sd:propertyX ?px ; sd:xMin ?lo .
  FILTER(CONTAINS(LCASE(STR(?px)),"temp") && ?lo < -273.16)
}""",
        sample_query="""SELECT ?lo ?px ?fig WHERE {
  ?c a sd:Curve ; sd:propertyX ?px ; sd:xMin ?lo . OPTIONAL { ?c sd:figureName ?fig }
  FILTER(CONTAINS(LCASE(STR(?px)),"temp") && ?lo < -273.16)
} ORDER BY ?lo LIMIT 30""",
        cols=["lo", "px", "fig"],
    ),
    Check(
        key="absurd_magnitude",
        title="Absurd |yMax| (> 1e25) beyond any physical quantity",
        severity="impossible",
        why="No materials quantity reaches 1e25 (even carrier concentration tops "
        "out ~1e23 cm^-3). Values above this are unit/scale or parsing errors. "
        "(A lower threshold like 1e6 would false-positive on legitimate carrier "
        "concentration ~1e19-1e21.)",
        count_query="""SELECT (COUNT(*) AS ?n) WHERE {
  ?c a sd:Curve ; sd:yMax ?y . FILTER(ABS(?y) > 1.0e25)
}""",
        sample_query="""SELECT ?y ?py ?fig WHERE {
  ?c a sd:Curve ; sd:yMax ?y ; sd:propertyY ?py . OPTIONAL { ?c sd:figureName ?fig }
  FILTER(ABS(?y) > 1.0e25)
} ORDER BY DESC(ABS(?y)) LIMIT 30""",
        cols=["y", "py", "fig"],
    ),
    Check(
        key="negative_nonneg_quantity",
        title="Negative value for a non-negative quantity",
        severity="impossible",
        why="These quantities are physically >= 0: thermal/electrical "
        "conductivity, resistivity, carrier concentration, power factor "
        "(S^2*sigma). yMin < 0 is a sign/parse error. EXCLUDED to avoid false "
        "positives: log()/ln() axes (legitimately negative when value < 1), "
        "'coefficient' (e.g. Temperature Coefficient of Resistivity is "
        "legitimately negative), mobility (Hall mobility sign convention), and "
        "Seebeck/thermopower/Hall (sign is meaningful).",
        count_query="""SELECT (COUNT(*) AS ?n) WHERE {
  ?c a sd:Curve ; sd:propertyY ?py ; sd:yMin ?lo . FILTER(?lo < 0
    && !CONTAINS(LCASE(STR(?py)),"log(") && !CONTAINS(LCASE(STR(?py)),"ln(")
    && !CONTAINS(LCASE(STR(?py)),"coefficient") && (
    CONTAINS(LCASE(STR(?py)),"thermal conductivity") ||
    CONTAINS(LCASE(STR(?py)),"electrical conductivity") ||
    CONTAINS(LCASE(STR(?py)),"resistivity") ||
    CONTAINS(LCASE(STR(?py)),"carrier concentration") ||
    CONTAINS(LCASE(STR(?py)),"carrier density") ||
    CONTAINS(LCASE(STR(?py)),"power factor")))
}""",
        sample_query="""SELECT ?lo ?py ?fig WHERE {
  ?c a sd:Curve ; sd:propertyY ?py ; sd:yMin ?lo . OPTIONAL { ?c sd:figureName ?fig }
  FILTER(?lo < 0
    && !CONTAINS(LCASE(STR(?py)),"log(") && !CONTAINS(LCASE(STR(?py)),"ln(")
    && !CONTAINS(LCASE(STR(?py)),"coefficient") && (
    CONTAINS(LCASE(STR(?py)),"thermal conductivity") ||
    CONTAINS(LCASE(STR(?py)),"electrical conductivity") ||
    CONTAINS(LCASE(STR(?py)),"resistivity") ||
    CONTAINS(LCASE(STR(?py)),"carrier concentration") ||
    CONTAINS(LCASE(STR(?py)),"carrier density") ||
    CONTAINS(LCASE(STR(?py)),"power factor")))
} ORDER BY ?lo LIMIT 30""",
        cols=["lo", "py", "fig"],
    ),
    Check(
        key="missing_propertyY",
        title="Curve without a propertyY label",
        severity="suspicious",
        why="A curve with no sd:propertyY cannot be interpreted (we don't know "
        "what quantity it measures). Likely an ingest/source gap.",
        count_query="""SELECT (COUNT(*) AS ?n) WHERE {
  ?c a sd:Curve . FILTER NOT EXISTS { ?c sd:propertyY ?py }
}""",
        sample_query="""SELECT ?id ?fig WHERE {
  ?c a sd:Curve . OPTIONAL { ?c sd:figureName ?fig }
  OPTIONAL { ?c <http://purl.org/dc/terms/identifier> ?id }
  FILTER NOT EXISTS { ?c sd:propertyY ?py }
} LIMIT 20""",
        cols=["id", "fig"],
    ),
    Check(
        key="missing_ymax",
        title="Curve without a yMax aggregate",
        severity="info",
        why="No sd:yMax means peak/range queries silently skip the curve. May be "
        "legitimate (empty y[]) but worth knowing the volume.",
        count_query="""SELECT (COUNT(*) AS ?n) WHERE {
  ?c a sd:Curve . FILTER NOT EXISTS { ?c sd:yMax ?y }
}""",
        sample_query="""SELECT ?py ?fig WHERE {
  ?c a sd:Curve . OPTIONAL { ?c sd:propertyY ?py } OPTIONAL { ?c sd:figureName ?fig }
  FILTER NOT EXISTS { ?c sd:yMax ?y }
} LIMIT 20""",
        cols=["py", "fig"],
    ),
]


def sparql(endpoint: str, query: str, timeout: float = 60.0) -> list[dict[str, str]]:
    data = urllib.parse.urlencode({"query": PREFIXES + query}).encode()
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/sparql-results+json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.load(r)
    out = []
    for b in body.get("results", {}).get("bindings", []):
        out.append({k: v.get("value", "") for k, v in b.items()})
    return out


def _count(endpoint: str, query: str) -> int:
    rows = sparql(endpoint, query)
    if not rows:
        return 0
    val = next(iter(rows[0].values()))
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def run(endpoint: str) -> tuple[str, dict]:
    lines: list[str] = []
    summary: dict = {"endpoint": endpoint, "checks": []}

    total_curves = _count(endpoint, "SELECT (COUNT(*) AS ?n) WHERE { ?c a sd:Curve }")
    lines.append("# Starrydata RDF data-quality audit\n")
    lines.append(f"- Endpoint: `{endpoint}`")
    lines.append(f"- Total curves: **{total_curves:,}**\n")

    sev_order = {"impossible": 0, "suspicious": 1, "info": 2}
    findings = []
    for chk in CHECKS:
        n = _count(endpoint, chk.count_query)
        findings.append((chk, n))
        summary["checks"].append({"key": chk.key, "severity": chk.severity, "count": n})

    n_impossible = sum(n for c, n in findings if c.severity == "impossible")
    n_suspicious = sum(n for c, n in findings if c.severity == "suspicious")
    lines.append("## Summary\n")
    lines.append(f"- **Impossible** (definite bugs): **{n_impossible:,}** records")
    lines.append(f"- **Suspicious** (review): **{n_suspicious:,}** records\n")
    lines.append("| severity | check | count |")
    lines.append("|---|---|---|")
    for chk, n in sorted(findings, key=lambda t: (sev_order[t[0].severity], -t[1])):
        flag = "🔴" if chk.severity == "impossible" and n else (
            "🟡" if chk.severity == "suspicious" and n else "🟢"
        )
        lines.append(f"| {flag} {chk.severity} | {chk.title} | {n:,} |")
    lines.append("")

    for chk, n in sorted(findings, key=lambda t: (sev_order[t[0].severity], -t[1])):
        if n == 0:
            continue
        lines.append(f"### {chk.title}  ({chk.severity}, {n:,})\n")
        lines.append(f"{chk.why}\n")
        if chk.sample_query:
            rows = sparql(endpoint, chk.sample_query)
            if rows:
                lines.append("| " + " | ".join(chk.cols) + " |")
                lines.append("|" + "---|" * len(chk.cols))
                for row in rows:
                    cells = []
                    for col in chk.cols:
                        v = (row.get(col, "") or "").replace("\n", " ").strip()
                        cells.append(v[:80])
                    lines.append("| " + " | ".join(cells) + " |")
                if n > len(rows):
                    lines.append(f"\n_…and {n - len(rows):,} more._")
                lines.append("")

    return "\n".join(lines), summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True, help="SPARQL query endpoint URL")
    ap.add_argument("--out", help="Write Markdown report to this path")
    ap.add_argument("--json", help="Write machine-readable summary JSON to this path")
    args = ap.parse_args()

    report, summary = run(args.endpoint)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"wrote {args.out}")
    else:
        print(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"wrote {args.json}")

    n_imp = sum(c["count"] for c in summary["checks"] if c["severity"] == "impossible")
    return 1 if n_imp else 0


if __name__ == "__main__":
    raise SystemExit(main())
