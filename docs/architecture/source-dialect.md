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
    preamble: str = "drop"        # "drop" (default) | "keyvalue" | "lines"
```

- `delimiter: "whitespace"` splits on runs of spaces/tabs (implies
  collapse); this is the Excel "consecutive delimiters as one" behavior and
  covers fixed-width-ish instrument tables such as the ICDD d-I list.
- The header row is the first row **after** `skip_rows`; data follows.
- `preamble` decides what happens to the `skip_rows` lines: `drop` (default)
  discards them exactly as before (byte-identical); `keyvalue`/`lines` parse
  them into metadata columns **broadcast** onto every body row (see "Header
  metadata" below). It is orthogonal to the other four fields — it interprets
  the preamble *block*, never the body tokenization.
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
2. **Well-formed short circuit**: when the comma (default) read yields csv
   **logical records** with a constant column count ≥ 2 from the FIRST
   record on, the file already reads correctly under the default rules —
   only the encoding can pin; no other candidate is consulted. (This is
   what protects a clean CSV whose cells contain spaces, blank lines, or
   quoted newlines from any false positive.)
3. **Single-char candidates** (`, \t ; |`): per candidate, tokenize the
   sample into csv **logical records** (a quoted cell containing newlines
   is ONE record; blank records are excluded from the count column) and
   find the trailing run of records with a constant token count ≥ 2. A
   candidate is valid when the run length ≥ 5 records. Pick by
   `(run_length, columns, candidate priority)` with priority
   comma > tab > semicolon > pipe.
4. **Whitespace subordination**: the whitespace candidate counts non-blank
   physical lines (`[ \t]+` splits — no quote concept) and only ever wins:
   - against a valid single-char candidate, when its run is strictly
     longer AND its column count differs (a tab table splits into the
     SAME columns under `[ \t]+`, so an equal count is a structural
     artifact of the single-char table, not independent evidence);
   - with no valid single-char candidate, when its run starts at the first
     record (a preamble-free whitespace table) or the comma read is not
     constant-width from the start (so a clean constant-width CSV — even a
     1-column one whose values happen to contain spaces — is never
     hijacked).
5. `skip_rows` = the adopted run's first record's **physical line index**
   (that row is the header).
6. No valid candidate ⇒ default dialect (current behavior, no annotations).

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

All fields optional with the defaults above. Lint validates: the codec must
be a known **text** codec (a bytes↔bytes codec like `zip`/`base64` resolves
via `codecs.lookup` but would crash the runtime decode), delimiter is one
printable char or `whitespace`, `skip_rows` is a non-negative int, the
filename must match a declared source. The guided-JSON schema mirrors this
(no `propertyNames` — Sakura guided does not implement it).

The LLM never has to author this section: the design pipeline overlays
detected dialects deterministically (`apply_detected_dialects(ir,
detected)`) after propose/skeleton — explicit IR values win over detected
ones, so the human gate can override. A refine round or hand edit can drop
the section, so every re-entry point re-pins: the design loop re-applies
after each round, and `/api/materialize` with a `dataset_id` resolves the
dataset's persisted source dir and re-detects (`materialize_schema(...,
source_dir=…)`) before compiling.

### Wizard "read settings" (follow-up ③ — human override before generation)

The dialect determines the header row (and therefore the column set), so the
human confirms/corrects it **before** generation, not after. `/api/inspect`
returns the structured detected dialect of every non-default source in an
additive `X-Asterism-Dialects` header (`{name: {encoding, delimiter, collapse,
skip_rows, origin}}`; the delimiter is the canonical token, JSON-escaped so the
header stays ASCII; a clean-CSV set yields `{}`). The workbench renders a
collapsible read-settings panel next to the inspect view showing one row per
non-default/legacy-suffix source — a clean-CSV set shows nothing (zero
friction). An edit becomes a per-source override sent as the `dialects` JSON
form field on `/api/propose`, `/api/propose/skeleton` and `/api/propose/continue`
(the delimiter is a translated label but always sent as the canonical token —
the two-layer contract). The API boundary-checks each override with the IR's
dialect linter (`mapping_ir._parse_dialects`, field-only) → a readable 422, never
a bad §9 annotation. The design loop merges the overrides OVER detection
(`effective = {**detected, **overrides}`) and drives **every** source read — the
Tier-0 oracle columns, the inline/skeleton inspection, and the §9 pin — from that
one effective map, so a `skip_rows` edit that moves the header stays consistent
everywhere. The override lands in §9 as an explicit `dialects:` entry — and because
it is the source's *complete intended* dialect, an override entry is pinned with **all
four fields, defaults included** (`apply_detected_dialects(..., full_fields=<override
names>)`). This is what makes an *explicit default* survive the materialize re-pin:
correcting a detected `skip_rows: 1` down to `0` writes `skip_rows: 0` verbatim into §9,
so re-detection (which yields `skip_rows: 1` again) merges UNDER the explicit prior
(`entry.update(prior)`) and the human's `0` wins. Without the full-field emission, "set
to default" and "unset" were indistinguishable — the default field was omitted and the
re-pin silently refilled it from re-detection (fixed 2026-07-12; regression: an override
that keeps cp932/tab but resets `skip_rows` 1→0 survives a `source_dir` re-pin).
Detection-only sources keep their **minimal** entries (only non-default fields) — the RML
compiler emits only non-default annotations either way, so the extra IR fields never
change the compiled artifact and a clean design stays byte-identical. An empty override
leaves `effective == detected` — byte-identical to today. (Open question: a *force-default*
override — resetting a mis-detected source to clean CSV, i.e. *every* field default — is
honored at design time but does not survive the materialize re-pin: the `is_default` gate
skips an all-default entry before it can be pinned, even under `full_fields`, so
re-detection re-pins the non-default from the persisted source; a §9 "read as plain CSV"
sentinel would be a separate scope.)

An override is keyed by the source's canonical filename, so it must NOT outlive the
source context it was confirmed against — instrument feeds reuse fixed names
(`measurement.txt` / `data.txt`), and a stale override re-sent for a DIFFERENT file of
the same name would make the server read a clean CSV under the wrong dialect (silent
column corruption). Every wizard path that discards the source context — clearing the
workbench, switching the source kind, replacing the picked files, and seeding a redesign
of another dataset — therefore drops the whole dialect context (detected + overrides +
source-name list), returning the new source to auto-detection (fixed 2026-07-12).
Emptying the React state (not merely `sessionStorage.removeItem`) is what sticks: the
persistence effect otherwise re-snapshots the surviving overrides and writes the removed
key straight back.

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
    ast:sourcePreamble "keyvalue" ;   # only when header metadata is opted in
] .
```

