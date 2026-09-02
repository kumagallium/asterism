"""Automatic propose→validate→refine self-correction loop (TODO ④ propose quality).

Why this exists
---------------
``propose`` emits a whole schema (incl. the §9 declarative RML mapping) in ONE LLM
call. Weak models repeatedly make design mistakes the deterministic validators catch
but the model does not fix on its own: hallucinated columns, wrong FnO parameter IRIs
(``fn:p_field1`` vs ``fn:p_field``), invalid Turtle, non-Tier-0 functions, invented
source filenames. The user used to click "AI に修正を依頼" by hand (the UI's
``composeFixComment`` renders the failures into a refine comment and re-runs). This
module automates that loop server-side.

Design (ADR ``propose-self-correction-loop.md``)
------------------------------------------------
* Round 0 = ``propose_schema`` (once). Rounds 1..N = ``refine_schema`` REUSED as-is —
  no new multi-turn LLM entrypoint (``complete()`` is single-turn by contract), and the
  cacheable SYSTEM_PROMPT stays byte-stable (per-round feedback rides the USER message).
* Each round: ``materialize_schema(write=False)`` to extract the §9 design; if it is
  absent emit "mapping spec missing" and stop collecting (nothing else is checkable).
  NEW proposals carry a §9 **mapping spec** (Mapping IR, ADR mapping-ir-compiler.md):
  validation is front-loaded at the IR level — parse → real files/columns/function-menu
  checks with difflib "Did you mean X?" → deterministic compile — so the feedback is
  always at a granularity a weak model can act on (never a Turtle parse error), and the
  unchanged RML gates run on the compiled output as backstop. LEGACY proposals (raw §9
  RML) keep the original pipeline: ``substitute_run_id`` then ``assert_rml_safe`` FIRST
  (the ONLY layer that flags invalid Turtle — ``validate_rml_design`` silently returns
  [] on unparseable Turtle), then ``validate_rml_design`` against the REAL uploaded
  source dir. FINALLY the **bundle trap validator** (T1-T10) runs on the SAME
  materialized bundle — the second gate ``/api/materialize`` runs and the kantan
  wizard stops on. Until 2026-08-16 the loop did not run it, so a trap failure
  surfaced only AFTER "converged" and the human's "AI に直してもらう" click WAS the
  missing round (live: ~5 clicks on a 70-line XRD file). Trap issues also force the
  whole-document refine — surgical §9 splicing cannot reach the §7 MIE or the
  diagram doc, where the traps that actually fire live.
* Feedback = ``composeFixComment``'s server twin PLUS a deterministic **Tier-0 oracle**
  appendix (exact filenames, BOM-safe real columns, every REGISTRY function with its
  exact parameter local-names) — the closed menu that stops a weak model from
  re-hallucinating. It is passed to refine as a SINGLE joined comment (the proven manual
  shape), in the USER message only (cache-safe).
* Stop conditions (priority): converged (zero issues); cancelled (``should_cancel`` /
  ``LLMCancelledError`` → raise, never swallowed into env-bail — the job runner turns
  it into the cancelled state); env-bail (any other LLM exception +
  registry/rdflib import failure → keep best, do NOT iterate); refine truncated
  (``complete`` False → keep the prior complete ``effective_schema_md``, stop); no-progress
  (normalized issue key-set unchanged or seen before → stop); ``max_rounds`` cap
  (api default 5 since the traps joined the oracle; 0 disables the loop = plain
  propose). Always carry ``effective_schema_md``
  (never ``refined_md``); snapshot the SMALLEST-issue schema as ``best`` and RETURN it
  with ITS remaining issues (not the last round's).

Trust boundary (inviolable): the loop pushes the LLM back INTO the closed Tier-0 set; it
NEVER widens it. Its output still passes the hard 422 ingest gate. Convergence means
"passed the static gates", strictly weaker than "ingests cleanly" — the 422 gate is the
real gate. Known blind spots (documented in the ADR + surfaced honestly in the result):
JSON/XML sources get no column-level feedback (tabular-only validator); un-iri_safe IRIs
from existing columns are caught by no static validator; a cornered weak model can erase
mappings to reach zero issues (surfaced as ``coverage_dropped``, not blocked).
"""
from __future__ import annotations

import contextlib
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from asterism import substrate
from asterism.rml_validate import read_csv_header
from asterism_step0.dialect import (
    SourceDialect,
    apply_detected_dialects,
    detect_dialect,
    is_default,
)
from asterism_step0.inspect import (
    _dialect_rows,
    _stream_rows,
    inspect_source_set,
    numeric_column_types,
    render_markdown,
)
from asterism_step0.instance_iri import placeholder_prefix_issue
from asterism_step0.llm import LLMCancelledError, LLMTruncatedError
from asterism_step0.mapping_ir import (
    MappingIRParseError,
    catalog_from_registry,
    parse_mapping_ir,
    validate_mapping_ir,
)
from asterism_step0.mapping_ir_schema import mapping_ir_json_schema
from asterism_step0.materialize import materialize_schema
from asterism_step0.propose import propose_schema
from asterism_step0.refine import refine_schema
from asterism_step0.rml_compile import RmlCompileError, compile_mapping_ir
from asterism_step0.skeleton_annotate import annotate_skeleton
from asterism_step0.spec_repair import (
    SPEC_REPAIR_SYSTEM_PROMPT,
    build_spec_repair_user,
    parse_spec_json,
    replace_mapping_spec_block,
)
from asterism_step0.spec_yaml import load_spec_yaml
from asterism_step0.staged_propose import (
    apply_column_decisions,
    apply_column_meanings,
    apply_data_facts,
    propose_from_skeleton,
    take_in_columns,
)
from asterism_step0.validate import SchemaBundle, validate_schema

# Column-checkable delimited files: classic CSV/TSV plus the legacy instrument
# export suffixes (ADR source-dialect.md) whose read rules a pinned dialect carries.
_TABULAR_SUFFIXES = frozenset({".csv", ".tsv", ".txt", ".dat", ".asc"})

# The refine comment intro (server twin of the UI's workbench:fix.commentIntro).
_FIX_INTRO = (
    "Fix ONLY the following design issues; keep everything else unchanged. Do not "
    "introduce new columns, functions, or source files — correct the §9 mapping "
    "spec (and any other named artifact) to match the real data and the vetted "
    "Tier-0 functions listed at the end."
)


