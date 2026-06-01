# Starrydata RDF data-quality audit

A standalone, read-only auditor that scans a SPARQL endpoint for
physically-impossible or suspicious values in the digitized curves, and writes
a Markdown report of the offending curves/papers.

Motivated by the ZT finding: a "highest ZT" query surfaced values up to
**~13,000** (real peak ZT is ~3.1) because the source figures were mislabeled
or mis-digitized. This auditor generalizes that into a small set of rules so
data-quality regressions are easy to spot after each re-ingest.

## Run

```bash
# against the deployed stack (full dataset)
python audit.py --endpoint http://10.0.0.1:7878/query --out REPORT.md --json summary.json

# against a local docker-compose stack
python audit.py --endpoint http://localhost:7878/query
```

Standard library only (no deps). SELECT-only (does not modify the store).
Exit code is non-zero when any **impossible** records are found (handy for CI).

## Checks

Severities: **impossible** = violates physics / aggregate invariants (definite
bug); **suspicious** = beyond the realistic range, worth human review.

| severity | check | rule |
|---|---|---|
| impossible | ZT peak above ceiling / negative | ZT (`propertyY` "ZT" / "Figure of merit…") with `yMax > 3.5` or `< 0` |
| impossible | Absurd \|yMax\| | `\|yMax\| > 1e25` (above any physical quantity; carrier conc. tops ~1e23) |
| impossible | Temperature below absolute zero | Temperature x-axis with `xMin < -273.16` |
| impossible | Inconsistent y/x aggregates | `yMin > yMax` or `xMin > xMax` |
| impossible | pointCount <= 0 | a digitized curve must have ≥1 point |
| suspicious | ZT in (3.0, 3.5] | at/above record territory; verify before quoting as a "record" |

Extend `CHECKS` in `audit.py` as new rules are discovered — this is the small,
hand-curated data-quality layer (the schema itself is described in the MIE /
ontology, not here).

## Notes

- `REPORT.md` / `summary.json` are **snapshots** of a run against the live
  endpoint; regenerate after each re-ingest. They are point-in-time, not
  authoritative.
- These rules detect that a value is *wrong*, not *why*. Fixing belongs upstream
  (the starrydata source figures / digitization), not in the RDF or the MIE —
  papering over outliers in the MIE does not converge; cleaning the data does.
- The MIE (`data/togomcp/mie/starrydata.yaml`) carries a `FILTER(?zt <= 3.5)`
  guard in its ZT example so AI clients exclude these outliers at query time
  until the data is cleaned.