Only non-default values are emitted. `rml_safety` is a closed checklist of the
predicates that could execute code or reach data (functions / query / source);
the `ast:` dialect predicates carry no execution semantics and pass it by
construction (an unknown `ast:` predicate is *not* rejected), so adding
`ast:sourcePreamble` needed no `rml_safety` change.

Annotation **values** are boundary-checked at every consumer
(`dialects_from_mapping` raises `DialectAnnotationError`): user-authored RML
(the raw-RML save path) reaches ingest unvetted, so a bytes codec, a
multi-char delimiter, a negative/non-integer `skip_rows` or a non-boolean
`collapse` surfaces as a structured 422 design issue (`validate_rml_design`
merges it into `issues`; the substrate maps it to `RmlValidationError`) —
never a 500.

### Runtime normalization (`asterism.dialect` + substrate)

A new preprocessing step, **first** in the existing work_dir chain (before
`tabularize_json_sources` / `sanitize_csv_sources` / `strip_bom_sources`):

- `dialects_from_mapping(graph)` reads (and boundary-checks) the
  annotations.
- **One tokenizer rule**, implemented identically on both sides
  (`dialect_rows` / `iter_rows`): decode with `dialect.encoding` (strict —
  a decode error is a real error, not something to paper over); `skip_rows`
  counts **physical lines**; after that a single-char delimiter reads csv
  **logical records** straight off the file handle (a quoted cell keeps its
  embedded newlines), every cell is **stripped**, blank records (all cells
  empty) are dropped, and `collapse` additionally drops empty cells;
  `whitespace` splits non-blank physical lines on runs of `[ \t]`
  (collapse implied). CRLF is handled by text-mode decoding.
- `normalize_source(src, dialect, dest)` streams those rows into a UTF-8
  comma CSV via `csv.writer` (embedded newlines are re-quoted correctly),
  renaming morph-kgc's reserved header columns (`subject`/`predicate` →
  `subject_`/`predicate_`, same rule as `asterism.tabularize.safe_col`) —
  the normalized copy bypasses the direct-CSV sanitizer, so the rename
  happens here.
- **Extension-based normalization**: a legacy-suffix source
  (`.txt`/`.dat`/`.asc`) is normalized even when NO dialect is annotated
  (default dialect = read as UTF-8 comma CSV) — morph-kgc cannot resolve
  those source types at all (`UnboundLocalError` deep in the engine), so
  the extension, not the annotation, decides. `.csv`/`.tsv` sources are
  normalized only under a non-default annotation (byte-identical default
  path preserved).