# ---------------------------------------------------------------------------
# Issue model + classification (pure, LLM-free)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    """One machine-detected design problem.

    ``category``/``subject`` form a canonical key used for BOTH de-duplication and
    round-to-round oscillation detection — keying on the raw message would be brittle
    (a difflib "Did you mean X?" suffix or a run-id substitution changes one character
    and defeats set-equality). ``message`` is the full human string fed to the LLM.
    """

    category: str
    subject: str
    message: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.category, self.subject.strip().lower())


_RE_SOURCE_FILE = re.compile(r"source file '([^']+)'")
_RE_COLUMN = re.compile(r"column '([^']+)'")
_RE_FN_EXTRA = re.compile(r"^(\S+) does not accept parameter '([^']+)'")
_RE_FN_MISSING = re.compile(r"^(\S+) is missing required parameter '([^']+)'")
_RE_QUOTED = re.compile(r"'([^']+)'")
# Mapping-IR message shapes (asterism_step0.mapping_ir / rml_compile). Keys are
# predicate/term-based, NOT property-index-based — a model reshuffling rows must
# not defeat the no-progress (oscillation) detection.
_RE_IR_FUNCTION = re.compile(r"function '([^']+)' is not in the vetted")
_RE_IR_ARG_EXTRA = re.compile(r"(\w+) does not take a constant arg '([^']+)'")
_RE_IR_ARG_MISSING = re.compile(r"(\w+) requires the constant arg '([^']+)'")
_RE_IR_UNKNOWN_FIELD = re.compile(r"unknown field '([^']+)'")
_RE_IR_FN_PLUS_TEMPLATE = re.compile(r"\(([^)]+)\): 'function' cannot be combined")
_RE_IR_TRANSFORM_MISUSE = re.compile(r"\(([^)]+)\): transform cannot contain")
_RE_IR_FN_NEEDS_COLUMN = re.compile(r"\(([^)]+)\)\.function requires 'column'")
_RE_IR_CARDINALITY = re.compile(r"'([^']+)' carries a cardinality marker")
_RE_IR_TYPE_CAST = re.compile(r"'([^']+)' is a type, not a Tier-0 function")
# Design advisory: a per-row map with no value of its own (asterism.rml_validate
# `_empty_shell_advisories`). Keyed by map so the column list in the message can
# change round to round without reading as a new issue.
_RE_EMPTY_SHELL = re.compile(r"^map '([^']+)' mints one entity per row")

# Message stems that mean the validator ENVIRONMENT is broken (missing rdflib /
# unimportable Tier-0 registry), NOT that the LLM made a mistake. The loop bails on
# these — refining cannot fix an env failure.
_ENV_STEMS = ("rdflib is required", "is not importable", "Tier 0 registry")


def _is_env_message(msg: str) -> bool:
    return any(stem in msg for stem in _ENV_STEMS)


def classify(message: str) -> Issue:
    """Map a validator message string to a canonically-keyed :class:`Issue`.

    Handles every shape ``validate_rml_design`` and ``assert_rml_safe`` emit; an
    unrecognized shape falls back to a whole-message key so it is NEVER silently
    un-keyed (which would defeat dedup + oscillation detection).
    """
    m = message.strip()
    if (mm := _RE_SOURCE_FILE.search(m)):
        return Issue("source", mm.group(1), m)
    if (mm := _RE_COLUMN.search(m)):
        return Issue("column", mm.group(1), m)
    if (mm := _RE_FN_EXTRA.match(m)):
        return Issue("function", f"{mm.group(1)}/+{mm.group(2)}", m)
    if (mm := _RE_FN_MISSING.match(m)):
        return Issue("function", f"{mm.group(1)}/-{mm.group(2)}", m)
    # Mapping-IR shapes (checked before the generic fallbacks).
    if (mm := _RE_IR_FUNCTION.search(m)):
        return Issue("function", mm.group(1), m)
    if (mm := _RE_IR_ARG_EXTRA.search(m)):
        return Issue("function", f"{mm.group(1)}/+{mm.group(2)}", m)
    if (mm := _RE_IR_ARG_MISSING.search(m)):
        return Issue("function", f"{mm.group(1)}/-{mm.group(2)}", m)
    if (mm := _RE_IR_UNKNOWN_FIELD.search(m)):
        return Issue("structural", mm.group(1), m)
    if (mm := _RE_IR_FN_PLUS_TEMPLATE.search(m)):
        return Issue("structural", f"fn+template/{mm.group(1)}", m)
    if (mm := _RE_IR_TRANSFORM_MISUSE.search(m)):
        return Issue("structural", f"transform-misuse/{mm.group(1)}", m)
    if (mm := _RE_IR_FN_NEEDS_COLUMN.search(m)):
        return Issue("function", f"{mm.group(1)}/-column", m)
    if (mm := _RE_IR_CARDINALITY.search(m)):
        return Issue("structural", f"cardinality/{mm.group(1)}", m)
    if (mm := _RE_IR_TYPE_CAST.search(m)):
        return Issue("function", f"typecast/{mm.group(1)}", m)
    if (mm := _RE_EMPTY_SHELL.match(m)):
        return Issue("structural", f"empty-shell/{mm.group(1)}", m)
    # assert_rml_safe shapes
    if "outside the closed Tier 0 set" in m:
        return Issue("function-set", _fn_set_subject(m), m)
    if "SQL" in m or "query/table source" in m:
        return Issue("safety", "sql-source", m)
    if "not parseable Turtle" in m or "not valid Turtle" in m:
        return Issue("turtle", "turtle", m)
    if "rml:source" in m:
        q = _RE_QUOTED.search(m)
        return Issue("source", q.group(1) if q else "source", m)
    return Issue("other", m.lower()[:120], m)


def _fn_set_subject(msg: str) -> str:
    """The offending IRIs in a 'functions outside the closed Tier 0 set: …' message."""
    _, _, tail = msg.partition("set:")
    return tail.strip() or "non-tier0"


# ---------------------------------------------------------------------------
# Source dialects (ADR source-dialect.md — design-side wiring)
# ---------------------------------------------------------------------------


def _read_header(path: Path, dialect: Any | None) -> list[str]:
    """``read_csv_header`` through a pinned dialect; None / all-default reads
    exactly as today (byte-identical current behavior — the is_default gate).
    A file the read rules cannot decode (e.g. a CP932 upload no map declares,
    so no dialect was pinned for it) is "cannot check" — never a crash that
    kills the whole design job."""
    try:
        if dialect is not None and not is_default(dialect):
            return read_csv_header(path, dialect=dialect)
        return read_csv_header(path)
    except UnicodeDecodeError:
        return []


def _detect_source_dialects(paths: list[Path]) -> dict[str, Any]:
    """Deterministically sniff each tabular upload's dialect (encoding / delimiter /
    header offset) ONCE per design run. Only non-default dialects are kept, so a
    clean UTF-8 comma CSV set yields ``{}`` and nothing downstream changes."""
    detected: dict[str, Any] = {}
    for p in paths:
        if p.suffix.lower() not in _TABULAR_SUFFIXES:
            continue
        try:
            dialect = detect_dialect(p)
        except OSError:
            continue
        if not is_default(dialect):
            detected[p.name] = dialect
    return detected


def merge_dialect_overrides(
    detected: Mapping[str, Any],
    overrides: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """The read rules actually in force: the human's corrections laid over detection
    FIELD BY FIELD (never source by source).

    An override carries only the fields the person wrote (:func:`main._parse_dialect_
    overrides`), so everything they did not touch keeps the detected value. Replacing
    the whole source entry instead — the shape this used to have — silently reset every
    untouched field to its class default: a correction to the delimiter alone threw away
    a detected ``cp932``, the design pinned ``utf-8-sig``, and ingest then refused the
    file it had read correctly all the way through the wizard (live 2026-08-26).

    A source detection said nothing about (a clean CSV) starts from the plain defaults,
    so an override on it still applies exactly as written.
    """
    out: dict[str, Any] = {str(k): v for k, v in detected.items()}
    for name, fields in (overrides or {}).items():
        if not fields:
            continue
        key = str(name)
        base = out.get(key) or SourceDialect()
        out[key] = replace(base, **dict(fields))
    return out


def _column_datatypes(
    skeleton: Mapping[str, Any], paths: list[Path], dialects: Mapping[str, Any]
) -> dict[str, dict[str, str]]:
    """Per map, the columns whose values are numbers IN THE DATA -> xsd type.

    Fed to per-map generation so a measurement lands as a typed literal. Without
    it SPARQL compares numbers as strings and answers confidently wrong ("the
    highest intensity is 9.4, not 100.0"). Scans every row rather than trusting
    the inspector's sampled `inferred_type`: one stray non-numeric cell would
    make the stamped datatype a lie.

    The columns considered are the ones the MATERIALIZED rows actually have, not
    ``ins.columns``: on a legacy instrument export the key/value preamble is
    broadcast onto every row (ADR source-dialect.md), so ``Volume`` /
    ``RIR(I/Ic)`` / ``Dcalc`` exist in the data but not in the inspector's body
    column list — and were silently never typed (live 2026-08-18: the review
    screen kept advising "column 'Volume' holds numbers but is mapped as an
    untyped literal" with nothing able to act on it). The dialect-aware reader
    is likewise the only one that sees them, so a non-``csv`` source kind no
    longer skips the check outright.

    Best-effort — any failure yields ``{}`` and generation proceeds as before.
    """
    try:
        inspections, _fks = inspect_source_set(paths, dialects=dialects)
    except Exception:
        return {}
    by_source: dict[str, dict[str, str]] = {}
    for path, ins in zip(paths, inspections, strict=False):
        if path.suffix.lower() not in _TABULAR_SUFFIXES:
            continue
        try:
            rows = _dialect_rows(path, ins.dialect) if ins.dialect else list(_stream_rows(path))
        except Exception:
            continue
        if not rows:
            continue
        by_source[path.name] = numeric_column_types(rows, list(rows[0].keys()))
    out: dict[str, dict[str, str]] = {}
    for map_entry in skeleton.get("maps") or []:
        if not isinstance(map_entry, Mapping):
            continue
        types = by_source.get(Path(str(map_entry.get("source") or "")).name)
        if types:
            out[str(map_entry.get("name"))] = types
    return out


def _json_backed_header_for(base: Path, csv_name: str) -> list[str]:
    """表化名 ``<stem>.csv`` の実列を、sibling の JSON から導出する。

    メニューの列挙と IR の環境検証の両方が同じ導出を使う — 名前だけ知らせて
    列一覧が空だと、モデルが camelCase の幻の列名を発明し、設計時は素通り・
    取り込みで爆発した (実測 2026-09-02、元素表 JSON)。
    """
    for sfx in (".json", ".geojson"):
        json_src = base / (Path(csv_name).stem + sfx)
        if json_src.exists():
            import tempfile

            from asterism.tabularize import tabularize_json_to_csv

            try:
                with tempfile.TemporaryDirectory() as td:
                    return tabularize_json_to_csv(json_src, Path(td) / csv_name)
            except Exception:
                return []
    return []


def _column_owners(
    skeleton: Mapping[str, Any], paths: list[Path], dialects: Mapping[str, Any]
) -> dict[str, dict[str, str]]:
    """Per map, which of its columns another map owns (ADR column-ownership G6).

    Reuses the skeleton gate's own annotation pass, so the constraint fed to
    generation is EXACTLY the verdict the human just approved on screen —
    "these 17 columns belong to sample" becomes "do not write them on peak".
    Best-effort: any failure yields the deterministic overlay alone and
    generation proceeds as before.

    On top of the annotation verdict, two facts the skeleton itself states are
    overlaid deterministically (same-source maps only):

    - a column a map DECLARES in ``owns`` belongs to that map;
    - a map's subject-template key columns belong to that map (first declarer
      wins — the skeleton orders parents first, so ``No`` goes to the card and
      ``(hkl)`` to the record).

    Without the key rule, key columns belonged to NOBODY, so the generation
    model could transcribe them as plain properties of any other map — observed
    live 2026-09-01 (XRD reference card: the model wrote ``(hkl)``/``No`` onto
    the SpaceGroup and CrystalSystem catalogs but not ChemicalFormula, i.e.
    nondeterministically), and every guard downstream was blind to it because
    ``drop_borrowed_properties`` only strips columns listed here. Joins are
    untouched: the guard and the drop both leave ``object_template`` (the link
    form) alone — G6 keys stay usable as keys everywhere.
    """
    owners: dict[str, dict[str, str]] = {}
    try:
        annotations = annotate_skeleton(skeleton, paths, dialects=dialects)
    except Exception:  # evidence is advisory; never block generation on it
        annotations = {}
    for name, ann in (annotations.get("maps") or {}).items():
        borrowed = ann.get("borrowed_columns") or []
        if borrowed:
            owners[str(name)] = {b["column"]: b["owner_map"] for b in borrowed}

    maps = [m for m in (skeleton.get("maps") or []) if isinstance(m, Mapping)]
    declared: dict[tuple[str, str], str] = {}  # (source, column) -> owner map
    for m in maps:
        src = str(m.get("source") or "")
        mname = str(m.get("name") or "")
        for col in m.get("owns") or []:
            declared.setdefault((src, str(col)), mname)
    for m in maps:  # skeleton order = parents first, so the card claims No
        src = str(m.get("source") or "")
        mname = str(m.get("name") or "")
        template = str((m.get("subject") or {}).get("template") or "")
        for match in re.finditer(r"\{([^{}]+)\}", template):
            declared.setdefault((src, match.group(1)), mname)
    for m in maps:
        src = str(m.get("source") or "")
        mname = str(m.get("name") or "")
        own_keys = {
            match.group(1)
            for match in re.finditer(
                r"\{([^{}]+)\}", str((m.get("subject") or {}).get("template") or "")
            )
        }
        for (osrc, col), owner in declared.items():
            # A key that sits in THIS map's own template is its identity (the
            # nested join), not a borrowed value — the peak keeps its No.
            if osrc == src and owner != mname and col not in own_keys:
                owners.setdefault(mname, {}).setdefault(col, owner)
    return owners


def _overlay_detected_dialects(
    schema_md: str,
    detected: Mapping[str, Any],
    override_names: frozenset[str] | set[str] | None = None,
) -> str:
    """Pin the detected dialects into the schema's §9 mapping spec (``dialects:``
    section) so they travel design → artifact → ingest.

    Deterministic and idempotent — applied after round-0 and after every refine
    round (a repair could drop the section), BEFORE validation/compile, so the
    closed-set checks and the compiled RML annotations always see the pinned
    dialects. ``detected`` here is the EFFECTIVE map (detection with the human's
    "read settings" merged in field by field), which makes it authoritative: it
    overwrites whatever the LLM wrote in ``dialects:`` rather than deferring to it.
    A round that revises how a file is READ is a round rewriting evidence — and the
    cost is not a worse design but an unopenable one (a detected ``cp932`` rewritten
    to ``utf-8-sig`` reaches the person as "save it again as CSV UTF-8", live
    2026-08-26). ``override_names`` are the human's per-source "read settings"
    overrides — those entries are pinned with ALL four fields (defaults included) so
    an explicit default (``skip_rows`` corrected 1→0) survives the materialize re-pin
    (FIX2); detection-only sources stay minimal. No-op when nothing non-default was
    detected, the schema has no §9 spec (legacy raw-RML proposals), or the block
    cannot be spliced — the design is then byte-untouched.
    """
    # dialect は表形式ソース専用 [mapping_ir の検証]。JSON の読み設定
    # [record_path 等] は staging meta 経由で別に運ばれる — ここで押し込むと
    # §9 が検証で落ち続ける [実測 2026-09-01]。
    detected = {
        k: v
        for k, v in (detected or {}).items()
        if Path(k).suffix.lower() not in (".json", ".geojson")
    }
    # detected が空でも進む: authoritative の overlay は「壊れた dialects を消す」
    # 掃除も担う (LLM が書いたフラット形などをコンパイラの前に除去 — 実測
    # 2026-09-01: 元素表 JSON がこれで空転)。掃除対象が無ければ byte 同一で返る。
    ir_yaml, _ = _extract_design(schema_md)
    if not ir_yaml or not ir_yaml.strip():
        return schema_md
    new_yaml = apply_detected_dialects(ir_yaml, detected, override_names, authoritative=True)
    if new_yaml == ir_yaml:
        return schema_md
    try:
        return replace_mapping_spec_block(schema_md, new_yaml)
    except ValueError:
        return schema_md


# ---------------------------------------------------------------------------
# The Tier-0 oracle (deterministic closed menu injected into the refine prompt)
# ---------------------------------------------------------------------------


def build_oracle(
    source_dir: Path | str,
    csv_paths: list[Path | str],
    *,
    dialects: Mapping[str, Any] | None = None,
) -> str:
    """A deterministic 'closed menu' appendix for the refine USER message.

    Enumerates (1) the exact legal source filenames, (2) each tabular file's real
    columns via the SAME BOM-safe header reader the validator uses (so the menu can't
    teach a column name the validator would then reject), and (3) every vetted Tier-0
    function with its column-input count and constant-arg names (the Mapping IR
    surface — FnO parameter IRIs are the compiler's business now). This turns each
    refine round from "you were wrong, try again" (which weak models re-break) into
    "pick only from this menu" — the single strongest lever for weak-model
    convergence. ``dialects`` (detected per file, ADR source-dialect.md) makes the
    column read match what ingest will normalize to. Pure + LLM-free.
    """
    base = Path(source_dir)
    # A JSON source is REFERENCED by its tabularized ``<stem>.csv`` name (the
    # inspection prompt and the compiler both enforce it) — listing the physical
    # ``.json`` here made the menu contradict the compiler and the repair loop
    # could not converge (live 2026-09-01, periodic-table JSON, 8 rounds).
    def _referable(p: Path | str) -> str:
        q = Path(p)
        return f"{q.stem}.csv" if q.suffix.lower() in (".json", ".geojson") else q.name

    names = sorted({_referable(p) for p in csv_paths})

    def _json_backed_header(csv_name: str) -> list[str]:
        return _json_backed_header_for(base, csv_name)
    lines: list[str] = [
        "── Reference (closed menu — use ONLY these names; do NOT invent or rename) ──",
        "Source files (use the filename EXACTLY as written):",
    ]
    for name in names:
        p = base / name
        cols = (
            _read_header(p, (dialects or {}).get(name))
            if p.suffix.lower() in _TABULAR_SUFFIXES and p.exists()
            else _json_backed_header(name)
            if p.suffix.lower() == ".csv"
            else []
        )
        if cols:
            lines.append(f"  • {name} — columns: {', '.join(cols)}")
        else:
            lines.append(f"  • {name}")
    lines.append(
        "Vetted Tier-0 functions for the §9 mapping spec (bare names in "
        "'function:'/'transform:'; constants by name in 'args:') — use no other "
        "function and no other arg name:"
    )
    for fn in catalog_from_registry():
        n_cols = len(fn.column_params)
        parts = [f"{n_cols} column input" + ("s" if n_cols != 1 else "")]
        if fn.constant_params:
            parts.append("args: " + ", ".join(sorted(fn.constant_params)))
        if fn.multivalued:
            parts.append("multi-valued (one triple per element)")
        lines.append(f"  • {fn.name} — {'; '.join(parts)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Feedback rendering (pure)
# ---------------------------------------------------------------------------


def render_feedback(issues: list[Issue], oracle: str) -> list[str]:
    """Turn machine issues into the ``comments`` list ``refine_schema`` consumes.

    Mirrors the UI's ``composeFixComment``: a bulleted list under a fix intro, PLUS the
    Tier-0 oracle appendix. Returned as a SINGLE joined comment string (matching the
    battle-tested manual shape — a weak model follows one cohesive block better than
    many separately-numbered directives). ``issues`` must be non-empty (the caller only
    calls this when there are issues, so refine never sees an empty comment list).
    """
    bullets = "\n".join(f"- {iss.message}" for iss in issues)
    body = f"{_FIX_INTRO}\n{bullets}"
    if oracle:
        body = f"{body}\n\n{oracle}"
    return [body]


def _dedup(issues: list[Issue]) -> list[Issue]:
    """Collapse issues sharing a canonical key (the same T9 mole can be reported by up
    to three layers). Order-stable: first occurrence wins."""
    seen: set[tuple[str, str]] = set()
    out: list[Issue] = []
    for iss in issues:
        if iss.key in seen:
            continue
        seen.add(iss.key)
        out.append(iss)
    return out


# ---------------------------------------------------------------------------
# Issue collection (deterministic; raises _LoopEnvError on validator env failure)
# ---------------------------------------------------------------------------


class _LoopEnvError(Exception):
    """A non-loopable environment failure (missing rdflib / unimportable registry).

    The loop keeps the last-good schema and stops rather than iterating — refining
    cannot fix a broken validator environment.
    """


def collect_issues(
    ir_yaml: str | None, rml_ttl: str | None, source_dir: Path
) -> list[Issue]:
    """Deterministic, LLM-free machine feedback for ONE candidate design.

    NEW proposals carry a §9 mapping spec (``ir_yaml``, ADR mapping-ir-compiler):
    validation is FRONT-LOADED at the IR level — parse (structural issues in the
    IR's vocabulary), environment checks (real files/columns with did-you-mean,
    the closed function menu), then deterministic compilation, and finally the
    unchanged RML gates on the compiled output as defense in depth. The feedback
    a weak model receives is therefore always at a granularity it can act on
    ("column 'titel' is not in papers.csv. Did you mean: title?"), never a
    Turtle parse error.

    LEGACY proposals (raw ``rml_ttl``, no spec) keep the original pipeline:
    ``assert_rml_safe`` BEFORE ``validate_rml_design`` because it is the only
    layer that flags invalid Turtle (the design validator silently returns []
    on unparseable Turtle — the "convergence hole").

    Raises :class:`_LoopEnvError` when the validators themselves are unavailable.
    """
    if ir_yaml and ir_yaml.strip():
        return _collect_ir_issues(ir_yaml, source_dir)
    if not rml_ttl or not rml_ttl.strip():
        return [
            Issue(
                "structural",
                "mapping-spec",
                "The §9 mapping spec is missing or empty. Emit a complete ```yaml "
                "fenced block under a 'Declarative mapping spec' heading (version/"
                "prefixes/maps).",
            )
        ]
    return _collect_rml_issues(rml_ttl, source_dir)


def _collect_ir_issues(ir_yaml: str, source_dir: Path) -> list[Issue]:
    """The front-loaded IR pipeline: parse → environment validation → compile →
    RML gates (backstop)."""
    try:
        ir = parse_mapping_ir(ir_yaml)
    except MappingIRParseError as exc:
        return _dedup([classify(m) for m in exc.issues])
    except ImportError as exc:  # PyYAML missing — an environment failure
        raise _LoopEnvError(str(exc)) from exc

    try:
        catalog = catalog_from_registry()
    except ImportError as exc:  # Tier-0 registry not importable
        raise _LoopEnvError(str(exc)) from exc
    try:
        files = sorted(p.name for p in Path(source_dir).iterdir() if p.is_file())
    except OSError:
        files = []
    # Closed-set column validation reads each header through the spec's pinned
    # dialect (the IR ``dialects:`` section — detected values were overlaid before
    # this runs, explicit ones win), so it sees the same rows Morph-KGC will.
    ir_dialects: Mapping[str, Any] = getattr(ir, "dialects", None) or {}
    headers = {
        f: (_read_header(Path(source_dir) / f, ir_dialects.get(f)) or None)
        for f in files
        if Path(f).suffix.lower() in _TABULAR_SUFFIXES
    }
    # JSON は取り込みで表化されるため、設計は <stem>.csv 名で参照する（規約）。
    # 別名の実在 + 表化ヘッダをここでも提示しないと、この存在チェックだけが
    # 規約を知らず、決定論組みの §9 を差し戻して LLM refine を誘発する
    # （実測 2026-09-02: 元素表 JSON — refine が dialects の junk まで書いた）。
    for f in list(files):
        q = Path(f)
        if q.suffix.lower() in (".json", ".geojson"):
            alias = f"{q.stem}.csv"
            if alias not in files:
                files.append(alias)
                headers[alias] = _json_backed_header_for(Path(source_dir), alias) or None
    files = sorted(files)
    messages = validate_mapping_ir(ir, files=files, headers=headers, catalog=catalog)
    # Policy gate (ADR instance-iri-base.md): placeholder namespaces (example.org
    # & co) identify nothing — the design must re-mint under the instance IRI
    # base it was given. Checked HERE (the AI-design pipeline), not in
    # parse_mapping_ir: example.org is legal RDF and standard in hand-written
    # fixtures; only generated designs are held to the minting policy.
    messages += [
        issue
        for name, iri in ir.prefixes.items()
        if (issue := placeholder_prefix_issue(name, iri))
    ]
    if messages:
        return _dedup([classify(m) for m in messages])

    try:
        compiled = compile_mapping_ir(ir, catalog)
    except RmlCompileError as exc:
        # Validation passed but compilation refused — surface verbatim (usually a
        # validator blind spot the LLM can still fix by restructuring the spec).
        return _dedup([classify(m) for m in exc.issues])

    # Defense in depth: the compiled RML still passes the unchanged gates. Any
    # issue here is a compiler bug or a validator blind spot — surfaced honestly,
    # never silently dropped.
    return _collect_rml_issues(compiled, source_dir)


def _collect_rml_issues(rml_ttl: str, source_dir: Path) -> list[Issue]:
    """The original RML pipeline (legacy proposals + backstop for compiled specs)."""
    prepared = substrate.substitute_run_id(rml_ttl)
    issues: list[Issue] = []
    # Safety FIRST — catches non-Tier-0 fn / SQL source / path escape AND invalid Turtle.
    try:
        substrate.assert_rml_safe(prepared, source_dir)
    except substrate.RmlSafetyError as exc:
        msg = str(exc)
        if _is_env_message(msg):
            raise _LoopEnvError(msg) from exc
        issues.append(classify(msg))
        # Unparseable Turtle: the design validator would silently pass — stop here so
        # the loop does not declare "converged" on a syntactically broken mapping.
        if "not parseable Turtle" in msg or "not valid Turtle" in msg:
            return _dedup(issues)
    except Exception as exc:  # rdflib/registry import failure surfaced as a raw error
        raise _LoopEnvError(str(exc)) from exc
    # Design validation — column / param / source-file moles with did-you-mean.
    try:
        substrate.validate_rml_design(prepared, source_dir)
    except substrate.RmlValidationError as exc:
        issues.extend(classify(m) for m in exc.issues)
    except Exception as exc:
        raise _LoopEnvError(str(exc)) from exc
    # Design-quality advisories (disconnected entities, …): not ingest-blocking,
    # but exactly the kind of defect the corrective loop CAN fix — a weak model
    # that transcribed each table into its own island gets told to add the join.
    with contextlib.suppress(Exception):  # advisory — never fail the loop on it
        issues.extend(
            classify(m) for m in substrate.design_advisories(prepared, source_dir)
        )
    return _dedup(issues)


def trap_issues(mat: Any) -> list[Issue]:
    """The BLOCKING failures of the bundle trap validator (T1-T10), as
    loop-feedable issues.

    Why the loop runs this at all: ``/api/materialize`` runs the SAME validator,
    and the kantan wizard stops on a blocking failure with a one-click
    "AI に直してもらう" button whose only effect is a refine round carrying
    exactly these lines. Before this, the loop converged on a validator the
    wizard does not run — so every trap failure surfaced AFTER "converged" and
    became a human click doing, by hand, a round the machine could do itself
    (live, 2026-08-16: ~5 clicks on a 70-line XRD file).

    The bundle carries the SAME fields ``/api/materialize`` passes, so the loop
    converges iff the wizard's gate would pass: no source CSVs, hence T1/T6
    report ``skip`` here exactly as they do there. Only ``fail`` counts — a
    ``warn`` (the T5 classDiagram lint) does not stop the wizard either.

    Each issue's message is the wizard's own fix shape: symptom + the trap's
    deterministic repair recipe. A symptom-only line loops weak models forever
    (the 2026-07-14 live T4 incident) — the recipe is the whole point.
    """
    paths = {k: Path(v) for k, v in mat.written_paths.items()}
    try:
        report = validate_schema(
            SchemaBundle(
                diagram_md=paths.get("mermaid") or paths.get("diagram"),
                mie_yaml=paths.get("mie_yaml") or paths.get("mie"),
                rml_ttl=paths.get("rml_ttl"),
                mapping_ir_yaml=paths.get("mapping_ir"),
            )
        )
    except Exception as exc:  # PyYAML / rdflib / pyoxigraph missing, or a check crash
        # Surfaced as an env failure (loop keeps the best schema and STOPS with the
        # reason recorded) — never swallowed. A silently-empty trap list would make
        # this whole gate a no-op that nothing would ever notice.
        raise _LoopEnvError(str(exc)) from exc
    out: list[Issue] = []
    for r in report.results:
        if r.status != "fail":
            continue
        head = f"{r.trap_id} {r.name}: {r.detail}"
        indented = "\n    ".join(r.fix.split("\n")) if r.fix else ""
        message = f"{head}\n  ↳ {indented}" if indented else head
        # Keyed by trap id (not the message): a recipe embeds derived candidate
        # terms, so keying on text would defeat the no-progress detection the
        # moment the model reshuffles a keyword list.
        out.append(Issue("trap", r.trap_id, message))
    return out


#: Traps whose fix recipe edits the §9 mapping spec ONLY — exactly the block a
#: surgical repair round regenerates and splices. Everything else fires in
#: another artifact (T4/T6/T7/T10 §7 MIE, T5 diagram doc) or the TBox (T3), so
#: it still needs the whole-document path. Keep this set
#: conservative: a trap listed here wrongly costs a wasted round, not a wrong
#: design — the round checks re-run either way.
_SPEC_REPAIRABLE_TRAPS = frozenset({"T1", "T9"})


def _verdict(schema_md: str, base: Path) -> tuple[str | None, list[Issue]]:
    """One round's complete deterministic verdict, from ONE materialize call:
    the extracted §9 design plus every issue BOTH gates report — the IR/RML
    validators against the real source, and the bundle trap validator.

    Returns ``(ir_yaml, issues)``. Only the spec matters to the caller (it picks
    the surgical-vs-whole-document repair path); the legacy raw RML is consumed
    here and never leaves.
    """
    with tempfile.TemporaryDirectory(prefix="asterism-loop-mat-") as tmp:
        # write=True (not the extraction-only write=False): the trap validator
        # reads the bundle off disk.
        mat = materialize_schema(schema_md, tmp, "design", write=True)
        if mat.mapping_ir_yaml is not None:
            ir_yaml, rml_ttl = mat.mapping_ir_yaml, None
        else:
            ir_yaml, rml_ttl = None, mat.rml_ttl
        issues = collect_issues(ir_yaml, rml_ttl, base)
        # A T9 mole is reported by both gates under different keys (the closed-set
        # check and the trap). Harmless: they name the same function and are fixed
        # by the same edit; dedup only collapses exact key matches.
        issues = _dedup(issues + trap_issues(mat))
    return ir_yaml, issues


# The advisory whose repair the machine can carry out ITSELF — see
# ``_stamp_numeric_datatypes``. Message shape: asterism.rml_validate
# ``_untyped_numeric_advisories``.
_RE_UNTYPED_NUMERIC = re.compile(
    r"column '([^']+)' holds numbers but is mapped as an untyped literal"
)


def _numeric_types_by_source(base: Path, ir: Any) -> dict[str, dict[str, str]]:
    """Per source file, the columns whose EVERY non-empty cell is a number ->
    xsd type, read through the spec's own pinned dialect (so the legacy
    instrument files' preamble/body split is the one the mapping will see)."""
    dialects: Mapping[str, Any] = getattr(ir, "dialects", None) or {}
    out: dict[str, dict[str, str]] = {}
    for name in {str(m.source) for m in ir.maps if getattr(m, "source", None)}:
        path = Path(base) / name
        if not path.is_file():
            continue
        dialect = dialects.get(name)
        try:
            rows = (
                _dialect_rows(path, dialect)
                if dialect is not None and not is_default(dialect)
                else list(_stream_rows(path))
            )
        except Exception:  # unreadable/odd source — simply not repairable here
            continue
        if not rows:
            continue
        out[name] = numeric_column_types(rows, list(rows[0].keys()))
    return out


# The advisory this repair answers (asterism.rml_validate
# ``_empty_shell_advisories``): a per-row map that mints an entity per row and
# binds no value column of its own.
_RE_EMPTY_SHELL = re.compile(r"map '([^']+)' mints one entity per row")


def _placeholders(template: str) -> list[str]:
    return re.findall(r"\{([^{}]+)\}", template or "")


def _determined_by(rows: list[dict[str, str]], column: str, keys: frozenset[str]) -> bool:
    """True when ``keys`` functionally determine ``column`` across the real rows.

    The same adjudication the duplicate-column advisory makes (G1): if two rows
    agreeing on the key disagree on the value, the key does not own it.
    """
    if not keys:
        return False
    seen: dict[tuple[str, ...], str] = {}
    for row in rows:
        k = tuple((row.get(c) or "").strip() for c in sorted(keys))
        v = (row.get(column) or "").strip()
        if not v:
            continue
        if k in seen and seen[k] != v:
            return False
        seen.setdefault(k, v)
    return True


def _place_row_values_on_their_own_map(
    schema_md: str, base: Path, issues: list[Issue]
) -> str | None:
    """Move a row's own values onto the per-row map that has none of its own.

    The empty-shell advisory (G14) fires when a map mints one entity per row and
    binds only links, so the row's values are recorded nowhere queryable. It
    already names the map and — from the real rows — the columns that belong on
    it. Live 2026-08-19 the person then had nowhere to act: the sentence says
    「行ごとに変わる列を『材料情報』に足すと直ります」 but no screen assigns
    columns to a kind, and ten AI rounds did not do it either.

    The edit is knowable without a model, so the machine makes it: a column that
    varies across the file and sits on a map whose key does NOT determine it is
    moved to the shell (it collapses into multi-values where it is, and is a
    plain value where it belongs). Deliberately limited to MOVES — the property
    keeps its own predicate, datatype and label, so nothing is invented. A column
    bound nowhere at all needs a new predicate minted for it and stays with the
    advisory.
    """
    if not any(_RE_EMPTY_SHELL.search(iss.message) for iss in issues):
        return None
    import yaml  # lazy (PyYAML is a step0 dependency)

    with tempfile.TemporaryDirectory(prefix="asterism-loop-shell-") as tmp:
        ir_yaml = materialize_schema(schema_md, tmp, "design", write=False).mapping_ir_yaml
    if not ir_yaml or not ir_yaml.strip():
        return None
    try:
        ir = parse_mapping_ir(ir_yaml)
        spec = load_spec_yaml(ir_yaml)
    except Exception:
        return None
    if not isinstance(spec, dict) or not isinstance(spec.get("maps"), list):
        return None

    rows_by_source = _rows_by_source(base, ir)
    moved = 0
    for source, rows in rows_by_source.items():
        if not rows:
            continue
        entries = [
            m
            for m in spec["maps"]
            if isinstance(m, dict) and str(m.get("source") or "") == source
        ]
        if len(entries) < 2:  # a shell needs somewhere its values could be parked
            continue

        def own_columns(entry: dict) -> set[str]:
            out: set[str] = set()
            for prop in entry.get("properties") or []:
                if isinstance(prop, dict) and isinstance(prop.get("column"), str):
                    out.add(prop["column"])
            return out

        def key_columns(entry: dict) -> frozenset[str]:
            subject = entry.get("subject")
            template = subject.get("template") if isinstance(subject, dict) else None
            return frozenset(_placeholders(template or ""))

        for shell in entries:
            shell_keys = key_columns(shell)
            if not shell_keys or own_columns(shell):
                continue  # not a per-row map, or not a shell
            for holder in entries:
                if holder is shell:
                    continue
                holder_keys = key_columns(holder)
                keep: list[Any] = []
                took = 0
                for prop in holder.get("properties") or []:
                    column = prop.get("column") if isinstance(prop, dict) else None
                    if not isinstance(column, str):
                        keep.append(prop)
                        continue
                    values = {(r.get(column) or "").strip() for r in rows} - {""}
                    if len(values) <= 1:
                        keep.append(prop)  # file-scoped metadata — the header's, by G1
                        continue
                    if _determined_by(rows, column, holder_keys):
                        keep.append(prop)  # genuinely this holder's own value
                        continue
                    # Including a column the shell's ID is built FROM: the ID
                    # encoding a value is not the same as recording it, and left
                    # here it still collapses onto one parent entity.
                    shell.setdefault("properties", []).append(prop)
                    took += 1
                if took:
                    holder["properties"] = keep
                    moved += took
    if not moved:
        return None
    repaired = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False).rstrip("\n")
    try:
        return replace_mapping_spec_block(schema_md, repaired)
    except ValueError:
        return None


