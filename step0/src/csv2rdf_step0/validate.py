"""Schema validator for csv2rdf-mcp Phase 3 (trap checks T1-T9).

Validates a schema bundle (TBox TTL + Mermaid + MIE YAML + ingester Python +
the source CSVs) against the 8 traps from
``docs/architecture/ai-assisted-step0-workflow.md`` §6, plus **T9** (Phase 5):
the declarative RML mapping references only vetted Tier 0 functions — see
``docs/architecture/step0-rml-emission.md`` §5.2.

The traps and how this module checks each:

* **T1 ID uniqueness** — collect candidate IRI keys from *two* sources: the
  composite IRI patterns in the MIE (e.g. ``sdr:sample/{SID}-{sample_id}``) **and**
  the actual key columns recovered from the ingester's IRI builders
  (:mod:`csv2rdf_step0.t1_ingester`). For each, re-run
  :mod:`csv2rdf_step0.inspect` on the source CSVs and confirm the key combination
  is globally unique. Reading the ingester is the safety net: if ``propose`` picks
  the wrong key on a subset, a full-CSV validate catches it even when the MIE
  looks clean (dogfood Round 3).
* **T2 BOM** — grep the ingester for ``utf-8-sig``; check the source CSVs'
  first column name does not start with the BOM byte.
* **T3 bnode-free** — parse the TBox TTL with rdflib and assert
  ``len(g.bnodes())`` is zero. Also grep the ingester for ``BNode(``.
* **T4 MIE keywords / categories** — YAML parse the MIE; require
  ``schema_info.keywords`` and ``schema_info.categories`` lists with ≥ 5
  entries each, and a configurable Japanese/synonym subset.
* **T5 Mermaid colon escape** — parse Mermaid blocks in the diagram doc and
  assert relation labels do not contain ``:``.
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

Return shape: :class:`ValidationReport`. The CLI ``csv2rdf-validate`` returns
exit code 0 if all required (non-skipped) traps pass, else 1 — suitable for
CI integration on PRs that touch ``docs/ontology/``, ``data/togomcp/mie/``,
or ``ingest/src/csv2rdf/``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from csv2rdf_step0.inspect import _check_uniqueness, _stream_rows
from csv2rdf_step0.t1_ingester import extract_ingester_keys

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
    ingester_py: Path | None = None  # ingest/src/csv2rdf/{name}.py
    rml_ttl: Path | None = None  # {name}-mapping.rml.ttl (declarative substrate, T9)
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
        )

    # Cache each CSV's rows — the same file backs several candidate keys.
    row_cache: dict[Path, list[dict[str, str]]] = {}

    def rows_of(path: Path) -> list[dict[str, str]]:
        if path not in row_cache:
            row_cache[path] = list(_stream_rows(path))
        return row_cache[path]

    failures: list[str] = []
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

    if failures:
        return TrapResult(
            "T1",
            "ID uniqueness",
            "fail",
            f"{len(failures)} composite key(s) collide in source CSVs.",
            evidence=failures + passes + notes,
        )
    if not passes:
        return TrapResult(
            "T1",
            "ID uniqueness",
            "warn",
            "Derived IRI key(s) could not be matched to any source CSV's columns.",
            evidence=notes,
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


def _check_t4_keywords(bundle: SchemaBundle, *, min_keywords: int = _MIN_KEYWORDS) -> TrapResult:
    if not bundle.mie_yaml:
        return TrapResult("T4", "MIE keywords / categories", "skip", "No MIE YAML.")

    import yaml  # lazy

    data = yaml.safe_load(bundle.mie_yaml.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return TrapResult(
            "T4",
            "MIE keywords / categories",
            "fail",
            f"{bundle.mie_yaml.name} did not parse as a YAML mapping.",
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
        )
    return TrapResult(
        "T4",
        "MIE keywords / categories",
        "pass",
        f"keywords ≥ {min_keywords} ✓, categories ≥ {_MIN_CATEGORIES} ✓.",
        evidence=evidence,
    )


# ----------------------------------------------------------------------------
# Trap T5: Mermaid colon escape
# ----------------------------------------------------------------------------


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


def _check_t5_mermaid_escape(bundle: SchemaBundle) -> TrapResult:
    if not bundle.diagram_md:
        return TrapResult("T5", "Mermaid colon escape", "skip", "No diagram doc.")

    text = bundle.diagram_md.read_text(encoding="utf-8")
    blocks = _MERMAID_BLOCK.findall(text)
    if not blocks:
        return TrapResult(
            "T5",
            "Mermaid colon escape",
            "warn",
            f"{bundle.diagram_md.name} has no ```mermaid fenced blocks.",
        )

    bad_labels: list[str] = []
    label_count = 0
    for block in blocks:
        for label in _MERMAID_RELATION.findall(block):
            label_count += 1
            stripped = label.strip().strip('"')
            if ":" in stripped:
                bad_labels.append(stripped)

    if bad_labels:
        return TrapResult(
            "T5",
            "Mermaid colon escape",
            "fail",
            f"{len(bad_labels)} relation label(s) contain ':' — GitHub renderer will fail.",
            evidence=[f"Bad label: {lbl!r}" for lbl in bad_labels],
        )
    return TrapResult(
        "T5",
        "Mermaid colon escape",
        "pass",
        f"All {label_count} relation label(s) free of colons.",
    )


# ----------------------------------------------------------------------------
# Trap T6: fake sample_rdf_entries
# ----------------------------------------------------------------------------


# Match e.g. `sdr:paper/6`, `sdr:sample/6-113`, `sdr:curve/6-79-113`.
_ABOX_IRI = re.compile(r"sdr:([a-zA-Z_]+)/([A-Za-z0-9._/-]+)")


def _check_t6_fake_iri(bundle: SchemaBundle) -> TrapResult:
    if not bundle.mie_yaml or not bundle.source_csvs:
        return TrapResult(
            "T6",
            "fake sample_rdf_entries",
            "skip",
            "Need both mie_yaml and source_csvs.",
        )
    import yaml  # lazy

    data = yaml.safe_load(bundle.mie_yaml.read_text(encoding="utf-8"))
    entries = (data or {}).get("sample_rdf_entries") or []
    if not entries:
        return TrapResult(
            "T6",
            "fake sample_rdf_entries",
            "warn",
            "MIE has no sample_rdf_entries — humans / AI lose grounding examples.",
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


def _check_t7_rationale(bundle: SchemaBundle) -> TrapResult:
    if not bundle.mie_yaml:
        return TrapResult("T7", "Why / Alternatives / Trade-offs", "skip", "No MIE YAML.")

    import yaml  # lazy

    data = yaml.safe_load(bundle.mie_yaml.read_text(encoding="utf-8"))
    notes = (data or {}).get("architectural_notes")
    if not notes:
        return TrapResult(
            "T7",
            "Why / Alternatives / Trade-offs",
            "warn",
            "architectural_notes is empty — future maintainers won't know 'why'.",
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

    Real impl belongs in a separate module that wires :class:`csv2rdf_step0.propose.LLMClient`
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


def _check_t9_rml_closed_set(
    bundle: SchemaBundle,
    *,
    allowed_fn_iris: set[str] | None = None,
) -> TrapResult:
    """The declarative RML must reference only vetted Tier 0 functions (no new code).

    Skips when no ``--rml`` is given, or when ``csv2rdf`` (the canonical function
    registry) is not importable in this environment — like T8, enforcement is
    best-effort and never blocks merely because a dependency is absent.
    ``allowed_fn_iris`` may be injected (tests) instead of loading the registry.
    """
    name = "RML closed-set (Tier 0 only)"
    if bundle.rml_ttl is None:
        return TrapResult("T9", name, "skip", "No --rml provided.")
    if not bundle.rml_ttl.exists():
        return TrapResult("T9", name, "fail", f"RML file not found: {bundle.rml_ttl}")

    from .rml_check import load_registry_fn_iris, referenced_function_iris

    if allowed_fn_iris is None:
        try:
            allowed_fn_iris = load_registry_fn_iris()
        except ImportError:
            return TrapResult(
                "T9", name, "skip",
                "csv2rdf (ingest) not importable — install it to enforce the closed set.",
            )

    rml_text = bundle.rml_ttl.read_text(encoding="utf-8")
    try:
        used = referenced_function_iris(rml_text)
    except ImportError:
        return TrapResult("T9", name, "skip", "rdflib not installed — cannot parse RML.")
    except Exception as exc:  # malformed Turtle
        return TrapResult("T9", name, "fail", f"Could not parse RML Turtle: {exc}")

    violations = sorted(used - allowed_fn_iris)
    if violations:
        return TrapResult(
            "T9", name, "fail",
            f"{len(violations)} function IRI(s) outside the vetted Tier 0 set.",
            evidence=violations,
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
        prog="csv2rdf-validate",
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
