# Legacy instrument files ingest as-is: source dialect end-to-end

Date: 2026-07-11
Related: ADR [`../architecture/source-dialect.md`](../architecture/source-dialect.md)

## Question

Can Asterism ingest real legacy instrument exports **as-is** — Shift-JIS
encoding, tab separation, a preamble line before the header, non-ASCII
filenames, `.txt` extensions, and whitespace-separated card tables
(consecutive delimiters as one) — deterministically and with no LLM in the
dialect loop?

## Method

Two real XRD files from an actual instrument (untouched bytes, original
Japanese filenames):

| file | shape |
|---|---|
| `xrd_測定結果.txt` | CP932 (Shift-JIS), CRLF, **tab**-separated, line 1 = sample name preamble, line 2 = header `2θ (deg)` / `強度 (cps)`, 3,001 data rows |
| `xrd_参考文献.txt` | UTF-8 ICDD card: 23 `Key: value` preamble lines, then a **whitespace**-separated d-I table `2theta d I (hkl)` (47 rows) |

Baseline (pre-change, measured): upload → 400 (extension + non-ASCII name);
CLI inspect → `UnicodeDecodeError` crash (measurement file) / silent 1-column
garbage (card file); no delimiter/skip-rows mechanism anywhere.

Steps on branch `feat/legacy-file-dialect`:

1. `asterism-inspect` on both raw files (design-side detection).
2. `asterism-materialize --source-dir` with a §9 mapping spec that does
   **not** mention dialects (auto-pin path).
3. `asterism.substrate.materialize_to_graph` (real Morph-KGC 2.10.0) on the
   compiled RML + raw files (runtime normalization path).
4. Full test suites of all packages + byte-equivalence checks vs `git HEAD`
   for clean-CSV inputs.

## Result

1. **inspect** detects and reads both files correctly:
   - measurement: `Dialect: encoding=cp932, delimiter=tab, skip_rows=1
     (auto-detected)` — 2 columns, both `xsd:double`, 3,001 rows, unique-key
     detection on `2θ (deg)`.
   - card: `Dialect: delimiter=whitespace, skip_rows=23 (auto-detected)` —
     4 columns `2theta`(double) / `d`(double) / `I`(double) /
     `(hkl)`(string), 47 rows.
2. **materialize --source-dir** pins both dialects into the IR
   (`dialects:` section) and the compiled RML carries the annotations
   (`ast:sourceEncoding "cp932"`, `ast:sourceDelimiter "\t"` /
   `"whitespace"`, `ast:sourceSkipRows 1` / `23`) on the logical sources.
