"""Schema validator for asterism Phase 3 (trap checks T1-T9).

Validates a schema bundle (TBox TTL + Mermaid + MIE YAML + ingester Python +
the source CSVs) against the 8 traps from
``docs/architecture/ai-assisted-step0-workflow.md`` §6, plus **T9** (Phase 5):
the declarative RML mapping references only vetted Tier 0 functions — see
``docs/architecture/step0-rml-emission.md`` §5.2.

The traps and how this module checks each:

* **T1 ID uniqueness** — collect candidate IRI keys from *two* sources: the
  composite IRI patterns in the MIE (e.g. ``sdr:sample/{SID}-{sample_id}``) **and**
  the actual key columns recovered from the ingester's IRI builders
  (:mod:`asterism_step0.t1_ingester`). For each, re-run
  :mod:`asterism_step0.inspect` on the source CSVs and confirm the key combination
  is globally unique. Reading the ingester is the safety net: if ``propose`` picks
  the wrong key on a subset, a full-CSV validate catches it even when the MIE
  looks clean (dogfood Round 3).
* **T2 BOM** — grep the ingester for ``utf-8-sig``; check the source CSVs'
  first column name does not start with the BOM byte.
* **T3 bnode-free** — parse the TBox TTL with rdflib and assert
  ``len(g.bnodes())`` is zero. Also grep the ingester for ``BNode(``.
* **T4 MIE keywords / categories** — YAML parse the MIE; require
  ``schema_info.keywords`` and ``schema_info.categories`` lists with ≥ 5
  entries each, and a configurable Japanese/synonym subset. On failure the
  trap emits a paste-ready ``schema_info`` fix recipe whose keyword candidates
  are derived deterministically from the design itself (title words, Mermaid
  class names, §9 mapping-spec map/class/column names, RML class/reference
  names, CSV stems/headers) — domain-independent by construction.
* **T5 Mermaid classDiagram syntax** — parse Mermaid blocks in the diagram doc.
  A colon in a relation label is a blocking ``fail`` (GitHub / mermaid.js both
  choke). A best-effort ``classDiagram`` lint (diagram header, class-name
  charset, relation-arrow shape, colon/paren danger) reports the AI-generated
  breakage that renders as a "bomb icon" in the UI as a non-blocking ``warn``
  (dogfood 2026-07-08).
* **T6 fake sample_rdf_entries** — for every ``sdr:<entity>/<key>`` IRI in the
  MIE's ``sample_rdf_entries``, confirm ``key`` appears in the corresponding
  CSV column. Catches hallucinated SIDs / sample_ids.
* **T7 Why / Alternatives / Trade-offs** — YAML parse
  ``architectural_notes`` and check that every "decision-like" bullet has
  ``Why`` / ``Alternatives`` / ``Trade-offs`` keywords (heuristic; not
  enforced if the section is missing).
* **T8 hallucination test** (opt-in, requires API key) — invoke an
  :class:`LLMClient` with a curated list of natural-language questions plus
  the connected MCP tool surface. Compare the LLM's answers against SPARQL
  ground truth. Returns a soft pass/warn — flaky by nature.

Return shape: :class:`ValidationReport`. The CLI ``asterism-validate`` returns
exit code 0 if all required (non-skipped) traps pass, else 1 — suitable for
CI integration on PRs that touch ``docs/ontology/``, ``data/togomcp/mie/``,
or ``ingest/src/asterism/``.
"""
from __future__ import annotations

import contextlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asterism_step0.inspect import _check_uniqueness, _stream_rows
from asterism_step0.t1_ingester import extract_ingester_keys

# ----------------------------------------------------------------------------
# Report dataclasses
# ----------------------------------------------------------------------------


@dataclass
class TrapResult:
    """One trap check's outcome."""

    trap_id: str  # "T1" through "T9"
    name: str
    status: str  # "pass" | "fail" | "warn" | "skip"
    detail: str  # human-readable explanation
    evidence: list[str] = field(default_factory=list)  # supporting paths/quotes
    fix: str = ""
    """Deterministic repair recipe, set by the check itself on an actionable
    fail/warn (empty on pass/skip). Says WHERE (design section + YAML path),
    WHAT SHAPE, and — where derivable from the design — a paste-ready example.
    Written for the one-click "ask AI to fix" flow: the 2026-07-14 live T4
    incident showed a symptom-only detail loops weak models forever."""

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    @property
    def blocking(self) -> bool:
        """Failures are blocking for CI; warns and skips are not."""
        return self.status == "fail"


@dataclass
class ValidationReport:
    """Result of validating one schema bundle."""

    results: list[TrapResult]
    bundle_paths: dict[str, str]  # which files were validated

    @property
    def all_passed(self) -> bool:
        return all(r.status in {"pass", "skip", "warn"} for r in self.results)

    @property
    def blocking_failures(self) -> list[TrapResult]:
        return [r for r in self.results if r.blocking]

    def exit_code(self) -> int:
        return 0 if self.all_passed else 1


# ----------------------------------------------------------------------------
# Schema bundle
# ----------------------------------------------------------------------------


@dataclass
class SchemaBundle:
    """The set of files validated together.

    Any field may be None — validators skip traps whose required input is missing.
    Typical layout matches Phase 1 starrydata:
    """

    tbox_ttl: Path | None = None  # docs/ontology/{name}.ttl
    diagram_md: Path | None = None  # docs/ontology/diagram.md
    mie_yaml: Path | None = None  # data/togomcp/mie/{name}.yaml
    ingester_py: Path | None = None  # ingest/src/asterism/{name}.py
    rml_ttl: Path | None = None  # {name}-mapping.rml.ttl (declarative substrate, T9)
    mapping_ir_yaml: Path | None = None  # {name}-mapping.yaml (§9 spec; feeds T4's fix)
    source_csvs: list[Path] = field(default_factory=list)
    fk_hint_columns: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Trap T1: ID uniqueness
# ----------------------------------------------------------------------------