- Substrate rewrites `rml:source` to the normalized `.csv` work file and
  **strips the annotations** before handing the mapping to morph-kgc, which
  therefore sees exactly what it sees today.
- `read_csv_header` (design validation / design_loop) accepts an optional
  dialect so closed-set column validation reads the same rows morph-kgc
  will — including the reserved-column rename, and including legacy-suffix
  files read under the default rules. An undecodable file is "cannot
  check" (`[]`), never a crash; the loud, structured error belongs to the
  ingest boundary. Column validation covers `.txt`/`.dat`/`.asc` sources
  (`_TABULAR_SUFFIXES`), dialected or not, and step0's inspector reads
  legacy-suffix files through the same default rules so the design sees
  the exact columns ingest will produce.

### Header metadata (preamble broadcast)

Instrument files carry valuable metadata *above* the header row — a sample
name, an ICDD card's Name / Space Group / Cell. Dropping it discards facts the
scientist wants. `preamble` turns that preamble into columns **broadcast** onto
every body row (denormalize), so one source stays one work CSV and Morph-KGC is
still untouched — it just reads a wider flat table:

- **Parsing** (`read_preamble(lines, mode, delimiter=…)`, both twins, identical):
  `lines` makes each non-blank preamble line a `preamble_{i+1}` column; `keyvalue`
  parses `Key: value` in a fixed deterministic priority — a section heading
  (`^\s*-{3,}`) is skipped, a `key: value` line splits on its **first colon
  only** (a second colon stays in the value, a multi-value cell like the 6-number
  `Cell` is kept whole for Tier-0 to split later), and any other non-blank line
  **without a colon** is a **continuation** appended to the previous key's value
  (a colon-free wrapped line rejoins its field). A duplicate key is suffixed
  `key_2`/`key_3`. MVP is ASCII-colon only (measurement files are `lines`; cards
  use ASCII colons). **Deterministic cost:** a wrapped line that itself contains a
  colon (e.g. the continuation of an ICDD `Comment` note) cannot be told from a
  real new field, so it is parsed as its own `key: value` pair — the text is not
  lost but lands in an extra, oddly-named column the designer does not map (see
  Limitations).
- **`keyvalue_cells` (2026-07-23, the ZEM meta line)**: some instrument exports
  pack the metadata into ONE preamble line of *delimiter-separated cells*, each
  holding `key=value` — e.g. ZEM3's
  `Al3V-SPS-2 <TAB> T.C.type=K <TAB> Distance=620.000000E-3 <TAB> …`. The line
  mode would collapse that into a single giant column, so `keyvalue_cells`
  tokenizes each preamble line under the **body's `delimiter`**
  (quote-aware; `whitespace` run-split), always drops empty cells, and splits
  each cell on its **first `=` only** (stripped key/value — lossless). A cell
  without `=` (or with an empty left side) is a bare value — typically the
  sample name leading the line — named `preamble_{n}` by order of appearance.
  Duplicate keys suffix like the line mode. `:` cells are deliberately NOT
  split (a colon is too common inside values — timestamps, ratios — to be a
  safe cell-level separator; `=` almost never appears in instrument values).
- **Broadcast** (`iter_rows` / `dialect_rows`): the metadata columns are appended
  **after** the body columns (body positions/names never move — this is what
  makes the `drop` path byte-identical), each body row is fitted to the body
  header width so the constant metadata lands in the right columns, and every
  data row carries the constant preamble values. `resolve_header(body, meta)`
  (both twins) `safe_column`s each meta name and suffixes a collision **on the
  meta side only** (a meta `d` next to a body `d` → `d_2`).
- **Design experience**: a scientist writes two TriplesMaps over the one wide
  source — a Card map keyed on a preamble column (e.g. `template ".../card/{No}"`,
  constant across all rows → **one** entity after store set-semantics dedup) and
  a Peak map keyed on a body column, linked back with `object_template
  ".../card/{No}"`. The shared column means **no `rr:joinCondition`** — the
  existing IR/compiler already expresses this. Denormalization is a bounded,
  temporary CSV cost; the graph holds one deduped Card.
