# Source dialect: ingesting legacy instrument files as-is

Status: accepted (2026-07-11)

## Problem

Real instrument exports are rarely clean UTF-8 comma CSV. A 2026-07-11 audit
with real XRD files confirmed the current pipeline rejects or silently
mangles them at the *entrance* while the pipeline body is healthy (the same
data converted to UTF-8 comma CSV flows perfectly):

- `xrd_測定結果.txt` — CP932 (Shift-JIS), CRLF, **tab**-separated, one
  preamble line (sample name) before the header row. Today: 400 at upload
  (extension + non-ASCII filename), `UnicodeDecodeError` in inspect
  (`utf-8-sig` hardcoded), 1-column garbage if transcoded (comma hardcoded,
  no header-offset support).
- `xrd_参考文献.txt` — UTF-8 ICDD card: `Key: value` preamble followed by a
  **whitespace-separated** d-I table (`2theta d I (hkl)`) where consecutive
  spaces act as one delimiter (Excel's "treat consecutive delimiters as
  one"). Today: silently misread as a 1-column table.

Design goal: **users throw the file in as-is**; the system detects the
dialect deterministically at design time, pins it in the design artifacts,
and normalizes deterministically at ingest time. No LLM in the loop for
dialect handling.

## Decision

Introduce a **source dialect** model that travels design → artifact →
ingest:

```
inspect (deterministic sniffing)
  → Mapping IR `dialects:` section (pinned, human-gateable)
  → RML annotations on rml:logicalSource (asterism vocab)
  → substrate normalization to UTF-8 comma CSV in work_dir (deterministic)
  → morph-kgc reads the normalized file (unchanged)
```

Detection happens **once at design time** and is pinned; ingest never
re-detects. Normalization is a pure function of `(bytes, dialect)`.
Absence of dialect (all defaults) ⇒ byte-identical current behavior.

### Dialect model (pinned contract)

```python
@dataclass(frozen=True)
class SourceDialect:
    encoding: str = "utf-8-sig"   # any Python codec name; "cp932" for Shift-JIS
    delimiter: str = ","          # single char, or the sentinel "whitespace"
    collapse: bool = False        # treat consecutive delimiters as one
    skip_rows: int = 0            # lines before the header row (preamble)
```

- `delimiter: "whitespace"` splits on runs of spaces/tabs (implies
  collapse); this is the Excel "consecutive delimiters as one" behavior and
  covers fixed-width-ish instrument tables such as the ICDD d-I list.
- The header row is the first row **after** `skip_rows`; data follows.
- Implemented twice on purpose (design side and runtime side communicate
  via the RML artifact, not Python imports — same boundary as the IR
  compiler): `asterism_step0.dialect` and `asterism.dialect`, both
  stdlib-only, same field names and semantics.

### Deterministic detection (design side, `asterism_step0.dialect`)

`detect_dialect(path) -> SourceDialect`, over a sample (first 1 MiB /
first 200 lines):

1. **Encoding**: UTF-16 BOM check → try `utf-8-sig` (strict) → try `cp932`
   (strict) → `latin-1` (always succeeds). First full decode of the sample
   wins.
2. **Delimiter + header offset**: for each candidate
   `[",", "\t", ";", "|", whitespace]` compute per-line token counts, find
   the trailing run of lines with a constant count ≥ 2. A candidate is
   valid when the run length ≥ 5 rows. Pick by
   `(run_length, columns, candidate priority)` with priority
   comma > tab > semicolon > pipe > whitespace. `skip_rows` = run start
   index (that row is the header).
3. No valid candidate ⇒ default dialect (current behavior, no annotations).

`is_default(dialect)` gates all downstream emission: default dialect emits
nothing anywhere.

### Mapping IR (design artifact)

Optional top-level section, alongside the existing maps:

```yaml
dialects:
  "xrd_measurement.txt":
    encoding: cp932
    delimiter: "\t"
    skip_rows: 1
  "xrd_reference.txt":
    delimiter: whitespace
    skip_rows: 23
```

All fields optional with the defaults above. Lint validates: codec must
resolve via `codecs.lookup`, delimiter is one printable char or
`whitespace`, `skip_rows` is a non-negative int, the filename must match a
declared source. The guided-JSON schema mirrors this (no `propertyNames` —
Sakura guided does not implement it).

The LLM never has to author this section: the design pipeline overlays
detected dialects deterministically (`apply_detected_dialects(ir,
detected)`) after propose/skeleton — explicit IR values win over detected
ones, so the human gate can override.

### RML annotations (artifact contract)

Emitted by the IR compiler on the `rml:logicalSource` node, namespace
`ast: <https://kumagallium.github.io/asterism/vocab#>` (the existing
`ASTERISM_NS`):

```turtle
rml:logicalSource [
    rml:source "xrd_measurement.txt" ;
    rml:referenceFormulation ql:CSV ;
    ast:sourceEncoding "cp932" ;
    ast:sourceDelimiter "\t" ;      # or "whitespace"
    ast:sourceCollapse true ;
    ast:sourceSkipRows 1 ;
] .
```

Only non-default values are emitted. `rml_safety` allowlists exactly these
four predicates on logical sources (they carry no execution semantics).

### Runtime normalization (`asterism.dialect` + substrate)

A new preprocessing step, **first** in the existing work_dir chain (before
`tabularize_json_sources` / `sanitize_csv_sources` / `strip_bom_sources`):

- `dialects_from_mapping(graph)` reads the annotations.
- `normalize_source(src, dialect, dest)`: decode with `dialect.encoding`
  (strict — a decode error is a real error, not something to paper over),
  drop `skip_rows` lines and blank lines, split rows (whitespace → split on
  runs of `[ \t]`; single char → split, then drop empty tokens when
  `collapse`), write UTF-8 comma CSV via `csv.writer`. CRLF is handled by
  text-mode decoding.
- Substrate rewrites `rml:source` to the normalized `.csv` work file and
  **strips the annotations** before handing the mapping to morph-kgc, which
  therefore sees exactly what it sees today.
- `read_csv_header` (design validation / design_loop) accepts an optional
  dialect so closed-set column validation reads the same rows morph-kgc
  will.

### Entrance widening

- Extensions: tabular uploads additionally accept `.tsv .txt .dat .asc`
  (API `_SAFE_SOURCE_NAME`, registry `_SOURCE_SUFFIXES` +
  `source_kind_of` → csv, `rml_safety._ALLOWED_SOURCE_SUFFIXES`, UI
  `accept` attributes). This also fixes the pre-existing inconsistency
  where `.tsv` was supported internally but unreachable through any
  entrance.
- Filenames: non-ASCII tabular filenames are slugged at every entrance,
  mirroring the existing document-name sanitizer, so 「xrd_測定結果.txt」
  becomes a stable ASCII name that the IR/RML can reference. The canonical
  name is returned to the client.
- `/api/inspect` maps decode/parse failures to 422 with a readable message
  instead of a 500 traceback (safety net; detection should normally
  prevent this).

## Scope decisions

- **Append**: incremental append of dialected sources is rejected with a
  clear 422 for now. Byte-level batch accumulation cannot safely
  concatenate preamble-bearing batches; normalizing at accumulation time
  would double-apply `skip_rows` at materialize. Snapshot re-ingest works.
  Follow-up: normalize-at-accumulation + clear the pinned dialect on the
  accumulated source.
- **ICDD card metadata**: the `Key: value` preamble of reference cards is
  *skipped*, not ingested. The d-I table ingests via
  `delimiter: whitespace`. Semantic card parsing is a separate,
  document-layer-shaped problem.
- **Legacy starrydata watcher**: unchanged (starrydata-specific, CSV-only
  by design).

## Why not alternatives

- **csvw:dialect in RML**: morph-kgc 2.10.0 has no CSVW dialect support
  (pandas reader is `encoding='utf-8'` hardcoded, no config surface), so
  standard vocabulary would be dead weight only we consume. The
  normalization layer keeps morph-kgc untouched and the annotations are
  ours either way. Revisit if morph-kgc grows dialect support.
- **chardet/charset_normalizer**: rejected previously
  (design-rationale §6) and still: a fixed, ordered strict-decode attempt
  list is deterministic and auditable; a statistical detector is neither.
  §6's re-evaluation trigger ("need to handle Shift_JIS") fired — this is
  the re-evaluation, and the answer is a pinned attempt list, not a
  detector dependency.
- **Fixing files by hand**: contradicts the product promise ("throw the
  file in as-is") and doesn't scale to instrument-attached watchers.