# Match composite-IRI patterns in MIE shape_expressions:
#   sdr:sample/{SID}-{sample_id}
#   sdr:curve/{SID}-{figure_id}-{sample_id}
# We extract the placeholders ({SID}, {sample_id}, ...) and treat them as the
# composite key columns to test for global uniqueness.
_IRI_TEMPLATE = re.compile(r"sdr:([a-zA-Z_]+)/((?:\{[A-Za-z_][A-Za-z0-9_]*\}[-/]?)+)")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _extract_composite_keys_with_entity(mie_text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Pull every ``(entity, placeholder-tuple)`` pair from MIE-style text."""
    seen: set[tuple[str, tuple[str, ...]]] = set()
    out: list[tuple[str, tuple[str, ...]]] = []
    for match in _IRI_TEMPLATE.finditer(mie_text):
        entity = match.group(1)
        placeholders = tuple(_PLACEHOLDER.findall(match.group(2)))
        if placeholders and (entity, placeholders) not in seen:
            seen.add((entity, placeholders))
            out.append((entity, placeholders))
    return out


def _extract_composite_keys(mie_text: str) -> list[tuple[str, ...]]:
    """Pull every composite-IRI placeholder tuple from MIE-style text."""
    seen: set[tuple[str, ...]] = set()
    out: list[tuple[str, ...]] = []
    for _entity, placeholders in _extract_composite_keys_with_entity(mie_text):
        if placeholders not in seen:
            seen.add(placeholders)
            out.append(placeholders)
    return out


# Sections that document what NOT to do. IRI templates appearing here are
# negative examples ("do not mint sdr:sample/{sample_id}"), so scanning them
# for T1 produces false positives — exclude them.
_NEGATIVE_SECTIONS = ("anti_patterns", "common_errors")


def _mie_text_for_iri_scan(mie_path: Path) -> str:
    """Return the MIE text with negative-example sections stripped.

    A well-written MIE documents the *bad* single-key IRI in ``anti_patterns``
    (e.g. "do NOT mint sdr:sample/{sample_id}"). Naively regexing the whole
    file would extract that negative example and flag T1 — a false positive.
    We drop ``anti_patterns`` / ``common_errors`` before scanning.

    Falls back to the raw text if the YAML does not parse as a mapping.
    """
    raw = mie_path.read_text(encoding="utf-8")
    try:
        import yaml  # lazy

        data = yaml.safe_load(raw)
    except Exception:
        return raw
    if not isinstance(data, dict):
        return raw
    filtered = {k: v for k, v in data.items() if k not in _NEGATIVE_SECTIONS}
    import yaml

    return yaml.safe_dump(filtered, allow_unicode=True, sort_keys=False)


def _source_columns(csvs: list[Path]) -> list[str]:
    """Union of header column names across the source CSVs (first-seen order)."""
    cols: list[str] = []
    for path in csvs:
        first = next(iter(_stream_rows(path)), None)
        if first:
            for c in first:
                if c not in cols:
                    cols.append(c)
    return cols


def _collect_t1_candidates(
    bundle: SchemaBundle,
) -> tuple[dict[tuple[str, tuple[str, ...]], list[str]], list[str]]:
    """Gather candidate IRI keys from the MIE templates *and* the ingester.

    Returns ``(candidates, notes)`` where ``candidates`` maps an
    ``(entity, column-tuple)`` pair to the human-readable sources that produced
    it, and ``notes`` records ingester keys that were only partially resolvable
    (left out of the uniqueness check). The entity drives which source CSV the
    key is checked against — a ``paper`` key belongs in ``papers.csv``, not in
    ``samples.csv`` where its column happens to repeat by design.
    """
    candidates: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    notes: list[str] = []

    def add(entity: str, cols: tuple[str, ...], label: str) -> None:
        if not cols:
            return
        sources = candidates.setdefault((entity, cols), [])
        if label not in sources:
            sources.append(label)

    if bundle.mie_yaml:
        # Scan everything EXCEPT anti_patterns / common_errors — those document
        # negative examples and would cause false positives (dogfood Finding 3).
        mie_text = _mie_text_for_iri_scan(bundle.mie_yaml)
        for entity, key in _extract_composite_keys_with_entity(mie_text):
            add(entity, key, "MIE template")

    if bundle.ingester_py:
        columns = _source_columns(bundle.source_csvs)
        try:
            ikeys = extract_ingester_keys(
                bundle.ingester_py.read_text(encoding="utf-8"), columns
            )
        except Exception as exc:  # pragma: no cover - defensive
            notes.append(f"ingester parse failed: {exc}")
            ikeys = []
        for k in ikeys:
            if k.fully_resolved:
                add(k.entity, k.columns, f"ingester {k.func or '<module>'}() → sdr:{k.entity}")
            elif k.columns or k.unresolved:
                # Secondary resources (descriptor/{key}/{i}, ingestion/{run_id})
                # carry an unresolvable placeholder; don't guess — just report.
                notes.append(
                    f"ingester sdr:{k.entity} ({k.func}): "
                    f"resolved {list(k.columns)}, unresolved {list(k.unresolved)} — skipped"
                )

    return candidates, notes


def _pick_csv_for_key(
    entity: str,
    key: tuple[str, ...],
    csvs: list[Path],
    rows_of: Any,
) -> Path | None:
    """Choose which source CSV to test ``key`` against.

    Among CSVs that contain *all* of the key's columns, prefer one whose
    filename matches ``entity`` (``paper`` → ``papers.csv``). This stops a
    single-column key like ``(SID,)`` from being judged against the wrong file
    where the column repeats legitimately. Falls back to the first match.
    """
    matching = [
        p
        for p in csvs
        if (rows := rows_of(p)) and all(c in rows[0] for c in key)
    ]
    if not matching:
        return None
    ent = entity.lower().rstrip("s")
    if ent:
        for p in matching:
            if ent in p.stem.lower().rstrip("s"):
                return p
    return matching[0]


# Fix recipes (T1). Deterministic, domain-independent: they name the design
# section + YAML path to edit and the shape to paste; the colliding entity and
# its current key columns are inlined from the check's own findings.
_T1_FIX_NO_TEMPLATE = (
    "Declare each entity's IRI template so uniqueness can be checked: in §9 (mapping "
    "spec) give every map a `subject.template` built from source key column(s) — "
    'placeholders in braces, e.g. "prefix:entity/{keyColumn}" — and mirror it in §2 '
    "(IRI scheme)."
)
_T1_FIX_UNMATCHED = (
    "The IRI template placeholders do not match any source file's header columns. In §9 "
    "(mapping spec) `subject.template`, use column names EXACTLY as the source header "
    "spells them (case-sensitive); do not invent or rename columns."
)


def _t1_fix_collisions(failed_keys: list[tuple[str, tuple[str, ...]]]) -> str:
    lines = [
        "In §9 (mapping spec), find the map that mints each entity below and extend its "
        "`subject.template` with additional source column(s) — composite form "
        '"prefix:entity/{colA}-{colB}" — until the combination is unique across the '
        "source rows; update the matching §2 (IRI scheme) template to stay consistent:"
    ]
    for entity, key in failed_keys:
        lines.append(
            f"- {entity}: the current key ({', '.join(key)}) collides — add a "
            "discriminating column from the same source file's header."
        )
    lines.append(
        "Use only real header columns (the workbench skeleton gate lists verified-unique "
        "candidate key combinations as chips)."
    )
    return "\n".join(lines)


def _check_t1_uniqueness(bundle: SchemaBundle) -> TrapResult:
    if not bundle.source_csvs:
        return TrapResult(
            "T1",
            "ID uniqueness (composite key globally unique)",
            "skip",
            "Need source_csvs to run.",
        )
    if not bundle.mie_yaml and not bundle.ingester_py:
        return TrapResult(
            "T1",
            "ID uniqueness",
            "skip",
            "Need mie_yaml or ingester_py to derive IRI keys.",
        )

    candidates, notes = _collect_t1_candidates(bundle)
    if not candidates:
        detail = "No composite IRI templates found in MIE"
        if bundle.ingester_py:
            detail += " and no resolvable composite key in ingester"
        return TrapResult(
            "T1",
            "ID uniqueness",
            "warn",
            detail + " (no sdr:<entity>/{...} patterns).",
            evidence=notes,
            fix=_T1_FIX_NO_TEMPLATE,
        )

    # Cache each CSV's rows — the same file backs several candidate keys.
    row_cache: dict[Path, list[dict[str, str]]] = {}

    def rows_of(path: Path) -> list[dict[str, str]]:
        if path not in row_cache:
            row_cache[path] = list(_stream_rows(path))
        return row_cache[path]

    failures: list[str] = []
    failed_keys: list[tuple[str, tuple[str, ...]]] = []
    passes: list[str] = []
    for (entity, key), sources in candidates.items():
        src = "; ".join(sources)
        csv_path = _pick_csv_for_key(entity, key, bundle.source_csvs, rows_of)
        if csv_path is None:
            notes.append(
                f"sdr:{entity} ({', '.join(key)}) [{src}] → no source CSV has all these columns"
            )
            continue
        report = _check_uniqueness(rows_of(csv_path), key)
        label = f"{csv_path.name}: sdr:{entity} ({', '.join(key)}) [{src}]"
        if report.is_unique:
            passes.append(f"{label} → 0 collisions ({report.total_rows_considered:,} rows)")
        else:
            failures.append(
                f"{label} → {report.collision_count:,} collisions "
                f"({report.distinct_tuples:,} of "
                f"{report.total_rows_considered:,} rows distinct)"
            )
            failed_keys.append((entity, key))

    if failures:
        return TrapResult(
            "T1",
            "ID uniqueness",
            "fail",
            f"{len(failures)} composite key(s) collide in source CSVs.",
            evidence=failures + passes + notes,
            fix=_t1_fix_collisions(failed_keys),
        )
    if not passes:
        return TrapResult(
            "T1",
            "ID uniqueness",
            "warn",
            "Derived IRI key(s) could not be matched to any source CSV's columns.",
            evidence=notes,
            fix=_T1_FIX_UNMATCHED,
        )
    return TrapResult(
        "T1",
        "ID uniqueness",
        "pass",
        f"All {len(passes)} composite key(s) globally unique.",
        evidence=passes + notes,
    )


# ----------------------------------------------------------------------------
# Trap T2: BOM handling
# ----------------------------------------------------------------------------


_BOM_BYTE = b"\xef\xbb\xbf"

# Fix recipe (T2): where (§8 ingester) + the exact call shape to paste.
_T2_FIX = (
    "In §8 (ingester), open every source file with `encoding=\"utf-8-sig\"` — e.g. "
    '`open(path, encoding="utf-8-sig", newline="")` — replacing any plain `utf-8` open. '
    "utf-8-sig strips a leading BOM so it can never leak into the first column name."
)


def _check_t2_bom(bundle: SchemaBundle) -> TrapResult:
    issues: list[str] = []
    evidence: list[str] = []

    if bundle.ingester_py:
        text = bundle.ingester_py.read_text(encoding="utf-8")
        if "utf-8-sig" in text or "utf_8_sig" in text:
            evidence.append(f"{bundle.ingester_py.name}: uses utf-8-sig ✓")
        else:
            issues.append(f"{bundle.ingester_py.name}: no utf-8-sig found in source")

    for csv_path in bundle.source_csvs:
        with csv_path.open("rb") as fh:
            head = fh.read(3)
        # A BOM in the file is fine if the ingester strips it (which we just
        # verified). What we want to catch is a parser that opens with plain
        # utf-8 leaving the BOM in the first column name. We can't fully
        # simulate that here; we just record whether the file has a BOM so
        # the human reviewer knows.
        if head == _BOM_BYTE:
            evidence.append(f"{csv_path.name}: has BOM (utf-8-sig will strip it)")

    if issues:
        return TrapResult(
            "T2",
            "BOM (utf-8-sig in ingester)",
            "fail",
            "Ingester missing utf-8-sig — BOM may leak into column names.",
            evidence=issues + evidence,
            fix=_T2_FIX,
        )
    if not bundle.ingester_py:
        return TrapResult("T2", "BOM", "skip", "No ingester to check.")
    return TrapResult(
        "T2",
        "BOM",
        "pass",
        "Ingester opens CSV with utf-8-sig.",
        evidence=evidence,
    )


# ----------------------------------------------------------------------------
# Trap T3: bnode-free
# ----------------------------------------------------------------------------


# Fix recipe (T3): every anonymous node gets a stable IRI template instead.
_T3_FIX = (
    "Give every entity a stable IRI instead of a blank node: in §9 (mapping spec) every "
    "map's `subject` needs a `template:` (or `constant:`); declare the per-class IRI "
    "template in §2 (IRI scheme); remove every `BNode()` call from §8 (ingester); and "
    "avoid TBox constructs that create anonymous nodes (e.g. owl:Restriction cardinality "
    "blocks — state cardinality in §6 model.yaml instead). Blank nodes break re-ingest "
    "idempotency."
)


def _check_t3_bnode_free(bundle: SchemaBundle) -> TrapResult:
    if not bundle.tbox_ttl and not bundle.ingester_py:
        return TrapResult("T3", "bnode-free", "skip", "Need TBox TTL or ingester to check.")

    issues: list[str] = []
    evidence: list[str] = []

    if bundle.tbox_ttl:
        import rdflib  # lazy; optional dep used only by validator

        g = rdflib.Graph()
        g.parse(str(bundle.tbox_ttl), format="turtle")
        bnodes = {s for s, _, _ in g.triples((None, None, None)) if isinstance(s, rdflib.BNode)}
        bnodes |= {o for _, _, o in g.triples((None, None, None)) if isinstance(o, rdflib.BNode)}
        if bnodes:
            issues.append(
                f"{bundle.tbox_ttl.name}: {len(bnodes)} blank node(s) in TBox "
                "(LinkML-style cardinality restrictions, or hand-written bnodes)"
            )
        else:
            evidence.append(f"{bundle.tbox_ttl.name}: 0 bnodes in TBox ✓")

    if bundle.ingester_py:
        text = bundle.ingester_py.read_text(encoding="utf-8")
        # Match rdflib.BNode( or `from rdflib import ... BNode` followed by a call.
        if re.search(r"\bBNode\s*\(", text):
            issues.append(
                f"{bundle.ingester_py.name}: ingester source calls BNode() — emits bnodes at ingest"
            )
        else:
            evidence.append(f"{bundle.ingester_py.name}: no BNode() calls in ingester ✓")

    if issues:
        return TrapResult(
            "T3",
            "bnode-free",
            "fail",
            "Blank nodes break re-ingest idempotency (Phase 1 design-rationale §2).",
            evidence=issues + evidence,
            fix=_T3_FIX,
        )
    return TrapResult(
        "T3",
        "bnode-free",
        "pass",
        "No blank nodes in TBox or ingester.",
        evidence=evidence,
    )


# ----------------------------------------------------------------------------
# Trap T4: MIE keywords / categories
# ----------------------------------------------------------------------------


_MIN_KEYWORDS = 5
_MIN_CATEGORIES = 1

# Fix recipe (broken §7 YAML — shared by T4/T6/T7 via _mie_broken).
_MIE_BROKEN_FIX = (
    "Re-emit the WHOLE §7 (MIE YAML extras) block as valid YAML — the file must parse "
    "before any content check can run. Common causes: an unterminated quoted string, a "
    "bare `:` or `#` inside an unquoted value (quote the value), inconsistent "
    "indentation, tab characters."
)


def _load_mie_yaml(bundle: SchemaBundle) -> tuple[object, str | None]:
    """Parse the MIE YAML for a trap check: ``(data, parse_error)``.

    An LLM-drafted MIE can be broken YAML (observed live: an unparseable
    ``sparql_query_examples`` list item 500'd materialize from inside
    ``validate_schema``). Broken YAML is a validation FINDING the checks must
    report — never an exception that crashes the endpoint — so the reviewer
    gets a trap result they can bounce back to the AI, like every other issue.
    """
    import yaml  # lazy

    try:
        return yaml.safe_load(bundle.mie_yaml.read_text(encoding="utf-8")), None
    except Exception as exc:  # YAMLError, OSError, decode errors — all findings
        return None, " ".join(str(exc).split())[:240]


def _mie_broken(trap_id: str, name: str, bundle: SchemaBundle, err: str) -> TrapResult:
    return TrapResult(
        trap_id,
        name,
        "fail",
        f"{bundle.mie_yaml.name} is not parseable YAML — fix §7 (MIE YAML) first: {err}",
        fix=_MIE_BROKEN_FIX,
    )


# --- T4 fix-recipe derivation -------------------------------------------------
#
# The recipe's keyword candidates come from the DESIGN ITSELF (title words,
# Mermaid class names, §9 mapping-spec map/class/column names, RML class /
# reference local names, CSV stems/headers) — deterministic and domain-
# independent by construction: no hardcoded vocabulary of any field. When the
# design yields ≥ 5 terms the emitted YAML block alone satisfies T4 (tested by
# parsing the recipe); when it yields fewer, the recipe states exactly how many
# terms the author must add — it never invents domain words to pad the list.

_T4_MAX_KEYWORDS = 8  # keep the paste block a curated shortlist, not a dump
_T4_TERM = re.compile(r"[^\W\d_][\w-]*")  # a word that starts with a letter
_MERMAID_CLASS_DECL = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
_RML_CLASS = re.compile(r"\brr:class\s+<?([^\s;.\]>]+)>?")
_RML_REFERENCE = re.compile(r'\brml:reference\s+"([^"]+)"')
# Line-shape fallbacks for a §9 spec that does not even parse as YAML (that
# broken state is exactly when the recipe is needed most).
_IR_SCALAR_LINE = re.compile(
    r"^\s*-?\s*(name|source|column|predicate):\s*\"?([^\"\n]+?)\"?\s*$", re.MULTILINE
)
_IR_FLOW_LIST = re.compile(r"^\s*(?:classes|columns):\s*\[([^\]]*)\]", re.MULTILINE)


def _local_name(term: str) -> str:
    """CURIE/IRI → local name (``xr:Card`` → ``Card``; plain names pass through)."""
    return re.split(r"[:#/]", term.strip())[-1]


def _terms_from_mapping_ir(text: str) -> list[str]:
    """Keyword-candidate terms from a §9 mapping-spec text: map names, source
    stems, subject-class local names, property columns, predicate local names.
    Tolerant of broken YAML (regex fallback) — never raises."""
    import yaml  # lazy

    try:
        doc = yaml.safe_load(text)
    except Exception:
        doc = None
    terms: list[str] = []
    if isinstance(doc, Mapping) and isinstance(doc.get("maps"), list):
        for m in doc["maps"]:
            if not isinstance(m, Mapping):
                continue
            if isinstance(m.get("name"), str):
                terms.append(m["name"])
            if isinstance(m.get("source"), str):
                terms.append(Path(m["source"]).stem)
            subject = m.get("subject")
            if isinstance(subject, Mapping) and isinstance(subject.get("classes"), list):
                terms += [_local_name(c) for c in subject["classes"] if isinstance(c, str)]
            if isinstance(m.get("properties"), list):
                for p in m["properties"]:
                    if not isinstance(p, Mapping):
                        continue
                    if isinstance(p.get("predicate"), str):
                        terms.append(_local_name(p["predicate"]))
                    if isinstance(p.get("column"), str):
                        terms.append(p["column"])
                    if isinstance(p.get("columns"), list):
                        terms += [c for c in p["columns"] if isinstance(c, str)]
        return terms
    for key, val in _IR_SCALAR_LINE.findall(text):
        if key == "source":
            terms.append(Path(val).stem)
        elif key == "predicate":
            terms.append(_local_name(val))
        else:
            terms.append(val)
    for body in _IR_FLOW_LIST.findall(text):
        terms += [_local_name(v.strip().strip("\"'")) for v in body.split(",") if v.strip()]
    return terms


def _t4_candidate_terms(bundle: SchemaBundle, schema_info: Mapping) -> list[str]:
    """All derivable keyword candidates, in source-priority order (title words
    first, CSV header columns last). File problems are silently skipped — the
    recipe generator must never crash a trap check."""
    terms: list[str] = []
    title = schema_info.get("title") if isinstance(schema_info, Mapping) else None
    if isinstance(title, str):
        terms += _T4_TERM.findall(title)
    if bundle.diagram_md:
        with contextlib.suppress(OSError):
            text = bundle.diagram_md.read_text(encoding="utf-8")
            terms += _MERMAID_CLASS_DECL.findall(text)
    if bundle.mapping_ir_yaml:
        with contextlib.suppress(OSError):
            terms += _terms_from_mapping_ir(bundle.mapping_ir_yaml.read_text(encoding="utf-8"))
    if bundle.rml_ttl:
        with contextlib.suppress(OSError):
            rml_text = bundle.rml_ttl.read_text(encoding="utf-8")
            terms += [_local_name(c) for c in _RML_CLASS.findall(rml_text)]
            terms += _RML_REFERENCE.findall(rml_text)
    terms += [p.stem for p in bundle.source_csvs]
    with contextlib.suppress(Exception):  # unreadable/odd CSVs must not break the recipe
        terms += _source_columns(bundle.source_csvs)
    return terms


def _t4_fix_recipe(bundle: SchemaBundle, schema_info: object, *, min_keywords: int) -> str:
    """Build the paste-ready §7 ``schema_info`` repair recipe.

    The emitted YAML preserves every existing ``schema_info`` field (a paste
    must be lossless), keeps the author's keywords verbatim, then appends
    derived candidates up to the cap. ``categories`` falls back to the generic
    placeholder ``dataset`` (a placeholder is allowed; invented domain terms
    are not).
    """
    import yaml  # lazy

    info: dict = dict(schema_info) if isinstance(schema_info, Mapping) else {}
    existing_keywords = list(info["keywords"]) if isinstance(info.get("keywords"), list) else []
    categories = list(info["categories"]) if isinstance(info.get("categories"), list) else []
    keywords = list(existing_keywords)  # the author's entries survive verbatim
    seen = {str(k).strip().lower() for k in keywords}

    if len(keywords) < min_keywords:
        for term in _t4_candidate_terms(bundle, info):
            if len(keywords) >= _T4_MAX_KEYWORDS:
                break
            t = term.strip().strip("_-")
            if len(t) < 2 or not any(ch.isalpha() for ch in t) or t.lower() in seen:
                continue
            seen.add(t.lower())
            keywords.append(t)

    title = info.get("title")
    if not (isinstance(title, str) and title.strip()):
        stem = bundle.mie_yaml.stem if bundle.mie_yaml else "dataset"
        title = re.sub(r"[-._]mie$", "", stem, flags=re.IGNORECASE) or stem
    info["title"] = title
    info["keywords"] = keywords
    info["categories"] = categories if categories else ["dataset"]

    block = yaml.safe_dump({"schema_info": info}, allow_unicode=True, sort_keys=False).rstrip()

    intro = (
        "Replace the `schema_info` block in §7 (MIE YAML extras) with the YAML below; "
        "keep every other §7 key (sample_rdf_entries, sparql_query_examples, "
        f"anti_patterns, architectural_notes) unchanged. T4 requires at least "
        f"{min_keywords} entries in `schema_info.keywords` and at least "
        f"{_MIN_CATEGORIES} in `schema_info.categories`."
    )
    if len(keywords) > len(existing_keywords):
        intro += (
            " The appended keyword candidates are derived from this design's own title, "
            "class/map and column names — refine the wording freely, but keep the counts."
        )
    parts = [intro]
    shortfall = min_keywords - len(keywords)
    if shortfall > 0:
        parts.append(
            f"Only {len(keywords)} keyword(s) could be derived from the design itself — "
            f"after pasting, add {shortfall} more short term(s) describing this data to "
            "`schema_info.keywords` (words a searcher would type; do not leave the list "
            "under the threshold)."
        )
    parts.append(block)
    return "\n\n".join(parts)


def _check_t4_keywords(bundle: SchemaBundle, *, min_keywords: int = _MIN_KEYWORDS) -> TrapResult:
    if not bundle.mie_yaml:
        return TrapResult("T4", "MIE keywords / categories", "skip", "No MIE YAML.")

    data, err = _load_mie_yaml(bundle)
    if err:
        return _mie_broken("T4", "MIE keywords / categories", bundle, err)
    if not isinstance(data, dict):
        return TrapResult(
            "T4",
            "MIE keywords / categories",
            "fail",
            f"{bundle.mie_yaml.name} did not parse as a YAML mapping.",
            fix=(
                "The §7 (MIE YAML extras) block must be a YAML MAPPING (top-level "
                "`key: value` sections such as `schema_info`, `sample_rdf_entries`, "
                "`anti_patterns`), not a list or plain text. Rebuild it starting from "
                "the block below, then re-add the other sections.\n\n"
                + _t4_fix_recipe(bundle, {}, min_keywords=min_keywords)
            ),
        )
    schema_info = data.get("schema_info") or {}
    keywords = schema_info.get("keywords") or []
    categories = schema_info.get("categories") or []

    issues: list[str] = []
    if len(keywords) < min_keywords:
        issues.append(f"keywords has {len(keywords)} entries, need ≥ {min_keywords}")
    if len(categories) < _MIN_CATEGORIES:
        issues.append(f"categories has {len(categories)} entries, need ≥ {_MIN_CATEGORIES}")

    evidence = [
        f"keywords: {len(keywords)} entries (first 5: {keywords[:5]})",
        f"categories: {len(categories)} entries ({categories[:5]})",
    ]
    if issues:
        return TrapResult(
            "T4",
            "MIE keywords / categories",
            "fail",
            "; ".join(issues) + " — AI discovery via find_databases will miss this dataset.",
            evidence=evidence,
            fix=_t4_fix_recipe(bundle, schema_info, min_keywords=min_keywords),
        )
    return TrapResult(
        "T4",
        "MIE keywords / categories",
        "pass",
        f"keywords ≥ {min_keywords} ✓, categories ≥ {_MIN_CATEGORIES} ✓.",
        evidence=evidence,
    )


# ----------------------------------------------------------------------------
# Trap T5: Mermaid classDiagram syntax
# ----------------------------------------------------------------------------
#
# Two layers, two severities:
#   * colon-in-relation-label → FAIL. GitHub and mermaid.js both choke on it;
#     this is the original, high-confidence check.
#   * best-effort classDiagram lint → WARN. Line-head keywords, class-name
#     charset, relation-arrow shape, and colon/paren danger. A regex lint can
#     never be a full Mermaid parser, so its findings never *block* CI — they
#     surface in the report so the reviewer / AI can fix a diagram that would
#     otherwise render as a broken "bomb icon" in the UI (dogfood 2026-07-08,
#     production dataset-9422ba7c). Ingest / promote are unaffected; the damage
#     is purely visual, hence warn.


# Capture the body of any ```mermaid ... ``` fenced block.
_MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(.*?)\n```", re.DOTALL)

# Mermaid relation arrow patterns (classDiagram): A --> B, A ..> B, A o-- B, etc.
# We only care about labels AFTER the colon delimiter, e.g. `A --> B : has`.
_MERMAID_RELATION = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*"
    r'(?:"[^"]*"\s*)?'  # optional cardinality `"1"`
    r"(?:-->|\.\.>|<--|<\.\.|--|o--|--o|\*--|--\*|<\|--|--\|>)"
    r'\s*(?:"[^"]*"\s*)?'  # optional cardinality on the other end
    r"[A-Za-z_][A-Za-z0-9_]*"
    r"\s*:\s*(.+)$",
    re.MULTILINE,
)

# Valid classDiagram relation arrows (Mermaid 11), matched as whole tokens.
# https://mermaid.js.org/syntax/classDiagram.html
_CLASS_ARROWS = frozenset(
    {
        "<|--", "--|>", "<|..", "..|>",  # inheritance / realization
        "*--", "--*", "o--", "--o",       # composition / aggregation
        "<--", "-->", "--",               # association / link (solid)
        "<..", "..>", "..",               # dependency / link (dashed)
    }
)

# A safe bare classDiagram identifier: letter/underscore start, then
# letters/digits/underscore, with an optional generic suffix `~...~` (List~int~).
_CLASS_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:~[^~]+~)?$")

# A relation line: LEFT [card] ARROW [card] RIGHT [: label]. The arrow group is
# any run of relation punctuation (2+ chars) so we can flag *invalid* arrows
# (`->`, `==>`, `=>`), not merely recognise the valid ones.
_REL_LINE_RE = re.compile(
    r"^(?P<left>\S+)\s+"
    r'(?:"[^"]*"\s+)?'  # optional cardinality on the left, e.g. "1"
    r"(?P<arrow>[-.<>|*o=~]{2,})"
    r'\s*(?:"[^"]*"\s+)?'  # optional cardinality on the right, e.g. "*"
    r"(?P<right>[^\s:]+)"
    r"\s*(?::\s*(?P<label>.*))?$"
)

# Non-classDiagram diagram headers we recognise — a ```mermaid block that opens
# with one of these is a different grammar, so the classDiagram lint stays quiet.
_KNOWN_DIAGRAM_HEADERS = (
    "flowchart", "graph", "sequenceDiagram", "stateDiagram", "erDiagram",
    "journey", "gantt", "pie", "mindmap", "timeline", "gitGraph",
    "quadrantChart", "requirementDiagram", "C4Context", "block-beta", "xychart",
)


def _lint_class_name(token: str) -> str | None:
    """Return an issue string if ``token`` is an unsafe classDiagram class name.

    Strips an optional bracketed/quoted display label (``Foo["Long name"]``), a
    ``:::cssClass`` style suffix, and a trailing block-opening ``{`` before
    validating the bare identifier — those parts may legally hold other chars.
    """
    bare = token.strip()
    bare = re.sub(r"\[.*\]$", "", bare).strip()  # drop ["display label"]
    bare = re.sub(r":::\w+$", "", bare).strip()  # drop :::cssStyle
    bare = bare.rstrip("{").strip()  # drop a trailing block-opening brace
    if not bare:
        return None
    if not _CLASS_NAME_RE.match(bare):
        return (
            f"class name {bare!r} has characters Mermaid rejects "
            "(allowed: letters, digits, underscore)"
        )
    return None


def _lint_classdiagram(block: str) -> list[str]:
    """Best-effort deterministic lint of one Mermaid ``classDiagram`` block.

    NOT a parser — it recognises the common valid line shapes and flags the
    AI-generated mistakes that make Mermaid 11 render a broken "bomb icon":
    a missing/foreign diagram header, illegal class-name characters, malformed
    relation arrows, and colon/paren danger inside members and labels. Returns
    human-readable issue strings (empty when the block looks clean).
    """
    issues: list[str] = []
    lines = block.splitlines()

    # The diagram header must be the first meaningful line.
    header = next(
        (ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("%%")),
        None,
    )
    if header is None:
        return ["mermaid block is empty"]
    if not re.match(r"^classDiagram(-v2)?\b", header):
        # A foreign header is fine (other diagram type); a header that matches
        # nothing known is genuinely broken.
        if not any(header.startswith(k) for k in _KNOWN_DIAGRAM_HEADERS):
            issues.append(
                f"first line {header!r} is not a valid diagram header "
                "(expected 'classDiagram')"
            )
        return issues

    in_members = False  # inside a `class X { ... }` member block
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue

        if in_members:
            if line.startswith("}"):
                in_members = False
                continue
            # Member line. A colon here breaks GitHub/mermaid property rendering
            # (ttl2mermaid deliberately emits `+x xsd_string`, never `+x: t`).
            if ":" in line:
                issues.append(
                    f"member {line!r} contains ':' — Mermaid property lines are "
                    "space-separated (`+name type`), not `+name: type`"
                )
            continue

        # Structural lines that are always fine.
        if re.match(r"^classDiagram(-v2)?\b", line):
            continue
        if re.match(r"^direction\s+(TB|BT|LR|RL)\b", line):
            continue
        if re.match(r"^direction\b", line):
            issues.append(f"invalid direction {line!r} (use TB, BT, LR or RL)")
            continue
        if line in ("{", "}"):
            continue
        if re.match(
            r"^(note\b|<<|click\b|link\b|style\b|cssClass\b|callback\b|namespace\b)",
            line,
        ):
            continue

        # class declaration (optionally opening a `{` member block).
        m = re.match(r"^class\s+(?P<rest>.+)$", line)
        if m:
            issue = _lint_class_name(m.group("rest").strip())
            if issue:
                issues.append(issue)
            if line.rstrip().endswith("{"):
                in_members = True
            continue

        # relation line?
        rel = _REL_LINE_RE.match(line)
        if rel:
            arrow = rel.group("arrow")
            if arrow not in _CLASS_ARROWS:
                issues.append(
                    f"relation {line!r} uses arrow {arrow!r}, not a valid "
                    "classDiagram arrow (e.g. -->, --|>, o--, ..>)"
                )
            for endpoint in (rel.group("left"), rel.group("right")):
                ep_issue = _lint_class_name(endpoint)
                if ep_issue:
                    issues.append(f"relation endpoint: {ep_issue}")
            label = (rel.group("label") or "").strip()
            # Colon-in-label is the FAIL check's job; here flag unquoted brackets.
            if any(c in label for c in "()[]{}") and '"' not in label:
                issues.append(
                    f"relation label {label!r} has unquoted brackets/parens — "
                    "wrap the label in double quotes"
                )
            continue

        # member shorthand: `ClassName : +member` (the colon is legal here).
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*:\s*.+$", line):
            continue

        # Nothing matched — a structural line Mermaid likely cannot parse.
        issues.append(f"unrecognized classDiagram line: {line!r}")

    opens, closes = block.count("{"), block.count("}")
    if opens != closes:
        issues.append(f"unbalanced braces: {opens} '{{' vs {closes} '}}'")
    return issues


# Fix recipes (T5): the section is §1 (class diagram); the evidence lists the
# exact offending labels/lines, so the recipes reference it.
_T5_FIX_COLON = (
    "In §1 (class diagram), rewrite each relation label listed in evidence WITHOUT the "
    "colon — Mermaid reserves `:` as the label delimiter, so use the plain local name "
    "(`Paper --> Sample : author`, never `Paper --> Sample : schema:author`; prefix→IRI "
    "mapping belongs in §2)."
)
_T5_FIX_LINT = (
    "In §1 (class diagram), fix each lint finding listed in evidence: class names may "
    "use only letters/digits/underscore; relation arrows must be valid classDiagram "
    "arrows (e.g. `-->`, `--|>`, `o--`, `..>`); member lines are space-separated "
    "(`+name type`, no colon); double-quote any label containing brackets/parens."
)
_T5_FIX_NO_BLOCK = (
    "Add a fenced ```mermaid code block containing a `classDiagram` to §1 (class "
    "diagram): declare each entity class (`class Name`) and the relations between them "
    "(`Child --> Parent : linkName`)."
)


def _check_t5_mermaid_escape(bundle: SchemaBundle) -> TrapResult:
    name = "Mermaid classDiagram syntax"
    if not bundle.diagram_md:
        return TrapResult("T5", name, "skip", "No diagram doc.")

    text = bundle.diagram_md.read_text(encoding="utf-8")
    blocks = _MERMAID_BLOCK.findall(text)
    if not blocks:
        return TrapResult(
            "T5",
            name,
            "warn",
            f"{bundle.diagram_md.name} has no ```mermaid fenced blocks.",
            fix=_T5_FIX_NO_BLOCK,
        )

    bad_labels: list[str] = []  # colon-in-relation-label → FAIL
    lint_issues: list[str] = []  # best-effort classDiagram lint → WARN
    label_count = 0
    for block in blocks:
        for label in _MERMAID_RELATION.findall(block):
            label_count += 1
            stripped = label.strip().strip('"')
            if ":" in stripped:
                bad_labels.append(stripped)
        lint_issues.extend(_lint_classdiagram(block))

    if bad_labels:
        return TrapResult(
            "T5",
            name,
            "fail",
            f"{len(bad_labels)} relation label(s) contain ':' — "
            "GitHub / mermaid.js renderer will fail.",
            evidence=[f"Bad label: {lbl!r}" for lbl in bad_labels]
            + [f"lint: {issue}" for issue in lint_issues],
            fix=_T5_FIX_COLON,
        )
    if lint_issues:
        return TrapResult(
            "T5",
            name,
            "warn",
            f"{len(lint_issues)} classDiagram lint issue(s) — the UI may render "
            "this diagram as a broken 'bomb icon' (ingest/promote unaffected).",
            evidence=lint_issues,
            fix=_T5_FIX_LINT,
        )
    return TrapResult(
        "T5",
        name,
        "pass",
        f"classDiagram lint clean; all {label_count} relation label(s) colon-free.",
    )


# ----------------------------------------------------------------------------
# Trap T6: fake sample_rdf_entries
# ----------------------------------------------------------------------------


# Match e.g. `sdr:paper/6`, `sdr:sample/6-113`, `sdr:curve/6-79-113`.
_ABOX_IRI = re.compile(r"sdr:([a-zA-Z_]+)/([A-Za-z0-9._/-]+)")

# Fix recipes (T6): examples must be verbatim copies of real source rows.
_T6_FIX_FAKE = (
    "In §7 (MIE YAML extras) `sample_rdf_entries`, replace each invented ID listed in "
    "evidence with a value copied VERBATIM from a real source row (open the source file "
    "and copy the key column's value character-for-character into the example IRI). "
    "Never invent example IDs."
)
_T6_FIX_MISSING = (
    "Add 1-3 `sample_rdf_entries` to §7 (MIE YAML extras), each with a `title:` and an "
    "`rdf:` block showing real triples — build every example IRI from values copied "
    "verbatim out of a real source row (never invented)."
)


def _check_t6_fake_iri(bundle: SchemaBundle) -> TrapResult:
    if not bundle.mie_yaml or not bundle.source_csvs:
        return TrapResult(
            "T6",
            "fake sample_rdf_entries",
            "skip",
            "Need both mie_yaml and source_csvs.",
        )
    data, err = _load_mie_yaml(bundle)
    if err:
        return _mie_broken("T6", "fake sample_rdf_entries", bundle, err)
    entries = (data.get("sample_rdf_entries") if isinstance(data, dict) else None) or []
    if not entries:
        return TrapResult(
            "T6",
            "fake sample_rdf_entries",
            "warn",
            "MIE has no sample_rdf_entries — humans / AI lose grounding examples.",
            fix=_T6_FIX_MISSING,
        )

    # Extract every sdr:<entity>/<key> IRI from every entry's `rdf:` block.
    found_iris: list[tuple[str, str]] = []
    for e in entries:
        rdf_text = e.get("rdf") or ""
        for entity, key in _ABOX_IRI.findall(rdf_text):
            found_iris.append((entity, key))

    if not found_iris:
        return TrapResult(
            "T6",
            "fake sample_rdf_entries",
            "warn",
            "sample_rdf_entries exist but contain no sdr:<entity>/<key> IRIs.",
            fix=_T6_FIX_MISSING,
        )

    # For each IRI, extract the first key component (the supposed primary ID)
    # and check that it appears somewhere in the source CSVs. Cheap and
    # catches the common "AI invented a SID" bug.
    csv_values: set[str] = set()
    for csv_path in bundle.source_csvs:
        for row in _stream_rows(csv_path):
            for v in row.values():
                if v and len(v) <= 64:  # avoid loading huge JSON literals
                    csv_values.add(v.strip())

    missing: list[str] = []
    for entity, composite_key in found_iris:
        head = composite_key.split("-", 1)[0].split("/", 1)[0]
        if head not in csv_values:
            missing.append(f"{entity}/{composite_key} → '{head}' not in any source CSV")

    if missing:
        return TrapResult(
            "T6",
            "fake sample_rdf_entries",
            "fail",
            f"{len(missing)} IRI(s) reference IDs absent from source CSVs (fake examples).",
            evidence=missing[:10],
            fix=_T6_FIX_FAKE,
        )
    return TrapResult(
        "T6",
        "fake sample_rdf_entries",
        "pass",
        f"All {len(found_iris)} sample IRI head(s) trace to real CSV values.",
    )


# ----------------------------------------------------------------------------
# Trap T7: Why / Alternatives / Trade-offs in architectural_notes
# ----------------------------------------------------------------------------


_WHY_KEYWORDS = ("why", "理由", "rationale")
_ALT_KEYWORDS = ("alternative", "alternative considered", "代替", "代案", "alt:")
_TRADEOFF_KEYWORDS = (
    "trade-off",
    "tradeoff",
    "limitation",
    "limit:",
    "cost:",
    "drawback",
    "代償",
    "限界",
)


def _t7_fix(missing: list[str]) -> str:
    """Recipe naming the exact missing rationale element(s) from the finding."""
    return (
        "In §7 (MIE YAML extras) `architectural_notes` (the summary of §5 Design "
        f"rationale), add the missing element(s): {', '.join(missing)}. For each major "
        "design decision write three lines — `Why:` (grounded in the source data), "
        "`Alternatives:` (what you considered and rejected), `Trade-offs:` (the cost of "
        "this choice and when to revisit it)."
    )


def _check_t7_rationale(bundle: SchemaBundle) -> TrapResult:
    if not bundle.mie_yaml:
        return TrapResult("T7", "Why / Alternatives / Trade-offs", "skip", "No MIE YAML.")

    data, err = _load_mie_yaml(bundle)
    if err:
        return _mie_broken("T7", "Why / Alternatives / Trade-offs", bundle, err)
    notes = data.get("architectural_notes") if isinstance(data, dict) else None
    if not notes:
        return TrapResult(
            "T7",
            "Why / Alternatives / Trade-offs",
            "warn",
            "architectural_notes is empty — future maintainers won't know 'why'.",
            fix=_t7_fix(["Why", "Alternatives", "Trade-offs"]),
        )

    text = notes.lower() if isinstance(notes, str) else str(notes).lower()
    has_why = any(k in text for k in _WHY_KEYWORDS)
    has_alt = any(k in text for k in _ALT_KEYWORDS)
    has_tradeoff = any(k in text for k in _TRADEOFF_KEYWORDS)

    sections = (("Why", has_why), ("Alternatives", has_alt), ("Trade-offs", has_tradeoff))
    missing = [name for name, present in sections if not present]
    if missing:
        return TrapResult(
            "T7",
            "Why / Alternatives / Trade-offs",
            "warn",
            f"architectural_notes lacks: {', '.join(missing)}.",
            evidence=[f"present: Why={has_why}, Alt={has_alt}, Trade-offs={has_tradeoff}"],
            fix=_t7_fix(missing),
        )
    return TrapResult(
        "T7",
        "Why / Alternatives / Trade-offs",
        "pass",
        "architectural_notes mentions Why + Alternatives + Trade-offs.",
    )


# ----------------------------------------------------------------------------
# Trap T8: hallucination test (opt-in)
# ----------------------------------------------------------------------------


def _check_t8_hallucination(
    bundle: SchemaBundle,
    *,
    llm: Any = None,
    nl_questions: list[str] | None = None,
) -> TrapResult:
    """Skip by default; needs an LLM client + curated NL questions.

    Real impl belongs in a separate module that wires :class:`asterism_step0.propose.LLMClient`
    to ``find_databases`` / ``run_sparql`` via the MCP transport. Here we
    just provide the slot so the CLI can opt in once the harness exists.
    """
    if llm is None:
        return TrapResult(
            "T8",
            "AI hallucination test",
            "skip",
            "Pass --llm to opt in. Requires API key + curated NL questions.",
        )
    return TrapResult(
        "T8",
        "AI hallucination test",
        "skip",
        "Not implemented yet — placeholder for Phase 3 #6 follow-up.",
    )


# ----------------------------------------------------------------------------
# Trap T9: RML closed-set (declarative substrate) — step0-rml-emission.md §5.2
# ----------------------------------------------------------------------------


# Fix recipes (T9): §9 may reference only the vetted Tier 0 closed set, and
# humans/AI never hand-write RML — the deterministic compiler owns that syntax.
_T9_FIX_CLOSED_SET = (
    "In §9 (mapping spec), `function:` / `transform:` may name ONLY vetted Tier 0 "
    "functions (the closed menu in the design instructions — bare names, no prefix). "
    "For each out-of-set function listed in evidence: pick the closest Tier 0 function, "
    "or drop the transform and map the column raw (`fallback: true` with the predicate "
    "renamed `...Raw`). Never invent or hand-write a function."
)
_T9_FIX_UNPARSEABLE = (
    "Do not hand-write RML/Turtle. Re-emit §9 as the YAML mapping spec "
    "(version/prefixes/maps) and let the deterministic compiler produce the RML."
)
_T9_FIX_FILE_MISSING = (
    "Re-run materialize so the §9 mapping spec is compiled to RML again, or point "
    "--rml at the compiled mapping file."
)


def _check_t9_rml_closed_set(
    bundle: SchemaBundle,
    *,
    allowed_fn_iris: set[str] | None = None,
) -> TrapResult:
    """The declarative RML must reference only vetted Tier 0 functions (no new code).

    Skips when no ``--rml`` is given, or when ``asterism`` (the canonical function
    registry) is not importable in this environment — like T8, enforcement is
    best-effort and never blocks merely because a dependency is absent.
    ``allowed_fn_iris`` may be injected (tests) instead of loading the registry.
    """
    name = "RML closed-set (Tier 0 only)"
    if bundle.rml_ttl is None:
        return TrapResult("T9", name, "skip", "No --rml provided.")
    if not bundle.rml_ttl.exists():
        return TrapResult(
            "T9",
            name,
            "fail",
            f"RML file not found: {bundle.rml_ttl}",
            fix=_T9_FIX_FILE_MISSING,
        )

    from .rml_check import load_registry_fn_iris, referenced_function_iris

    if allowed_fn_iris is None:
        try:
            allowed_fn_iris = load_registry_fn_iris()
        except ImportError:
            return TrapResult(
                "T9", name, "skip",
                "asterism (ingest) not importable — install it to enforce the closed set.",
            )

    rml_text = bundle.rml_ttl.read_text(encoding="utf-8")
    try:
        used = referenced_function_iris(rml_text)
    except ImportError:
        return TrapResult("T9", name, "skip", "rdflib not installed — cannot parse RML.")
    except Exception as exc:  # malformed Turtle
        return TrapResult(
            "T9",
            name,
            "fail",
            f"Could not parse RML Turtle: {exc}",
            fix=_T9_FIX_UNPARSEABLE,
        )

    violations = sorted(used - allowed_fn_iris)
    if violations:
        return TrapResult(
            "T9", name, "fail",
            f"{len(violations)} function IRI(s) outside the vetted Tier 0 set.",
            evidence=violations,
            fix=_T9_FIX_CLOSED_SET,
        )
    return TrapResult(
        "T9", name, "pass",
        f"All {len(used)} referenced function(s) are vetted Tier 0.",
    )


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------


def validate_schema(
    bundle: SchemaBundle,
    *,
    llm: Any = None,
    allowed_fn_iris: set[str] | None = None,
) -> ValidationReport:
    """Run all 9 trap checks against ``bundle``. Returns a :class:`ValidationReport`."""
    results = [
        _check_t1_uniqueness(bundle),
        _check_t2_bom(bundle),
        _check_t3_bnode_free(bundle),
        _check_t4_keywords(bundle),
        _check_t5_mermaid_escape(bundle),
        _check_t6_fake_iri(bundle),
        _check_t7_rationale(bundle),
        _check_t8_hallucination(bundle, llm=llm),
        _check_t9_rml_closed_set(bundle, allowed_fn_iris=allowed_fn_iris),
    ]
    bundle_paths = {
        "tbox_ttl": str(bundle.tbox_ttl) if bundle.tbox_ttl else "",
        "diagram_md": str(bundle.diagram_md) if bundle.diagram_md else "",
        "mie_yaml": str(bundle.mie_yaml) if bundle.mie_yaml else "",
        "ingester_py": str(bundle.ingester_py) if bundle.ingester_py else "",
        "rml_ttl": str(bundle.rml_ttl) if bundle.rml_ttl else "",
        "mapping_ir_yaml": str(bundle.mapping_ir_yaml) if bundle.mapping_ir_yaml else "",
        "source_csvs": ", ".join(str(p) for p in bundle.source_csvs),
    }
    return ValidationReport(results=results, bundle_paths=bundle_paths)


# ----------------------------------------------------------------------------
# Markdown rendering for CLI / CI logs
# ----------------------------------------------------------------------------


_STATUS_GLYPH = {"pass": "✓", "fail": "✗", "warn": "⚠", "skip": "·"}


def render_report(report: ValidationReport) -> str:
    lines: list[str] = []
    lines.append("# Schema validation report\n")
    lines.append("## Bundle\n")
    for k, v in report.bundle_paths.items():
        lines.append(f"- **{k}**: `{v or '(not provided)'}`")
    lines.append("\n## Trap results\n")
    lines.append("| # | Trap | Status | Detail |")
    lines.append("|---|---|---|---|")
    for r in report.results:
        glyph = _STATUS_GLYPH.get(r.status, "?")
        lines.append(f"| {r.trap_id} | {r.name} | {glyph} {r.status} | {r.detail} |")
    lines.append("")
    for r in report.results:
        if r.evidence:
            lines.append(f"### {r.trap_id} {r.name} — evidence")
            for line in r.evidence:
                lines.append(f"- {line}")
            lines.append("")
    # Repair recipes last (additive: the table above is unchanged). Fenced so a
    # multi-line recipe (e.g. T4's paste-ready YAML) survives Markdown rendering.
    for r in report.results:
        if r.fix:
            lines.append(f"### {r.trap_id} {r.name} — suggested fix")
            lines.append("")
            lines.append("```")
            lines.append(r.fix)
            lines.append("```")
            lines.append("")
    if report.all_passed:
        summary = "all checks passed"
    else:
        summary = f"{len(report.blocking_failures)} blocking failure(s)"
    lines.append(f"\n**Summary**: {summary} (exit code {report.exit_code()}).")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _build_arg_parser():  # type: ignore[no-untyped-def]
    import argparse

    p = argparse.ArgumentParser(
        prog="asterism-validate",
        description=(
            "Run the trap validator (T1-T9) on a schema bundle "
            "(TBox / diagram / MIE / ingester / RML / CSVs). "
            "Returns exit 0 if all required traps pass, else 1. Suitable for CI."
        ),
    )
    p.add_argument("--tbox", type=Path, default=None, help="TBox TTL path")
    p.add_argument("--diagram", type=Path, default=None, help="Mermaid diagram .md path")
    p.add_argument("--mie", type=Path, default=None, help="MIE YAML path")
    p.add_argument("--ingester", type=Path, default=None, help="Ingester .py path")
    p.add_argument(
        "--rml",
        type=Path,
        default=None,
        help="Declarative RML mapping .ttl path (T9 closed-set check).",
    )
    p.add_argument(
        "--mapping-ir",
        type=Path,
        default=None,
        help="§9 mapping spec .yaml path (improves T4's derived fix recipe).",
    )
    p.add_argument(
        "--csv", type=Path, action="append", default=[], help="Source CSV (repeatable)"
    )
    p.add_argument("--fk", action="append", default=[], help="FK column hint (repeatable)")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write Markdown report here. Defaults to stdout.",
    )
    return p


def _main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    bundle = SchemaBundle(
        tbox_ttl=args.tbox,
        diagram_md=args.diagram,
        mie_yaml=args.mie,
        ingester_py=args.ingester,
        rml_ttl=args.rml,
        mapping_ir_yaml=args.mapping_ir,
        source_csvs=args.csv,
        fk_hint_columns=args.fk,
    )
    report = validate_schema(bundle)
    md = render_report(report)
    if args.output is None:
        print(md)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md, encoding="utf-8")
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(_main())