def _rows_by_source(base: Path, ir: Any) -> dict[str, list[dict[str, str]]]:
    """Every source's rows, read through the spec's own pinned dialect."""
    dialects: Mapping[str, Any] = getattr(ir, "dialects", None) or {}
    out: dict[str, list[dict[str, str]]] = {}
    for name in {str(m.source) for m in ir.maps if getattr(m, "source", None)}:
        path = Path(base) / name
        if not path.is_file():
            continue
        dialect = dialects.get(name)
        try:
            out[name] = (
                _dialect_rows(path, dialect)
                if dialect is not None and not is_default(dialect)
                else list(_stream_rows(path))
            )
        except Exception:  # unreadable/odd source — simply not repairable here
            continue
    return out


def _stamp_numeric_datatypes(schema_md: str, base: Path, issues: list[Issue]) -> str | None:
    """Deterministically add the missing ``datatype:`` to the §9 property rows
    the untyped-numeric advisory named. Returns the repaired document, or None
    when there is nothing (or nothing repairable) to do.

    Why this is not the LLM's job: by the time the advisory fires, the machine
    has already read every row, proven every non-empty cell is a number, and
    decided integer-vs-double. The exact edit is KNOWN. Handing that to a model
    and hoping adds a round-trip, and weak models simply fail it — live
    2026-08-17, gpt-oss-120b on a 70-line XRD card: the loop spent its rounds,
    stopped on ``no_progress``, and the user then clicked "AI に直してもらう"
    three more times; all four columns (Z value / Volume / RIR(I/Ic) / Dcalc)
    were still untyped at the end. Applying the same 4-line edit here clears it
    in zero LLM calls.

    Repairs only what the advisory proved: a property row that binds one of the
    named columns, from a source whose rows are all-numeric for it, and that has
    no ``datatype`` and no ``function`` of its own (a pipeline's output type is
    the function's business — the same carve-out the validator makes). LEGACY
    raw-RML designs carry no spec to edit and are left to the LLM.
    """
    columns = {m.group(1) for iss in issues if (m := _RE_UNTYPED_NUMERIC.search(iss.message))}
    if not columns:
        return None
    import yaml  # lazy (PyYAML is a step0 dependency)

    with tempfile.TemporaryDirectory(prefix="asterism-loop-dt-") as tmp:
        ir_yaml = materialize_schema(schema_md, tmp, "design", write=False).mapping_ir_yaml
    if not ir_yaml or not ir_yaml.strip():
        return None
    try:
        ir = parse_mapping_ir(ir_yaml)
        spec = load_spec_yaml(ir_yaml)
    except Exception:
        return None
    if not isinstance(spec, dict) or not isinstance(spec.get("maps"), list):
        return None

    by_source = _numeric_types_by_source(base, ir)
    changed = 0
    for map_entry in spec["maps"]:
        if not isinstance(map_entry, dict):
            continue
        types = by_source.get(str(map_entry.get("source") or ""))
        if not types:
            continue
        for prop in map_entry.get("properties") or []:
            if not isinstance(prop, dict):
                continue
            column = prop.get("column")
            if not isinstance(column, str) or column not in columns:
                continue
            if prop.get("datatype") or prop.get("function"):
                continue
            xsd = types.get(column)
            if not xsd:
                continue
            prop["datatype"] = xsd
            changed += 1
    if not changed:
        return None
    repaired = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False).rstrip("\n")
    try:
        return replace_mapping_spec_block(schema_md, repaired)
    except ValueError:
        return None


