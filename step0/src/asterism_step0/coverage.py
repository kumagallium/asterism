"""Track C — Tier 0 *coverage* measurement over a diverse corpus.

The declarative substrate routes "real computation" (date cleaning, array
aggregation, …) through a **closed, once-vetted** function library
(``asterism.functions``, Tier 0). The open question for onboarding *arbitrary*
datasets is: **is that small head of functions "enough"?** This module turns
that opinion into a measured gate.

It depends only on the project's **stable** interfaces — never on the internals
of ``functions.py`` or the ``propose`` system prompt (those are evolved by the
parallel Track A / Track B sessions; coupling here would create merge
conflicts). Specifically:

* :mod:`asterism_step0.inspect` — deterministic column inspection (the demand
  sniffer reads :class:`~asterism_step0.inspect.ColumnSummary`).
* :mod:`asterism_step0.materialize` — extracts the ``turtle`` RML block from a
  proposal Markdown (``materialize_schema(write=False).rml_ttl``).
* :mod:`asterism_step0.rml_check` — ``referenced_function_iris`` /
  ``closed_set_violations`` / the ``FN_NAMESPACE`` constant. The allowed IRI
  set is derived from ``asterism.functions.REGISTRY`` via
  ``load_registry_fn_iris`` when the ingest package is importable; the analyzer
  itself takes the allowed set as a **parameter** so it stays a pure function
  (testable without the ingest package or any LLM — mirroring rml_check's
  design).

What we measure, per dataset, from the proposal's RML mapping:

1. **`…Raw` fallback rate** — of the columns the proposal treated as *needing
   computation* (a vetted function **or** a ``…Raw`` fallback), how many fell
   back to the raw literal because no function fit. This is the headline number
   the "enough" gate is defined on.
2. **T9 misses** — function IRIs the proposal *referenced* that are **not** in
   the closed set. The ``propose`` prompt forbids inventing functions, so a
   compliant model rarely produces these; when it does, it is a strong demand
   signal (a Tier 1 → Tier 0 promotion candidate) *and* a guard-rail check.
3. **Per-function usage** — how many times each vetted function was used across
   the corpus (which head functions actually earn their place).

Because a strict prompt suppresses literal T9 misses, (1)+(2) alone can hide
demand for *scalar* transforms (epoch→date, bool/enum, DOI normalisation) that
a compliant model simply maps to a direct literal. So we add a conservative,
clearly-secondary **demand sniffer** (:func:`sniff_demand`) over the inspection
that categorises columns by the transform they appear to need and cross-checks
how the proposal actually handled each. This is heuristic and never feeds the
gate; it exists to point Track A at the highest-demand missing functions.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from asterism_step0.inspect import ColumnSummary, SourceInspection, inspect_source_set
from asterism_step0.materialize import materialize_schema
from asterism_step0.rml_check import FN_NAMESPACE

if TYPE_CHECKING:
    import rdflib

# ----------------------------------------------------------------------------
# RML / FnO vocabulary (mirrors the `propose` §9 prompt; kept local so this
# module does not import the prompt text — Track A/B evolve that independently)
# ----------------------------------------------------------------------------

_RR = "http://www.w3.org/ns/r2rml#"
_RML = "http://semweb.mmlab.be/ns/rml#"
_RMLF = "http://w3id.org/rml/"

_RR_OBJECT_MAP = _RR + "objectMap"
_RR_PREDICATE = _RR + "predicate"
_RR_TEMPLATE = _RR + "template"
_RR_SUBJECT_MAP = _RR + "subjectMap"
_RML_REFERENCE = _RML + "reference"
_RMLF_FUNCTION_EXECUTION = _RMLF + "functionExecution"
_RMLF_FUNCTION = _RMLF + "function"

# ``# fallback: <col> not expanded`` comment the propose prompt mandates on a
# `…Raw` fallback. rdflib drops comments, so we count them on the raw text.
_FALLBACK_COMMENT = re.compile(r"#\s*fallback\b", re.IGNORECASE)
# ``rr:template "…/{col}-{other}"`` — pull the ``{col}`` reference names out.
_TEMPLATE_REF = re.compile(r"\{([^{}]+)\}")


def _local_name(iri: str) -> str:
    """Return the local part of an IRI (after the last ``#`` or ``/``)."""
    for sep in ("#", "/"):
        if sep in iri:
            iri = iri.rsplit(sep, 1)[1]
    return iri


def _is_raw_predicate(predicate_iri: str) -> bool:
    """True if the predicate local name follows the ``…Raw`` fallback convention.

    We require a CamelCase ``Raw`` suffix (``authorsRaw``) or an explicit
    ``_raw`` suffix, not a bare substring, so predicates like ``drawTool`` or
    ``rawScore`` do not false-positive.
    """
    lname = _local_name(predicate_iri)
    return (lname.endswith("Raw") and len(lname) > 3) or lname.lower().endswith("_raw")


# ----------------------------------------------------------------------------
# Per-object-map classification
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ObjectMapInfo:
    """One ``rr:objectMap`` and how it produces its object."""

    predicate: str  # predicate IRI (may be "" if not discoverable)
    kind: str  # "function" | "raw" | "direct"
    function_iri: str | None  # set when kind == "function"
    references: tuple[str, ...]  # column / dot-path selectors this map reads


def _classify_object_maps(g: rdflib.Graph) -> list[ObjectMapInfo]:
    """Walk an rdflib graph of an RML mapping and classify every object map."""
    import rdflib

    rr_om = rdflib.URIRef(_RR_OBJECT_MAP)
    rr_pred = rdflib.URIRef(_RR_PREDICATE)
    rmlf_fe = rdflib.URIRef(_RMLF_FUNCTION_EXECUTION)
    rmlf_fn = rdflib.URIRef(_RMLF_FUNCTION)

    out: list[ObjectMapInfo] = []
    for pom, _, om in g.triples((None, rr_om, None)):
        predicate = next((str(p) for p in g.objects(pom, rr_pred)), "")
        fe = next(iter(g.objects(om, rmlf_fe)), None)
        if fe is not None:
            fn_iri = next((str(f) for f in g.objects(fe, rmlf_fn)), None)
            refs = tuple(sorted({str(r) for r in _function_references(g, fe)}))
            out.append(ObjectMapInfo(predicate, "function", fn_iri, refs))
            continue
        refs = _direct_references(g, om)
        kind = "raw" if _is_raw_predicate(predicate) else "direct"
        out.append(ObjectMapInfo(predicate, kind, None, refs))
    return out


def _function_references(g: rdflib.Graph, fe: rdflib.term.Node) -> list[str]:
    """Collect every ``rml:reference`` reachable under a functionExecution node."""
    import rdflib

    rml_ref = rdflib.URIRef(_RML_REFERENCE)
    # functionExecution → input → inputValueMap → reference. Rather than walk
    # each intermediate predicate (names vary across RML-FnO drafts), collect
    # any reference literal in the bnode closure rooted at ``fe``.
    refs: list[str] = []
    stack: list[rdflib.term.Node] = [fe]
    seen: set[rdflib.term.Node] = set()
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        for ref in g.objects(node, rml_ref):
            refs.append(str(ref))
        for _, _, o in g.triples((node, None, None)):
            if isinstance(o, rdflib.BNode):
                stack.append(o)
    return refs


def _direct_references(g: rdflib.Graph, om: rdflib.term.Node) -> tuple[str, ...]:
    """References of a non-function object map: ``rml:reference`` or template vars."""
    import rdflib

    rml_ref = rdflib.URIRef(_RML_REFERENCE)
    rr_tmpl = rdflib.URIRef(_RR_TEMPLATE)
    refs = {str(r) for r in g.objects(om, rml_ref)}
    for tmpl in g.objects(om, rr_tmpl):
        refs.update(_TEMPLATE_REF.findall(str(tmpl)))
    return tuple(sorted(refs))


def _subject_references(g: rdflib.Graph) -> set[str]:
    """Column names used in subject-map IRI templates (treated as direct use)."""
    import rdflib

    rr_subj = rdflib.URIRef(_RR_SUBJECT_MAP)
    rr_tmpl = rdflib.URIRef(_RR_TEMPLATE)
    refs: set[str] = set()
    for _, _, sm in g.triples((None, rr_subj, None)):
        for tmpl in g.objects(sm, rr_tmpl):
            refs.update(_TEMPLATE_REF.findall(str(tmpl)))
    return refs


# ----------------------------------------------------------------------------
# Demand sniffer (heuristic, secondary — never feeds the gate)
# ----------------------------------------------------------------------------

# Conservative per-cell patterns. Each maps a *category* to a predicate over a
# column's (name, inferred_type, sample_values). The category names double as
# the suggested Track A function family.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_MESSY_DATE = re.compile(
    r"^(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"  # 12/06/1998
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"  # Jun 12 1998 / June 12, 1998
    r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})$"  # 12 Jun 1998
)
_EPOCH_MILLIS = re.compile(r"^\d{12,13}$")
_GROUPED_OR_CURRENCY = re.compile(r"^[^\d-]{0,3}-?\d{1,3}(,\d{3})+(\.\d+)?$|^[$€£¥]\s?-?\d")
_PERCENT = re.compile(r"^-?\d+(\.\d+)?\s*%$")
# Range separator: ASCII hyphen, EN DASH, EM DASH, or tilde. The dashes are
# `re` unicode escapes so the source stays ASCII (no RUF001 confusable char).
_RANGE = re.compile(r"^-?\d+(\.\d+)?\s*[-\u2013\u2014~]\s*-?\d+(\.\d+)?$")
# value + unit in one cell, e.g. "300 K", "12.5 mm/s". A space between the
# number and the unit is required so ID codes like "1000chmg" do not match.
_VALUE_UNIT = re.compile(r"^-?\d+(\.\d+)?\s+[A-Za-zµ°%/]{1,6}[A-Za-z0-9µ°/^*-]*$")
_BOOLEANISH = {
    "yes", "no", "y", "n", "true", "false", "t", "f",
    "male", "female", "m", "0", "1",
}
_DOI = re.compile(r"^(https?://(dx\.)?doi\.org/)?10\.\d{3,9}/\S+$", re.IGNORECASE)
_URL = re.compile(r"^https?://", re.IGNORECASE)
_UNIT_IN_NAME = re.compile(r"\((?:mm|cm|m|kg|g|mg|ml|l|k|°c|°f|ppm|%|s|hz)\)|_(?:mm|cm|kg|g|ppm)\b",
                           re.IGNORECASE)