3. **Morph-KGC materialization of the raw files: 9,238 triples** —
   3,001 `DiffractionPoint` + 47 `ReferencePeak`. Spot checks against the
   physics: measured peak `2θ=40.06 → intensity 37500.0` (the file's true
   maximum, typed `xsd:double`); reference line `2θ=40.07 → d=2.248,
   (hkl)=(1,1,2), I=100.0` (the ICDD card's strongest line). The measured
   40.06 peak sits on the reference (1,1,2) line — the Al3V identification
   use case survives the round trip.
4. **No regressions**: step0 417 passed / ingest 451 passed / api 247
   passed / mcp 49 passed / ui lint+build green (counts include the
   adversarial-review regression tests added 2026-07-11; the real-file
   dogfood above was re-run after those fixes — same detections, same
   9,238 triples, same spot values). For clean UTF-8 comma CSV the inspect
   Markdown and compiled RML are **byte-identical** to `git HEAD` output
   (default dialect emits nothing anywhere).

## Conclusion

Legacy instrument files now flow as-is through the full pipeline. Dialect
handling is deterministic end-to-end: detection is a fixed-order strict
attempt list + trailing-constant-run scan (no chardet, no LLM), the result
is pinned in the design artifacts (IR → RML annotations, human-gateable),
and ingest normalizes with a pure function so Morph-KGC sees exactly what
it saw before. The "consecutive delimiters as one" behavior (Excel-style)
is covered by `delimiter: whitespace`.

## Limitations

- The `Key: value` metadata block of ICDD cards is skipped, not ingested
  (the d-I table is; semantic card parsing is a separate document-layer
  problem).
- Append (incremental batches) onto dialected sources is now supported
  (follow-up ①, plan B): the persisted copy grows in its native dialect (a
  later batch's repeated preamble+header is byte-sliced off before its data
  rows are concatenated), so a snapshot re-ingest normalizes it exactly once.
  The RML/IR is never rewritten.
- Detection scans the head of the file (1 MiB / 200 lines).
- The detection false positives an adversarial review (2026-07-11) had
  confirmed are **fixed and pinned by regression tests**: an interior blank
  line / quoted-newline cells in a clean CSV no longer invent a
  `skip_rows` (single-char candidates count csv *logical records*, and a
  constant-width comma read short-circuits to default); a preamble whose
  whitespace token count coincidentally matches the table no longer beats
  the true delimiter (whitespace only wins on a longer run with a
  *different* column count); a 1-column CSV of multi-word values / a comma
  CSV whose cells contain spaces are never hijacked by the whitespace
  candidate.
- UTF-16 is recognized via BOM only; exotic encodings fall through to
  latin-1 (lossless byte round-trip, possibly wrong glyphs — visible at
  inspect).

## Follow-up fixes (2026-07-12): three data-loss bugs an adversarial review confirmed

The follow-up work (append + wizard "read settings" overrides) shipped three
**data-loss / corruption** defects a subsequent adversarial review reproduced
in a venv. All three are fixed with red-first regression tests; the real-file
dogfood above was **re-run unchanged after these fixes — same detections, same
9,238 triples** (the fixes touch the append and override paths, not the
detection/normalization core).

1. **Append overwrote clean legacy-suffix sources.** The incremental-append
   accumulator grew the persisted file only for `.csv`; a clean (default-dialect)
   `.txt/.tsv/.dat/.asc` second batch fell through to a whole-file overwrite, so
   the first batch's rows vanished and a snapshot re-ingest diverged from the live
   graph. Fix: a module constant `_APPENDABLE_TABULAR = {.csv,.tsv,.txt,.dat,.asc}`
   gates the grow-not-overwrite branch (all read under the same default comma-CSV
   rules, so the existing header-compare + concat is correct for each). Test:
   `test_accumulate_clean_legacy_tabular_appends_not_overwrites` (4 suffixes).

2. **An override reset to a default value was silently reverted at re-pin.** An
   override keeping cp932/tab but correcting a detected `skip_rows: 1` down to `0`
   emitted nothing for `skip_rows` (a default field), so "set to default" and
   "unset" were indistinguishable; the `/api/materialize` re-pin re-detected the
   file and `entry.update(prior)` refilled `skip_rows` from the re-detected `1`,
   losing the human's correction (design preview and actual ingest diverged). Fix:
   an override-derived §9 entry emits **all four fields** (`dialect_ir_fields(...,
   full=True)` via `apply_detected_dialects(..., full_fields=<override names>)`), so
   the explicit `skip_rows: 0` is authoritative and wins the re-pin merge.
   Detection-only entries stay minimal (byte-equivalence; the RML compiler emits only
   non-default annotations regardless). Tests: step0
   `test_apply_detected_dialects_full_fields_*`, api
   `test_override_reset_to_default_survives_materialize_repin` (+ non-default and
   clean-CSV controls).

3. **A stale UI override leaked into the next dataset.** `dialectOverrides` (keyed
   by the source's canonical filename) was not cleared when the workbench discarded
   its source context — clearing the workbench, switching source kind, replacing the
   files, or seeding a redesign — so a device's fixed filename (`measurement.txt`)
   carried an old override onto an unrelated same-named file, which the server then
   read under the wrong dialect (silent column corruption). Fix: all four paths call
   a `resetDialectContext()` that empties `dialectOverrides` / `sourceNames` /
   `detectedDialects` (emptying the state, not just `sessionStorage.removeItem`, so
   the persistence effect cannot write the removed key back). UI has no unit suite;
   verified by `npm run lint` + `build` green and code review of the four paths.

## Reproduce

```sh
# design side (step0 venv)
step0/.venv/bin/asterism-inspect <dir>/xrd_測定結果.txt
step0/.venv/bin/asterism-materialize proposal.md --name xrd \
  --output-dir out --source-dir <dir>   # §9 without dialects → auto-pinned

# runtime side (ingest venv, real morph-kgc)
python -c "
from asterism.substrate import materialize_to_graph
print(len(materialize_to_graph(open('out/xrd-mapping.rml.ttl').read(), '<dir>')))
"

# suites
(cd step0 && .venv/bin/python -m pytest tests/ -q)
(cd ingest && .venv/bin/python -m pytest tests/ -q)
(cd api   && .venv/bin/python -m pytest tests/ -q)
```