# The transform message the identity repair keys on (mapping_ir._check_transform).
_RE_TRANSFORM_NOT_FN = re.compile(r"transform function '([^']+)' \(for \{([^}]+)\}\)")


def _looks_like_data_not_headers(names: Iterable[str]) -> bool:
    """True when a header row's own cells read as numbers.

    A column heading is a name. When every heading parses as a number, the row
    that was taken for the header is a row of data — the table was started too
    late (or too early). Deliberately requires ALL of them: one numeric heading
    is a naming choice, a whole row of them is a misread.
    """
    values = [str(n or "").strip() for n in names]
    values = [v for v in values if v]
    if not values:
        return False
    for value in values:
        try:
            float(value)
        except ValueError:
            return False
    return True


def _header_cells(path: Path, dialect: SourceDialect) -> list[str]:
    """The column names that came from the file's HEADER ROW under ``dialect``.

    Broadcast preamble columns are excluded: they are machine-named
    (``preamble_1`` …) from lines above the header, so including them would
    hide what the header row itself says.
    """
    header_only = replace(dialect, preamble="drop")
    rows = _dialect_rows(path, header_only)
    return list(rows[0].keys()) if rows else []


def _drop_language_tags_from_numbers(
    schema_md: str, base: Path, issues: list[Issue]
) -> str | None:
    """Remove ``language:`` from a column whose every value is a number.

    A language tag says which human language a piece of TEXT is in. A number is
    not in a language, and RDF allows a literal to carry a datatype or a language
    but never both — so the tag also blocks the datatype the numbers deserve, and
    with it every range and comparison query over that column.

    Live 2026-08-19: a model tagged a 3001-point sweep's angle and intensity as
    Japanese. The deterministic datatype repair then could not apply (it would
    have produced datatype + language), so the untyped-numeric advisory came back
    round after round with nothing able to clear it.

    Proven from the data, per column: only when every non-empty value parses as a
    number. A column of Japanese text keeps its tag.
    """
    import yaml  # lazy (PyYAML is a step0 dependency)

    with tempfile.TemporaryDirectory(prefix="asterism-loop-lang-") as tmp:
        ir_yaml = materialize_schema(schema_md, tmp, "design", write=False).mapping_ir_yaml
    if not ir_yaml or not ir_yaml.strip():
        return None
    try:
        ir = parse_mapping_ir(ir_yaml)
        spec = load_spec_yaml(ir_yaml)
    except Exception:
        return None
    if not isinstance(spec, dict) or not isinstance(spec.get("maps"), list):
        return None

    numeric = _numeric_types_by_source(base, ir)
    dropped = 0
    for entry in spec["maps"]:
        if not isinstance(entry, dict):
            continue
        types = numeric.get(str(entry.get("source") or ""))
        if not types:
            continue
        for prop in entry.get("properties") or []:
            if not isinstance(prop, dict) or not prop.get("language"):
                continue
            column = prop.get("column")
            if isinstance(column, str) and types.get(column):
                prop.pop("language")
                dropped += 1
    if not dropped:
        return None
    repaired = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False).rstrip("\n")
    try:
        return replace_mapping_spec_block(schema_md, repaired)
    except ValueError:
        return None


_RE_ONLY_PLACEHOLDER = re.compile(r"^\{([^{}]+)\}$")