@dataclass(frozen=True)
class DemandHit:
    """One column that appears to need a transform, and how the proposal handled it."""

    column: str
    category: str  # messy_date, epoch_millis, …
    handled_as: str  # "function:<name>" | "raw" | "direct" | "unmapped"


def _fraction_match(samples: list[str], pattern: re.Pattern[str]) -> float:
    vals = [s.strip() for s in samples if s.strip()]
    if not vals:
        return 0.0
    return sum(1 for v in vals if pattern.search(v)) / len(vals)


def _categorize_column(col: ColumnSummary) -> str | None:
    """Best-effort single demand category for a column (None ⇒ no transform needed)."""
    samples = col.sample_values
    name = col.name
    # Multi-valued / nested first — these are the canonical `…Raw` fallback case.
    if col.inferred_type in ("json-array", "json-object"):
        return "multivalue_or_json"
    # A column whose name advertises a unit (value lives clean, unit is constant).
    if _UNIT_IN_NAME.search(name) and col.inferred_type in ("xsd:double", "xsd:integer"):
        return "value_with_unit_name"
    # Pattern votes over the (few) sample values; require a clear majority.
    for category, pattern in (
        ("epoch_millis", _EPOCH_MILLIS),
        ("messy_date", _MESSY_DATE),
        ("percent", _PERCENT),
        ("numeric_range", _RANGE),
        ("grouped_or_currency", _GROUPED_OR_CURRENCY),
        ("value_with_unit", _VALUE_UNIT),
        ("doi", _DOI),
        ("url", _URL),
    ):
        # epoch_millis only meaningful when the inspector typed it as integer.
        if category == "epoch_millis" and col.inferred_type != "xsd:integer":
            continue
        if _fraction_match(samples, pattern) >= 0.6:
            return category
    # Boolean / small enum (treated as bool/enum-normalisation demand).
    nonempty = [s.strip().lower() for s in samples if s.strip()]
    if nonempty and all(v in _BOOLEANISH for v in nonempty) and col.unique_count <= 3:
        return "boolean"
    return None