- **Identify-and-advise, never auto-adopt**: `detect_dialect` always returns
  `preamble="drop"` — broadcasting changes the column set (a semantic change), so
  it must be opt-in to preserve "default emits nothing". The inspector classifies
  a dropped preamble block (`detect_preamble_form` →
  `keyvalue`/`keyvalue_cells`/`lines`; the cell form wins only when the block
  actually splits into more cells than lines under the body delimiter AND a
  majority of those cells are `key=value` — a single-cell-per-line card with a
  stray `=` falls through to the line classification) and the
  inspect Markdown adds one advisory line + a paste-ready `dialects:` snippet
  **only when a preamble was detected**; a clean CSV's Markdown stays
  byte-identical. `/api/inspect` carries the classification as `preamble_hint`
  in the `X-Asterism-Dialects` header entry (additive), so the wizard's "keep
  the metadata" answer pins the DETECTED shape instead of hardcoding one. The wizard's read-settings panel surfaces a `preamble` selector
  for any source with a preamble block, so opt-in is one click. A human override
  travels as a full-field `dialects:` entry (`preamble` included, like the other
  FIX2 fields) and survives the materialize re-pin because re-detection always
  yields `drop` and `entry.update(prior)` keeps the explicit choice.
- **Travel + boundary check**: the `preamble` field pins into the IR
  `dialects:` section (linted against the closed set
  `{drop, keyvalue, lines, keyvalue_cells}`,
  and flagged when set with `skip_rows: 0` — nothing to read), compiles to
  `ast:sourcePreamble` (emitted only when non-`drop`), and is boundary-checked in
  `dialects_from_mapping` (an out-of-set value → `DialectAnnotationError` → 422)
  so a hand-authored raw-RML mapping cannot smuggle a bad mode in.
- **Ragged / over-split bodies**: the body row is fitted to the body header
  width, so a short row is padded and an over-split row (e.g. a whitespace `(hkl)`
  cell with internal spaces) is truncated to keep the metadata aligned — the same
  forgiveness `csv.DictReader` already extends, applied so the physical CSV stays
  rectangular for the appended columns.
- **Append**: unchanged — an append batch of a dialected source is still refused,
  and a broadcast source is simply "more dialected", so no new work and no
  regression (`strip_preamble_and_header` stays `drop`-oriented and untouched).

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

- **Append** (follow-up ①, plan B — native dialect accumulation): incremental
  append of a dialected source is supported. The persisted copy grows **in its
  own dialect** — the RML/IR is never rewritten, so no un-pin and no double
  normalization. The first batch is written as-is (its single preamble+header
  stays); every later batch has its repeated `skip_rows + 1` preamble/header
  physical lines sliced off (`asterism.dialect.strip_preamble_and_header`, a
  byte-level, decode-free `\n` count) before its native data bytes are
  concatenated. The accumulated file is therefore "preamble once, header once,
  then every data row", and a snapshot re-ingest normalizes it **exactly once**
  through the unchanged pinned dialect (`skip_rows` skips the single surviving
  preamble). The batch materialize is unchanged (the raw device export flows
  through `normalize_dialect_sources` as always). A multi-source mapping's
  uncovered dialected source gets a native preamble+header stand-in (0 data rows
  after normalization). Idempotency is unaffected (the `.applied_batches/<id>`
  marker folds a batch once; the physical-line slice is deterministic, so a
  replay is byte-identical). Fail-closed: if the pinned annotations cannot be
  read the append is refused (snapshot re-ingest still works) — the offset is
  unknown, so a batch cannot be safely accumulated. *Why plan B over
  normalize-at-accumulation + un-pin: that path doubles the dialect state (a
  retained IR/JSON dialect for future batches vs an un-pinned RML for the
  accumulated source) and mutates a human-vetted artifact on first append; plan
  B keeps every artifact immutable and the two design/raw-RML paths uniform.*
  A **non-dialected** (default-dialect) tabular source accumulates by the same
  grow-not-overwrite rule: the second batch's data rows are byte-concatenated after
  the repeated header is dropped. This covers EVERY suffix the widened entrance
  accepts read under the default rules — `.csv/.tsv/.txt/.dat/.asc`
  (`_APPENDABLE_TABULAR`), not only `.csv`. Before 2026-07-12 the append branch was
  gated on `.csv` alone, so a clean `.txt/.tsv/.dat/.asc` second batch fell through
  to a whole-file overwrite and lost every earlier append — a snapshot re-ingest then
  diverged from the live graph (fixed; regression test pins the two-batch growth for
  each suffix).
- **ICDD card metadata**: the `Key: value` preamble of a reference card is
  *dropped by default*, but a scientist can opt in to ingesting it as columns
  (`preamble: keyvalue`, "Header metadata" below) so the card's Name / Cell /
  Space Group travel alongside the d-I table. The d-I table itself ingests via
  `delimiter: whitespace`. Deeper semantic card parsing (typed cell values,
  crystallographic modelling) remains a separate, document-layer-shaped problem.
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