def _bind_single_placeholder_templates_to_their_column(
    schema_md: str, base: Path, issues: list[Issue]
) -> str | None:
    """Rewrite ``object_template: "{col}"`` (a literal) as ``column: col``.

    A template whose whole content is one placeholder, emitted as a literal, is
    the column itself: the compiler turns the first into
    ``rr:template "{col}"; rr:termType rr:Literal`` and the second into
    ``rml:reference "col"``, which produce the same triples (R2RML only
    percent-encodes template values for IRIs).

    They are not the same to a READER, though, and that is the point. Every
    screen and check that asks "which columns does this design use" looks for
    ``column:``. Live 2026-08-19 a model wrote all nine properties the long way,
    so the "column meanings" review — the screen whose whole job is confirming
    what each column means — listed nothing at all, and the columns were filed
    under "IDs and fixed values that are added automatically" instead.

    Only the exact shape is rewritten, and only for literals: a template with any
    text around the placeholder builds a value, and one without
    ``object_type: literal`` builds an IRI from the cell, which ``column:``
    cannot express (the compiler refuses it outright).
    """
    import yaml  # lazy (PyYAML is a step0 dependency)

    with tempfile.TemporaryDirectory(prefix="asterism-loop-ot-") as tmp:
        ir_yaml = materialize_schema(schema_md, tmp, "design", write=False).mapping_ir_yaml
    if not ir_yaml or not ir_yaml.strip():
        return None
    try:
        spec = load_spec_yaml(ir_yaml)
    except Exception:
        return None
    if not isinstance(spec, dict) or not isinstance(spec.get("maps"), list):
        return None

    rewritten = 0
    for entry in spec["maps"]:
        if not isinstance(entry, dict):
            continue
        for prop in entry.get("properties") or []:
            if not isinstance(prop, dict):
                continue
            template = prop.get("object_template")
            if not isinstance(template, str) or prop.get("transform") or prop.get("function"):
                continue
            if prop.get("object_type") != "literal":
                continue
            m = _RE_ONLY_PLACEHOLDER.match(template.strip())
            if not m:
                continue
            prop.pop("object_template")
            prop.pop("object_type")  # a bare column is a literal by definition
            prop["column"] = m.group(1)
            rewritten += 1
    if not rewritten:
        return None
    repaired = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False).rstrip("\n")
    try:
        return replace_mapping_spec_block(schema_md, repaired)
    except ValueError:
        return None


def _fix_a_dialect_that_reads_data_as_headers(
    schema_md: str, base: Path, issues: list[Issue]
) -> str | None:
    """Replace a pinned read dialect whose header row is data with the detected one.

    The design pins how each source is read (ADR source-dialect.md). When that
    pin starts the table on the wrong line, every later stage is consistent and
    wrong: the headings become values, the rows before them are broadcast as
    ``preamble_1 … preamble_N``, and the real columns are reported as "not
    imported" — while nothing is technically invalid, so no check objects.

    Live 2026-08-19: a two-column instrument sweep pinned ``skip_rows: 12`` where
    detection said 1. The review screen offered ``20.200000`` and ``3966.666667``
    as column names (the file's 13th line) beside twelve ``preamble_*`` columns.

    Repaired only on proof and only when there is a better answer: the pinned
    dialect must yield an all-numeric header AND re-detection must yield one that
    is not. Detection reads the same bytes, so this compares two readings of the
    file rather than guessing at either.
    """
    import yaml  # lazy (PyYAML is a step0 dependency)

    with tempfile.TemporaryDirectory(prefix="asterism-loop-dl-") as tmp:
        ir_yaml = materialize_schema(schema_md, tmp, "design", write=False).mapping_ir_yaml
    if not ir_yaml or not ir_yaml.strip():
        return None
    try:
        spec = load_spec_yaml(ir_yaml)
    except Exception:
        return None
    if not isinstance(spec, dict):
        return None
    pinned = spec.get("dialects")
    if not isinstance(pinned, dict) or not pinned:
        return None

    fixed = 0
    for name, entry in list(pinned.items()):
        path = Path(base) / str(name)
        if not isinstance(entry, dict) or not path.is_file():
            continue
        try:
            dialect = SourceDialect(**{k: v for k, v in entry.items() if v is not None})
            pinned_names = _header_cells(path, dialect)
        except Exception:  # an unreadable pin is someone else's error to report
            continue
        if not _looks_like_data_not_headers(pinned_names):
            continue
        try:
            detected = detect_dialect(path)
            detected_names = _header_cells(path, detected)
        except Exception:
            continue
        if not detected_names or _looks_like_data_not_headers(detected_names):
            continue  # no better answer — leave the design alone
        # Only what the evidence is about. The header row being data proves the
        # table starts on the wrong line and nothing else: the encoding and the
        # delimiter plainly worked, and the preamble MODE is a design decision
        # (this design reads the lines above the header as columns and uses one
        # of them). Replacing the whole dialect would silently drop that.
        if int(entry.get("skip_rows") or 0) == detected.skip_rows:
            continue
        entry["skip_rows"] = detected.skip_rows
        fixed += 1
    if not fixed:
        return None
    repaired = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False).rstrip("\n")
    try:
        return replace_mapping_spec_block(schema_md, repaired)
    except ValueError:
        return None


def _drop_subject_transforms_that_empty_every_row(
    schema_md: str, base: Path, issues: list[Issue]
) -> str | None:
    """Remove a subject ``transform`` whose function returns "" for EVERY real row.

    A subject built from an empty value is no subject at all: Morph-KGC drops the
    row silently, so the import "succeeds" with zero triples and nothing says
    why. Live 2026-08-19, a 3001-row XRD scan: the model wrote
    ``subject.transform: {2θ (deg): iri_safe}``. ``iri_safe`` sanitizes a whole
    URL and returns "" for anything without a scheme — every 2θ value, every row.

    The transform is not merely harmful, it is unnecessary: R2RML engines
    percent-encode ``rr:template`` placeholders themselves, so dropping it leaves
    the IDs the design already describes. Proven from the data, never guessed:
    the function runs on the real column and is removed only when it empties
    every non-empty value it is given. A function that works on some rows is a
    judgment about the data and stays.
    """
    import yaml  # lazy (PyYAML is a step0 dependency)

    with tempfile.TemporaryDirectory(prefix="asterism-loop-tf0-") as tmp:
        ir_yaml = materialize_schema(schema_md, tmp, "design", write=False).mapping_ir_yaml
    if not ir_yaml or not ir_yaml.strip():
        return None
    try:
        ir = parse_mapping_ir(ir_yaml)
        spec = load_spec_yaml(ir_yaml)
    except Exception:
        return None
    if not isinstance(spec, dict) or not isinstance(spec.get("maps"), list):
        return None
    # The Tier-0 callables themselves (the catalog carries only signatures).
    try:
        from asterism.functions import REGISTRY as _TIER0

        callables = {
            spec.name: spec.func
            for spec in _TIER0
            if len(getattr(spec, "params", {}) or {"value": 1}) <= 1
        }
    except Exception:
        return None

    rows_by_source = _rows_by_source(base, ir)
    dropped = 0
    for entry in spec["maps"]:
        if not isinstance(entry, dict):
            continue
        subject = entry.get("subject")
        if not isinstance(subject, dict):
            continue
        transform = subject.get("transform")
        if not isinstance(transform, dict) or not transform:
            continue
        rows = rows_by_source.get(str(entry.get("source") or ""))
        if not rows:
            continue
        keep: dict[str, Any] = {}
        for column, fn_name in transform.items():
            # Only single-input functions are sampled: one that also takes a
            # constant (a lookup table, a pattern) cannot be judged from the
            # column alone.
            fn = callables.get(str(fn_name))
            values = [(r.get(str(column)) or "").strip() for r in rows]
            values = [v for v in values if v][:200]
            if fn is None or not values:
                keep[column] = fn_name
                continue
            try:
                empties_all = all(not str(fn(v) or "").strip() for v in values)
            except Exception:  # a function that raises is not this repair's call
                keep[column] = fn_name
                continue
            if empties_all:
                dropped += 1
            else:
                keep[column] = fn_name
        if len(keep) != len(transform):
            if keep:
                subject["transform"] = keep
            else:
                subject.pop("transform", None)
    if not dropped:
        return None
    repaired = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False).rstrip("\n")
    try:
        return replace_mapping_spec_block(schema_md, repaired)
    except ValueError:
        return None


def _drop_identity_transforms(schema_md: str, base: Path, issues: list[Issue]) -> str | None:
    """Deterministically remove ``transform: {X: X}`` entries whose value is NOT a
    Tier-0 function. Returns the repaired document, or None when nothing applies.

    Live 2026-08-18 (gpt-oss-120b, XRD card): the model wrote
    ``subject.transform: {No: No}`` / ``{hkl: hkl}`` — reading ``transform`` as
    "placeholder ← column" — and kept re-emitting it through five manual rounds.
    A transform maps a placeholder to a FUNCTION; a value equal to its own key
    that names no function cannot mean anything valid, so removing it is the
    only edit the message can lead to. Kept when the value IS a function (a
    column literally named ``slug`` with ``transform: {slug: slug}`` is legal).
    """
    named = {m.group(1) for iss in issues if (m := _RE_TRANSFORM_NOT_FN.search(iss.message))}
    if not named:
        return None
    try:
        functions = set(catalog_from_registry().names())
    except Exception:
        return None
    with tempfile.TemporaryDirectory(prefix="asterism-loop-tf-") as tmp:
        ir_yaml = materialize_schema(schema_md, tmp, "design", write=False).mapping_ir_yaml
    if not ir_yaml or not ir_yaml.strip():
        return None
    import yaml

    try:
        spec = load_spec_yaml(ir_yaml)
    except yaml.YAMLError:
        return None
    if not isinstance(spec, dict) or not isinstance(spec.get("maps"), list):
        return None

    def _prune(owner: Any) -> int:
        if not isinstance(owner, dict) or not isinstance(owner.get("transform"), dict):
            return 0
        tf = owner["transform"]
        drop = [
            k for k, v in tf.items()
            if isinstance(v, str) and str(k) == v and v in named and v not in functions
        ]
        for k in drop:
            del tf[k]
        if not tf:
            del owner["transform"]
        return len(drop)

    changed = 0
    for map_entry in spec["maps"]:
        if not isinstance(map_entry, dict):
            continue
        changed += _prune(map_entry.get("subject"))
        for prop in map_entry.get("properties") or []:
            changed += _prune(prop)
    if not changed:
        return None
    repaired = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False).rstrip("\n")
    try:
        return replace_mapping_spec_block(schema_md, repaired)
    except ValueError:
        return None


def _move_xsd_unit_to_datatype(schema_md: str, base: Path, issues: list[Issue]) -> str | None:
    """Move an xsd: CURIE written into unit: over to datatype:.

    unit is display metadata — a human-readable string like "W/(m·K)" that
    never compiles into RML. An xsd: term there is never a unit; it is the
    datatype field filled in one line too low. Observed live (2026-08-18, XRD
    card): unit: xsd:integer on the Z-value row, while the datatype the data
    proved was silently absent, so the value stayed untyped AND the review screen
    showed "xsd:integer" as its unit.

    Deterministic because there is exactly one thing it can mean. Skipped when
    the row already has a datatype (the author's choice wins — the stray unit
    is then just dropped as the display noise it is).
    """
    import yaml

    with tempfile.TemporaryDirectory(prefix="asterism-loop-unit-") as tmp:
        ir_yaml = materialize_schema(schema_md, tmp, "design", write=False).mapping_ir_yaml
    if not ir_yaml or not ir_yaml.strip():
        return None
    try:
        spec = load_spec_yaml(ir_yaml)
    except yaml.YAMLError:
        return None
    if not isinstance(spec, dict) or not isinstance(spec.get("maps"), list):
        return None
    changed = 0
    for map_entry in spec["maps"]:
        if not isinstance(map_entry, dict):
            continue
        for prop in map_entry.get("properties") or []:
            if not isinstance(prop, dict):
                continue
            unit = prop.get("unit")
            if not isinstance(unit, str) or not unit.strip().startswith("xsd:"):
                continue
            if not prop.get("datatype"):
                prop["datatype"] = unit.strip()
            del prop["unit"]
            changed += 1
    if not changed:
        return None
    repaired = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False).rstrip("\n")
    try:
        return replace_mapping_spec_block(schema_md, repaired)
    except ValueError:
        return None


# Deterministic repairs, in order. Each sees the CURRENT document + its issues
# and returns a repaired document or None; each is kept only if the re-verdict
# has strictly fewer issues (see _evaluate).
_REPAIRS: tuple[Callable[[str, Path, list[Issue]], str | None], ...] = (
    _drop_identity_transforms,  # parse-level: unblocks every later check
    _move_xsd_unit_to_datatype,  # a misfiled datatype, before typing is judged
    _place_row_values_on_their_own_map,  # before typing: it relocates the rows
    _stamp_numeric_datatypes,
)