def sniff_demand(
    inspections: list[SourceInspection], column_handling: dict[str, str]
) -> list[DemandHit]:
    """Categorise computation-needing columns and cross-check the proposal.

    ``column_handling`` maps a column/dot-path selector to ``"function:<name>"``
    / ``"raw"`` / ``"direct"`` (built from the RML). A column with no entry is
    reported as ``"unmapped"`` (the proposal ignored it). Heuristic and
    secondary — see the module docstring.
    """
    hits: list[DemandHit] = []
    for ins in inspections:
        for col in ins.columns:
            category = _categorize_column(col)
            if category is None:
                continue
            handled = column_handling.get(col.name, "unmapped")
            hits.append(DemandHit(column=col.name, category=category, handled_as=handled))
    return hits


# ----------------------------------------------------------------------------
# Per-dataset coverage
# ----------------------------------------------------------------------------


@dataclass
class DatasetCoverage:
    """Coverage metrics for one dataset's proposal."""

    dataset: str
    has_rml: bool
    total_object_maps: int = 0
    function_maps: int = 0
    raw_fallbacks: int = 0
    direct_maps: int = 0
    fallback_comments: int = 0
    function_usage: Counter[str] = field(default_factory=Counter)  # local name → count
    t9_misses: Counter[str] = field(default_factory=Counter)  # out-of-set fn IRI → count
    t9_checked: bool = True  # False ⇒ allowed set unavailable, misses not computed
    demand: list[DemandHit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def computed_columns(self) -> int:
        """Object maps that needed real computation: vetted function OR `…Raw`."""
        return self.function_maps + self.raw_fallbacks

    @property
    def raw_rate(self) -> float | None:
        """Fraction of computed columns that fell back to a raw literal."""
        denom = self.computed_columns
        return None if denom == 0 else self.raw_fallbacks / denom


def analyze_proposal(
    dataset: str,
    proposal_md: str,
    inspections: list[SourceInspection],
    allowed_fn_iris: set[str] | None,
) -> DatasetCoverage:
    """Analyze one proposal's RML mapping against a dataset's inspection.

    Pure function: pass ``allowed_fn_iris=None`` to skip the T9-miss check (the
    other metrics still compute). Returns a :class:`DatasetCoverage`.
    """
    import rdflib

    rml_ttl = materialize_schema(
        proposal_md, output_dir=".", dataset_name=dataset, write=False
    ).rml_ttl
    cov = DatasetCoverage(dataset=dataset, has_rml=rml_ttl is not None)
    if rml_ttl is None:
        cov.warnings.append("proposal has no RML (§9 turtle) block — skipped")
        return cov

    try:
        g = rdflib.Graph()
        g.parse(data=rml_ttl, format="turtle")
    except Exception as exc:  # a malformed mapping is data, not a crash
        cov.warnings.append(f"RML did not parse as Turtle: {exc}")
        cov.has_rml = False
        return cov

    object_maps = _classify_object_maps(g)
    cov.total_object_maps = len(object_maps)
    cov.fallback_comments = len(_FALLBACK_COMMENT.findall(rml_ttl))

    column_handling: dict[str, str] = {}
    for om in object_maps:
        if om.kind == "function":
            cov.function_maps += 1
            handling = f"function:{_local_name(om.function_iri)}" if om.function_iri else "function"
        elif om.kind == "raw":
            cov.raw_fallbacks += 1
            handling = "raw"
        else:
            cov.direct_maps += 1
            handling = "direct"
        for ref in om.references:
            # A function/raw handling outranks a plain direct one for the same column.
            if column_handling.get(ref, "direct") == "direct" or handling != "direct":
                column_handling[ref] = handling
    for ref in _subject_references(g):
        column_handling.setdefault(ref, "direct")

    # Per-function usage + T9 misses over EVERY referenced function (object maps,
    # subject templates, …), so a `fn:slug` used to build an IRI also counts.
    fn_pred = rdflib.URIRef(_RMLF_FUNCTION)
    referenced = Counter(str(o) for _, _, o in g.triples((None, fn_pred, None)))
    if allowed_fn_iris is None:
        cov.t9_checked = False
        cov.warnings.append("allowed function set unavailable — T9-miss check skipped")
        for fn_iri, n in referenced.items():
            cov.function_usage[_local_name(fn_iri)] += n
    else:
        for fn_iri, n in referenced.items():
            if fn_iri in allowed_fn_iris:
                cov.function_usage[_local_name(fn_iri)] += n
            else:
                cov.t9_misses[fn_iri] += n

    cov.demand = sniff_demand(inspections, column_handling)
    return cov


# ----------------------------------------------------------------------------
# Corpus aggregate
# ----------------------------------------------------------------------------


@dataclass
class CorpusReport:
    """Aggregate coverage across a corpus of datasets."""

    datasets: list[DatasetCoverage]
    raw_rate_gate: float

    @property
    def datasets_with_rml(self) -> list[DatasetCoverage]:
        return [d for d in self.datasets if d.has_rml]

    @property
    def total_computed_columns(self) -> int:
        return sum(d.computed_columns for d in self.datasets_with_rml)

    @property
    def total_raw_fallbacks(self) -> int:
        return sum(d.raw_fallbacks for d in self.datasets_with_rml)

    @property
    def corpus_raw_rate(self) -> float | None:
        """Pooled `…Raw` rate: total raw fallbacks / total computed columns."""
        denom = self.total_computed_columns
        return None if denom == 0 else self.total_raw_fallbacks / denom

    @property
    def function_usage(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for d in self.datasets_with_rml:
            total.update(d.function_usage)
        return total

    @property
    def t9_misses(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for d in self.datasets_with_rml:
            total.update(d.t9_misses)
        return total

    @property
    def demand_by_category(self) -> dict[str, Counter[str]]:
        """category → Counter of how columns in that category were handled."""
        out: dict[str, Counter[str]] = {}
        for d in self.datasets_with_rml:
            for hit in d.demand:
                bucket = hit.handled_as
                if bucket.startswith("function:"):
                    bucket = "function"
                out.setdefault(hit.category, Counter())[bucket] += 1
        return out

    @property
    def gate_passes(self) -> bool | None:
        rate = self.corpus_raw_rate
        return None if rate is None else rate < self.raw_rate_gate


# Initial "enough" gate. Of the columns a proposal treats as needing real
# computation, fewer than this fraction should fall back to a raw literal.
# Rationale + how to recalibrate: experiments/coverage-corpus/README.md.
DEFAULT_RAW_RATE_GATE = 0.15


def aggregate(
    coverages: list[DatasetCoverage], *, raw_rate_gate: float = DEFAULT_RAW_RATE_GATE
) -> CorpusReport:
    return CorpusReport(datasets=list(coverages), raw_rate_gate=raw_rate_gate)


# ----------------------------------------------------------------------------
# Rendering (Markdown + JSON-able dict)
# ----------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def report_to_dict(report: CorpusReport) -> dict[str, object]:
    """A JSON-serialisable view of the corpus report."""
    return {
        "raw_rate_gate": report.raw_rate_gate,
        "corpus_raw_rate": report.corpus_raw_rate,
        "gate_passes": report.gate_passes,
        "total_computed_columns": report.total_computed_columns,
        "total_raw_fallbacks": report.total_raw_fallbacks,
        "function_usage": dict(report.function_usage.most_common()),
        "t9_misses": dict(report.t9_misses.most_common()),
        "demand_by_category": {
            cat: dict(counts.most_common()) for cat, counts in report.demand_by_category.items()
        },
        "datasets": [
            {
                "dataset": d.dataset,
                "has_rml": d.has_rml,
                "total_object_maps": d.total_object_maps,
                "function_maps": d.function_maps,
                "raw_fallbacks": d.raw_fallbacks,
                "direct_maps": d.direct_maps,
                "fallback_comments": d.fallback_comments,
                "computed_columns": d.computed_columns,
                "raw_rate": d.raw_rate,
                "function_usage": dict(d.function_usage.most_common()),
                "t9_misses": dict(d.t9_misses.most_common()),
                "t9_checked": d.t9_checked,
                "warnings": d.warnings,
            }
            for d in report.datasets
        ],
    }


def render_report_md(report: CorpusReport) -> str:
    """Render the corpus report as Markdown."""
    lines: list[str] = []
    gate = report.raw_rate_gate
    rate = report.corpus_raw_rate
    verdict = (
        "n/a (no computed columns)"
        if report.gate_passes is None
        else ("✅ PASS" if report.gate_passes else "❌ FAIL")
    )
    lines.append("# Tier 0 coverage report")
    lines.append("")
    lines.append(
        f"**Gate** — corpus `…Raw` rate {_pct(rate)} "
        f"{'<' if (rate is not None and rate < gate) else '≥'} target {_pct(gate)} → {verdict}"
    )
    lines.append("")
    lines.append(
        f"- Datasets analysed: {len(report.datasets_with_rml)} "
        f"(of {len(report.datasets)} in corpus)"
    )
    lines.append(
        f"- Computed columns (function + `…Raw`): {report.total_computed_columns}; "
        f"of those, raw fallbacks: {report.total_raw_fallbacks}"
    )
    lines.append("")

    lines.append("## Per-dataset")
    lines.append("")
    lines.append("| dataset | object maps | function | raw | direct | computed | `…Raw` rate |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|")
    for d in report.datasets:
        if not d.has_rml:
            lines.append(f"| {d.dataset} | — | — | — | — | — | (no RML) |")
            continue
        lines.append(
            f"| {d.dataset} | {d.total_object_maps} | {d.function_maps} | {d.raw_fallbacks} "
            f"| {d.direct_maps} | {d.computed_columns} | {_pct(d.raw_rate)} |"
        )
    lines.append("")

    lines.append("## Per-function usage (which head functions earn their place)")
    lines.append("")
    usage = report.function_usage
    if usage:
        lines.append("| function | uses |")
        lines.append("|---|--:|")
        for name, n in usage.most_common():
            lines.append(f"| `fn:{name}` | {n} |")
    else:
        lines.append("_(no vetted functions used across the corpus)_")
    lines.append("")

    lines.append("## T9 misses (referenced-but-undefined functions = demand signal)")
    lines.append("")
    misses = report.t9_misses
    if misses:
        lines.append("| referenced function IRI | count |")
        lines.append("|---|--:|")
        for iri, n in misses.most_common():
            short = iri.replace(FN_NAMESPACE, "fn:")
            lines.append(f"| `{short}` | {n} |")
    else:
        lines.append("_(none — every referenced function is in the closed set)_")
    lines.append("")

    lines.append("## Demand by category (heuristic — does NOT feed the gate)")
    lines.append("")
    lines.append(
        "Columns whose values look like they need a transform, and how the "
        "proposal actually handled them. `direct`/`unmapped` rows in a category "
        "with no covering function are the strongest Track A signals."
    )
    lines.append("")
    demand = report.demand_by_category
    if demand:
        lines.append("| category | function | raw | direct | unmapped |")
        lines.append("|---|--:|--:|--:|--:|")
        for cat in sorted(demand):
            c = demand[cat]
            lines.append(
                f"| {cat} | {c.get('function', 0)} | {c.get('raw', 0)} "
                f"| {c.get('direct', 0)} | {c.get('unmapped', 0)} |"
            )
    else:
        lines.append("_(no computation-needing columns detected by the sniffer)_")
    lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Corpus driver — prepare prompts / build the report
# ----------------------------------------------------------------------------

_SOURCE_SUFFIXES = (".csv", ".json", ".geojson")


def _dataset_dirs(corpus: Path) -> list[Path]:
    datasets = corpus / "datasets"
    base = datasets if datasets.is_dir() else corpus
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "source").is_dir())


def _source_files(ds_dir: Path) -> list[Path]:
    return sorted(
        p for p in (ds_dir / "source").iterdir() if p.suffix.lower() in _SOURCE_SUFFIXES
    )


def _domain_hint(ds_dir: Path) -> str:
    dom = ds_dir / "domain.md"
    return dom.read_text(encoding="utf-8") if dom.is_file() else f"Dataset: {ds_dir.name}"


def prepare_prompts(corpus: Path, runs: Path) -> list[str]:
    """For each dataset, write ``runs/<id>/inspection.md`` and ``prompt.md``.

    ``prompt.md`` is the exact user message :func:`propose_schema` would send —
    so the proposals can be generated by *any* model (the subscription agent,
    or the Anthropic API) and dropped in as ``runs/<id>/proposal.md``.
    """
    from asterism_step0.inspect import render_markdown
    from asterism_step0.propose import SYSTEM_PROMPT

    prepared: list[str] = []
    for ds_dir in _dataset_dirs(corpus):
        sources = _source_files(ds_dir)
        if not sources:
            continue
        inspections, fks = inspect_source_set(sources)
        inspection_md = render_markdown(inspections, fks)
        domain = _domain_hint(ds_dir)
        user_message = (
            f"# Source inspection\n\n{inspection_md}\n\n# Domain context\n\n{domain.strip()}\n"
        )
        out = runs / ds_dir.name
        out.mkdir(parents=True, exist_ok=True)
        (out / "inspection.md").write_text(inspection_md, encoding="utf-8")
        (out / "prompt.md").write_text(
            "<!-- SYSTEM PROMPT (asterism_step0.propose.SYSTEM_PROMPT) -->\n\n"
            f"{SYSTEM_PROMPT}\n\n---\n\n<!-- USER MESSAGE -->\n\n{user_message}",
            encoding="utf-8",
        )
        prepared.append(ds_dir.name)
    return prepared


def build_report(
    corpus: Path,
    runs: Path,
    *,
    allowed_fn_iris: set[str] | None,
    raw_rate_gate: float = DEFAULT_RAW_RATE_GATE,
) -> CorpusReport:
    """Analyze every ``runs/<id>/proposal.md`` against its dataset's inspection."""
    coverages: list[DatasetCoverage] = []
    for ds_dir in _dataset_dirs(corpus):
        run_dir = runs / ds_dir.name
        proposal = run_dir / "proposal.md"
        if not proposal.is_file():
            cov = DatasetCoverage(dataset=ds_dir.name, has_rml=False)
            cov.warnings.append("no proposal.md — run prepare, then author the proposal")
            coverages.append(cov)
            continue
        sources = _source_files(ds_dir)
        inspections, _ = inspect_source_set(sources)
        coverages.append(
            analyze_proposal(
                ds_dir.name, proposal.read_text(encoding="utf-8"), inspections, allowed_fn_iris
            )
        )
    return aggregate(coverages, raw_rate_gate=raw_rate_gate)


def _resolve_allowed_iris() -> tuple[set[str] | None, str | None]:
    """Best-effort allowed-IRI set from the ingest REGISTRY (None ⇒ skip T9)."""
    try:
        from asterism_step0.rml_check import load_registry_fn_iris

        return load_registry_fn_iris(), None
    except ImportError:
        return None, (
            "asterism (ingest) not importable — T9-miss check skipped. Install it "
            "(`uv pip install -e ../ingest`) to enable the closed-set demand signal."
        )


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="asterism-coverage",
        description=(
            "Measure Tier 0 function coverage over a corpus: `…Raw` fallback rate, "
            "T9 misses, and per-function usage. Decouples LLM generation (prepare) "
            "from deterministic analysis (report)."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("prepare", help="Write inspection.md + prompt.md per dataset.")
    pp.add_argument("--corpus", type=Path, required=True, help="Corpus root (has datasets/).")
    pp.add_argument("--runs", type=Path, required=True, help="Where to write per-dataset run dirs.")

    pr = sub.add_parser("report", help="Analyze proposals → coverage.md / coverage.json.")
    pr.add_argument("--corpus", type=Path, required=True, help="Corpus root (has datasets/).")
    pr.add_argument("--runs", type=Path, required=True, help="Run dirs holding proposal.md files.")
    pr.add_argument(
        "--out", type=Path, default=None, help="Output dir (default: <runs>/../report)."
    )
    pr.add_argument(
        "--gate",
        type=float,
        default=DEFAULT_RAW_RATE_GATE,
        help=f"`…Raw` rate gate (default {DEFAULT_RAW_RATE_GATE}).",
    )
    return p


def _main(argv: list[str] | None = None) -> int:
    import json
    import sys

    args = _build_arg_parser().parse_args(argv)

    if args.command == "prepare":
        prepared = prepare_prompts(args.corpus, args.runs)
        sys.stdout.write(f"Prepared {len(prepared)} dataset prompt(s) under {args.runs}:\n")
        for name in prepared:
            sys.stdout.write(f"  - {name}\n")
        sys.stdout.write(
            "\nNext: author each runs/<id>/proposal.md (follow prompt.md), then "
            "`asterism-coverage report`.\n"
        )
        return 0

    # report
    allowed, note = _resolve_allowed_iris()
    if note:
        sys.stderr.write(f"note: {note}\n")
    report = build_report(args.corpus, args.runs, allowed_fn_iris=allowed, raw_rate_gate=args.gate)
    out_dir = args.out or (args.runs.parent / "report")
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_report_md(report)
    (out_dir / "coverage.md").write_text(md + "\n", encoding="utf-8")
    (out_dir / "coverage.json").write_text(
        json.dumps(report_to_dict(report), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    sys.stdout.write(md + "\n")
    sys.stdout.write(f"\nWrote {out_dir / 'coverage.md'} and {out_dir / 'coverage.json'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