def _evaluate(schema_md: str, base: Path) -> tuple[str, str | None, list[Issue]]:
    """Evaluate, then let the machine fix what it can BEFORE the LLM is asked.

    Returns ``(schema_md, ir_yaml, issues)`` — ``schema_md`` is the possibly
    deterministically-repaired document, and the issues are those of THAT
    document. Each repair in :data:`_REPAIRS` is applied in turn and kept only
    if it STRICTLY reduces the issue count, so a bad rule can never make a
    round worse than doing nothing. Repairs compose: each runs against the
    issues of the document the previous one produced, so a trap cleared here
    never reaches the routing decision (and therefore never forces the
    whole-document path — see the loop below).
    """
    # Unconditional, BEFORE the verdict: a subject transform that empties every
    # row makes a design that passes every check and then produces nothing. The
    # issue-gated repairs below never see it, because there is no issue — the
    # only symptom is an import that "succeeds" with zero triples.
    with contextlib.suppress(Exception):
        # A column bound the long way is invisible to every screen and check that
        # asks which columns the design uses. FIRST: the passes below read
        # `column:`, so a design that never says it looks empty to them.
        rebound = _bind_single_placeholder_templates_to_their_column(schema_md, base, [])
        if rebound is not None:
            schema_md = rebound
    with contextlib.suppress(Exception):
        # A number is in no language, and the tag blocks the datatype it needs.
        untagged = _drop_language_tags_from_numbers(schema_md, base, [])
        if untagged is not None:
            schema_md = untagged
    with contextlib.suppress(Exception):
        # Before anything reads a column name: a dialect that starts the table on
        # the wrong line makes every later stage consistent and wrong.
        redialected = _fix_a_dialect_that_reads_data_as_headers(schema_md, base, [])
        if redialected is not None:
            schema_md = redialected
    with contextlib.suppress(Exception):
        emptied = _drop_subject_transforms_that_empty_every_row(schema_md, base, [])
        if emptied is not None:
            schema_md = emptied
    ir_yaml, issues = _verdict(schema_md, base)
    for repair in _REPAIRS:
        if not issues:
            break
        repaired_md = repair(schema_md, base, issues)
        if repaired_md is None:
            continue
        try:
            repaired_ir, repaired_issues = _verdict(repaired_md, base)
        except _LoopEnvError:
            continue
        if len(repaired_issues) >= len(issues):
            continue  # a repair that does not help is discarded, never applied
        schema_md, ir_yaml, issues = repaired_md, repaired_ir, repaired_issues
    return schema_md, ir_yaml, issues


def _reference_count(ir_yaml: str | None, rml_ttl: str | None) -> int:
    """A cheap proxy for how much of the source a design covers. Used only to
    surface a soft ``coverage_dropped`` signal (a cornered weak model can delete
    mappings to reach zero issues).

    With a mapping spec the proxy is the number of property rows (more accurate
    than the old ``rml:reference`` count); an unparseable spec counts its
    ``predicate:`` lines instead (still monotone in mapped rows). Legacy raw RML
    keeps the original ``rml:reference`` count.
    """
    if ir_yaml and ir_yaml.strip():
        try:
            ir = parse_mapping_ir(ir_yaml)
        except Exception:
            return len(re.findall(r"^\s*-\s*predicate\s*:", ir_yaml, re.MULTILINE))
        return sum(len(m.properties) for m in ir.maps)
    if not rml_ttl:
        return 0
    return len(re.findall(r"\brml:reference\b", rml_ttl))


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


@dataclass
class RoundRecord:
    """One loop round's outcome, for progress display + the result summary."""

    n: int
    issue_count: int
    categories: dict[str, int]
    refine_truncated: bool = False
    env_error: str | None = None


@dataclass
class DesignLoopResult:
    """The loop's outcome. ``proposal_md`` is the BEST (fewest-issue) schema reached;
    ``remaining_issues`` are the messages for THAT schema (not the last round's)."""

    proposal_md: str
    csv_inspection_md: str
    domain_hint: str
    metadata: dict[str, Any]
    rounds: list[RoundRecord]
    converged: bool
    terminal_reason: str
    remaining_issues: list[str]
    initial_issue_count: int
    tabular_only: bool
    coverage_dropped: bool = False


def _cats(issues: list[Issue]) -> dict[str, int]:
    out: dict[str, int] = {}
    for iss in issues:
        out[iss.category] = out.get(iss.category, 0) + 1
    return out


def _emit(on_progress: Callable[[dict[str, Any]], None] | None, **data: Any) -> None:
    if on_progress is not None:
        on_progress(data)


def _surgical_spec_repair(
    llm: Any,
    schema_md: str,
    ir_yaml: str,
    issues: list[Issue],
    oracle: str,
) -> str:
    """One repair round that regenerates ONLY the §9 mapping spec (Phase 2).

    Sets the guided-JSON schema on the client when it supports the attribute
    (OpenAI-compatible; others ignore it and answer from the prompt contract —
    the output is parsed either way and re-gated by the normal round checks).
    Returns the schema_md with the repaired spec spliced in. Raises
    ``ValueError`` (loop-feedable) when the repair output cannot be parsed or
    spliced; LLM errors propagate exactly like refine's.
    """
    try:
        function_names = [f.name for f in catalog_from_registry()]
    except ImportError:
        function_names = None
    user = build_spec_repair_user(ir_yaml, [i.message for i in issues], oracle)
    schema = mapping_ir_json_schema(function_names)
    had_attr = hasattr(llm, "response_schema")
    prior = getattr(llm, "response_schema", None)
    try:
        if had_attr:
            llm.response_schema = schema
        from asterism_step0.llm import as_completion

        raw = as_completion(llm.complete(SPEC_REPAIR_SYSTEM_PROMPT, user)).text
    finally:
        if had_attr:
            llm.response_schema = prior
    new_spec = parse_spec_json(raw)
    return replace_mapping_spec_block(schema_md, new_spec)


# ---------------------------------------------------------------------------
# The orchestrator (synchronous + fully unit-testable with a mock LLM)
# ---------------------------------------------------------------------------


def run_design_loop(
    csv_paths: list[Path | str],
    domain_hint: str,
    source_dir: Path | str,
    *,
    fk_hint_columns: list[str] | None = None,
    record_path: str | None = None,
    llm: Any = None,
    max_rounds: int = 3,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    on_llm_call: Callable[[str], None] | None = None,
    language: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    skeleton: Mapping[str, Any] | None = None,
    dialect_overrides: Mapping[str, Any] | None = None,
    iri_base: str | None = None,
    column_meanings: Sequence[Mapping[str, Any]] | None = None,
    column_decisions: Sequence[Mapping[str, Any]] | None = None,
    deterministic_rules: bool = False,
) -> DesignLoopResult:
    """Run the propose→validate→refine self-correction loop.

    ``skeleton`` (Phase 2b) switches round-0 from the single-shot
    ``propose_schema`` to staged generation from a CONFIRMED skeleton
    (``propose_from_skeleton``: per-map property tables + document, §9 assembled
    deterministically). Everything after round-0 — validate, surgical §9 refine,
    stop conditions — is identical; the staged result is the same §1-9 Markdown.

    ``source_dir`` is the dir holding the uploaded CSVs (the propose job's temp dir),
    which lets the loop run the source-aware ``validate_rml_design``. ``on_progress`` is
    called with a dict per phase (for SSE). ``on_llm_call(feature)`` is called right
    after each LLM call so the caller can record usage (``last_usage`` is overwritten per
    call): feature is ``"propose"`` for round 0 and ``"propose.autocorrect"`` for refines.
    ``dialect_overrides`` (ADR source-dialect.md — the wizard's "read settings")
    are the human's per-source dialect edits confirmed BEFORE generation, as
    ``{source: {field: value}}`` carrying ONLY the fields they wrote; they are merged
    over the detected dialects FIELD BY FIELD (:func:`merge_dialect_overrides`) and that
    effective map drives every source read (oracle columns, inline/skeleton inspection,
    §9 pin), so a ``skip_rows`` edit that moves the header row stays consistent across
    the whole design while the detected encoding it never mentioned stays put. An empty
    override leaves ``effective == detected`` — byte-identical to today.

    ``column_meanings`` are the ``(source, column)`` meanings settled BEFORE the
    design (ADR meaning-before-identity). They are projected onto §9 after
    round-0 AND re-asserted after every later round, for the same reason the
    data facts are: a meaning is not something a generation round may revise.

    ``column_decisions`` are the human calls about whether a column is taken in
    at all, made on the meaning screen BEFORE the design exists. Re-asserted
    every round for the same reason: what the person will not have in their
    dataset is not a generation round's to reinstate.

    ``iri_base`` (ADR instance-iri-base.md) pins where the single-shot round-0
    mints this dataset's new namespaces; the staged path gets it at skeleton
    time instead (the confirmed skeleton already carries settled prefixes).
    ``language`` is the output language for the schema's human-readable prose; it is
    forwarded to BOTH propose and every refine round (otherwise an autocorrect round
    would silently flip the prose back to English). ``should_cancel`` is the job's
    cooperative cancel poll: checked before round-0 propose and before every refine
    round; when it reports True the loop raises :class:`LLMCancelledError` instead of
    spending another LLM call. Never raises for a bad design; only a round-0 propose
    failure (or the caller's LLM raising) and a cancel (``LLMCancelledError`` — never
    swallowed into ``env_error``) propagate. See the module docstring for the stop
    conditions.
    """
    paths = [Path(p) for p in csv_paths]
    base = Path(source_dir)
    tabular_only = all(p.suffix.lower() in _TABULAR_SUFFIXES for p in paths)

    if should_cancel is not None and should_cancel():
        raise LLMCancelledError("cancelled")
    # Detect ONCE at design time (ADR source-dialect.md); pinned into every §9
    # candidate below so the artifacts carry the dialect, ingest never re-detects.
    # A human "read settings" override wins over detection FIELD BY FIELD (the wizard
    # confirms the dialect BEFORE generation); the effective map drives every source
    # read below so a skip_rows edit that moves the header stays consistent everywhere,
    # while a field the person never touched keeps what detection found. An empty
    # override leaves effective == detected (byte-identical to today).
    detected = _detect_source_dialects(paths)
    effective = merge_dialect_overrides(detected, dialect_overrides)
    # Human-override source names: pinned into §9 with ALL four fields so an explicit
    # default (e.g. skip_rows corrected 1→0) survives the materialize re-pin (FIX2).
    override_names = frozenset(dialect_overrides or {})
    oracle = build_oracle(base, paths, dialects=effective)
    # The data-derived facts (who owns a column, which columns are numbers) —
    # re-asserted on §9 after EVERY round below, so no LLM round can undo them.
    data_facts: tuple[Mapping[str, Mapping[str, str]], Mapping[str, Mapping[str, str]]] = ({}, {})
    if skeleton is not None:
        # Phase 2b staged round-0. The oracle IS the per-map menu (exact files /
        # columns / function signatures), so property generation stays inside the
        # same closed set the refine rounds enforce. on_progress forwards the
        # per-map / document frames as-is; on_llm_call fires per staged call.
        try:
            function_names: list[str] | None = [f.name for f in catalog_from_registry()]
        except ImportError:
            function_names = None
        inspections, fks = inspect_source_set(
            paths, fk_hint_columns=fk_hint_columns, record_path=record_path,
            dialects=effective,
        )
        inspection_md = render_markdown(inspections, fks)
        # Column ownership on the CONFIRMED skeleton (ADR
        # column-ownership-and-growth G6). Recomputed here rather than carried
        # from the gate: the human may have edited a key, and the verdict must
        # describe the skeleton actually being generated from.
        column_owners = _column_owners(skeleton, paths, effective)
        # Numeric columns get a datatype from the DATA (ADR §B): an untyped
        # number is compared lexically by SPARQL — a silent wrong answer.
        column_datatypes = _column_datatypes(skeleton, paths, effective)
        data_facts = (column_owners, column_datatypes)
        _emit(on_progress, phase="propose", round=0, message="骨格から設計を生成中")
        schema_md = propose_from_skeleton(
            skeleton,
            inspection_md,
            domain_hint,
            llm=llm,
            menu=oracle,
            language=language,
            function_names=function_names,
            on_progress=lambda **d: _emit(on_progress, **d),
            on_llm_call=on_llm_call,
            column_owners=column_owners,
            column_types=column_datatypes,
            column_meanings=column_meanings,
            excluded_columns=_excluded_by_source(column_decisions),
            # かんたん経路 (ADR deterministic-design-assembly): §9 と文書を
            # 決定論で組む。LLM は refine (検証エラー時のみ) と相談に残る。
            deterministic=deterministic_rules,
        )
        metadata: dict[str, Any] = {
            "llm_class": type(llm).__name__,
            "staged": True,
            "deterministic_rules": deterministic_rules,
        }
    else:
        _emit(on_progress, phase="propose", round=0, message="初期設計を生成中")
        proposal = propose_schema(
            paths,
            domain_hint,
            fk_hint_columns=fk_hint_columns,
            record_path=record_path,
            llm=llm,
            language=language,
            dialects=effective,
            iri_base=iri_base,
        )
        if on_llm_call is not None:
            on_llm_call("propose")
        schema_md = proposal.proposal_md
        inspection_md = proposal.csv_inspection_md
        metadata = dict(proposal.metadata)
    schema_md = _overlay_detected_dialects(schema_md, effective, override_names)
    schema_md = _overlay_data_facts(schema_md, *data_facts)
    schema_md = _overlay_column_meanings(schema_md, column_meanings)
    schema_md = _overlay_column_decisions(schema_md, column_decisions)
    schema_md = _overlay_taken_in_columns(
        schema_md, skeleton, column_meanings, column_decisions, *data_facts
    )

    def _result(
        best_schema: str,
        best_issues: list[Issue],
        rounds: list[RoundRecord],
        *,
        converged: bool,
        reason: str,
        initial: int,
        base_refs: int,
    ) -> DesignLoopResult:
        return DesignLoopResult(
            proposal_md=best_schema,
            csv_inspection_md=inspection_md,
            domain_hint=domain_hint,
            metadata=dict(metadata),
            rounds=rounds,
            converged=converged,
            terminal_reason=reason,
            remaining_issues=[iss.message for iss in best_issues],
            initial_issue_count=initial,
            tabular_only=tabular_only,
            coverage_dropped=(
                _reference_count(*_extract_design(best_schema)) < base_refs
                if base_refs
                else False
            ),
        )

    base_refs = _reference_count(*_extract_design(schema_md))

    # Evaluate round 0.
    try:
        schema_md, ir_yaml, issues = _evaluate(schema_md, base)
    except _LoopEnvError as exc:
        return _result(
            schema_md, [], [RoundRecord(0, 0, {}, env_error=str(exc))],
            converged=False, reason="env_error", initial=0, base_refs=base_refs,
        )
    initial = len(issues)
    rounds: list[RoundRecord] = [RoundRecord(0, initial, _cats(issues))]
    best_schema, best_issues = schema_md, issues
    _emit(on_progress, phase="validated", round=0, issue_count=initial,
          categories=_cats(issues), message=f"設計を検証: {initial} 件の問題")

    if not issues:
        return _result(schema_md, [], rounds, converged=True, reason="converged",
                       initial=initial, base_refs=base_refs)
    if max_rounds <= 0:
        return _result(best_schema, best_issues, rounds, converged=False,
                       reason="no_autocorrect", initial=initial, base_refs=base_refs)

    # No-progress detection is PER REPAIR MODE. Surgical (§9-only, guided JSON)
    # and whole-document refine are different tools; a keyset surgical could not
    # move gets ONE whole-document attempt before the loop gives up. Live
    # 2026-08-18 (gpt-oss-120b, XRD card): the two surgical rounds returned
    # degenerate JSON that was discarded, the loop stopped on no_progress at 38
    # issues — and the human's five "AI に直してもらう" clicks, which are exactly
    # whole-document refines, then took the same design 38 → 2. Those were
    # rounds the machine could have run.
    seen_by_mode: dict[str, set[frozenset[tuple[str, str]]]] = {
        "surgical": set(), "document": set(),
    }
    escalated = False  # once surgical failed on a keyset, stay whole-document
    prev_issues = issues
    for n in range(1, max_rounds + 1):
        # A pending cancel outranks every stop condition: raise before spending
        # another LLM call (the job runner turns this into the cancelled state).
        if should_cancel is not None and should_cancel():
            raise LLMCancelledError("cancelled")
        keyset = frozenset(i.key for i in prev_issues)

        # Phase 2 surgical repair (ADR mapping-ir-phase2-guided-repair): with a
        # mapping spec present, ONLY the §9 block is regenerated — guided JSON
        # where the provider supports it — and spliced back deterministically.
        # ~10x fewer output tokens per round, no whole-document truncation
        # risk, and unrelated sections are byte-untouched. The legacy raw-RML
        # path keeps the whole-document refine.
        #
        # Which traps force the whole-document path: surgical repair regenerates
        # ONLY the §9 spec, so a trap that fires in ANOTHER artifact (T4/T7/T10
        # in the §7 MIE, T5 in the diagram doc, T3 in the TBox) cannot be
        # cleared by splicing §9 — a surgical round would burn a call and change
        # nothing, and the no-progress detector would stop the loop one round
        # later with the trap still open. T2 reads §9 `dialects:`, but that
        # block is machine-owned (_overlay_detected_dialects re-pins it
        # authoritatively every round), so no LLM round can clear it either.
        #
        # But the converse used to be true too: ONE such trap sent the round down
        # the whole-document path even when every OTHER issue was a §9 issue —
        # and whole-document refine is the path weak models fail worst (mid-
        # document truncation, unrelated sections degraded). Since the machine
        # now clears what it can before this point (``_evaluate``), the traps
        # still standing here are only the ones that genuinely need a model; the
        # routing looks at WHERE they live rather than at their mere existence.
        # Whole-document refine remains the fallback, never the first resort.
        blocking_traps = {
            i.subject
            for i in prev_issues
            if i.category == "trap" and i.subject not in _SPEC_REPAIRABLE_TRAPS
        }
        surgical = (
            bool(ir_yaml and ir_yaml.strip())
            and not escalated
            and not blocking_traps
        )
        mode = "surgical" if surgical else "document"
        if keyset in seen_by_mode[mode]:  # cycle / no-progress in THIS mode
            if surgical:
                # Escalate: the same issues, one whole-document attempt.
                surgical, mode, escalated = False, "document", True
            if keyset in seen_by_mode[mode]:  # both tools already failed here
                return _result(best_schema, best_issues, rounds, converged=False,
                               reason="no_progress", initial=initial, base_refs=base_refs)
        seen_by_mode[mode].add(keyset)
        _emit(on_progress, phase="refine", round=n, issue_count=len(prev_issues),
              categories=_cats(prev_issues),
              message=(
                  f"{len(prev_issues)} 件の問題を修正中 (§9 仕様のみ再生成)"
                  if surgical
                  else f"{len(prev_issues)} 件の問題を修正中"
                  + (" (設計全体を再生成)" if escalated else "")
              ))
        try:
            if surgical:
                schema_md = _surgical_spec_repair(
                    llm, schema_md, ir_yaml or "", prev_issues, oracle
                )
            else:
                comments = render_feedback(prev_issues, oracle)  # non-empty by construction
                ref = refine_schema(schema_md, comments, llm=llm, language=language)
        except LLMCancelledError:
            # A user cancel is NOT an env failure — it must reach the job runner
            # (which discards the run), never be swallowed into env_error below.
            raise
        except LLMTruncatedError as exc:
            rounds.append(RoundRecord(n, len(prev_issues), _cats(prev_issues),
                                      refine_truncated=True, env_error=f"truncated: {exc}"))
            return _result(best_schema, best_issues, rounds, converged=False,
                           reason="refine_truncated", initial=initial, base_refs=base_refs)
        except ValueError as exc:
            # Unparseable/unspliceable surgical output — an LLM-quality flake,
            # not an env failure: record the round (schema unchanged) and
            # escalate, so the next round retries the SAME issues with the
            # whole-document refine instead of stopping on no_progress.
            if on_llm_call is not None:
                on_llm_call("propose.autocorrect")
            rounds.append(RoundRecord(n, len(prev_issues), _cats(prev_issues),
                                      env_error=f"spec repair discarded: {exc}"))
            escalated = True
            continue
        except Exception as exc:  # provider 429/quota/etc — non-loopable, keep best
            rounds.append(RoundRecord(n, len(prev_issues), _cats(prev_issues), env_error=str(exc)))
            return _result(best_schema, best_issues, rounds, converged=False,
                           reason="env_error", initial=initial, base_refs=base_refs)
        if on_llm_call is not None:
            on_llm_call("propose.autocorrect")

        if not surgical:
            if not ref.complete:  # refine dropped an artifact (truncation) → keep prior
                rounds.append(
                    RoundRecord(n, len(prev_issues), _cats(prev_issues), refine_truncated=True)
                )
                return _result(best_schema, best_issues, rounds, converged=False,
                               reason="refine_truncated", initial=initial, base_refs=base_refs)
            schema_md = ref.effective_schema_md  # == ref.refined_md when complete

        # Re-pin the effective dialects (a repair round could have dropped the
        # section; explicit values the round kept still win). Idempotent.
        schema_md = _overlay_detected_dialects(schema_md, effective, override_names)
        schema_md = _overlay_data_facts(schema_md, *data_facts)
        schema_md = _overlay_column_meanings(schema_md, column_meanings)
        schema_md = _overlay_column_decisions(schema_md, column_decisions)
        schema_md = _overlay_taken_in_columns(
            schema_md, skeleton, column_meanings, column_decisions, *data_facts
        )
        try:
            schema_md, ir_yaml, issues = _evaluate(schema_md, base)
        except _LoopEnvError as exc:
            rounds.append(RoundRecord(n, len(prev_issues), _cats(prev_issues), env_error=str(exc)))
            return _result(best_schema, best_issues, rounds, converged=False,
                           reason="env_error", initial=initial, base_refs=base_refs)
        rounds.append(RoundRecord(n, len(issues), _cats(issues)))
        _emit(on_progress, phase="validated", round=n, issue_count=len(issues),
              categories=_cats(issues), message=f"再検証: {len(issues)} 件の問題")

        if len(issues) < len(best_issues):
            best_schema, best_issues = schema_md, issues
        if not issues:
            return _result(schema_md, [], rounds, converged=True, reason="converged",
                           initial=initial, base_refs=base_refs)
        prev_issues = issues

    return _result(best_schema, best_issues, rounds, converged=False,
                   reason="max_rounds", initial=initial, base_refs=base_refs)


def _overlay_data_facts(
    schema_md: str,
    column_owners: Mapping[str, Mapping[str, str]] | None,
    column_types: Mapping[str, Mapping[str, str]] | None,
) -> str:
    """Re-assert the data-derived facts on the §9 spec after ANY round.

    Sibling of :func:`_overlay_detected_dialects`, same posture: the model may
    rearrange predicates and prose, it may not un-know what the rows proved —
    a column belongs to one map (ADR column-ownership G6), a number is
    typed (ADR numeric-literal-typing N2), and one cell is recorded once.
    Round-0 applied all three per map; a later self-correction round rewrote §9
    from memory and lost them (live).
    Idempotent; a schema with no §9 spec, or one that cannot be re-spliced, is
    left byte-untouched. No-op when there is nothing to assert.
    """
    # 所有権も型も無いときでも素通ししない: 二重記録の除去は入力を要らない
    # (同じ列を同じ読み方で二度書いた、という設計だけで分かる事実)。
    ir_yaml, _ = _extract_design(schema_md)
    if not ir_yaml or not ir_yaml.strip():
        return schema_md
    import yaml

    try:
        doc = load_spec_yaml(ir_yaml)
    except yaml.YAMLError:
        return schema_md
    if not isinstance(doc, dict):
        return schema_md
    new_doc, changed = apply_data_facts(
        doc, column_owners=column_owners, column_types=column_types
    )
    if not changed:
        return schema_md
    new_yaml = yaml.safe_dump(new_doc, sort_keys=False, allow_unicode=True)
    try:
        return replace_mapping_spec_block(schema_md, new_yaml)
    except ValueError:
        return schema_md


def _overlay_column_meanings(
    schema_md: str, column_meanings: Sequence[Mapping[str, Any]] | None
) -> str:
    """Re-assert the settled ``(source, column)`` meanings on §9 after ANY round.

    Sibling of :func:`_overlay_data_facts`, same posture and the same reason: a
    later round may rearrange the design, it may not rewrite what a column MEANS
    (ADR meaning-before-identity §6 / data-facts-invariant N6). The meaning was
    read off the data or typed by the person who took it, before this design
    existed. Idempotent; a schema with no §9, or one that cannot be re-spliced,
    is left byte-untouched.
    """
    if not column_meanings:
        return schema_md
    ir_yaml, _ = _extract_design(schema_md)
    if not ir_yaml or not ir_yaml.strip():
        return schema_md
    import yaml

    try:
        doc = load_spec_yaml(ir_yaml)
    except yaml.YAMLError:
        return schema_md
    if not isinstance(doc, dict):
        return schema_md
    new_doc, changed = apply_column_meanings(doc, column_meanings)
    if not changed:
        return schema_md
    new_yaml = yaml.safe_dump(new_doc, sort_keys=False, allow_unicode=True)
    try:
        return replace_mapping_spec_block(schema_md, new_yaml)
    except ValueError:
        return schema_md


def _excluded_by_source(
    column_decisions: Sequence[Mapping[str, Any]] | None,
) -> dict[str, list[str]]:
    """``{source: [column, …]}`` for the columns the person excluded."""
    out: dict[str, list[str]] = {}
    for decision in column_decisions or ():
        if str(decision.get("action") or "") != "exclude":
            continue
        source = str(decision.get("source") or "")
        column = str(decision.get("column") or "")
        if source and column:
            out.setdefault(source, []).append(column)
    return out


def _column_homes(
    skeleton: Mapping[str, Any] | None, column_owners: Mapping[str, Mapping[str, str]] | None
) -> dict[str, dict[str, str]]:
    """``{source: {column: the map that owns it}}`` — read off the gate's verdict.

    "peak must not write these 18 columns, crystal owns them" is exactly the
    statement "those columns live on crystal". A source with a single map needs
    no verdict at all — :func:`take_in_columns` answers that case itself.
    """
    maps = [m for m in ((skeleton or {}).get("maps") or []) if isinstance(m, Mapping)]
    source_of = {str(m.get("name") or ""): str(m.get("source") or "") for m in maps}
    homes: dict[str, dict[str, str]] = {}
    for map_name, borrowed in (column_owners or {}).items():
        source = source_of.get(str(map_name), "")
        if not source:
            continue
        for column, owner in (borrowed or {}).items():
            homes.setdefault(source, {})[str(column)] = str(owner)
    return homes


def _overlay_taken_in_columns(
    schema_md: str,
    skeleton: Mapping[str, Any] | None,
    column_meanings: Sequence[Mapping[str, Any]] | None,
    column_decisions: Sequence[Mapping[str, Any]] | None,
    column_owners: Mapping[str, Mapping[str, str]] | None,
    column_types: Mapping[str, Mapping[str, str]] | None,
) -> str:
    """Take in the columns the reader kept, without asking a second time.

    On the meaning screen every column is taken in unless its checkbox is
    cleared (ADR meaning-before-identity §3/§9), so a kept column the generated
    design reads nowhere is not a question — it is a gap between the answer and
    the design, and the machine closes it. Sibling of the other overlays: run
    after every round, idempotent, byte-untouched when there is nothing to add.
    """
    kept = [
        m
        for m in (column_meanings or ())
        if isinstance(m, Mapping) and str(m.get("source") or "") and str(m.get("column") or "")
    ]
    if not kept or not skeleton:
        return schema_md
    excluded = {
        (str(d.get("source") or ""), str(d.get("column") or ""))
        for d in column_decisions or ()
        if str(d.get("action") or "") == "exclude"
    }
    wanted = [m for m in kept if (str(m["source"]), str(m["column"])) not in excluded]
    if not wanted:
        return schema_md
    ir_yaml, _ = _extract_design(schema_md)
    if not ir_yaml or not ir_yaml.strip():
        return schema_md
    import yaml

    try:
        doc = load_spec_yaml(ir_yaml)
    except yaml.YAMLError:
        return schema_md
    if not isinstance(doc, dict):
        return schema_md
    new_doc, added = take_in_columns(
        doc,
        wanted,
        homes=_column_homes(skeleton, column_owners),
        column_types=column_types,
    )
    if not added:
        return schema_md
    new_yaml = yaml.safe_dump(new_doc, sort_keys=False, allow_unicode=True)
    try:
        return replace_mapping_spec_block(schema_md, new_yaml)
    except ValueError:
        return schema_md


def _overlay_column_decisions(
    schema_md: str, column_decisions: Sequence[Mapping[str, Any]] | None
) -> str:
    """Re-assert the human include/exclude calls on §9 after ANY round.

    Third sibling of :func:`_overlay_data_facts`. A column the person said they
    do not want is not a column a later round may put back, and one they asked
    for is not one a rewrite may drop. Idempotent; a schema with no §9, an
    unreadable one, or a decision the design cannot place is left byte-untouched
    (the decision endpoint reports those properly — a loop round must not fail
    over one).
    """
    if not column_decisions:
        return schema_md
    ir_yaml, _ = _extract_design(schema_md)
    if not ir_yaml or not ir_yaml.strip():
        return schema_md
    import yaml

    try:
        doc = load_spec_yaml(ir_yaml)
    except yaml.YAMLError:
        return schema_md
    if not isinstance(doc, dict):
        return schema_md
    try:
        new_doc, changed = apply_column_decisions(doc, column_decisions)
    except ValueError:
        return schema_md
    if not changed:
        return schema_md
    new_yaml = yaml.safe_dump(new_doc, sort_keys=False, allow_unicode=True)
    try:
        return replace_mapping_spec_block(schema_md, new_yaml)
    except ValueError:
        return schema_md


def repair_design(schema_md: str, source_dir: Path | str) -> str:
    """Deterministic repair + data-fact re-assertion for a design that did NOT
    come from :func:`run_design_loop`.

    The loop re-asserts what the rows proved after EVERY round
    (:func:`_overlay_data_facts`) and runs :data:`_REPAIRS` before spending a
    call. The MANUAL path — the wizard's "AI に直してもらう" button, which is
    ``/api/refine`` followed by ``/api/materialize`` — went through neither, so
    every click could quietly un-do a fact the machine had already established.
    Live 2026-08-18: the numeric datatypes were present after the loop and gone
    after four manual rounds; the advisory came back each time, which invited
    the next click. Same defect ADR data-facts-invariant fixed for automatic
    rounds, on the path nobody had covered.

    Map→source comes from the spec itself (the IR is skeleton-shaped for this
    purpose), so no confirmed skeleton is needed. Best-effort in every
    direction: an unreadable source, an unparseable spec or a validator
    environment failure returns the input unchanged — this must never be the
    reason a save fails.
    """
    base = Path(source_dir)
    md = schema_md
    try:
        # 1) Unconditional: a number the rows proved stays typed, whatever the
        #    round did to §9. Not issue-driven — the untyped-numeric advisory is
        #    invisible while any earlier validation message short-circuits the
        #    IR pipeline, and this fact does not depend on it.
        #
        #    Read through the spec's PINNED dialects, never a fresh detection:
        #    re-detecting this file yields ``preamble: drop`` where the design
        #    pinned ``keyvalue``, and the broadcast preamble columns (Volume,
        #    RIR(I/Ic), Dcalc, …) then vanish from the rows — the exact columns
        #    whose untyped-numeric advisory kept coming back (live 2026-08-18).
        ir_yaml, _ = _extract_design(md)
        if ir_yaml and ir_yaml.strip():
            ir = parse_mapping_ir(ir_yaml)
            by_source = _numeric_types_by_source(base, ir)
            types = {
                str(m.name): by_source[str(m.source)]
                for m in ir.maps
                if getattr(m, "source", None) and by_source.get(str(m.source))
            }
            if types:
                md = _overlay_data_facts(md, None, types)
    except Exception:  # a fact overlay must never break the save
        md = schema_md
    try:
        # 2) Issue-driven, each kept only when it strictly reduces the count.
        md, _ir, _issues = _evaluate(md, base)
    except _LoopEnvError:
        return md
    return md


def _extract_design(schema_md: str) -> tuple[str | None, str | None]:
    """Pull the §9 design out of a schema Markdown via the SAME deterministic
    extractor the materialize endpoint uses (no LLM): ``(mapping_ir_yaml,
    rml_ttl)``. New proposals carry the mapping spec (first slot); legacy ones
    carry raw RML (second slot); ``(None, None)`` = a dropped/renamed §9 — a
    structural failure, not a clean design. The loop re-parses/compiles the
    spec itself (``_collect_ir_issues``) so the extraction stays extraction."""
    with tempfile.TemporaryDirectory(prefix="asterism-loop-mat-") as tmp:
        mat = materialize_schema(schema_md, tmp, "design", write=False)
    if mat.mapping_ir_yaml is not None:
        return mat.mapping_ir_yaml, None
    return None, mat.rml_ttl


# ---------------------------------------------------------------------------
# The same backstop, for the "ask the AI to change it" path
# ---------------------------------------------------------------------------


def repair_after_refine(
    schema_md: str,
    csv_paths: list[Path | str],
    source_dir: Path | str,
    *,
    llm: Any = None,
    max_rounds: int = 2,
    on_llm_call: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Validate + self-correct a REFINED design, with the round-0 discipline.

    Round 0 of :func:`run_design_loop` re-pins the source dialects, re-asserts
    the data-derived facts, validates, and lets the machine repair what it can
    before any model is asked. The human-initiated path — "AI に直してもらう" /
    "AI に反映して作り直す" — did none of that: whatever the model returned went
    straight to materialize. That is the most fragile document in the system
    (a weak model rewriting §9 from memory drops ``datatype: xsd:double`` and
    the numbers become strings — range questions then answer wrongly rather
    than fail, the exact invariant #372 was built to hold), so it is the
    document that most needs the backstop.

    Returns ``(schema_md, autocorrect)`` — the possibly-repaired document plus a
    summary shaped like the propose response's ``autocorrect`` block, so the UI
    treats both paths identically. Only ``_surgical_spec_repair`` (the §9-only
    round) is used here: the user asked for one change, and silently spending
    whole-document refines on top of it would undo prose they just approved.
    Never raises for a bad design; a cancel propagates like everywhere else.
    """
    paths = [Path(p) for p in csv_paths]
    base = Path(source_dir)
    rounds: list[RoundRecord] = []

    def _summary(
        *, converged: bool, reason: str, initial: int, remaining: list[Issue]
    ) -> dict[str, Any]:
        return {
            "enabled": max_rounds > 0,
            "converged": converged,
            "terminal_reason": reason,
            "initial_issue_count": initial,
            "final_issue_count": len(remaining),
            "rounds": [
                {"n": r.n, "issue_count": r.issue_count, "categories": r.categories}
                for r in rounds
            ],
            "remaining_issues": [i.message for i in remaining],
        }

    effective = _detect_source_dialects(paths)
    schema_md = _overlay_detected_dialects(schema_md, effective, frozenset())
    try:
        schema_md, ir_yaml, issues = _evaluate(schema_md, base)
    except _LoopEnvError as exc:
        rounds.append(RoundRecord(0, 0, {}, env_error=str(exc)))
        return schema_md, _summary(
            converged=False, reason="env_error", initial=0, remaining=[]
        )
    initial = len(issues)
    rounds.append(RoundRecord(0, initial, _cats(issues)))
    if not issues:
        return schema_md, _summary(
            converged=True, reason="converged", initial=initial, remaining=[]
        )
    if max_rounds <= 0 or not (ir_yaml and ir_yaml.strip()) or llm is None:
        # Legacy raw-RML designs carry no §9 spec to splice; report honestly
        # rather than fall back to a whole-document rewrite behind the user.
        return schema_md, _summary(
            converged=False, reason="no_autocorrect", initial=initial, remaining=issues
        )

    oracle = build_oracle(base, paths, dialects=effective)
    best_schema, best_issues = schema_md, issues
    seen_keysets: set[frozenset[tuple[str, str]]] = set()
    prev_issues = issues
    for n in range(1, max_rounds + 1):
        if should_cancel is not None and should_cancel():
            raise LLMCancelledError("cancelled")
        keyset = frozenset(i.key for i in prev_issues)
        if keyset in seen_keysets:  # same issues as last round: stop, do not spin
            return best_schema, _summary(
                converged=False, reason="no_progress", initial=initial,
                remaining=best_issues,
            )
        seen_keysets.add(keyset)
        if any(i.subject not in _SPEC_REPAIRABLE_TRAPS
               for i in prev_issues if i.category == "trap"):
            # A trap outside §9 cannot be spliced away; the wizard's stop card
            # (with its own fix recipe) is the honest next step.
            return best_schema, _summary(
                converged=False, reason="needs_whole_document", initial=initial,
                remaining=best_issues,
            )
        try:
            schema_md = _surgical_spec_repair(
                llm, best_schema, ir_yaml or "", prev_issues, oracle
            )
        except LLMCancelledError:
            raise
        except Exception as exc:  # provider failure / unspliceable output
            rounds.append(
                RoundRecord(n, len(prev_issues), _cats(prev_issues), env_error=str(exc))
            )
            return best_schema, _summary(
                converged=False, reason="env_error", initial=initial,
                remaining=best_issues,
            )
        if on_llm_call is not None:
            on_llm_call("refine.autocorrect")
        schema_md = _overlay_detected_dialects(schema_md, effective, frozenset())
        try:
            schema_md, ir_yaml, issues = _evaluate(schema_md, base)
        except _LoopEnvError as exc:
            rounds.append(
                RoundRecord(n, len(prev_issues), _cats(prev_issues), env_error=str(exc))
            )
            return best_schema, _summary(
                converged=False, reason="env_error", initial=initial,
                remaining=best_issues,
            )
        rounds.append(RoundRecord(n, len(issues), _cats(issues)))
        if len(issues) < len(best_issues):
            best_schema, best_issues = schema_md, issues
        if not issues:
            return schema_md, _summary(
                converged=True, reason="converged", initial=initial, remaining=[]
            )
        prev_issues = issues
    return best_schema, _summary(
        converged=False, reason="max_rounds", initial=initial, remaining=best_issues
    )
